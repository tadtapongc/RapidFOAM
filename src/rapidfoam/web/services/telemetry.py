"""Residual / solver / y+ parsing and summarisation (no FastAPI imports)."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from rapidfoam.config import DEFAULT_CONFIG
from rapidfoam.core import caseconfig
from rapidfoam.web.state import ssh_client

log = logging.getLogger("rapidfoam.web")


_RESIDUAL_RE = re.compile(
    r"Solving for (p|Ux|Uy|Uz|k|omega|epsilon|nuTilda),\s+Initial residual\s+=\s+([0-9\.eE\+\-]+)",
    re.IGNORECASE,
)

# Canonical residual field names. Anything unrecognised is kept verbatim so a
# field is never silently relabelled (the historical epsilon -> omega bug).
_RESIDUAL_VAR_NAMES = {
    "p": "p", "ux": "Ux", "uy": "Uy", "uz": "Uz",
    "k": "k", "omega": "omega", "epsilon": "epsilon", "nutilda": "nuTilda",
}


def parse_residuals_from_log(log_text: str) -> dict[float, dict[str, float]]:
    """Extract initial residuals from OpenFOAM solver log text."""
    rows: dict[float, dict[str, float]] = {}
    current_iter: Optional[float] = None
    previous: Optional[float] = None
    for raw in log_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Anchored match, so "ExecutionTime ="/"ClockTime =" are not mistaken
        # for the iteration counter.
        time_match = _LOG_TIME_RE.match(line)
        if time_match:
            try:
                current_iter = float(time_match.group(1))
            except ValueError:
                current_iter = None
            # A restarted/requeued run supersedes the old trajectory from here on.
            if current_iter is not None and previous is not None and current_iter <= previous:
                rows = {key: value for key, value in rows.items() if key < round(current_iter, 8)}
            previous = current_iter
            continue
        if current_iter is None or current_iter <= 0:
            continue
        match = _RESIDUAL_RE.search(line)
        if not match:
            continue
        var = _RESIDUAL_VAR_NAMES.get(match.group(1).lower(), match.group(1))
        t_key = round(current_iter, 8)
        entry = rows.setdefault(t_key, {})
        # Keep the FIRST residual for each variable in this time-step (initial residual)
        if var not in entry:
            try:
                val = float(match.group(2))
            except ValueError:
                continue
            if math.isfinite(val) and val > 0:
                entry[var] = val
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



_SOLVE_ITERS_RE = re.compile(r"Solving for (\w+),.*?No Iterations\s+(\d+)")
_CONTINUITY_RE = re.compile(
    r"time step continuity errors\s*:.*?global\s*=\s*([0-9.eE+\-]+).*?cumulative\s*=\s*([0-9.eE+\-]+)"
)
_EXEC_TIME_RE = re.compile(r"ExecutionTime\s*=\s*([0-9.eE+\-]+)\s*s")
_LOG_TIME_RE = re.compile(r"^Time = ([0-9.eE+\-]+)")


def parse_solver_diagnostics_from_log(log_text: str) -> dict[float, dict[str, Any]]:
    """Extract per-iteration continuity, linear-solver effort, and timing from a log.

    Reads the ``time step continuity errors``, ``No Iterations``, and
    ``ExecutionTime`` entries that map to the current ``Time =`` step.
    """
    rows: dict[float, dict[str, Any]] = {}
    current: Optional[float] = None
    previous: Optional[float] = None
    for raw in log_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LOG_TIME_RE.match(line)
        if match:
            try:
                current = float(match.group(1))
            except ValueError:
                current = None
            if current is not None and previous is not None and current <= previous:
                # A restarted run supersedes the old trajectory from here on.
                rows = {key: value for key, value in rows.items() if key < round(current, 8)}
            previous = current
            continue
        if current is None or current <= 0:
            continue
        key = round(current, 8)
        row = rows.setdefault(
            key,
            {
                "time": current,
                "linear_iters": 0,
                "linear_iters_max": 0,
                "continuity_global": None,
                "continuity_cumulative": None,
                "execution_time": None,
            },
        )
        match = _SOLVE_ITERS_RE.search(line)
        if match:
            iterations = int(match.group(2))
            row["linear_iters"] += iterations
            row["linear_iters_max"] = max(row["linear_iters_max"], iterations)
        match = _CONTINUITY_RE.search(line)
        if match:
            row["continuity_global"] = float(match.group(1))
            row["continuity_cumulative"] = float(match.group(2))
        match = _EXEC_TIME_RE.search(line)
        if match:
            row["execution_time"] = float(match.group(1))
    return rows


def _read_yplus_texts(texts: list[str]) -> dict[str, dict[str, float]]:
    """Parse y+ data directly from text segments (no temp files)."""
    rows: dict[str, dict[str, float]] = {}
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                time = float(parts[0])
                lo = float(parts[2])
                hi = float(parts[3])
                avg = float(parts[4])
            except ValueError:
                continue
            if not math.isfinite(time):
                continue
            patch = parts[1]
            prev = rows.get(patch)
            if prev is None or time >= prev.get("time", -math.inf):
                rows[patch] = {"min": lo, "max": hi, "average": avg, "time": time}
    return rows


def _summarise_yplus(
    data: dict[str, dict[str, float]],
    target: Optional[float],
) -> dict[str, Any]:
    """Attach realised y+ to a target and flag patches that miss it.

    Delegates the regime-aware verdict to ``postproc.yplus.check_yplus_target``
    so the Studio and ``read_forces.py --yplus`` agree, then adapts it to the
    compact JSON shape the panel consumes.
    """
    if not data:
        return {"available": False}
    from rapidfoam.postproc.yplus import check_yplus_target

    summary = check_yplus_target(data, target)
    if not summary.get("available"):
        return {"available": False}

    per_patch: dict[str, dict[str, Any]] = {}
    for patch, stats in summary["patches"].items():
        avg = stats.get("average")
        entry: dict[str, Any] = {
            "min": stats.get("min"),
            "max": stats.get("max"),
            "average": avg,
            "status": stats.get("status"),
        }
        if target and target > 0 and isinstance(avg, (int, float)) and math.isfinite(avg):
            ratio = avg / target
            entry["ratio"] = round(ratio, 3)
            entry["ok"] = stats.get("status") in ("on_target", "acceptable")
        per_patch[patch] = entry

    # Without a configured target the panel reports realised values only, so no
    # patch is listed as "missed" (matching the previous contract).
    missed = list(summary.get("off_target", [])) if target else []
    return {
        "available": True,
        "target": target,
        "band": summary.get("band"),
        "patches": per_patch,
        "missed": missed,
        "note": summary.get("note", ""),
    }




# ---- moved from web/server.py (Phase 6c) ----
def _read_text_files(paths: list[Path]) -> list[str]:
    """Read a list of files into memory, skipping unreadable entries."""
    segments: list[str] = []
    for path in paths:
        try:
            segments.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return segments


async def _read_remote_bundle(specs: list[tuple[str, Optional[int]]], report: Optional[dict] = None) -> dict[str, str]:
    """Read many remote files with a single SSH command (see read_remote_bundle)."""
    if not ssh_client.is_connected:
        if report is not None:
            report.clear()
            report.update({"ok": False, "reason": "not connected"})
        return {}
    if not specs:
        return {}
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(ssh_client.read_remote_bundle, specs, report=report),
            timeout=_REMOTE_BUNDLE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("Remote bundle read timed out after %ss", _REMOTE_BUNDLE_TIMEOUT)
        if report is not None:
            report.clear()
            report.update({"ok": False, "reason": f"timed out after {_REMOTE_BUNDLE_TIMEOUT:.0f}s"})
        return {}
    except Exception as exc:
        log.warning("Remote bundle read failed: %s", exc)
        if report is not None:
            report.clear()
            report.update({"ok": False, "reason": str(exc)[:200]})
        return {}


def _sorted_segments(bundle: dict[str, str], suffix: str, contain: str | None = None) -> list[str]:
    """Select bundle entries ending with ``suffix`` (and containing ``contain``).

    ``contain`` disambiguates the combined ``forces`` output from the per-part
    ``forces_<part>`` outputs, which share the same ``/force.dat`` suffix.
    """
    paths = [
        path for path in bundle
        if path.endswith(suffix) and (contain is None or contain in path)
    ]
    paths.sort(key=_time_dir_key)
    return [bundle[path] for path in paths]


def _per_part_forces(
    parts_segments: dict[str, list[str]],
    drag_idx: int,
    drag_sign: int,
    df_idx: int,
    df_sign: int,
    sym_scale: float,
) -> dict[str, dict[str, Any]]:
    """Trailing-window drag/downforce per component (forces_<part> outputs)."""
    from rapidfoam.postproc.forces import (
        normalize_component_columns,
        parse_tabular_dat,
        window_stats,
    )

    drag_key = f"total_{'xyz'[drag_idx]}"
    df_key = f"total_{'xyz'[df_idx]}"
    result: dict[str, dict[str, Any]] = {}
    for part, segments in parts_segments.items():
        times, rows, header = parse_tabular_dat(segments)
        cols = normalize_component_columns(rows, header)
        if not times or drag_key not in cols or df_key not in cols:
            continue
        drags = [value * drag_sign * sym_scale for value in cols[drag_key]]
        downforces = [value * df_sign * sym_scale for value in cols[df_key]]
        drag_avg, drag_pct = window_stats(drags)
        df_avg, df_pct = window_stats(downforces)
        result[part] = {
            "drag": round(drag_avg, 3) if drag_avg is not None else None,
            "downforce": round(df_avg, 3) if df_avg is not None else None,
            "ld": round(abs(df_avg / drag_avg), 2)
            if drag_avg not in (None, 0) and df_avg is not None else None,
            "drag_pct": round(drag_pct, 3) if drag_pct is not None else None,
            "downforce_pct": round(df_pct, 3) if df_pct is not None else None,
            "iterations": len(times),
        }
    return result


def _time_dir_key(path: str) -> float:
    try:
        return float(Path(path).parent.name)
    except ValueError:
        return 0.0


# Short-lived cache so the forces/residuals/solver/mesh/surface endpoints share
# one remote read per poll instead of issuing separate SSH command batches. Reads
# are single-flighted per case (one asyncio.Lock per key) so concurrent callers
# await the same read rather than each firing an SSH command on the shared,
# serialised session. Empty/failed reads are cached briefly so a poll does not
# repeat a slow/failing command on every endpoint.
_REMOTE_TELEMETRY_TTL = 2.5
_REMOTE_TELEMETRY_EMPTY_TTL = 1.0
# Hard cap on a single remote bundle read so a stalled SSH command cannot hang a
# telemetry request indefinitely (the SSH call itself also has a deadline).
_REMOTE_BUNDLE_TIMEOUT = 12.0


def _remote_telemetry_key(case_name: str) -> str:
    return f"{ssh_client.host}|{ssh_client.remote_repo_path}|{case_name}"


class _RemoteEntry:
    """Per-case cache slot: last bundle + freshness + a single-flight lock."""

    __slots__ = ("bundle", "fetched_at", "ttl", "lock", "status")

    def __init__(self) -> None:
        self.bundle: dict[str, str] = {}
        self.fetched_at = 0.0
        self.ttl = 0.0
        self.lock = asyncio.Lock()
        self.status: dict[str, Any] = {}


_remote_telemetry_entries: dict[str, _RemoteEntry] = {}
_remote_telemetry_registry_lock = threading.Lock()


def _remote_entry(key: str) -> _RemoteEntry:
    with _remote_telemetry_registry_lock:
        entry = _remote_telemetry_entries.get(key)
        if entry is None:
            entry = _RemoteEntry()
            _remote_telemetry_entries[key] = entry
        return entry


def clear_remote_telemetry_cache() -> None:
    """Drop all cached remote bundles (used between tests)."""
    with _remote_telemetry_registry_lock:
        _remote_telemetry_entries.clear()


def remote_telemetry_status(case_name: str) -> dict[str, Any]:
    """Last diagnostic for a case's remote bundle read (ok / reason)."""
    return dict(_remote_entry(_remote_telemetry_key(case_name)).status)


