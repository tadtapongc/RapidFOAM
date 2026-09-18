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


def _load_case_configs(config_path: str | None, base: Path) -> list[dict]:
    """Load every candidate case config, most authoritative first."""
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    candidates.append(base / "case_config.json")
    candidates.append(Path("case_config.json"))

    configs: list[dict] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        try:
            with open(path, encoding="utf-8") as f:
                configs.append(json.load(f))
        except Exception:
            continue
    return configs


def is_symmetry_case(config_path: str | None = None, case_dir: str | Path | None = None) -> bool:
    """Check if case is configured with a symmetry boundary.

    A config that omits ``domain_faces`` still generates a half-model because
    the face assignment defaults the lateral-min face to the symmetry patch.
    Such cases must be detected as symmetric even without an explicit face list.
    The mesh boundary is used as a final authoritative fallback.
    """
    base = Path(case_dir) if case_dir else Path(".")
    configs = _load_case_configs(config_path, base)

    explicit_faces_seen = False
    for cfg in configs:
        faces = cfg.get("domain_faces")
        if not faces:
            continue
        explicit_faces_seen = True
        symmetry_name = cfg.get("patches", {}).get("symmetry", "symmetry")
        if any(v == symmetry_name or "symmetry" in str(v).lower() for v in faces.values()):
            return True

    # No explicit domain_faces anywhere: the generator derives a symmetry face,
    # so the case is a half-model by default.
    if configs and not explicit_faces_seen:
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
# COMPONENT / COEFFICIENT PARSING
# ============================================================

# ESI (OpenCFD) tabular layout used by force.dat and moment.dat.
FORCE_COMPONENT_COLUMNS = [
    "total_x", "total_y", "total_z",
    "pressure_x", "pressure_y", "pressure_z",
    "viscous_x", "viscous_y", "viscous_z",
]

# Classic Foundation layout: pressure/viscous force followed by
# pressure/viscous moment in a single combined file.
CLASSIC_COMPONENT_COLUMNS = [
    "pressure_x", "pressure_y", "pressure_z",
    "viscous_x", "viscous_y", "viscous_z",
    "pressure_mx", "pressure_my", "pressure_mz",
    "viscous_mx", "viscous_my", "viscous_mz",
]

# coefficient.dat columns written by the forceCoeffs function object (v2606).
COEFFICIENT_COLUMNS = [
    "Cd", "Cd(f)", "Cd(r)", "Cl", "Cl(f)", "Cl(r)",
    "CmPitch", "CmRoll", "CmYaw", "Cs", "Cs(f)", "Cs(r)",
]

_FIELD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\([A-Za-z0-9_]+\))?")


def parse_tabular_dat(
    segments: list[str],
) -> tuple[list[float], list[list[float]], Optional[list[str]]]:
    """Parse OpenFOAM tabular ``postProcessing`` ``*.dat`` text segments.

    Handles the ``# Time ...`` header to recover column names and de-duplicates
    / overrides samples from restarted runs. Parentheses from classic vector
    layouts are flattened so numeric columns can be split positionally.

    Returns:
        ``(times, rows, header_names)`` where ``header_names`` are the column
        names after ``Time`` (``None`` when the header is unusable).
    """
    samples: dict[float, tuple[float, list[float]]] = {}
    header_names: Optional[list[str]] = None

    for content in segments:
        if not content:
            continue
        segment_started = False
        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                if header_names is None and "Time" in line:
                    tokens = _FIELD_TOKEN_RE.findall(line)
                    if tokens and tokens[0].lower() == "time":
                        header_names = tokens[1:]
                continue
            parts = line.replace("(", " ").replace(")", " ").split()
            if not parts:
                continue
            try:
                t = float(parts[0])
                values = [float(v) for v in parts[1:]]
            except ValueError:
                continue
            if not values or not all(math.isfinite(v) for v in values):
                continue
            t_key = round(t, 8)
            if not segment_started:
                samples = {k: s for k, s in samples.items() if k < t_key}
                segment_started = True
            samples[t_key] = (t, values)

    if not samples:
        return [], [], None

    ordered = [samples[k] for k in sorted(samples)]
    times = [item[0] for item in ordered]
    rows = [item[1] for item in ordered]
    if header_names is not None and len(header_names) != len(rows[0]):
        header_names = None
    return times, rows, header_names


