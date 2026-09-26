"""Near-wall y+ reading and target verification.

Parses the ``yPlus`` function object output (``postProcessing/yPlus/<time>/yPlus.dat``)
which has the columns ``Time  patch  min  max  average``. Used to check that a
mesh's realised y+ matches the layer sizing target; a mismatch is the most
common cause of poor near-wall forces that is invisible in the force history.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from rapidfoam.postproc.forces import _dir_time


def find_yplus_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find yPlus.dat files, newest time directory last (handles parallel runs)."""
    base = Path(base_dir) if base_dir else Path(".")
    all_files: list[Path] = []

    yp_dir = base / "postProcessing" / "yPlus"
    if yp_dir.exists():
        for d in sorted(yp_dir.glob("*/"), key=_dir_time):
            f = d / "yPlus.dat"
            if f.exists():
                all_files.append(f)

    if not all_files:
        for proc_dir in sorted(base.glob("processor*")):
            pr = proc_dir / "postProcessing" / "yPlus"
            if pr.exists():
                for d in sorted(pr.glob("*/"), key=_dir_time):
                    f = d / "yPlus.dat"
                    if f.exists():
                        all_files.append(f)
                if all_files:
                    break

    return all_files


def read_yplus(files: Iterable[Path]) -> dict[str, dict[str, float]]:
    """Read yPlus.dat files into ``{patch: {min, max, average, time}}``.

    Later time directories override earlier ones, so the returned values are
    the most recent per patch. Malformed rows are skipped.
    """
    result: dict[str, dict[str, float]] = {}
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
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
                    patch = parts[1]
                    prev = result.get(patch)
                    if prev is None or time >= prev.get("time", -math.inf):
                        result[patch] = {"min": lo, "max": hi, "average": avg, "time": time}
        except OSError:
            continue
    return result


def check_yplus_target(
    data: dict[str, dict[str, float]],
    target: float | None,
    wall_function_tiers: tuple[float, float] = (30.0, 300.0),
    tolerance: float = 0.5,
    wall_resolved_max: float = 5.0,
) -> dict[str, object]:
    """Compare realised y+ against the sizing target.

    ``target`` is the requested y+ for the near-wall cell centre. A patch is
    "on target" when its average y+ is within +/-``tolerance`` (fraction) of the
    target.

    The "acceptable" fallback is regime-aware: a wall-function target (y+ >= 30)
    tolerates anything in ``wall_function_tiers``, while a wall-resolved target
    (y+ <= 5) tolerates only ``wall_resolved_max``. A low target realised far
    above its band is off-target, not acceptable.
    """
    if not data:
        return {"available": False, "note": "no yPlus output found"}

    lo_tier, hi_tier = wall_function_tiers
    per_patch: dict[str, dict[str, object]] = {}
    worst: tuple[float, str] | None = None

    def _acceptable(avg: float) -> bool:
        if target and 0 < target <= wall_resolved_max:
            return avg <= wall_resolved_max
        return lo_tier <= avg <= hi_tier

    for patch, stats in data.items():
        avg = stats.get("average")
        if avg is None or not math.isfinite(avg):
            status = "unknown"
        elif target and target > 0:
            ratio = avg / target
            if abs(ratio - 1.0) <= tolerance:
                status = "on_target"
            elif _acceptable(avg):
                status = "acceptable"
            else:
                status = "off_target"
        else:
            status = "on_target" if _acceptable(avg) else "off_target"

        per_patch[patch] = {**stats, "status": status}
        if status == "off_target" and (worst is None or (avg or 0) > worst[0]):
            worst = (avg or 0.0, patch)

    if target is None or target <= 0:
        return {
            "available": True,
            "target": None,
            "patches": per_patch,
            "off_target": [p for p, s in per_patch.items() if s["status"] == "off_target"],
            "note": "no y+ target configured; showing realised values only",
        }

    band_lo = target * (1.0 - tolerance)
    band_hi = target * (1.0 + tolerance)
    off_target = [p for p, s in per_patch.items() if s["status"] == "off_target"]
    if off_target:
        note = (
            f"y+ target {target:g} missed on {len(off_target)} patch(es): "
            + ", ".join(
                f"{p} avg {per_patch[p]['average']:.1f}" for p in off_target
            )
            + f" (target band {band_lo:.1f}-{band_hi:.1f})"
        )
    else:
        note = f"y+ target {target:g} met on all {len(per_patch)} patch(es)"

    return {
        "available": True,
        "target": target,
        "band": [round(band_lo, 2), round(band_hi, 2)],
        "patches": per_patch,
        "off_target": off_target,
        "worst_patch": worst[1] if worst else None,
        "note": note,
    }