def should_read_remote(case_name: str, local_dir: "Path | str | None") -> bool:
    """Whether a telemetry request should hit the cluster.

    A case that exists locally is served from local files even while connected,
    so connecting to a cluster never makes a local view block on SSH. The remote
    bundle is only fetched for cluster-only cases.
    """
    if not ssh_client.is_connected:
        return False
    if local_dir is None:
        return True
    try:
        return not Path(local_dir).is_dir()
    except Exception:
        return True


async def _read_remote_telemetry(case_name: str) -> dict[str, str]:
    """Read every telemetry file for a case in one SSH command (cached ~2.5s).

    Single-flight per case: concurrent callers await one shared read. Bounded by
    ``_REMOTE_BUNDLE_TIMEOUT``; a timed-out read is not cached, so the next poll
    retries rather than reusing a hung result.
    """
    if not ssh_client.is_connected:
        return {}
    entry = _remote_entry(_remote_telemetry_key(case_name))
    now = time.monotonic()
    if entry.bundle is not None and entry.ttl and now - entry.fetched_at < entry.ttl:
        return entry.bundle

    try:
        async with entry.lock:
            # Re-check after acquiring: another caller may have just refreshed it.
            now = time.monotonic()
            if entry.ttl and now - entry.fetched_at < entry.ttl:
                return entry.bundle
            report: dict[str, Any] = {}
            bundle = await _read_remote_bundle(_remote_bundle_specs(case_name), report=report)
            entry.status = report
            entry.bundle = bundle
            entry.ttl = _REMOTE_TELEMETRY_TTL if report.get("ok") else _REMOTE_TELEMETRY_EMPTY_TTL
            entry.fetched_at = time.monotonic()
            return bundle
    except Exception:
        return {}


