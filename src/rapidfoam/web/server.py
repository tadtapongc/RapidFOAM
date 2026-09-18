"""FastAPI web server providing REST API and serving the desktop UI."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import math
import os
import re
import shlex
import shutil
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rapidfoam import __version__
from rapidfoam.config import DEFAULT_CONFIG, deep_merge, find_stl, user_set, validate
from rapidfoam.geometry import (
    FIDELITY_PRESETS,
    compute_domain_box,
    compute_mesh_params,
    flow_axis_index_sign,
    resolve_layers,
    up_axis_index,
)
from rapidfoam.postproc.forces import (
    check_convergence,
    find_coefficient_files,
    find_force_files,
    find_moment_files,
    is_symmetry_case,
    load_axis_config,
    normalize_coefficient_columns,
    normalize_component_columns,
    parse_tabular_dat,
    read_forces,
    window_stats,
)
from rapidfoam.postproc.residuals import find_residual_files, read_residuals
from rapidfoam.stl_utils import stl_info
from rapidfoam.web.ssh_client import ClusterSSHClient

log = logging.getLogger("rapidfoam.web")

app = FastAPI(title="RapidFOAM Studio", version=__version__)

# Restrict CORS to local origins only to protect credentials and SSH operations
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ssh_client = ClusterSSHClient()
CREDENTIALS_FILE = Path.home() / ".rapidfoam_cluster.json"
# Backwards compatibility: migrate from old file if exists
_OLD_CREDENTIALS_FILE = Path.home() / ".cfd_gen_cluster.json"
if not CREDENTIALS_FILE.exists() and _OLD_CREDENTIALS_FILE.exists():
    try:
        shutil.copy2(_OLD_CREDENTIALS_FILE, CREDENTIALS_FILE)
    except Exception:
        pass
PROJECT_ROOT = Path.cwd()

CASE_NAME_REGEX = re.compile(r"^[A-Za-z0-9_-]+$")
JOB_ID_REGEX = re.compile(r"^[0-9]+$")
ALLOWED_LOG_TYPES = {
    "simpleFoam",
    "convergenceMonitor",
    "snappyHexMesh",
    "surfaceFeatureExtract",
    "blockMesh",
    "checkMesh",
    "renumberMesh",
    "potentialFoam",
}

# In-memory progress for case downloads (case_name -> snapshot)
_download_progress: dict[str, dict[str, Any]] = {}
_download_progress_lock = threading.Lock()


def _set_download_progress(case_name: str, **fields: Any) -> None:
    with _download_progress_lock:
        _download_progress.setdefault(case_name, {}).update(fields)


def _get_download_progress(case_name: str) -> dict[str, Any]:
    with _download_progress_lock:
        return dict(_download_progress.get(case_name, {"active": False}))


def _list_download_progress(active_only: bool = False) -> list[dict[str, Any]]:
    with _download_progress_lock:
        items: list[dict[str, Any]] = []
        for name, state in _download_progress.items():
            entry = {"case_name": name, **state}
            if active_only and not entry.get("active"):
                continue
            items.append(entry)
        return items


def get_saved_cluster_config() -> dict[str, Any]:
    """Load cached cluster credentials if available."""
    if CREDENTIALS_FILE.exists():
        try:
            return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "host": "",
        "port": 22,
        "username": "",
        "remote_repo_path": "",
        "save_password": True,
    }


def _restrict_file_access(path: Path) -> None:
    """Best-effort restriction of a credential file to the current user."""
    try:
        path.chmod(0o600)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import getpass
            import subprocess

            user = getpass.getuser()
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass


def save_cluster_config(cfg: dict[str, Any]) -> None:
    """Save cluster credentials safely on user machine."""
    try:
        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        to_save = dict(cfg)
        if not to_save.get("save_password"):
            to_save.pop("password", None)
        CREDENTIALS_FILE.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
        _restrict_file_access(CREDENTIALS_FILE)
    except Exception as exc:
        log.warning("Could not persist cluster credentials: %s", exc)


def merge_config_with_defaults(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge user configuration and selective overrides on top of DEFAULT_CONFIG."""
    clean_cfg = {k: v for k, v in raw_cfg.items() if not k.startswith("_")}
    overrides = clean_cfg.pop("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("'overrides' must be an object")
    merged = deep_merge(DEFAULT_CONFIG, clean_cfg)
    if overrides:
        merged = deep_merge(merged, overrides)
    return merged


def layer_preview(merged: dict[str, Any], raw_cfg: dict[str, Any], bounds: tuple) -> dict[str, Any]:
    """Resolve the near-wall layer spec for the Studio preview without mutating it."""
    preview_cfg = copy.deepcopy(merged)
    preset = FIDELITY_PRESETS.get(preview_cfg.get("fidelity", "standard"), FIDELITY_PRESETS["standard"])
    layers = preview_cfg.setdefault("layers", {})
    if not user_set(raw_cfg, "layers", "n_layers"):
        layers["n_layers"] = preset.get("n_layers", layers.get("n_layers"))
    if not user_set(raw_cfg, "layers", "expansion_ratio"):
        layers["expansion_ratio"] = preset.get("expansion_ratio", layers.get("expansion_ratio"))
    explicit_first = user_set(raw_cfg, "layers", "first_layer_thickness")
    if not explicit_first and not user_set(raw_cfg, "layers", "y_plus_target"):
        if preset.get("y_plus_target") is not None:
            layers["y_plus_target"] = preset["y_plus_target"]
    preview_cfg["mesh_params"] = compute_mesh_params(preview_cfg, bounds)
    return resolve_layers(
        preview_cfg,
        bounds,
        explicit_first_layer=explicit_first,
        explicit_min_thickness=user_set(raw_cfg, "layers", "min_thickness"),
    )


# -------------------------------------------------------------
# Pydantic Request Models
# -------------------------------------------------------------

class SSHConnectRequest(BaseModel):
    host: str = ""
    port: int = 22
    username: str = ""
    password: Optional[str] = None
    key_path: Optional[str] = None
    remote_repo_path: str = ""
    save_password: bool = True


class JobSubmitRequest(BaseModel):
    case_name: str


class JobCancelRequest(BaseModel):
    job_id: str


class CaseDownloadRequest(BaseModel):
    case_name: str
    overwrite: bool = False


class DomainBoxRequest(BaseModel):
    config: dict[str, Any]
    bounds: Optional[dict[str, list[float]]] = None


class GenerateCaseRequest(BaseModel):
    config: dict[str, Any]
    upload_to_cluster: bool = False
    generate_remotely: bool = False
    submit_slurm: bool = False
    generate_locally: bool = True


# -------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------

@app.post("/api/geometry/domain-box")
async def api_geometry_domain_box(req: DomainBoxRequest) -> dict[str, Any]:
    """Compute and preview the OpenFOAM wind tunnel domain box."""
    cfg = req.config
    try:
        merged = merge_config_with_defaults(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid configuration format: {exc}")

    bounds_tuple = None
    if req.bounds and "min" in req.bounds and "max" in req.bounds:
        bounds_tuple = (req.bounds["min"], req.bounds["max"])
    else:
        # Calculate from active STL files in stl/
        stl_files = cfg.get("stl_files", [])
        all_min = [float("inf")] * 3
        all_max = [float("-inf")] * 3
        for sname in stl_files:
            safe_sname = Path(sname).name
            p = find_stl(PROJECT_ROOT / "stl", safe_sname)
            if p and p.is_file():
                try:
                    _, _, b = stl_info(p)
                    for i in range(3):
                        all_min[i] = min(all_min[i], b[0][i])
                        all_max[i] = max(all_max[i], b[1][i])
                except Exception:
                    pass
        if all_min[0] != float("inf"):
            bounds_tuple = (all_min, all_max)
        else:
            # Default reference geometry bounds (half-model)
            bounds_tuple = ([-0.7, 0.035, -1.8], [0.7, 1.1, 1.2])

    try:
        # If explicit domain_box coordinates are configured, use them for domain
        if isinstance(cfg.get("domain_box"), dict) and "min" in cfg["domain_box"] and "max" in cfg["domain_box"]:
            domain = cfg["domain_box"]
        else:
            domain = compute_domain_box(merged, bounds_tuple)

        flow_idx, _ = flow_axis_index_sign(merged)
        up_idx = up_axis_index(merged)
        lateral_idx = next(i for i in range(3) if i != flow_idx and i != up_idx)
        center_lateral = (bounds_tuple[0][lateral_idx] + bounds_tuple[1][lateral_idx]) / 2.0
        return {
            "domain_box": domain,
            "bounds": {"min": bounds_tuple[0], "max": bounds_tuple[1]},
            "auto_symmetry_plane": round(center_lateral, 4),
            "lateral_axis": "xyz"[lateral_idx],
            "layer_preview": layer_preview(merged, cfg, bounds_tuple),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/cluster/saved-config")
async def api_get_saved_config() -> dict[str, Any]:
    """Get cached connection settings without exposing password in plaintext."""
    cfg = get_saved_cluster_config()
    return {
        "host": cfg.get("host", ""),
        "port": cfg.get("port", 22),
        "username": cfg.get("username", ""),
        "remote_repo_path": cfg.get("remote_repo_path", ""),
        "has_saved_password": bool(cfg.get("password")),
        "key_path": cfg.get("key_path", ""),
    }


@app.post("/api/cluster/connect")
async def api_cluster_connect(req: SSHConnectRequest) -> dict[str, Any]:
    """Connect to the remote HPC cluster and test the environment."""
    saved_cfg = get_saved_cluster_config()
    password_to_use = req.password
    # If no password was provided but one is stored locally for this host/user, use it
    if not password_to_use and saved_cfg.get("password"):
        if (not req.host or req.host == saved_cfg.get("host")) and (not req.username or req.username == saved_cfg.get("username")):
            password_to_use = saved_cfg.get("password")

    try:
        res = await asyncio.to_thread(
            ssh_client.connect,
            host=req.host,
            username=req.username,
            password=password_to_use,
            key_path=req.key_path,
            port=req.port,
            remote_repo_path=req.remote_repo_path,
        )
        save_cluster_config({
            "host": req.host,
            "port": req.port,
            "username": req.username,
            "password": password_to_use if req.save_password else None,
            "key_path": req.key_path,
            "remote_repo_path": req.remote_repo_path,
            "save_password": req.save_password,
        })
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/cluster/status")
async def api_cluster_status() -> dict[str, Any]:
    """Get active SSH session and SLURM queue status."""
    connected = ssh_client.is_connected
    jobs = []
    if connected:
        try:
            jobs = await asyncio.to_thread(ssh_client.get_slurm_queue)
        except Exception:
            pass

    return {
        "connected": connected,
        "host": ssh_client.host,
        "username": ssh_client.username,
        "remote_repo_path": ssh_client.remote_repo_path,
        "active_jobs": jobs,
    }


@app.post("/api/cluster/disconnect")
async def api_cluster_disconnect() -> dict[str, Any]:
    """Disconnect SSH session."""
    await asyncio.to_thread(ssh_client.disconnect)
    return {"connected": False}


@app.get("/api/config/schema-defaults")
async def api_config_defaults() -> dict[str, Any]:
    """Return default config template and presets for the UI."""
    return {
        "default_config": DEFAULT_CONFIG,
        "fidelity_presets": {
            name: {
                "desc": p.get("desc", ""),
                "cell_estimate": p.get("cell_estimate", ""),
                "n_cells_target": p.get("n_cells_target", 0),
                "runtime_estimate": p.get("runtime_estimate", ""),
                "layers": {
                    "y_plus_target": p.get("y_plus_target"),
                    "n_layers": p.get("n_layers"),
                    "expansion_ratio": p.get("expansion_ratio"),
                    "ground_layers": p.get("ground_layers", False),
                },
                "mesh": {
                    "cells_per_length": p.get("cells_per_length"),
                    "base_cell_size": p.get("base_cell_size"),
                    "surface_level": p.get("surface_level"),
                    "edge_level": p.get("edge_level"),
                    "near_wake_level": p.get("near_wake_level"),
                    "far_wake_level": p.get("far_wake_level"),
                },
                "solver": {
                    "end_time": p.get("end_time"),
                    "write_interval": p.get("write_interval"),
                },
            }
            for name, p in FIDELITY_PRESETS.items()
        },
    }


@app.get("/api/config/templates")
async def api_config_templates() -> list[dict[str, Any]]:
    """List available config files in configs/ folder."""
    cfg_dir = PROJECT_ROOT / "configs"
    templates = []
    if cfg_dir.is_dir():
        for p in sorted(cfg_dir.glob("*.json")):
            try:
                content = json.loads(p.read_text(encoding="utf-8"))
                templates.append({
                    "filename": p.name,
                    "case_name": content.get("case_name", p.stem),
                    "stl_files": content.get("stl_files", []),
                    "fidelity": content.get("fidelity", "standard"),
                })
            except Exception:
                templates.append({"filename": p.name, "case_name": p.stem, "stl_files": []})
    return templates


@app.get("/api/config/load-file")
async def api_config_load_file(filename: str = "config.json") -> dict[str, Any]:
    """Load and return JSON content of a specific config file."""
    safe_filename = Path(filename).name
    cfg_dir = (PROJECT_ROOT / "configs").resolve()
    path = (cfg_dir / safe_filename).resolve()
    if not path.is_relative_to(cfg_dir) or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Config file {safe_filename} not found")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        merged = merge_config_with_defaults(content)
        return {
            "filename": safe_filename,
            "raw_config": content,
            "merged_config": merged,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error reading config: {exc}")


@app.get("/api/stl/list")
async def api_stl_list() -> list[dict[str, Any]]:
    """List local STL files with geometric bounds and metadata."""
    stl_dir = PROJECT_ROOT / "stl"
    stls = []
    seen_paths = set()
    if stl_dir.is_dir():
        # Deduplicate paths (prevent duplicates on case-insensitive filesystems like Windows)
        all_candidates = sorted(stl_dir.glob("*.stl")) + sorted(stl_dir.glob("*.STL"))
        for p in all_candidates:
            resolved = p.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                solid_name, n_facets, bounds = stl_info(p)
                (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds
                dx = xmax - xmin
                dy = ymax - ymin
                dz = zmax - zmin
                is_likely_mm = max(dx, dy, dz) > 20.0
                stls.append({
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                    "format": "ASCII STL",
                    "solid_name": solid_name,
                    "triangles": n_facets,
                    "bounds": {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax]},
                    "dimensions": [dx, dy, dz],
                    "is_likely_mm": is_likely_mm,
                })
            except Exception as exc:
                stls.append({"filename": p.name, "size_bytes": p.stat().st_size, "error": str(exc)})
    return stls


@app.get("/api/stl/file/{filename}")
async def api_get_stl_file(filename: str):
    """Serve a local STL file by name."""
    safe_filename = Path(filename).name
    stl_dir = (PROJECT_ROOT / "stl").resolve()
    p = find_stl(stl_dir, safe_filename)
    if not p or not p.is_file() or not p.resolve().is_relative_to(stl_dir):
        raise HTTPException(status_code=404, detail=f"STL file '{safe_filename}' not found")
    return FileResponse(path=p, media_type="application/octet-stream", filename=p.name)


@app.get("/api/stl/check-exists")
async def api_stl_check_exists(filename: str) -> dict[str, Any]:
    """Check if an STL file already exists locally or on remote cluster."""
    raw_name = Path(filename).name
    safe_name = raw_name if raw_name.lower().endswith(".stl") else f"{raw_name}.stl"

    stl_dir = (PROJECT_ROOT / "stl").resolve()
    local_exists = (stl_dir / safe_name).is_file()

    cluster_exists = False
    if ssh_client.is_connected:
        try:
            remote_path = f"{ssh_client.remote_repo_path}/stl/{safe_name}"
            cluster_exists = ssh_client.remote_file_exists(remote_path)
        except Exception:
            pass

    return {
        "filename": safe_name,
        "exists": local_exists or cluster_exists,
        "local_exists": local_exists,
        "cluster_exists": cluster_exists,
    }


@app.get("/api/case/check-exists")
async def api_case_check_exists(case_name: str) -> dict[str, Any]:
    """Check if a case name already exists locally or on cluster, and whether it's currently running."""
    safe_name = case_name.strip()
    if not safe_name:
        return {"case_name": "", "exists": False, "local_exists": False, "cluster_exists": False, "is_running": False}

    local_case = (PROJECT_ROOT / "cases" / safe_name).is_dir()
    local_cfg = (PROJECT_ROOT / "configs" / f"{safe_name}.json").is_file()
    local_exists = local_case or local_cfg

    cluster_exists = False
    is_running = False
    if ssh_client.is_connected:
        try:
            remote_case = f"{ssh_client.remote_repo_path}/cases/{safe_name}"
            remote_cfg = f"{ssh_client.remote_repo_path}/configs/{safe_name}.json"
            cluster_exists = ssh_client.remote_file_exists(remote_case) or ssh_client.remote_file_exists(remote_cfg)
            is_running = ssh_client.is_case_running(safe_name)
        except Exception:
            pass

    return {
        "case_name": safe_name,
        "exists": local_exists or cluster_exists,
        "local_exists": local_exists,
        "cluster_exists": cluster_exists,
        "is_running": is_running,
    }


@app.post("/api/stl/upload")
async def api_stl_upload(
    file: UploadFile = File(...),
    override_name: Optional[str] = Form(None),
) -> dict[str, Any]:
    """Upload an STL file to local stl/ directory and inspect its bounds."""
    chosen_name = (override_name.strip() if override_name else "") or file.filename or "uploaded.stl"
    safe_name = Path(chosen_name).name
    if not safe_name.lower().endswith(".stl"):
        safe_name = f"{safe_name}.stl"

    stl_dir = (PROJECT_ROOT / "stl").resolve()
    stl_dir.mkdir(exist_ok=True)

    dest = (stl_dir / safe_name).resolve()
    if not dest.is_relative_to(stl_dir):
        raise HTTPException(status_code=400, detail="Invalid destination path")

    content = await file.read()
    dest.write_bytes(content)

    try:
        solid_name, n_facets, bounds = stl_info(dest)
        (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds
        dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
        return {
            "success": True,
            "filename": safe_name,
            "size_bytes": len(content),
            "format": "ASCII STL",
            "solid_name": solid_name,
            "triangles": n_facets,
            "bounds": {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax]},
            "dimensions": [dx, dy, dz],
            "is_likely_mm": max(dx, dy, dz) > 20.0,
        }
    except Exception as exc:
        return {
            "success": True,
            "filename": safe_name,
            "size_bytes": len(content),
            "warning": f"Uploaded, but geometry inspection failed: {exc}",
        }


@app.post("/api/case/generate-and-submit")
async def api_case_generate_and_submit(req: GenerateCaseRequest) -> dict[str, Any]:
    """Generate OpenFOAM case locally and/or on cluster, and optionally submit SLURM job."""
    cfg = req.config
    case_name = cfg.get("case_name", "").strip()
    if not case_name:
        raise HTTPException(status_code=400, detail="case_name is required")
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="case_name must contain only alphanumeric characters, underscores, and hyphens")

    # 1. Validate config
    try:
        merged = merge_config_with_defaults(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid configuration format: {exc}")
    errors, warnings = validate(merged, PROJECT_ROOT)
    if errors:
        raise HTTPException(status_code=400, detail=f"Config validation errors: {', '.join(errors)}")

    # 2. Save config locally only when an action actually uses it. A pure
    #    validation request must not create or overwrite a config file.
    local_cfg_path = PROJECT_ROOT / "configs" / f"{case_name}.json"
    if req.generate_locally or req.upload_to_cluster:
        cfg_dir = PROJECT_ROOT / "configs"
        cfg_dir.mkdir(exist_ok=True)
        local_cfg_path.write_text(json.dumps(cfg, indent=4) + "\n", encoding="utf-8")

    local_actions: dict[str, Any] = {}
    cluster_actions: dict[str, Any] = {}

    # 3. Generate locally if requested
    if req.generate_locally:
        from rapidfoam.cli import _do_generate
        try:
            await asyncio.to_thread(_do_generate, local_cfg_path, PROJECT_ROOT, dry_run=False)
            local_actions["generated_locally"] = True
            local_actions["case_path"] = str(PROJECT_ROOT / "cases" / case_name)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Local case generation failed: {exc}")

    # 4. Cluster synchronization and remote execution
    if req.upload_to_cluster:
        if not ssh_client.is_connected:
            raise HTTPException(status_code=400, detail="Not connected to cluster. Please connect via SSH first.")

        remote_repo = ssh_client.remote_repo_path

        # Upload STLs used in this config
        stl_files = cfg.get("stl_files", [])
        uploaded_stls = []
        for sname in stl_files:
            safe_sname = Path(sname).name
            local_stl = find_stl(PROJECT_ROOT / "stl", safe_sname)
            if local_stl and local_stl.is_file():
                remote_stl = f"{remote_repo}/stl/{local_stl.name}"
                await asyncio.to_thread(ssh_client.upload_file, local_stl, remote_stl)
                uploaded_stls.append(sname)

        cluster_actions["uploaded_stls"] = uploaded_stls

        # Upload config JSON
        remote_cfg = f"{remote_repo}/configs/{case_name}.json"
        await asyncio.to_thread(ssh_client.upload_text, json.dumps(cfg, indent=4) + "\n", remote_cfg)
        cluster_actions["uploaded_config"] = remote_cfg

        # Execute setup_case.py remotely
        if req.generate_remotely:
            gen_cmd = f"cd {shlex.quote(remote_repo)} && python3 setup_case.py {shlex.quote(f'configs/{case_name}.json')}"
            code, out, err = await asyncio.to_thread(ssh_client.run_command, gen_cmd, timeout=45)
            cluster_actions["setup_exit_code"] = code
            cluster_actions["setup_output"] = out.strip()
            cluster_actions["setup_error"] = err.strip()

            if code != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Remote setup_case.py failed with exit code {code}: {err or out}",
                )

        # Submit SLURM job
        if req.submit_slurm:
            submit_res = await asyncio.to_thread(ssh_client.submit_job, case_name)
            cluster_actions["slurm_submit"] = submit_res

    return {
        "success": True,
        "case_name": case_name,
        "warnings": warnings,
        "local_actions": local_actions,
        "cluster_actions": cluster_actions,
    }


@app.post("/api/case/submit")
async def api_case_submit(req: JobSubmitRequest) -> dict[str, Any]:
    """Submit sbatch for an existing case on the cluster."""
    if not CASE_NAME_REGEX.match(req.case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")
    if not ssh_client.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to cluster")
    res = await asyncio.to_thread(ssh_client.submit_job, req.case_name)
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Failed to submit job"))
    return res


@app.post("/api/case/cancel")
async def api_case_cancel(req: JobCancelRequest) -> dict[str, Any]:
    """Cancel a SLURM job."""
    job_id = str(req.job_id).strip()
    if not JOB_ID_REGEX.match(job_id):
        raise HTTPException(status_code=400, detail="job_id must be numeric")
    if not ssh_client.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to cluster")
    res = await asyncio.to_thread(ssh_client.cancel_job, job_id)
    return res


def _run_download(case_name: str, remote_case: str, local_dir: Path) -> None:
    """Background worker: mirror a remote case into the local cases/ folder."""

    def _progress(snapshot: dict[str, Any]) -> None:
        _set_download_progress(
            case_name,
            active=True,
            done=False,
            error=None,
            files=snapshot.get("files", 0),
            dirs=snapshot.get("dirs", 0),
            bytes=snapshot.get("bytes", 0),
            total_bytes=snapshot.get("total_bytes", 0),
        )

    _progress({"files": 0, "dirs": 0, "bytes": 0, "total_bytes": 0})
    try:
        stats = ssh_client.download_directory(remote_case, local_dir, _progress)
    except Exception as exc:
        _set_download_progress(case_name, active=False, done=True, error=str(exc))
        log.warning("Case download failed for %s: %s", case_name, exc)
        return
    _set_download_progress(
        case_name,
        active=False,
        done=True,
        error=None,
        files=stats.get("files", 0),
        dirs=stats.get("dirs", 0),
        bytes=stats.get("bytes", 0),
        total_bytes=stats.get("total_bytes", 0),
    )


@app.post("/api/case/download")
async def api_case_download(req: CaseDownloadRequest) -> dict[str, Any]:
    """Start mirroring a cluster case into the local cases/ folder.

    The transfer runs in a background executor thread, so the request returns
    immediately and the browser may navigate or reload while it continues.
    Poll ``/api/case/download/progress`` for status.
    """
    if not CASE_NAME_REGEX.match(req.case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")
    if not ssh_client.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to cluster")

    remote_case = f"{ssh_client.remote_repo_path}/cases/{req.case_name}"
    if not await asyncio.to_thread(ssh_client.remote_file_exists, remote_case):
        raise HTTPException(status_code=404, detail=f"Case '{req.case_name}' not found on cluster")

    if _get_download_progress(req.case_name).get("active"):
        return {"success": True, "already_running": True, "case_name": req.case_name}

    local_dir = PROJECT_ROOT / "cases" / req.case_name
    if not req.overwrite and local_dir.is_dir() and any(local_dir.iterdir()):
        raise HTTPException(
            status_code=409,
            detail=f"Case '{req.case_name}' already exists locally. Set overwrite to re-download.",
        )

    _set_download_progress(
        req.case_name,
        active=True, done=False, error=None,
        files=0, dirs=0, bytes=0, total_bytes=0,
    )
    asyncio.get_running_loop().run_in_executor(
        None, _run_download, req.case_name, remote_case, local_dir
    )
    return {"success": True, "started": True, "case_name": req.case_name}


@app.get("/api/case/download/active")
async def api_case_download_active() -> dict[str, Any]:
    """List in-progress case downloads (used to restore the UI after reload)."""
    return {"downloads": _list_download_progress(active_only=True)}


@app.get("/api/case/download/progress")
async def api_case_download_progress(case_name: str) -> dict[str, Any]:
    """Return live progress for an in-flight case download."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")
    return _get_download_progress(case_name)


def _read_text_files(paths: list[Path]) -> list[str]:
    """Read a list of files into memory, skipping unreadable entries."""
    segments: list[str] = []
    for path in paths:
        try:
            segments.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return segments


async def _read_remote_dat_segments(case_dir: str, subdir: str, filename: str) -> list[str]:
    """Read every ``<time>/<filename>`` under a remote postProcessing subdir."""
    base = f"{case_dir}/postProcessing/{subdir}"
    cmd = f"ls -1 {shlex.quote(base)} 2>/dev/null | sort -n"
    code, out, _ = await asyncio.to_thread(ssh_client.run_command, cmd, timeout=5)
    segments: list[str] = []
    if code != 0 or not out.strip():
        return segments
    for entry in out.strip().splitlines():
        entry = entry.strip()
        if re.match(r"^[0-9.]+$", entry):
            content = await asyncio.to_thread(
                ssh_client.read_remote_text, f"{base}/{entry}/{filename}"
            )
            if content:
                segments.append(content)
    return segments


def _subsample_indices(count: int, max_pts: int = 400) -> list[int]:
    """Evenly spaced indices that always retain the first and last sample."""
    if count <= max_pts:
        return list(range(count))
    step = math.ceil(count / max_pts)
    indices = list(range(0, count, step))
    if indices[-1] != count - 1:
        indices.append(count - 1)
    return indices


def _downsample_columns(cols: dict[str, list[float]], indices: list[int]) -> dict[str, list[float]]:
    return {
        name: [round(values[i], 4) for i in indices if i < len(values)]
        for name, values in cols.items()
    }


def _latest_vector(cols: dict[str, list[float]], kind: str) -> Optional[list[float]]:
    keys = [f"{kind}_{axis}" for axis in ("x", "y", "z")]
    if all(key in cols and cols[key] for key in keys):
        return [round(cols[key][-1], 4) for key in keys]
    return None


def _load_reference_quantities(
    cfg_path: Optional[str], case_dir: Optional[Path] = None
) -> dict[str, Any]:
    rho, velocity, aref, lref = 1.225, 0.0, 1.0, 1.0
    cofr: list[float] = [0.0, 0.0, 0.0]
    cfg_obj: Optional[dict[str, Any]] = None
    for candidate in (cfg_path, str(case_dir / "case_config.json") if case_dir else None):
        if not candidate:
            continue
        try:
            with open(candidate, encoding="utf-8") as handle:
                cfg_obj = json.load(handle)
            break
        except Exception:
            continue
    if cfg_obj:
        try:
            rho = float(cfg_obj.get("fluid", {}).get("rho", rho) or rho)
            velocity = float(cfg_obj.get("flow", {}).get("velocity", 0.0) or 0.0)
            aref = float(cfg_obj.get("force_refs", {}).get("Aref", 1.0) or 1.0)
            lref = float(cfg_obj.get("force_refs", {}).get("lRef", 1.0) or 1.0)
            raw_cofr = cfg_obj.get("force_refs", {}).get("CofR")
            if isinstance(raw_cofr, (list, tuple)) and len(raw_cofr) == 3:
                cofr = [float(v) for v in raw_cofr]
        except Exception:
            pass
    return {
        "rho": round(rho, 6),
        "velocity": round(velocity, 4),
        "Aref": round(aref, 6),
        "lRef": round(lref, 6),
        "CofR": [round(v, 6) for v in cofr],
        "dynamic_pressure": round(0.5 * rho * velocity * velocity, 4),
    }


def _axis_vector(index: int, sign: int) -> tuple[float, float, float]:
    vector = [0.0, 0.0, 0.0]
    vector[index] = float(sign)
    return (vector[0], vector[1], vector[2])


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _compute_coefficients(
    force_cols: dict[str, list[float]],
    moment_cols: dict[str, list[float]],
    drag_idx: int,
    drag_sign: int,
    df_idx: int,
    df_sign: int,
    rho: float,
    velocity: float,
    aref: float,
    lref: float,
    cofr_run: list[float],
    cofr_effective: list[float],
) -> dict[str, list[float]]:
    """Normalize raw force/moment histories with configurable reference values.

    Moments from the run are taken about ``cofr_run``; when ``cofr_effective``
    differs, they are shifted with ``M_new = M_old + (r_run - r_new) x F``.
    Axes follow the same conventions as the OpenFOAM ``forceCoeffs`` function
    object (dragging/lift directions plus pitch/roll/yaw about drag/lift/side).
    """
    dynamic_pressure = 0.5 * rho * velocity * velocity
    if dynamic_pressure <= 0 or aref <= 0:
        return {}
    force_keys = ("total_x", "total_y", "total_z")
    if not all(key in force_cols and force_cols[key] for key in force_keys):
        return {}

    drag_dir = _axis_vector(drag_idx, drag_sign)
    lift_dir = _axis_vector(df_idx, df_sign)
    side_dir = _cross(lift_dir, drag_dir)
    roll_axis, pitch_axis, yaw_axis = drag_dir, side_dir, lift_dir

    count = len(force_cols["total_x"])
    has_moment = all(
        key in moment_cols and len(moment_cols[key]) == count for key in force_keys
    )
    shift = None
    if has_moment and list(cofr_run) != list(cofr_effective):
        shift = (
            cofr_run[0] - cofr_effective[0],
            cofr_run[1] - cofr_effective[1],
            cofr_run[2] - cofr_effective[2],
        )

    q_area = dynamic_pressure * aref
    q_area_length = q_area * lref if lref > 0 else 0.0

    series: dict[str, list[float]] = {"Cd": [], "Cl": [], "Cs": []}
    if has_moment and q_area_length > 0:
        series["CmPitch"] = []
        series["CmRoll"] = []
        series["CmYaw"] = []

    for index in range(count):
        force = (
            force_cols["total_x"][index],
            force_cols["total_y"][index],
            force_cols["total_z"][index],
        )
        series["Cd"].append(_dot(force, drag_dir) / q_area)
        series["Cl"].append(_dot(force, lift_dir) / q_area)
        series["Cs"].append(_dot(force, side_dir) / q_area)

        if has_moment and q_area_length > 0:
            moment = (
                moment_cols["total_x"][index],
                moment_cols["total_y"][index],
                moment_cols["total_z"][index],
            )
            if shift:
                offset = _cross(shift, force)
                moment = (
                    moment[0] + offset[0],
                    moment[1] + offset[1],
                    moment[2] + offset[2],
                )
            series["CmPitch"].append(_dot(moment, pitch_axis) / q_area_length)
            series["CmRoll"].append(_dot(moment, roll_axis) / q_area_length)
            series["CmYaw"].append(_dot(moment, yaw_axis) / q_area_length)

    return series


@app.get("/api/telemetry/forces")
async def api_telemetry_forces(
    case_name: str,
    aref: Optional[float] = None,
    lref: Optional[float] = None,
    rho: Optional[float] = None,
    velocity: Optional[float] = None,
    cofr: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch force, coefficient, and component telemetry for a case.

    Drag/downforce histories are axis-mapped and doubled for symmetry cases.
    Coefficient histories prefer the solver's ``forceCoeffs`` output, but can
    be recomputed from raw forces using post-run reference overrides supplied
    as ``aref``/``lref``/``rho``/``velocity``/``cofr`` query parameters.
    """
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    local_case = PROJECT_ROOT / "cases" / case_name
    local_cfg = PROJECT_ROOT / "configs" / f"{case_name}.json"
    cfg_path = str(local_cfg) if local_cfg.is_file() else None
    case_dir = local_case if local_case.is_dir() else None

    drag_idx, drag_sign, df_idx, df_sign, drag_axis_name, df_axis_name = load_axis_config(
        config_path=cfg_path, case_dir=case_dir
    )
    is_sym = is_symmetry_case(config_path=cfg_path, case_dir=case_dir)
    sym_scale = 2.0 if is_sym else 1.0
    reference = _load_reference_quantities(cfg_path, case_dir)
    cofr_run = list(reference.get("CofR", [0.0, 0.0, 0.0]))

    # Post-run reference overrides from the inline telemetry editor. Blank
    # fields fall back to the case configuration.
    reference_overridden = any(
        value is not None for value in (aref, lref, rho, velocity, cofr)
    )
    if rho is not None:
        reference["rho"] = round(float(rho), 6)
    if velocity is not None:
        reference["velocity"] = round(float(velocity), 4)
    if aref is not None:
        reference["Aref"] = round(float(aref), 6)
    if lref is not None:
        reference["lRef"] = round(float(lref), 6)
    cofr_effective = cofr_run
    if cofr is not None:
        try:
            parsed_cofr = [float(part) for part in str(cofr).split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="cofr must be three comma-separated numbers")
        if len(parsed_cofr) != 3:
            raise HTTPException(status_code=400, detail="cofr must be three comma-separated numbers")
        cofr_effective = parsed_cofr
    reference["CofR"] = [round(v, 6) for v in cofr_effective]
    reference["dynamic_pressure"] = round(
        0.5 * reference["rho"] * reference["velocity"] * reference["velocity"], 4
    )

    force_segments: list[str] = []
    moment_segments: list[str] = []
    coeff_segments: list[str] = []

    if ssh_client.is_connected:
        remote_case_dir = f"{ssh_client.remote_repo_path}/cases/{case_name}"
        try:
            force_segments = await _read_remote_dat_segments(remote_case_dir, "forces", "force.dat")
            moment_segments = await _read_remote_dat_segments(remote_case_dir, "forces", "moment.dat")
            coeff_segments = await _read_remote_dat_segments(
                remote_case_dir, "forceCoeffs", "coefficient.dat"
            )
        except Exception as exc:
            log.warning("Remote force telemetry read failed for %s: %s", case_name, exc)

    if not force_segments and local_case.is_dir():
        force_segments = _read_text_files(find_force_files(local_case))
        moment_segments = _read_text_files(find_moment_files(local_case))
        coeff_segments = _read_text_files(find_coefficient_files(local_case))

    times, force_rows, force_header = parse_tabular_dat(force_segments)
    force_cols = normalize_component_columns(force_rows, force_header)

    raw_drags: list[float] = []
    raw_downforces: list[float] = []
    if times:
        drag_key = f"total_{'xyz'[drag_idx]}"
        df_key = f"total_{'xyz'[df_idx]}"
        if drag_key in force_cols and df_key in force_cols:
            raw_drags = [v * drag_sign for v in force_cols[drag_key]]
            raw_downforces = [v * df_sign for v in force_cols[df_key]]
        else:
            legacy_files = find_force_files(local_case) if local_case.is_dir() else []
            if legacy_files:
                try:
                    times, raw_drags, raw_downforces = read_forces(
                        legacy_files, drag_idx, drag_sign, df_idx, df_sign
                    )
                except Exception as exc:
                    log.warning("Could not read forces for %s: %s", case_name, exc)

    if not times:
        case_stage = "Generated"
        has_mesh = False
        if local_case.is_dir():
            if (local_case / "constant" / "polyMesh" / "points").is_file():
                has_mesh = True
                case_stage = "Meshed"
            if (local_case / "log.simpleFoam").is_file():
                case_stage = "Solving"
            elif (local_case / "log.snappyHexMesh").is_file():
                case_stage = "Meshing"

        return {
            "has_data": False,
            "case_name": case_name,
            "stage": case_stage,
            "has_mesh": has_mesh,
            "run_command": "./Allrun.parallel",
            "message": f"No force.dat found yet for case '{case_name}'. Current stage: {case_stage}.",
            "reference": reference,
            "coefficients": {"available": False, "summary": {}, "series": {}},
            "components": {"available": False},
        }

    # Apply symmetry doubling to full-car projections.
    drags = [v * sym_scale for v in raw_drags]
    downforces = [v * sym_scale for v in raw_downforces]
    if sym_scale != 1.0 and force_cols:
        force_cols = {name: [v * sym_scale for v in values] for name, values in force_cols.items()}

    converged, d_pct, f_pct, d_avg, f_avg = check_convergence(drags, downforces)
    ld_ratio = abs(f_avg / d_avg) if abs(d_avg) > 1e-3 else 0.0

    # ---- Components (force + moment), scaled to full-car if symmetric ----
    moment_times, moment_rows, moment_header = parse_tabular_dat(moment_segments)
    moment_cols = normalize_component_columns(moment_rows, moment_header)
    if sym_scale != 1.0 and moment_cols:
        moment_cols = {name: [v * sym_scale for v in values] for name, values in moment_cols.items()}

    # ---- Coefficients (solver output, recomputed, or config fallback) ----
    coeff_times, coeff_rows, coeff_header = parse_tabular_dat(coeff_segments)
    coeff_cols = normalize_coefficient_columns(coeff_rows, coeff_header)
    coeff_source = "forceCoeffs"
    recomputed: dict[str, list[float]] = {}
    if reference_overridden and force_cols:
        recomputed = _compute_coefficients(
            force_cols,
            moment_cols,
            drag_idx,
            drag_sign,
            df_idx,
            df_sign,
            reference["rho"],
            reference["velocity"],
            reference["Aref"],
            reference["lRef"],
            cofr_run,
            cofr_effective,
        )
    if recomputed:
        coeff_cols = recomputed
        coeff_times = times
        coeff_source = "recomputed"
    elif coeff_cols:
        if sym_scale != 1.0:
            coeff_cols = {name: [v * sym_scale for v in values] for name, values in coeff_cols.items()}
    else:
        velocity_ref = reference["velocity"]
        dynamic_pressure_area = (
            0.5 * reference["rho"] * velocity_ref * velocity_ref * reference["Aref"]
        )
        if dynamic_pressure_area > 0:
            coeff_cols = {
                "Cd": [v / dynamic_pressure_area for v in drags],
                "Cl": [v / dynamic_pressure_area for v in downforces],
            }
            coeff_times = times
            coeff_source = "computed"

    coeff_summary: dict[str, dict[str, Optional[float]]] = {}
    for name in ("Cd", "Cl", "Cs", "CmPitch", "CmRoll", "CmYaw"):
        values = coeff_cols.get(name)
        if values:
            avg, pct = window_stats(values)
            coeff_summary[name] = {
                "current": round(values[-1], 5),
                "avg": round(avg, 5) if avg is not None else None,
                "pct": round(pct, 3) if pct is not None else None,
            }

    sub_indices = _subsample_indices(len(times))
    coeff_sub_indices = _subsample_indices(len(coeff_times)) if coeff_times else []

    # ---- Force & moment component breakdown ----
    moment_sub_indices = _subsample_indices(len(moment_times)) if moment_times else []

    force_components = {
        "available": bool(force_cols),
        "iterations": [times[i] for i in sub_indices],
        "series": _downsample_columns(force_cols, sub_indices),
        "latest": {
            "total": _latest_vector(force_cols, "total"),
            "pressure": _latest_vector(force_cols, "pressure"),
            "viscous": _latest_vector(force_cols, "viscous"),
        },
    }
    moment_components = {
        "available": bool(moment_cols),
        "iterations": [moment_times[i] for i in moment_sub_indices],
        "series": _downsample_columns(moment_cols, moment_sub_indices),
        "latest": {
            "total": _latest_vector(moment_cols, "total"),
            "pressure": _latest_vector(moment_cols, "pressure"),
            "viscous": _latest_vector(moment_cols, "viscous"),
        },
    }

    return {
        "has_data": True,
        "case_name": case_name,
        "is_symmetry": is_sym,
        "drag_axis": drag_axis_name,
        "downforce_axis": df_axis_name,
        "total_iterations": len(times),
        "latest_iteration": times[-1] if times else 0,
        "converged": converged,
        "drag_avg": round(d_avg, 3),
        "downforce_avg": round(f_avg, 3),
        "drag_pct": round(d_pct, 3),
        "downforce_pct": round(f_pct, 3),
        "ld_ratio": round(ld_ratio, 3),
        "reference": reference,
        "series": {
            "iterations": [times[i] for i in sub_indices],
            "drag": [round(drags[i], 3) for i in sub_indices],
            "downforce": [round(downforces[i], 3) for i in sub_indices],
        },
        "coefficients": {
            "available": bool(coeff_cols),
            "source": coeff_source if coeff_cols else None,
            "summary": coeff_summary,
            "series": {
                "iterations": [coeff_times[i] for i in coeff_sub_indices],
                **_downsample_columns(coeff_cols, coeff_sub_indices),
            },
        },
        "components": {
            "available": bool(force_cols) or bool(moment_cols),
            "force": force_components,
            "moment": moment_components,
        },
    }


@app.get("/api/telemetry/export")
async def api_telemetry_export(case_name: str, format: str = "csv") -> Response:
    """Export the telemetry histories as CSV or JSON for offline analysis."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    data = await api_telemetry_forces(case_name)
    if not data.get("has_data"):
        raise HTTPException(status_code=404, detail=f"No telemetry available for case '{case_name}'")

    if format.lower() == "json":
        payload = json.dumps(data, indent=2)
        return Response(
            content=payload,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{case_name}_telemetry.json"'},
        )

    series = data.get("series", {})
    iterations = series.get("iterations", [])
    drags = series.get("drag", [])
    downforces = series.get("downforce", [])

    coeff_series = (data.get("coefficients") or {}).get("series", {})
    coeff_names = [name for name in ("Cd", "Cl", "Cs", "CmPitch", "CmRoll", "CmYaw") if name in coeff_series]
    coeff_lookup: dict[str, dict[float, Optional[float]]] = {}
    for name in coeff_names:
        coeff_lookup[name] = {
            round(t, 8): v
            for t, v in zip(coeff_series.get("iterations", []), coeff_series[name])
        }

    header = ["iteration", "drag_N", "downforce_N", "L_over_D"] + coeff_names
    lines = [",".join(header)]
    for index, iteration in enumerate(iterations):
        drag = drags[index] if index < len(drags) else None
        downforce = downforces[index] if index < len(downforces) else None
        ld = abs(downforce / drag) if drag not in (None, 0) and downforce is not None else None
        row = [
            str(iteration),
            "" if drag is None else f"{drag:.4f}",
            "" if downforce is None else f"{downforce:.4f}",
            "" if ld is None else f"{ld:.4f}",
        ]
        key = round(float(iteration), 8)
        for name in coeff_names:
            value = coeff_lookup[name].get(key)
            row.append("" if value is None else f"{value:.6f}")
        lines.append(",".join(row))

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{case_name}_telemetry.csv"'},
    )


def parse_residuals_from_log(log_text: str) -> dict[float, dict[str, float]]:
    """Extract initial residuals from OpenFOAM solver log text."""
    pattern = re.compile(
        r"Solving for (p|Ux|Uy|Uz|k|omega|epsilon|nuTilda),\s+Initial residual\s+=\s+([0-9\.eE\+\-]+)",
        re.IGNORECASE,
    )
    rows: dict[float, dict[str, float]] = {}
    current_iter: Optional[float] = None
    for line in log_text.splitlines():
        if "Time = " in line:
            try:
                current_iter = float(line.split("Time = ")[-1].strip())
            except ValueError:
                pass
        if current_iter is not None and current_iter > 0:
            match = pattern.search(line)
            if match:
                raw_var = match.group(1)
                v_lower = raw_var.lower()
                if v_lower == "p":
                    var = "p"
                elif v_lower == "ux":
                    var = "Ux"
                elif v_lower == "uy":
                    var = "Uy"
                elif v_lower == "uz":
                    var = "Uz"
                elif v_lower == "k":
                    var = "k"
                else:
                    var = "omega"
                t_key = round(current_iter, 8)
                if t_key not in rows:
                    rows[t_key] = {}
                # Keep the FIRST residual for each variable in this time-step (initial residual)
                if var not in rows[t_key]:
                    try:
                        val = float(match.group(2))
                        if math.isfinite(val) and val > 0:
                            rows[t_key][var] = val
                    except ValueError:
                        pass
    return rows


def parse_solver_info_text(content: str) -> dict[float, dict[str, float]]:
    """Parse tabulated residuals from solverInfo.dat or residuals.dat."""
    headers: list[str] = []
    rows: dict[float, dict[str, float]] = {}
    segment_started = False
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Time" in line:
                headers = line.strip("# \t\r\n").split()
            continue
        if not headers:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            t = float(parts[0])
        except ValueError:
            continue
        t_key = round(t, 8)
        if not segment_started:
            rows = {k: v for k, v in rows.items() if k < t_key}
            segment_started = True
        row: dict[str, float] = {}
        for i, h in enumerate(headers):
            if i < len(parts):
                try:
                    val = float(parts[i])
                    if math.isfinite(val) and val > 0:
                        row[h] = val
                except ValueError:
                    pass
        rows[t_key] = row
    return rows


@app.get("/api/telemetry/residuals")
async def api_telemetry_residuals(case_name: str) -> dict[str, Any]:
    """Parse solver residuals from solverInfo.dat or log.simpleFoam with aligned iterations."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    rows: dict[float, dict[str, float]] = {}

    # 1. Check remote cluster first if connected
    if ssh_client.is_connected:
        find_cmd = (
            f"cd {shlex.quote(ssh_client.remote_repo_path)} && "
            f"find cases/{shlex.quote(case_name)}/postProcessing/residuals "
            f"\\( -name 'solverInfo.dat' -o -name 'residuals.dat' \\) 2>/dev/null | sort -V"
        )
        code, out, _ = await asyncio.to_thread(ssh_client.run_command, find_cmd, timeout=5)
        if code == 0 and out.strip():
            remote_files = [f.strip() for f in out.strip().splitlines() if f.strip()]
            if remote_files:
                target_path = f"{ssh_client.remote_repo_path}/{remote_files[-1]}"
                cat_code, content, _ = await asyncio.to_thread(
                    ssh_client.run_command, f"cat {shlex.quote(target_path)}", timeout=10
                )
                if cat_code == 0 and content.strip():
                    rows = parse_solver_info_text(content)

        # Fallback to grep on remote log.simpleFoam if no solverInfo.dat
        if not rows:
            grep_cmd = (
                f"cd {shlex.quote(ssh_client.remote_repo_path)} && "
                f"grep -E 'Time = |Solving for ' cases/{shlex.quote(case_name)}/log.simpleFoam 2>/dev/null | tail -n 5000"
            )
            code, out, _ = await asyncio.to_thread(ssh_client.run_command, grep_cmd, timeout=10)
            if code == 0 and out.strip():
                rows = parse_residuals_from_log(out)

    # 2. Check local case directory if no rows yet
    local_case = PROJECT_ROOT / "cases" / case_name
    if not rows and local_case.is_dir():
        res_files = find_residual_files(local_case)
        if res_files:
            try:
                data_dict, headers = read_residuals(res_files)
                times = data_dict.get("Time", [])
                for idx, t in enumerate(times):
                    if t is not None:
                        t_key = round(float(t), 8)
                        row = {}
                        for h in headers:
                            val = data_dict.get(h, [])[idx] if idx < len(data_dict.get(h, [])) else None
                            if val is not None and math.isfinite(val) and val > 0:
                                row[h] = float(val)
                        rows[t_key] = row
            except Exception as exc:
                log.warning("Could not read local residual files: %s", exc)

        # Fallback to local log.simpleFoam
        if not rows:
            local_log = local_case / "log.simpleFoam"
            if local_log.is_file():
                try:
                    with open(local_log, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        log_content = "".join(lines[-5000:])
                        rows = parse_residuals_from_log(log_content)
                except Exception as exc:
                    log.warning("Could not read local log.simpleFoam: %s", exc)

    if not rows:
        return {
            "has_data": False,
            "message": f"No residual data or log.simpleFoam found for case '{case_name}'.",
        }

    sorted_iters = sorted(rows.keys())
    if not sorted_iters:
        return {"has_data": False, "message": "No iterations found in residual data."}

    var_candidates = {
        "p": ["p_initial", "p"],
        "Ux": ["Ux_initial", "Ux"],
        "Uy": ["Uy_initial", "Uy"],
        "Uz": ["Uz_initial", "Uz"],
        "k": ["k_initial", "k"],
        "omega": ["omega_initial", "omega", "epsilon_initial", "epsilon", "nuTilda_initial", "nuTilda"],
    }

    all_vars = ["p", "Ux", "Uy", "Uz", "k", "omega"]
    residuals_aligned: dict[str, list[Optional[float]]] = {v: [] for v in all_vars}

    for it in sorted_iters:
        row = rows[it]
        for v in all_vars:
            val = None
            for cand in var_candidates[v]:
                if cand in row and row[cand] is not None:
                    val = round(row[cand], 8)
                    break
            residuals_aligned[v].append(val)

    # Downsample if iterations > 400 to keep UI rendering fast while showing entire run
    max_pts = 400
    if len(sorted_iters) > max_pts:
        step = math.ceil(len(sorted_iters) / max_pts)
        sub_indices = list(range(0, len(sorted_iters), step))
        if (len(sorted_iters) - 1) not in sub_indices:
            sub_indices.append(len(sorted_iters) - 1)

        iters_sub = [int(sorted_iters[i]) for i in sub_indices]
        residuals_sub = {v: [residuals_aligned[v][i] for i in sub_indices] for v in all_vars}
    else:
        iters_sub = [int(it) for it in sorted_iters]
        residuals_sub = residuals_aligned

    return {
        "has_data": True,
        "total_iterations": len(sorted_iters),
        "latest_iteration": int(sorted_iters[-1]),
        "iterations": iters_sub,
        "residuals": residuals_sub,
    }


@app.get("/api/telemetry/logs")
async def api_telemetry_logs(case_name: str, log_type: str = "simpleFoam", lines: int = 100) -> dict[str, Any]:
    """Tail log files with strict type whitelisting and length limits."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")
    if log_type not in ALLOWED_LOG_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported log_type: {log_type}")

    safe_lines = max(1, min(int(lines), 2000))
    filename = f"log.{log_type}"
    content = ""

    if ssh_client.is_connected:
        remote_path = f"{ssh_client.remote_repo_path}/cases/{case_name}/{filename}"
        content = await asyncio.to_thread(ssh_client.read_remote_text, remote_path, max_lines=safe_lines)

    if not content:
        local_file = PROJECT_ROOT / "cases" / case_name / filename
        if local_file.is_file():
            try:
                with open(local_file, encoding="utf-8", errors="replace") as f:
                    raw_lines = f.readlines()
                    content = "".join(raw_lines[-safe_lines:])
            except Exception:
                pass

    return {
        "case_name": case_name,
        "log_type": log_type,
        "content": content or f"No entries in {filename} yet.",
        "log_file": filename,
        "size_bytes": len((content or "").encode("utf-8")),
    }


def read_file_tail(file_path: Path, max_bytes: int = 8192) -> str:
    """Read the tail of a file efficiently without loading the whole file into memory."""
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


@app.get("/api/cases")
async def api_list_cases() -> list[dict[str, Any]]:
    """List simulation cases from local directory and cluster with comprehensive metadata."""
    cases_dict: dict[str, dict[str, Any]] = {}

    # 1. Local cases
    local_cases_dir = PROJECT_ROOT / "cases"
    if local_cases_dir.is_dir():
        for d in local_cases_dir.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue

            case_name = d.name
            st = d.stat()
            mtime_dt = datetime.fromtimestamp(st.st_mtime)
            mtime_str = mtime_dt.strftime("%Y-%m-%d %H:%M")

            # Check case configuration
            cfg_file = d / "case_config.json"
            if not cfg_file.is_file():
                cfg_file = PROJECT_ROOT / "configs" / f"{case_name}.json"

            fidelity = "standard"
            velocity = "16.67"
            flow_dir = "-z"
            n_procs = 32
            stl_name = "--"
            if cfg_file.is_file():
                try:
                    with open(cfg_file, encoding="utf-8") as cf:
                        cd = json.load(cf)
                        fidelity = cd.get("fidelity", "standard")
                        v_val = cd.get("flow", {}).get("velocity", 16.67)
                        velocity = f"{v_val:.1f}" if isinstance(v_val, (int, float)) else str(v_val)
                        flow_dir = cd.get("flow", {}).get("direction", "-z")
                        n_procs = cd.get("parallel", {}).get("n_procs", 32)
                        stls = cd.get("stl_files", [])
                        if stls:
                            stl_name = Path(stls[0]).name
                except Exception:
                    pass

            status = "Generated"
            converged = False
            has_forces = False
            has_residuals = False
            has_mesh = (d / "constant" / "polyMesh" / "points").is_file()
            latest_iter: Optional[int] = None
            downforce_val: Optional[float] = None
            drag_val: Optional[float] = None
            ld_val: Optional[float] = None

            # Check forces
            force_files = find_force_files(d)
            if force_files:
                has_forces = True
                try:
                    drag_idx, drag_sign, df_idx, df_sign, _, _ = load_axis_config(
                        config_path=str(cfg_file) if cfg_file.is_file() else None,
                        case_dir=d,
                    )
                    is_sym = is_symmetry_case(
                        config_path=str(cfg_file) if cfg_file.is_file() else None,
                        case_dir=d,
                    )
                    times, drags, downforces = read_forces(
                        force_files, drag_idx, drag_sign, df_idx, df_sign
                    )
                    if is_sym:
                        drags = [drv * 2.0 for drv in drags]
                        downforces = [dfv * 2.0 for dfv in downforces]
                    if times:
                        latest_iter = int(times[-1])
                        c_conv, _, _, d_avg, f_avg = check_convergence(drags, downforces)
                        converged = c_conv
                        downforce_val = round(f_avg, 2)
                        drag_val = round(d_avg, 2)
                        ld_val = round(f_avg / d_avg, 2) if abs(d_avg) > 1e-3 else None
                        status = "Converged" if converged else "Solving"
                except Exception:
                    pass

            # Check log.simpleFoam
            log_simple = d / "log.simpleFoam"
            if log_simple.is_file():
                has_residuals = True
                tail = read_file_tail(log_simple)
                try:
                    log_mtime = log_simple.stat().st_mtime
                except OSError:
                    log_mtime = st.st_mtime
                if "End" in tail or "Finalising parallel run" in tail:
                    status = "Converged" if converged else "Completed"
                elif any(err in tail for err in ["FOAM FATAL", "Fatal error", "FOAM aborting", "sigFpe", "SIGFPE", "Floating point exception"]):
                    status = "Failed"
                elif status != "Converged":
                    # If the log was modified in the last 3 minutes, it's actively solving
                    if (datetime.now().timestamp() - log_mtime) < 180:
                        status = "Solving"
                    else:
                        status = "Completed" if (latest_iter and latest_iter > 0) else "Failed"

            # Check mesher stage if still generated
            if status == "Generated":
                log_snappy = d / "log.snappyHexMesh"
                if log_snappy.is_file():
                    tail = read_file_tail(log_snappy)
                    try:
                        snappy_mtime = log_snappy.stat().st_mtime
                    except OSError:
                        snappy_mtime = st.st_mtime
                    if "End" in tail or "Finalising parallel run" in tail:
                        status = "Meshed"
                    elif any(err in tail for err in ["FOAM FATAL", "Fatal error", "FOAM aborting", "sigFpe", "SIGFPE"]):
                        status = "Failed"
                    elif (datetime.now().timestamp() - snappy_mtime) < 180:
                        status = "Meshing"
                    else:
                        status = "Failed"
                elif has_mesh:
                    status = "Meshed"

            cases_dict[case_name] = {
                "name": case_name,
                "location": "Local",
                "path": f"cases/{case_name}",
                "modified": mtime_str,
                "modified_ts": st.st_mtime,
                "status": status,
                "fidelity": fidelity,
                "velocity": velocity,
                "direction": flow_dir,
                "n_procs": n_procs,
                "stl_name": stl_name,
                "has_forces": has_forces,
                "has_residuals": has_residuals,
                "has_mesh": has_mesh,
                "latest_iter": latest_iter,
                "converged": converged,
                "downforce": downforce_val,
                "drag": drag_val,
                "ld_ratio": ld_val,
            }

    # 2. Remote cases if cluster connected
    if ssh_client.is_connected:
        try:
            slurm_jobs = await asyncio.to_thread(ssh_client.get_slurm_queue)
            slurm_by_name: dict[str, dict[str, str]] = {
                j.get("name", ""): j for j in slurm_jobs if j.get("name")
            }

            remote_cases = await asyncio.to_thread(ssh_client.list_remote_cases_detailed)
            for rc in remote_cases:
                cname = rc["name"]

                # Cross-reference SLURM state
                sjob = slurm_by_name.get(cname)
                if not sjob:
                    for jn, job_obj in slurm_by_name.items():
                        # Only match prefix if the scheduler truncated a long job name (>= 8 chars)
                        if len(jn) >= 8 and cname.startswith(jn):
                            sjob = job_obj
                            break

                slurm_status = None
                if sjob:
                    st = sjob.get("state", "").upper()
                    if st in ("PENDING", "CONFIGURING"):
                        slurm_status = "Queued"
                    elif st in ("RUNNING",):
                        slurm_status = rc.get("status") if rc.get("status") in ("Meshing", "Solving") else "Solving"
                    elif st in ("COMPLETING",):
                        slurm_status = "Completing"
                    elif st in ("FAILED", "NODE_FAIL", "TIMEOUT", "CANCELLED"):
                        slurm_status = "Failed"

                disk_status = rc.get("status", "Generated")
                # Clean up zombie "Solving" / "Meshing" status if not active in SLURM or within last 3 mins
                if not sjob and disk_status in ("Solving", "Meshing"):
                    now_ts = datetime.now().timestamp()
                    is_active = (now_ts - rc.get("modified_ts", 0)) < 180
                    if not is_active:
                        if rc.get("converged"):
                            disk_status = "Converged"
                        elif rc.get("latest_iter") is not None and rc.get("latest_iter", 0) > 0:
                            disk_status = "Completed"
                        else:
                            disk_status = "Failed"

                final_status = slurm_status or disk_status

                if cname in cases_dict:
                    local_entry = cases_dict[cname]
                    local_entry["location"] = "Local & Cluster"
                    if rc.get("modified_ts", 0) > local_entry.get("modified_ts", 0):
                        local_entry["modified_ts"] = rc["modified_ts"]
                        local_entry["modified"] = rc.get("modified", local_entry["modified"])
                    # If remote has simulation activity or results, update local entry
                    if final_status in ("Solving", "Meshing", "Queued", "Converged", "Completed", "Failed") or rc.get("latest_iter") is not None:
                        local_iter = local_entry.get("latest_iter") or 0
                        remote_iter = rc.get("latest_iter") or 0
                        # Status always reflects the newest known state, but numeric
                        # progress must never regress because of a stale remote snapshot.
                        local_entry["status"] = final_status
                        if remote_iter >= local_iter:
                            if rc.get("latest_iter") is not None:
                                local_entry["latest_iter"] = rc["latest_iter"]
                            if rc.get("downforce") is not None:
                                local_entry["downforce"] = rc["downforce"]
                            if rc.get("drag") is not None:
                                local_entry["drag"] = rc["drag"]
                            if rc.get("ld_ratio") is not None:
                                local_entry["ld_ratio"] = rc["ld_ratio"]
                            if rc.get("converged"):
                                local_entry["converged"] = rc["converged"]
                        if rc.get("has_forces"):
                            local_entry["has_forces"] = True
                        if rc.get("has_residuals"):
                            local_entry["has_residuals"] = True
                        if rc.get("fidelity") and rc["fidelity"] != "--":
                            local_entry["fidelity"] = rc["fidelity"]
                        if rc.get("velocity") and rc["velocity"] != "--":
                            local_entry["velocity"] = rc["velocity"]
                        if rc.get("direction") and rc["direction"] != "--":
                            local_entry["direction"] = rc["direction"]
                        if rc.get("stl_name") and rc["stl_name"] != "--" and local_entry.get("stl_name") == "--":
                            local_entry["stl_name"] = rc["stl_name"]
                else:
                    cases_dict[cname] = {
                        "name": cname,
                        "location": "Cluster",
                        "path": f"{ssh_client.remote_repo_path}/cases/{cname}",
                        "modified": rc.get("modified", "--"),
                        "modified_ts": rc.get("modified_ts", 0.0),
                        "status": final_status,
                        "fidelity": rc.get("fidelity", "--"),
                        "velocity": rc.get("velocity", "--"),
                        "direction": rc.get("direction", "--"),
                        "n_procs": rc.get("n_procs", "--"),
                        "stl_name": rc.get("stl_name", "--"),
                        "has_forces": rc.get("has_forces", False),
                        "has_residuals": rc.get("has_residuals", False),
                        "has_mesh": rc.get("has_mesh", False),
                        "latest_iter": rc.get("latest_iter"),
                        "converged": rc.get("converged", False),
                        "downforce": rc.get("downforce"),
                        "drag": rc.get("drag"),
                        "ld_ratio": rc.get("ld_ratio"),
                    }
        except Exception as exc:
            log.warning("Could not list detailed remote cases: %s", exc)

    return sorted(cases_dict.values(), key=lambda x: x.get("modified_ts", 0.0), reverse=True)


@app.delete("/api/cases/{case_name}")
async def api_case_delete(case_name: str) -> dict[str, Any]:
    """Delete a simulation case directory."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    target_dir = PROJECT_ROOT / "cases" / case_name
    cases_root = (PROJECT_ROOT / "cases").resolve()
    deleted = False
    if target_dir.is_dir() and target_dir.resolve().is_relative_to(cases_root):
        shutil.rmtree(target_dir)
        deleted = True

    if ssh_client.is_connected:
        quoted_remote = shlex.quote(f"{ssh_client.remote_repo_path}/cases/{case_name}")
        code, out, _ = await asyncio.to_thread(
            ssh_client.run_command,
            f"[ -e {quoted_remote} ] && rm -rf {quoted_remote} && echo __DELETED__",
        )
        if code == 0 and "__DELETED__" in out:
            deleted = True

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Case '{case_name}' not found")

    return {"success": True, "case_name": case_name}


# -------------------------------------------------------------
# Mount Static Frontend
# -------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    """CLI launcher for the web server."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Launch RapidFOAM Studio (Rapidamente Formula Student).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--restart", action="store_true", help="Restart server if an instance is already running")
    args = parser.parse_args()

    import socket
    import threading
    import time
    import urllib.request

    def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0

    def get_pid_on_port(port: int) -> Optional[int]:
        try:
            if sys.platform == "win32":
                import subprocess
                out = subprocess.check_output("netstat -ano -p tcp", shell=True, text=True, errors="replace")
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                        return int(parts[4])
            else:
                import subprocess
                out = subprocess.check_output(["lsof", "-t", f"-i:{port}"], text=True, errors="replace")
                pids = [int(p) for p in out.strip().splitlines() if p.strip().isdigit()]
                return pids[0] if pids else None
        except Exception:
            pass
        return None

    def kill_process_tree(pid: int) -> bool:
        try:
            if sys.platform == "win32":
                import subprocess
                subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True)
                return True
            else:
                os.kill(pid, 9)
                return True
        except Exception:
            return False

    target_port = args.port
    if is_port_in_use(target_port, args.host):
        is_cfd_studio = False
        try:
            req = urllib.request.urlopen(f"http://{args.host}:{target_port}/api/config/schema-defaults", timeout=1)
            if req.status == 200:
                is_cfd_studio = True
        except Exception:
            pass

        if is_cfd_studio and not args.restart:
            # An existing studio is already serving this port: reuse it.
            url = f"http://{args.host}:{target_port}"
            print(f"[*] RapidFOAM Studio is already running on {url}. Use --restart to force a restart.")
            if not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return

        if is_cfd_studio and args.restart:
            old_pid = get_pid_on_port(target_port)
            print(f"[*] Found existing RapidFOAM Studio running on port {target_port} (PID {old_pid or 'unknown'}).")
            print("[*] Restarting server to ensure latest code is active...")
            if old_pid and old_pid != os.getpid():
                kill_process_tree(old_pid)
                time.sleep(1.0)

        # If port is still busy (e.g. by another application), find next available port
        while is_port_in_use(target_port, args.host):
            target_port += 1
        if target_port != args.port:
            print(f"[*] Port {args.port} was busy. Switched to next available port: {target_port}")
        args.port = target_port

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print("\n" + "=" * 60)
    cfg = get_saved_cluster_config()
    target = cfg.get("host") or "Not configured (set in Web UI)"
    print("  RapidFOAM Studio Web Server | Rapidamente Formula Student")
    print(f"  Cluster target: {target}")
    print(f"  Listening on:   {url}")
    print("=" * 60 + "\n")

    if not args.no_browser:
        def _open_browser() -> None:
            time.sleep(0.8)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
