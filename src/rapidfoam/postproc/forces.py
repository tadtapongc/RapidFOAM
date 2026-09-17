"""Force reading and convergence checking."""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

from rapidfoam.geometry import AXIS_MAP as AXIS_MAP, axis_index_sign


def load_axis_config(
    config_path: str | None = None,
    case_dir: str | Path | None = None,
) -> tuple[int, int, int, int, str, str]:
    """Load drag/downforce axis from config or case_config.json.

    Returns:
        (drag_idx, drag_sign, df_idx, df_sign, drag_axis_str, df_axis_str)
    """
    cfg = None
    base = Path(case_dir) if case_dir else Path(".")
    if config_path and Path(config_path).exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    elif (base / "case_config.json").exists():
        with open(base / "case_config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    elif Path("case_config.json").exists():
        with open("case_config.json", encoding="utf-8") as f:
            cfg = json.load(f)

    # Support both old and new config formats
    if cfg:
        outputs = cfg.get("outputs", {})
        drag_axis = outputs.get("drag_axis") or cfg.get("drag_axis", "-z")
        df_axis = outputs.get("downforce_axis") or cfg.get("downforce_axis", "-y")
    else:
        drag_axis = "-z"
        df_axis = "-y"

    drag_idx, drag_sign = axis_index_sign(drag_axis)
    df_idx, df_sign = axis_index_sign(df_axis)
    return drag_idx, drag_sign, df_idx, df_sign, drag_axis, df_axis


def is_symmetry_case(config_path: str | None = None, case_dir: str | Path | None = None) -> bool:
    """Check if case is configured with a symmetry boundary."""
    cfg = None
    base = Path(case_dir) if case_dir else Path(".")
    if config_path and Path(config_path).exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    elif (base / "case_config.json").exists():
        with open(base / "case_config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    elif Path("case_config.json").exists():
        with open("case_config.json", encoding="utf-8") as f:
            cfg = json.load(f)

    if cfg:
        faces = cfg.get("domain_faces", {})
        symmetry_name = cfg.get("patches", {}).get("symmetry", "symmetry")
        if any(v == symmetry_name or "symmetry" in str(v).lower() for v in faces.values()):
            return True

    # Fallback: check constant/polyMesh/boundary
    boundary_file = base / "constant" / "polyMesh" / "boundary"
    if boundary_file.exists():
        try:
            content = boundary_file.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\btype\s+symmetry(?:Plane)?\s*;", content):
                return True
        except Exception:
            pass

    return False


# ============================================================
# FILE DISCOVERY
# ============================================================

def _dir_time(p: Path) -> float:
    try:
        return float(p.name)
    except ValueError:
        return 0.0


def force_layout_from_header(line: str) -> bool | None:
    """Infer the force.dat vector layout from a header/comment line.

    Returns:
        True  if the first force vector is already the total force (ESI format).
        False if the file stores pressure then viscous vectors separately
              (classic OpenFOAM Foundation format), so the total is their sum.
        None  if the line carries no layout information.
    """
    text = str(line).lower()
    if "total_x" in text or "total_y" in text or "total_z" in text:
        return True
    if "pressure" in text and "viscous" in text:
        return False
    return None


def find_force_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find force.dat files across time directories."""
    base = Path(base_dir) if base_dir else Path(".")
    all_files: list[Path] = []

    forces_dir = base / "postProcessing" / "forces"
    if forces_dir.exists():
        for d in sorted(forces_dir.glob("*/"), key=_dir_time):
            f = d / "force.dat"
            if f.exists():
                all_files.append(f)

    # Processor fallback (parallel live data)
    if not all_files:
        for proc_dir in sorted(base.glob("processor*")):
            pf = proc_dir / "postProcessing" / "forces"
            if pf.exists():
                for d in sorted(pf.glob("*/"), key=_dir_time):
                    f = d / "force.dat"
                    if f.exists():
                        all_files.append(f)
                if all_files:
                    break

    return all_files


# ============================================================
# DATA READING
# ============================================================

def read_forces(
    files: list[Path] | Path,
    drag_idx: int,
    drag_sign: int,
    df_idx: int,
    df_sign: int,
) -> tuple[list[float], list[float], list[float]]:
    """Parse force.dat files.

    Returns:
        (times, drags, downforces)
    """
    samples: dict[float, tuple[float, float, float]] = {}

    if not isinstance(files, (list, tuple)):
        files = [files]

    for path in files:
        segment_started = False
        # Default to the total-first (ESI) layout; only switch when a header
        # explicitly identifies the classic pressure/viscous layout.
        columnar_total = True
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        layout = force_layout_from_header(line)
                        if layout is not None:
                            columnar_total = layout
                        continue
                    parts = line.replace("(", "").replace(")", "").split()
                    if len(parts) < 10:
                        continue
                    try:
                        values = [float(v) for v in parts[:10]]
                    except ValueError:
                        continue
                    if not all(math.isfinite(v) for v in values):
                        continue
                    t = values[0]
                    t_key = round(t, 8)
                    if not segment_started:
                        # A restarted run supersedes the old trajectory from here,
                        # including old future samples it has not reached yet.
                        samples = {key: sample for key, sample in samples.items() if key < t_key}
                        segment_started = True
                    if columnar_total:
                        drag_value = values[1 + drag_idx]
                        df_value = values[1 + df_idx]
                    else:
                        # Classic layout: sum pressure + viscous components.
                        drag_value = values[1 + drag_idx] + values[4 + drag_idx]
                        df_value = values[1 + df_idx] + values[4 + df_idx]
                    # Files arrive in restart order; newer valid rows replace overlaps.
                    samples[t_key] = (
                        t, drag_value * drag_sign, df_value * df_sign,
                    )
        except OSError:
            pass

    combined = sorted(samples.values())
    return ([c[0] for c in combined], [c[1] for c in combined],
            [c[2] for c in combined])


# ============================================================
# CONVERGENCE CHECK
# ============================================================

def check_convergence(
    drags: list[float],
    downforces: list[float],
    window: int = 200,
    threshold: float = 0.5,
) -> tuple[bool, float, float, float, float]:
    """Check force convergence.

    Returns:
        (converged, drag_pct, df_pct, drag_avg, df_avg)

    Threshold is 0.5% over 200 iterations — reliable for external aero.
    """
    if window < 2 or threshold <= 0 or not math.isfinite(threshold):
        raise ValueError("window must be >= 2 and threshold must be finite and > 0")
    if len(drags) != len(downforces):
        raise ValueError("Drag and downforce histories must have equal lengths")
    if len(drags) < window:
        window = len(drags)
    if window < 20:
        if drags and all(math.isfinite(v) for v in drags + downforces):
            return False, 100.0, 100.0, statistics.mean(drags), statistics.mean(downforces)
        return False, 100.0, 100.0, 0.0, 0.0


    d_win = drags[-window:]
    f_win = downforces[-window:]
    if not all(math.isfinite(v) for v in d_win + f_win):
        return False, 100.0, 100.0, 0.0, 0.0
    d_avg = statistics.mean(d_win)
    f_avg = statistics.mean(f_win)
    d_std = statistics.stdev(d_win)
    f_std = statistics.stdev(f_win)

    d_pct = (d_std / abs(d_avg) * 100) if d_avg != 0 else 100.0
    f_pct = (f_std / abs(f_avg) * 100) if f_avg != 0 else 100.0

    return (d_pct < threshold and f_pct < threshold), d_pct, f_pct, d_avg, f_avg


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    times: list[float],
    drags: list[float],
    downforces: list[float],
    drag_axis: str,
    df_axis: str,
    is_symmetry: bool = False,
) -> bool:
    """Print force summary with convergence info. Returns True if converged."""
    if not times:
        print("  No force data found.")
        return False

    converged, d_pct, f_pct, d_avg, f_avg = check_convergence(drags, downforces)
    ld = abs(f_avg / d_avg) if d_avg != 0 else 0

    print(f"\n{'='*65}")
    print(f"  FORCE RESULTS ({len(times)} iterations)")
    if is_symmetry:
        print("  ℹ  SYMMETRY DETECTED: Showing Half-Model and Full-Car (x2)")
    print(f"{'='*65}")

    if is_symmetry:
        print("  [Half-Model Simulated]")
        print(f"    Drag ({drag_axis}):        {drags[-1]:>10.3f} N")
        print(f"    Downforce ({df_axis}):    {downforces[-1]:>10.3f} N")
        if drags[-1] != 0:
            print(f"    L/D:                {abs(downforces[-1]/drags[-1]):>10.3f}")
        print("\n  [Full-Car Projected (x2)]")
        print(f"    Drag ({drag_axis}):        {drags[-1] * 2:>10.3f} N")
        print(f"    Downforce ({df_axis}):    {downforces[-1] * 2:>10.3f} N")
        if drags[-1] != 0:
            print(f"    L/D:                {abs(downforces[-1]/drags[-1]):>10.3f}")
        print(f"{'-'*65}")
        print("  Averaged (last 200 iterations):")
        print(f"    Half-Model:  Drag = {d_avg:>9.3f} N (±{d_pct:.2f}%) | DF = {f_avg:>9.3f} N (±{f_pct:.2f}%)")
        print(f"    Full-Car:    Drag = {d_avg * 2:>9.3f} N (±{d_pct:.2f}%) | DF = {f_avg * 2:>9.3f} N (±{f_pct:.2f}%)")
        print(f"    L/D:         {ld:>9.3f}")
    else:
        print(f"  Drag ({drag_axis}):        {drags[-1]:>10.3f} N")
        print(f"  Downforce ({df_axis}):    {downforces[-1]:>10.3f} N")
        if drags[-1] != 0:
            print(f"  L/D:                {abs(downforces[-1]/drags[-1]):>10.3f}")
        print(f"{'-'*65}")
        print("  Averaged (last 200 iterations):")
        print(f"    Drag:         {d_avg:>10.3f} N  (±{d_pct:.3f}%)")
        print(f"    Downforce:    {f_avg:>10.3f} N  (±{f_pct:.3f}%)")
        print(f"    L/D:          {ld:>10.3f}")

    print(f"  Status: {'✓ CONVERGED' if converged else '✗ NOT CONVERGED'}")
    print(f"{'='*65}\n")

    return converged
