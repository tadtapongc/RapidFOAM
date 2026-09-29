"""FastAPI web server providing REST API and serving the desktop UI."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import shlex
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from rapidfoam import __version__
from rapidfoam.config import DEFAULT_CONFIG, effective_config, find_stl, user_set, validate
from rapidfoam.meshing.presets import apply_fidelity_preset
from rapidfoam.geometry import (
    compute_domain_box,
    flow_axis_index_sign,
    up_axis_index,
)
from rapidfoam.postproc.forces import (
    check_convergence,
    find_force_files,
    is_symmetry_case,
    load_axis_config,
    read_forces,
)
from rapidfoam.stl_utils import EdgeStats, FeatureAngleStats, stl_analyze_full, stl_info

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

from rapidfoam.web.routers import cluster as _cluster_router
from rapidfoam.web.routers import config as _config_router
from rapidfoam.web.routers.cluster import (  # noqa: F401
    api_cluster_connect,
    api_cluster_disconnect,
    api_cluster_status,
    api_get_saved_config,
)
from rapidfoam.web.routers.config import (  # noqa: F401
    api_config_defaults,
    api_config_load_file,
    api_config_templates,
)
from rapidfoam.web.state import (  # noqa: F401
    ALLOWED_LOG_TYPES,
    CASE_NAME_REGEX,
    JOB_ID_REGEX,
    PROJECT_ROOT,
    _download_progress,
    _download_progress_lock,
    _get_download_progress,
    _list_download_progress,
    _set_download_progress,
    get_saved_cluster_config,
    save_cluster_config,
    ssh_client,
)

app.include_router(_cluster_router.router)
app.include_router(_config_router.router)

def merge_config_with_defaults(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge user configuration and selective overrides on top of DEFAULT_CONFIG."""
    return effective_config(raw_cfg)


def _local_stl_exists(name: str) -> bool:
    """True when the named STL resolves inside the project's local stl/ folder."""
    return find_stl(PROJECT_ROOT / "stl", Path(name).name) is not None


def layer_preview(
    merged: dict[str, Any],
    raw_cfg: dict[str, Any],
    bounds: tuple,
    feature_stats: EdgeStats | None = None,
    angle_stats: FeatureAngleStats | None = None,
) -> dict[str, Any]:
    """Resolve the near-wall layer spec for the Studio preview without mutating it."""
    from rapidfoam.meshing.plan import build_mesh_plan

    preview_cfg = copy.deepcopy(merged)
    # Apply the same preset resolution and derivation the generator uses, so the
    # preview can no longer disagree with the generated case (PAIN_POINTS #1).
    apply_fidelity_preset(preview_cfg, lambda section, key: user_set(raw_cfg, section, key))
    plan = build_mesh_plan(
        preview_cfg,
        bounds,
        feature_stats=feature_stats,
        angle_stats=angle_stats,
        explicit_feature_angle=user_set(raw_cfg, "feature_extract", "includedAngle"),
        explicit_first_layer=user_set(raw_cfg, "layers", "first_layer_thickness"),
        explicit_min_thickness=user_set(raw_cfg, "layers", "min_thickness"),
    )
    resolved = dict(plan.layer_spec.resolved)
    resolved["auto_size"] = plan.mesh_params.get("auto_size")
    resolved["feature_angle"] = plan.mesh_params.get("feature_angle")
    resolved["surface_level"] = plan.mesh_params.get("surface_level")
    resolved["edge_level"] = plan.mesh_params.get("edge_level")
    return resolved


# -------------------------------------------------------------
# Pydantic Request Models
# -------------------------------------------------------------

from rapidfoam.web.schemas import (  # noqa: F401
    CaseDownloadRequest,
    DomainBoxRequest,
    GenerateCaseRequest,
    JobCancelRequest,
    JobSubmitRequest,
    SSHConnectRequest,
)