def _remote_bundle_specs(case_name: str) -> list[tuple[str, Any]]:
    """The glob specs the telemetry bundle fetches for a case."""
    return [
        (f"cases/{case_name}/postProcessing/forces/*/force.dat", None),
        (f"cases/{case_name}/postProcessing/forces_*/*/force.dat", None),
        (f"cases/{case_name}/postProcessing/forces/*/moment.dat", None),
        (f"cases/{case_name}/postProcessing/forceCoeffs/*/coefficient.dat", None),
        (f"cases/{case_name}/postProcessing/residuals/*/solverInfo.dat", None),
        (f"cases/{case_name}/postProcessing/residuals/*/residuals.dat", None),
        (f"cases/{case_name}/log.simpleFoam", 20000),
        # Mesh-quality inputs: checkMesh is small; the final snappy layer table
        # sits near the end of the log, so a modest tail is enough. Glob the
        # snappy log so two-pass cases also fetch log.snappyHexMesh.layering.
        # Full checkMesh (not a tail): its "cells:" line sits at the top, and the
        # grid study reads it for the realised cell count.
        (f"cases/{case_name}/log.checkMesh", None),
        (f"cases/{case_name}/log.snappyHexMesh*", 600),
        (f"cases/{case_name}/log.surfaceCheck", None),
        (f"cases/{case_name}/postProcessing/yPlus/*/yPlus.dat", None),
        (f"cases/{case_name}/postProcessing/fieldMinMax/*/fieldMinMax.dat", None),
        (f"cases/{case_name}/case_config.json", None),
    ]


