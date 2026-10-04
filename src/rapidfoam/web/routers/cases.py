"""Case listing and deletion endpoints."""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from rapidfoam.config import DEFAULT_CONFIG
from rapidfoam.postproc.forces import (
    check_convergence,
    find_force_files,
    is_symmetry_case,
    load_axis_config,
    read_forces,
)
from rapidfoam.web.services.telemetry import read_file_tail
from rapidfoam.web.state import CASE_NAME_REGEX, PROJECT_ROOT, ssh_client

router = APIRouter()

# Local case scan is CPU/IO heavy (a full force.dat parse per case); run it
# off the event loop and cache briefly so overlapping Archive views do not rescan.
_LOCAL_CASES_TTL = 3.0
_local_cases_cache: dict[str, Any] = {"ts": 0.0, "data": {}, "root": None}


def _invalidate_local_cases_cache() -> None:
    with _local_cases_cache_lock:
        _local_cases_cache["ts"] = 0.0
        _local_cases_cache["data"] = {}
        _local_cases_cache["root"] = None


_local_cases_cache_lock = threading.Lock()


def _scan_local_cases() -> dict[str, dict[str, Any]]:
    """Scan local cases/ for metadata (sync; call via to_thread)."""
    cases: dict[str, dict[str, Any]] = {}
    local_cases_dir = PROJECT_ROOT / "cases"
    if not local_cases_dir.is_dir():
        return cases
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

        cases[case_name] = {
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
    return cases


async def _cached_local_cases() -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    root = str(PROJECT_ROOT)
    with _local_cases_cache_lock:
        cached = _local_cases_cache["data"]
        if (cached and _local_cases_cache["root"] == root
                and now - _local_cases_cache["ts"] < _LOCAL_CASES_TTL):
            return cached
    cases = await asyncio.to_thread(_scan_local_cases)
    with _local_cases_cache_lock:
        _local_cases_cache["data"] = cases
        _local_cases_cache["ts"] = now
        _local_cases_cache["root"] = root
    return cases


@router.get("/api/cases")
async def api_list_cases() -> list[dict[str, Any]]:
    """List simulation cases from local directory and cluster with comprehensive metadata."""
    cases_dict: dict[str, dict[str, Any]] = {}

    # 1. Local cases (scanned off the event loop, briefly cached)
    cases_dict.update(await _cached_local_cases())

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
            from rapidfoam.web import state as _state

            _state.log.warning("Could not list detailed remote cases: %s", exc)

    return sorted(cases_dict.values(), key=lambda x: x.get("modified_ts", 0.0), reverse=True)


@router.delete("/api/cases/{case_name}")
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