def _named_columns(rows: list[list[float]], names: list[str]) -> dict[str, list[float]]:
    return {name: [row[i] for row in rows] for i, name in enumerate(names)}


def normalize_component_columns(
    rows: list[list[float]],
    header_names: Optional[list[str]] = None,
) -> dict[str, list[float]]:
    """Map parsed force/moment rows to canonical total/pressure/viscous keys."""
    if not rows:
        return {}
    ncols = len(rows[0])
    if header_names and "total_x" in header_names:
        return _named_columns(rows, header_names)
    if ncols >= len(CLASSIC_COMPONENT_COLUMNS):
        cols = _named_columns(rows, CLASSIC_COMPONENT_COLUMNS[:ncols])
        for axis in ("x", "y", "z"):
            cols[f"total_{axis}"] = [
                p + v for p, v in zip(cols[f"pressure_{axis}"], cols[f"viscous_{axis}"])
            ]
            cols[f"total_m{axis}"] = [
                p + v for p, v in zip(cols[f"pressure_m{axis}"], cols[f"viscous_m{axis}"])
            ]
        return cols
    if ncols >= len(FORCE_COMPONENT_COLUMNS):
        return _named_columns(rows, FORCE_COMPONENT_COLUMNS[:ncols])
    return {}


def normalize_coefficient_columns(
    rows: list[list[float]],
    header_names: Optional[list[str]] = None,
) -> dict[str, list[float]]:
    """Map parsed coefficient.dat rows to canonical coefficient keys."""
    if not rows:
        return {}
    ncols = len(rows[0])
    if header_names and len(header_names) == ncols:
        return _named_columns(rows, header_names)
    if ncols >= len(COEFFICIENT_COLUMNS):
        return _named_columns(rows, COEFFICIENT_COLUMNS[:ncols])
    return {}


def find_moment_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find moment.dat files across time directories."""
    base = Path(base_dir) if base_dir else Path(".")
    all_files: list[Path] = []

    forces_dir = base / "postProcessing" / "forces"
    if forces_dir.exists():
        for d in sorted(forces_dir.glob("*/"), key=_dir_time):
            f = d / "moment.dat"
            if f.exists():
                all_files.append(f)

    if not all_files:
        for proc_dir in sorted(base.glob("processor*")):
            pf = proc_dir / "postProcessing" / "forces"
            if pf.exists():
                for d in sorted(pf.glob("*/"), key=_dir_time):
                    f = d / "moment.dat"
                    if f.exists():
                        all_files.append(f)
                if all_files:
                    break

    return all_files


def find_coefficient_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find forceCoeffs coefficient.dat files across time directories."""
    base = Path(base_dir) if base_dir else Path(".")
    all_files: list[Path] = []
    names = ("coefficient.dat", "forceCoeffs.dat")

    coeff_dir = base / "postProcessing" / "forceCoeffs"
    if coeff_dir.exists():
        for d in sorted(coeff_dir.glob("*/"), key=_dir_time):
            for name in names:
                f = d / name
                if f.exists():
                    all_files.append(f)
                    break

    if not all_files:
        for proc_dir in sorted(base.glob("processor*")):
            pf = proc_dir / "postProcessing" / "forceCoeffs"
            if pf.exists():
                for d in sorted(pf.glob("*/"), key=_dir_time):
                    for name in names:
                        f = d / name
                        if f.exists():
                            all_files.append(f)
                            break
                if all_files:
                    break

    return all_files


def window_stats(values: list[Optional[float]], window: int = 200) -> tuple[Optional[float], Optional[float]]:
    """Mean and relative standard deviation (%) over the trailing window.

    Mirrors the convergence metric used by :func:`check_convergence` so
    coefficients and forces report variation on the same basis.
    """
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return None, None
    size = min(window, len(finite))
    if size < 2:
        return finite[-1], 100.0
    sub = finite[-size:]
    avg = statistics.mean(sub)
    pct = (statistics.stdev(sub) / abs(avg) * 100) if avg != 0 else 100.0
    return avg, pct


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