# --- Grid-study staging (cluster-aware) -----------------------------------
#
# ``grid_study`` discovers and reads cases from a single directory tree. To let
# the Grid Independence panel analyse a study whose levels live only on the
# cluster, we stage the small set of files it needs (coefficient.dat, force.dat,
# case_config.json) into one temporary root: local levels are copied, cluster-only
# levels come from the cached remote bundle. ``grid_study`` then runs unchanged.

# Only these staged entries are needed by _coefficients_for_case / config reads.
def _is_staged_study_path(rel: str) -> bool:
    if rel in ("case_config.json", "log.checkMesh"):
        return True
    if rel.startswith("postProcessing/forceCoeffs/") and rel.endswith("/coefficient.dat"):
        return True
    if rel.startswith("postProcessing/forces/") and rel.endswith("/force.dat"):
        return True
    return False


def _matches_study_base(name: str, base: str) -> bool:
    return bool(name) and (name == base or name.startswith(base + "_"))


def _stage_local_case(src: Path, dst: Path) -> None:
    """Copy the study-relevant files of a local case into the staging root."""
    from rapidfoam.postproc.forces import find_coefficient_files, find_force_files

    config = src / "case_config.json"
    if config.is_file():
        dest = dst / "case_config.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(config.read_bytes())
    checkmesh = src / "log.checkMesh"
    if checkmesh.is_file():
        dest = dst / "log.checkMesh"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(checkmesh.read_bytes())
    for finder in (find_coefficient_files, find_force_files):
        try:
            files = finder(src)
        except Exception:
            files = []
        for path in files:
            try:
                rel = path.relative_to(src)
            except ValueError:
                continue
            dest = dst / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_bytes(path.read_bytes())
            except OSError:
                continue


