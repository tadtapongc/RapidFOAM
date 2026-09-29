"""Case generation, submission, download and domain-preview endpoints."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from rapidfoam.config import effective_config, find_stl, validate
from rapidfoam.geometry import compute_domain_box, flow_axis_index_sign, up_axis_index
from rapidfoam.stl_utils import EdgeStats, FeatureAngleStats, stl_analyze_full
from rapidfoam.web.schemas import (
    CaseDownloadRequest,
    DomainBoxRequest,
    GenerateCaseRequest,
    JobCancelRequest,
    JobSubmitRequest,
)
from rapidfoam.web.services.downloads import _run_download
from rapidfoam.web.services.geometry import _local_stl_exists, layer_preview
from rapidfoam.web.state import (
    CASE_NAME_REGEX,
    JOB_ID_REGEX,
    PROJECT_ROOT,
    _download_progress,
    _download_progress_lock,
    _get_download_progress,
    _list_download_progress,
    _set_download_progress,
    ssh_client,
)

router = APIRouter()


@router.post("/api/geometry/domain-box")
async def api_geometry_domain_box(req: DomainBoxRequest) -> dict[str, Any]:
    """Compute and preview the OpenFOAM wind tunnel domain box."""
    cfg = req.config
    try:
        merged = effective_config(cfg)
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


@router.get("/api/case/check-exists")
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


@router.post("/api/case/generate-and-submit")
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
        merged = effective_config(cfg)
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


@router.post("/api/case/submit")
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


@router.post("/api/case/cancel")
async def api_case_cancel(req: JobCancelRequest) -> dict[str, Any]:
    """Cancel a SLURM job."""
    job_id = str(req.job_id).strip()
    if not JOB_ID_REGEX.match(job_id):
        raise HTTPException(status_code=400, detail="job_id must be numeric")
    if not ssh_client.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to cluster")
    res = await asyncio.to_thread(ssh_client.cancel_job, job_id)
    return res


@router.post("/api/case/download")
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


@router.get("/api/case/download/active")
async def api_case_download_active() -> dict[str, Any]:
    """List in-progress case downloads (used to restore the UI after reload)."""
    return {"downloads": _list_download_progress(active_only=True)}


@router.get("/api/case/download/progress")
async def api_case_download_progress(case_name: str) -> dict[str, Any]:
    """Return live progress for an in-flight case download."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")
    return _get_download_progress(case_name)