@app.post("/api/geometry/domain-box")
async def api_geometry_domain_box(req: DomainBoxRequest) -> dict[str, Any]:
    """Compute and preview the OpenFOAM wind tunnel domain box."""
    cfg = req.config
    try:
        merged = merge_config_with_defaults(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid configuration format: {exc}")

    # Gather STL edge and crease-angle statistics for feature-based auto-sizing
    # (and bounds when the caller did not supply them), one streaming pass per file.
    feature_stats = EdgeStats()
    angle_stats = FeatureAngleStats()
    have_stats = False
    computed_min = [float("inf")] * 3
    computed_max = [float("-inf")] * 3
    for sname in cfg.get("stl_files", []):
        safe_sname = Path(sname).name
        p = find_stl(PROJECT_ROOT / "stl", safe_sname)
        if p and p.is_file():
            try:
                _, _, b, stats, angles = stl_analyze_full(p)
            except Exception:
                continue
            feature_stats.merge(stats)
            angle_stats.merge(angles)
            have_stats = True
            for i in range(3):
                computed_min[i] = min(computed_min[i], b[0][i])
                computed_max[i] = max(computed_max[i], b[1][i])

    bounds_tuple = None
    if req.bounds and "min" in req.bounds and "max" in req.bounds:
        bounds_tuple = (req.bounds["min"], req.bounds["max"])
    elif computed_min[0] != float("inf"):
        bounds_tuple = (computed_min, computed_max)
    else:
        # Default reference geometry bounds (half-model)
        bounds_tuple = ([-0.7, 0.035, -1.8], [0.7, 1.1, 1.2])

    stats_for_sizing = feature_stats if have_stats else None
    stats_for_angle = angle_stats if angle_stats.n_angles > 0 else None

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
            "layer_preview": layer_preview(merged, cfg, bounds_tuple, stats_for_sizing, stats_for_angle),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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

    # Every referenced geometry must exist locally before we generate or ship
    # anything: a silent skip would upload a config whose STL is missing (or a
    # stale same-named STL) and only fail later on the cluster.
    missing_stls = [
        Path(sname).name for sname in cfg.get("stl_files", [])
        if not _local_stl_exists(Path(sname).name)
    ]
    if missing_stls:
        raise HTTPException(
            status_code=400,
            detail=(
                "STL not found in stl/: " + ", ".join(missing_stls) +
                ". Upload the geometry before generating or submitting."
            ),
        )

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
        from rapidfoam.casegen.builder import build_case
        try:
            await asyncio.to_thread(build_case, local_cfg_path, PROJECT_ROOT, False)
            local_actions["generated_locally"] = True
            local_actions["case_path"] = str(PROJECT_ROOT / "cases" / case_name)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Local case generation failed: {exc}")

    # 4. Cluster synchronization and remote execution
    if req.upload_to_cluster:
        if not ssh_client.is_connected:
            raise HTTPException(status_code=400, detail="Not connected to cluster. Please connect via SSH first.")

        remote_repo = ssh_client.remote_repo_path

        # Upload STLs used in this config. Existence was verified above, so any
        # miss here is a genuine race (file deleted mid-request) and must fail
        # loudly instead of shipping a config with missing geometry.
        stl_files = cfg.get("stl_files", [])
        uploaded_stls = []
        for sname in stl_files:
            safe_sname = Path(sname).name
            local_stl = find_stl(PROJECT_ROOT / "stl", safe_sname)
            if not (local_stl and local_stl.is_file()):
                raise HTTPException(
                    status_code=400,
                    detail=f"STL '{safe_sname}' disappeared before upload; re-upload the geometry.",
                )
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

    local_dir = PROJECT_ROOT / "cases" / req.case_name

    # Reserve the case atomically: check-for-active and set-active must happen in
    # one critical section, otherwise two concurrent POSTs can both see inactive
    # and both start writing into the same local case directory.
    with _download_progress_lock:
        state = _download_progress.get(req.case_name, {})
        if state.get("active"):
            return {"success": True, "already_running": True, "case_name": req.case_name}
        if not req.overwrite and local_dir.is_dir() and any(local_dir.iterdir()):
            raise HTTPException(
                status_code=409,
                detail=f"Case '{req.case_name}' already exists locally. Set overwrite to re-download.",
            )
        _download_progress[req.case_name] = {
            **state,
            "active": True, "done": False, "error": None,
            "files": 0, "dirs": 0, "bytes": 0, "total_bytes": 0,
        }

    try:
        asyncio.get_running_loop().run_in_executor(
            None, _run_download, req.case_name, remote_case, local_dir
        )
    except Exception:
        _set_download_progress(req.case_name, active=False, done=True, error="Failed to schedule download")
        raise
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


from rapidfoam.web.routers import telemetry as _telemetry_router
from rapidfoam.web.routers.telemetry import (  # noqa: F401
    api_telemetry_export,
    api_telemetry_forces,
    api_telemetry_logs,
    api_telemetry_mesh,
    api_telemetry_residuals,
    api_telemetry_solver,
    api_telemetry_surface,
)
from rapidfoam.web.services import telemetry as _telemetry_service
from rapidfoam.web.services.telemetry import (  # noqa: F401
    _project_force_columns_for_symmetry,
    _project_moment_columns_for_symmetry,
    _read_remote_telemetry,
    _read_text_files,
    _read_yplus_texts,
    _sorted_segments,
    _summarise_yplus,
    parse_residuals_from_log,
    parse_solver_diagnostics_from_log,
    read_file_tail,
)
_remote_telemetry_cache = _telemetry_service._remote_telemetry_cache

app.include_router(_telemetry_router.router)


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

            default_flow = DEFAULT_CONFIG.get("flow", {})
            default_velocity = float(default_flow.get("velocity", 16.67))
            default_direction = default_flow.get("direction", "-z")
            default_nprocs = int(DEFAULT_CONFIG.get("parallel", {}).get("n_procs", 32))
            fidelity = DEFAULT_CONFIG.get("fidelity", "standard")
            velocity = f"{default_velocity:.1f}"
            flow_dir = default_direction
            n_procs = default_nprocs
            stl_name = "--"
            if cfg_file.is_file():
                try:
                    with open(cfg_file, encoding="utf-8") as cf:
                        cd = json.load(cf)
                        fidelity = cd.get("fidelity", fidelity)
                        v_val = cd.get("flow", {}).get("velocity", default_velocity)
                        velocity = f"{v_val:.1f}" if isinstance(v_val, (int, float)) else str(v_val)
                        flow_dir = cd.get("flow", {}).get("direction", default_direction)
                        n_procs = cd.get("parallel", {}).get("n_procs", default_nprocs)
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