def _stage_remote_case(case_dir: Path, name: str, bundle: dict[str, str]) -> None:
    """Write a remote case's study-relevant bundle entries into the staging root."""
    prefix = f"cases/{name}/"
    for path, text in bundle.items():
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if not _is_staged_study_path(rel):
            continue
        dest = case_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")


async def prepare_study_root(base: str, local_cases_dir: Path) -> tuple[Path, Optional[Path]]:
    """Resolve a cases root for ``grid_study``, including cluster-only levels.

    Returns ``(root, temp_root)``. ``temp_root`` is a staging directory the caller
    must remove, or ``None`` when the local ``cases/`` directory can be used
    directly (not connected, or the cluster holds no level for ``base``). When the
    cluster does hold levels, every local and remote level is staged into one
    temporary root so ``grid_study`` sees a single coherent ladder.
    """
    if not ssh_client.is_connected:
        return local_cases_dir, None
    try:
        remote = await asyncio.to_thread(ssh_client.list_remote_cases_detailed)
    except Exception:
        return local_cases_dir, None
    remote_names = [
        str(case.get("name", "")) for case in remote
        if isinstance(case, dict) and _matches_study_base(str(case.get("name", "")), base)
    ]
    if not remote_names:
        return local_cases_dir, None

    temp_root = Path(tempfile.mkdtemp(prefix="rapidfoam-study-"))
    local_names: set[str] = set()
    if local_cases_dir.is_dir():
        for entry in local_cases_dir.iterdir():
            if entry.is_dir() and _matches_study_base(entry.name, base):
                local_names.add(entry.name)
                try:
                    _stage_local_case(entry, temp_root / entry.name)
                except OSError:
                    pass

    remote_only = [name for name in remote_names if name and name not in local_names]
    bundles = await asyncio.gather(
        *(_read_remote_telemetry(name) for name in remote_only),
        return_exceptions=True,
    )
    for name, bundle in zip(remote_only, bundles):
        if isinstance(bundle, dict) and bundle:
            try:
                _stage_remote_case(temp_root / name, name, bundle)
            except OSError:
                pass
    return temp_root, temp_root


def _subsample_indices(count: int, max_pts: int = 400) -> list[int]:
    """Evenly spaced indices that always retain the first and last sample.

    ``max_pts <= 0`` disables downsampling (used by the full-history export).
    """
    if max_pts <= 0 or count <= max_pts:
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


