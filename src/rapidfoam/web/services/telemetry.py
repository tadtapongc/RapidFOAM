"""Residual / solver / y+ parsing and summarisation (no FastAPI imports)."""

from __future__ import annotations

import math
import re
from typing import Any, Optional


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