def _average_vector(
    cols: dict[str, list[float]], kind: str, window: int = 200
) -> Optional[list[float]]:
    """Trailing-window mean of a force/moment vector component (same basis as KPIs)."""
    keys = [f"{kind}_{axis}" for axis in ("x", "y", "z")]
    averages: list[float] = []
    for key in keys:
        series = cols.get(key)
        if not series:
            return None
        size = min(window, len(series))
        window_values = [v for v in series[-size:] if v is not None and math.isfinite(v)]
        if not window_values:
            return None
        averages.append(round(sum(window_values) / len(window_values), 4))
    return averages


def _load_stl_files(cfg_path: Optional[str], case_dir: Optional[Path] = None) -> list[str]:
    """Return the STL basenames referenced by a case config (for 3D telemetry)."""
    return caseconfig.stl_files(
        caseconfig.read_case_config(config_path=cfg_path, case_dir=case_dir)
    )


def _load_reference_quantities(
    cfg_path: Optional[str], case_dir: Optional[Path] = None
) -> dict[str, Any]:
    # Fallbacks mirror the universal defaults (DEFAULT_CONFIG), not magic numbers.
    default_fluid = DEFAULT_CONFIG.get("fluid", {})
    default_refs = DEFAULT_CONFIG.get("force_refs", {})
    rho = float(default_fluid.get("rho", 1.225))
    velocity = 0.0
    aref = float(default_refs.get("Aref", 1.0))
    lref = float(default_refs.get("lRef", 1.0))
    cofr: list[float] = list(default_refs.get("CofR", [0.0, 0.0, 0.0]))
    ground_plane: Optional[float] = None
    ground_clearance: Optional[float] = None
    cfg_obj = caseconfig.read_case_config(config_path=cfg_path, case_dir=case_dir)
    if cfg_obj:
        try:
            rho = float(cfg_obj.get("fluid", {}).get("rho", rho) or rho)
            velocity = float(cfg_obj.get("flow", {}).get("velocity", 0.0) or 0.0)
            aref = float(cfg_obj.get("force_refs", {}).get("Aref", aref) or aref)
            lref = float(cfg_obj.get("force_refs", {}).get("lRef", lref) or lref)
            raw_cofr = cfg_obj.get("force_refs", {}).get("CofR")
            if isinstance(raw_cofr, (list, tuple)) and len(raw_cofr) == 3:
                cofr = [float(v) for v in raw_cofr]
            if cfg_obj.get("ground_plane") is not None:
                ground_plane = float(cfg_obj["ground_plane"])
            if cfg_obj.get("ground_clearance") is not None:
                ground_clearance = float(cfg_obj["ground_clearance"])
        except Exception:
            pass
    return {
        "rho": round(rho, 6),
        "velocity": round(velocity, 4),
        "Aref": round(aref, 6),
        "lRef": round(lref, 6),
        "CofR": [round(v, 6) for v in cofr],
        "ground_plane": ground_plane,
        "ground_clearance": ground_clearance,
        "dynamic_pressure": round(0.5 * rho * velocity * velocity, 4),
    }


def _load_vehicle_geometry(
    cfg_path: Optional[str], case_dir: Optional[Path] = None
) -> dict[str, Optional[float]]:
    """Vehicle geometry used for aero-balance (wheelbase + static front weight %)."""
    return caseconfig.vehicle_geometry(
        caseconfig.read_case_config(config_path=cfg_path, case_dir=case_dir)
    )


def _compute_aero_balance(
    force: Optional[list[float]],
    moment: Optional[list[float]],
    cofr: list[float],
    drag_idx: int,
    drag_sign: int,
    df_idx: int,
    df_sign: int,
    wheelbase: Optional[float],
    front_pct: Optional[float],
) -> dict[str, Any]:
    """Center of pressure and front/rear aero load split.

    Locates the CoP along the flow axis (where the pitch moment vanishes),
    then applies the lever rule with downforce applied at the CoP. Assumes
    ``CofR`` is at the CoG and ``front_pct`` is the static front weight fraction.
    """
    if (not force or not moment or wheelbase is None or front_pct is None
            or wheelbase <= 0 or not (0.0 <= front_pct <= 100.0)):
        return {"available": False}
    e_flow = _axis_vector(drag_idx, drag_sign)
    lift = _axis_vector(df_idx, df_sign)
    pitch = _cross(lift, e_flow)
    norm = math.sqrt(_dot(pitch, pitch))
    if norm <= 1e-12:
        return {"available": False}
    pitch = (pitch[0] / norm, pitch[1] / norm, pitch[2] / norm)
    force_v = (float(force[0]), float(force[1]), float(force[2]))
    moment_v = (float(moment[0]), float(moment[1]), float(moment[2]))
    origin = (float(cofr[0]), float(cofr[1]), float(cofr[2]))

    denom = _dot(pitch, _cross(e_flow, force_v))
    if abs(denom) < 1e-9:
        return {"available": False, "note": "vertical aerodynamic force too small"}
    offset = _dot(pitch, moment_v) / denom
    cop = tuple(origin[i] + offset * e_flow[i] for i in range(3))

    downforce = _dot(force_v, lift)
    if abs(downforce) < 1e-9:
        return {"available": False, "note": "no downforce"}

    projection = lambda point: _dot(point, e_flow)  # noqa: E731
    s_cg = projection(origin)
    d_front = (1.0 - front_pct / 100.0) * wheelbase
    d_rear = (front_pct / 100.0) * wheelbase
    s_front = s_cg - d_front        # forward = -e_flow
    s_rear = s_cg + d_rear
    s_cop = projection(cop)
    length = s_rear - s_front
    if abs(length) < 1e-9:
        return {"available": False}

    front_load = downforce * (s_rear - s_cop) / length
    rear_load = downforce - front_load
    inside = 0.0 <= front_load <= downforce if downforce > 0 else False
    note = "Assumes CofR is at the CoG."
    if not inside:
        note += " CoP is outside the wheelbase — set CofR to the CoG."
    return {
        "available": True,
        "cop": [round(c, 4) for c in cop],
        "cop_behind_front_axle": round(s_cop - s_front, 4),
        "cop_pct_wheelbase": round((s_cop - s_front) / length * 100.0, 2),
        "wheelbase": round(wheelbase, 4),
        "static_front_pct": round(front_pct, 2),
        "front_load": round(front_load, 3),
        "rear_load": round(rear_load, 3),
        "front_pct": round(front_load / downforce * 100.0, 2),
        "downforce": round(downforce, 3),
        "inside_wheelbase": bool(inside),
        "note": note,
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


def _lateral_axis_index(drag_idx: int, df_idx: int) -> int:
    """Index of the axis normal to the symmetry plane (neither flow nor up)."""
    for index in range(3):
        if index != drag_idx and index != df_idx:
            return index
    return 0


def _project_force_columns_for_symmetry(
    cols: dict[str, list[float]], lateral_idx: int
) -> dict[str, list[float]]:
    """Full-car forces: in-plane components double, the normal component cancels."""
    projected: dict[str, list[float]] = {}
    for name, values in cols.items():
        axis = name[-1] if name else ""
        if axis not in "xyz":
            # Unknown / derived column (e.g. a magnitude) has no defined
            # orientation on the symmetry plane; leave it untouched.
            projected[name] = list(values)
        elif "xyz".index(axis) == lateral_idx:
            projected[name] = [0.0 for _ in values]
        else:
            projected[name] = [value * 2.0 for value in values]
    return projected


def _project_moment_columns_for_symmetry(
    cols: dict[str, list[float]], lateral_idx: int
) -> dict[str, list[float]]:
    """Full-car moments (pseudovector): about-normal doubles, others cancel."""
    projected: dict[str, list[float]] = {}
    for name, values in cols.items():
        axis = name[-1] if name else ""
        if axis not in "xyz":
            # Unknown / derived column has no defined orientation here.
            projected[name] = list(values)
        elif "xyz".index(axis) == lateral_idx:
            projected[name] = [value * 2.0 for value in values]
        else:
            projected[name] = [0.0 for _ in values]
    return projected


# Force/moment coefficients perpendicular to the symmetry plane cancel for the
# projected full car (side force, roll, yaw).
_SYMMETRY_ZERO_COEFFICIENTS = {"Cs", "Cs(f)", "Cs(r)", "CmRoll", "CmYaw"}


def _project_coefficient_columns_for_symmetry(
    cols: dict[str, list[float]],
) -> dict[str, list[float]]:
    """Full-car coefficients: in-plane double, out-of-plane (side/roll/yaw) cancel."""
    return {
        name: (
            [0.0 for _ in values]
            if name in _SYMMETRY_ZERO_COEFFICIENTS
            else [value * 2.0 for value in values]
        )
        for name, values in cols.items()
    }


def _shift_moment_columns(
    moment_cols: dict[str, list[float]],
    force_cols: dict[str, list[float]],
    cofr_run: list[float],
    cofr_effective: list[float],
) -> dict[str, list[float]]:
    """Translate stored moments from the run-time CofR to an effective CofR.

    Uses the parallel-axis theorem ``M_new = M_old + (CofR_run - CofR_new) x F``
    so the moment vector, pressure/viscous breakdown, and coefficients all agree
    on the same reference point.
    """
    if not moment_cols:
        return moment_cols
    delta = (
        cofr_run[0] - cofr_effective[0],
        cofr_run[1] - cofr_effective[1],
        cofr_run[2] - cofr_effective[2],
    )
    if delta == (0.0, 0.0, 0.0):
        return moment_cols

    shifted = {name: list(values) for name, values in moment_cols.items()}
    for kind in ("total", "pressure", "viscous"):
        mkeys = [f"{kind}_x", f"{kind}_y", f"{kind}_z"]
        fkeys = mkeys if kind != "total" else ["total_x", "total_y", "total_z"]
        if not all(key in moment_cols for key in mkeys):
            continue
        if not all(key in force_cols for key in fkeys):
            continue
        count = min(len(moment_cols[mkeys[0]]), len(force_cols[fkeys[0]]))
        for index in range(count):
            force = (
                force_cols[fkeys[0]][index],
                force_cols[fkeys[1]][index],
                force_cols[fkeys[2]][index],
            )
            offset = _cross(delta, force)
            shifted[mkeys[0]][index] += offset[0]
            shifted[mkeys[1]][index] += offset[1]
            shifted[mkeys[2]][index] += offset[2]
    return shifted


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
) -> dict[str, list[float]]:
    """Normalize force/moment histories with the effective reference values.

    ``moment_cols`` must already be expressed about the effective CofR (see
    :func:`_shift_moment_columns`). Axes follow the OpenFOAM ``forceCoeffs``
    conventions (drag/lift directions plus pitch/roll/yaw about drag/lift/side).
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
            series["CmPitch"].append(_dot(moment, pitch_axis) / q_area_length)
            series["CmRoll"].append(_dot(moment, roll_axis) / q_area_length)
            series["CmYaw"].append(_dot(moment, yaw_axis) / q_area_length)

    return series




def _read_text_tail_lines(path: Path, max_lines: int = 50000) -> str:
    """Read at most the last ``max_lines`` of a text file without buffering it all."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=max_lines))
    except OSError:
        return ""


def _load_solver_end_time(cfg_path: Optional[str], case_dir: Optional[Path] = None) -> Optional[float]:
    return caseconfig.solver_end_time_from_case(config_path=cfg_path, case_dir=case_dir)




def _yplus_target_from_config(config_dict: Optional[dict[str, Any]]) -> Optional[float]:
    """Read ``layers.y_plus_target`` (or its resolved fallback) from a config."""
    return caseconfig.yplus_target(config_dict)




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

