"""Grid-independence study across fidelity meshes.

Compares the forces of the ``fast`` / ``standard`` / ``fine`` meshes of a case,
reports the discretisation deltas, a Richardson-style extrapolation, and a
mesh-convergence verdict. Reads existing case artifacts only (postproc layer);
nothing here runs OpenFOAM.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable, Optional

from rapidfoam.core import caseconfig
from rapidfoam.postproc.forces import (
    axis_index_sign,
    find_coefficient_files,
    find_force_files,
    normalize_coefficient_columns,
    read_forces,
    window_stats,
)

# Cd must refine within this fraction and Cl within this one to call it converged.
CD_THRESHOLD = 0.03
CL_THRESHOLD = 0.05


def _coefficients_for_case(case_dir: Path) -> dict[str, Any]:
    """Trailing-window Cd/Cl for one case, from coefficient.dat or forces."""
    coeff_files = find_coefficient_files(case_dir)
    if coeff_files:
        from rapidfoam.postproc.forces import parse_tabular_dat

        segments = []
        for path in coeff_files:
            try:
                segments.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        times, rows, header = parse_tabular_dat(segments)
        cols = normalize_coefficient_columns(rows, header)
        cd = cols.get("Cd")
        cl = cols.get("Cl")
        if cd and cl:
            cd_avg, cd_pct = window_stats(cd)
            cl_avg, cl_pct = window_stats(cl)
            return {
                "available": True,
                "source": "coefficient.dat",
                "cd": cd_avg,
                "cl": cl_avg,
                "cd_pct": cd_pct,
                "cl_pct": cl_pct,
                "iterations": len(times),
            }

    # Fall back to raw forces + config reference values.
    cfg = caseconfig.read_case_config(case_dir=case_dir)
    outputs = cfg.get("outputs", {})
    drag_axis = outputs.get("drag_axis", "-z")
    df_axis = outputs.get("downforce_axis", "-y")
    drag_idx, drag_sign = axis_index_sign(drag_axis)
    df_idx, df_sign = axis_index_sign(df_axis)
    files = find_force_files(case_dir)
    if not files:
        return {"available": False}
    times, drags, downforces = read_forces(files, drag_idx, drag_sign, df_idx, df_sign)
    if not times:
        return {"available": False}
    refs = cfg.get("force_refs", {})
    fluid = cfg.get("fluid", {})
    flow = cfg.get("flow", {})
    rho = float(fluid.get("rho", 1.225) or 1.225)
    velocity = float(flow.get("velocity", 0.0) or 0.0)
    aref = float(refs.get("Aref", 1.0) or 1.0)
    q_area = 0.5 * rho * velocity * velocity * aref
    if q_area <= 0:
        return {"available": False}
    # Symmetry doubles the half-model force.
    sym = 2.0 if caseconfig.has_symmetry(cfg) else 1.0
    cd_series = [v * sym / q_area for v in drags]
    cl_series = [v * sym / q_area for v in downforces]
    cd_avg, cd_pct = window_stats(cd_series)
    cl_avg, cl_pct = window_stats(cl_series)
    return {
        "available": True,
        "source": "computed",
        "cd": cd_avg,
        "cl": cl_avg,
        "cd_pct": cd_pct,
        "cl_pct": cl_pct,
        "iterations": len(times),
    }


def _richardson_p(cd_fast: float, cd_std: float, cd_fine: float, r: float) -> Optional[float]:
    """Observed order of accuracy from three Cd levels (None if it cannot be formed)."""
    d_sf = abs(cd_fine - cd_std)
    d_fs = abs(cd_std - cd_fast)
    if d_sf <= 0 or d_fs <= 0 or r <= 1.0:
        return None
    return math.log(d_fs / d_sf) / math.log(r)


# Safety factor for the Roache Grid Convergence Index. 1.25 is the standard for
# a study with three or more grids.
GCI_SAFETY_FACTOR = 1.25


def _gci_pct(coarse: float, fine: float, r: float, p: float, fs: float = GCI_SAFETY_FACTOR) -> Optional[float]:
    """Roache Grid Convergence Index, as a percentage of the fine value.

    ``GCI = Fs * |(f_fine - f_coarse) / f_fine| / (r^p - 1)``. Returns ``None``
    when the observed order ``p`` is unavailable or the ratio is degenerate.
    """
    if coarse is None or fine is None or fine == 0 or r <= 1.0 or p is None:
        return None
    denom = r ** p - 1.0
    if denom <= 0:
        return None
    return round(fs * (abs(fine - coarse) / abs(fine)) / denom * 100.0, 3)


_CHECKMESH_CELLS_RE = re.compile(r"^\s*cells:\s+(\d+)", re.MULTILINE)


def _read_mesh_cells(case_dir: Path) -> Optional[int]:
    """Realised cell count from ``log.checkMesh`` (``cells:`` line), or None.

    This is the *meshed* count (background + snappy refinement + layers), as
    opposed to ``mesh_params.block_cells`` which is only the background grid.
    """
    try:
        text = (case_dir / "log.checkMesh").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _CHECKMESH_CELLS_RE.search(text)
    return int(match.group(1)) if match else None


def _level_entry(
    coeffs: dict[str, Any],
    case_name: str,
    cells: Optional[int],
    mesh_cells: Optional[int] = None,
) -> dict[str, Any]:
    """One ``per_fidelity`` row from a parsed coefficients dict.

    Carries the trailing-window variation (``cd_pct``/``cl_pct``) and the aero
    efficiency ``ld`` (|Cl/Cd|) alongside the rounded coefficients, so the panel
    can show convergence *quality* and not just the values. ``cells`` is the
    background block count (used for the refinement ratio); ``mesh_cells`` is the
    realised checkMesh count, informational only.
    """
    cd = coeffs.get("cd")
    cl = coeffs.get("cl")
    return {
        "available": True,
        "case": case_name,
        "cd": round(cd, 5) if cd is not None else None,
        "cl": round(cl, 5) if cl is not None else None,
        "cd_pct": round(coeffs["cd_pct"], 3) if coeffs.get("cd_pct") is not None else None,
        "cl_pct": round(coeffs["cl_pct"], 3) if coeffs.get("cl_pct") is not None else None,
        "ld": round(abs(cl / cd), 3) if cd not in (None, 0) and cl is not None else None,
        "source": coeffs.get("source"),
        "cells": cells,
        "mesh_cells": mesh_cells,
        "iterations": coeffs.get("iterations"),
    }


def _is_monotonic(values: list[Optional[float]]) -> Optional[bool]:
    """True/False when a series is monotone, None when it has fewer than two points."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    increasing = all(b >= a for a, b in zip(vals, vals[1:]))
    decreasing = all(b <= a for a, b in zip(vals, vals[1:]))
    return increasing or decreasing


def grid_study(
    base: str,
    *,
    cases_root: Path | str | None = None,
    fidelities: tuple[str, ...] = ("fast", "standard", "fine"),
    name_for: Callable[[str, str], str] | None = None,
    cd_thresh: float = CD_THRESHOLD,
    cl_thresh: float = CL_THRESHOLD,
) -> dict[str, Any]:
    """Compare the fidelities of a case and report mesh convergence.

    ``base`` is the case stem; by default the ``fast``/``standard``/``fine`` cases
    are looked up as ``<base>_fast`` / ``<base>_standard`` / ``<base>_fine`` (and
    ``<base>`` itself is accepted as ``standard`` when the suffixed one is
    absent). ``name_for(fidelity, base)`` overrides the name mapping.
    """
    root = Path(cases_root) if cases_root else Path("cases")

    # Refinement study (configure_refinement_study) writes <base>_cpl<N> variants;
    # if present, use those ordered coarse->fine — a valid refinement-only ladder.
    cpl_dirs: list[tuple[int, Path]] = []
    for d in root.glob(f"{base}_cpl*"):
        suffix = d.name[len(base) + 4:]
        if d.is_dir() and suffix.isdigit():
            cpl_dirs.append((int(suffix), d))
    if len(cpl_dirs) >= 3:
        cpl_dirs.sort()
        per_fidelity: dict[str, Any] = {}
        pinned: dict[str, Any] = {}
        for cpl, case_dir in cpl_dirs:
            label = f"cpl{cpl}"
            coeffs = _coefficients_for_case(case_dir)
            if not coeffs.get("available"):
                per_fidelity[label] = {"available": False, "note": "no force/coefficient data"}
                continue
            cfg = caseconfig.read_case_config(case_dir=case_dir)
            mesh = cfg.get("mesh_params", {})
            cells = mesh.get("block_cells")
            n = int(cells[0]) * int(cells[1]) * int(cells[2]) if isinstance(cells, (list, tuple)) and len(cells) == 3 else None
            # Screened physics: confirm the near-wall/solver settings are identical
            # across the ladder (they must be for a valid Richardson estimate).
            pinned[label] = {
                "y_plus_target": cfg.get("layers", {}).get("y_plus_target"),
                "n_layers": cfg.get("layers", {}).get("n_layers"),
                "end_time": cfg.get("solver", {}).get("end_time"),
            }
            per_fidelity[label] = _level_entry(coeffs, case_dir.name, n, _read_mesh_cells(case_dir))
        # Only levels that actually produced force/coefficient data can be
        # compared; passing unavailable levels to _finish_study would index a
        # missing "cd"/"cl" key (a level may still be meshing/solving). Mirrors
        # the fidelity branch below.
        available = [label for label, info in per_fidelity.items() if info.get("available")]
        report = _finish_study(available, per_fidelity, cd_thresh, cl_thresh)
        report["mode"] = "refinement"
        report["pinned"] = pinned
        report["pinned_consistent"] = len({tuple(sorted(v.items())) for v in pinned.values()}) <= 1
        if not report["pinned_consistent"]:
            report["note"] = "⚠ near-wall/solver settings differ between levels — Richardson p is not strictly valid. " + report.get("note", "")
        return report

    def _resolve(fidelity: str) -> Optional[Path]:
        candidates = []
        if name_for is not None:
            candidates.append(root / name_for(fidelity, base))
        candidates.append(root / f"{base}_{fidelity}")
        if fidelity == "standard":
            candidates.append(root / base)
        for c in candidates:
            if c.is_dir():
                return c
        return None

    per_fidelity: dict[str, Any] = {}
    for fidelity in fidelities:
        case_dir = _resolve(fidelity)
        if case_dir is None:
            per_fidelity[fidelity] = {"available": False, "note": "case not found"}
            continue
        coeffs = _coefficients_for_case(case_dir)
        if not coeffs.get("available"):
            per_fidelity[fidelity] = {"available": False, "note": "no force/coefficient data"}
            continue
        mesh = caseconfig.read_case_config(case_dir=case_dir).get("mesh_params", {})
        cells = mesh.get("block_cells")
        cell_count = None
        if isinstance(cells, (list, tuple)) and len(cells) == 3:
            cell_count = int(cells[0]) * int(cells[1]) * int(cells[2])
        per_fidelity[fidelity] = _level_entry(coeffs, case_dir.name, cell_count, _read_mesh_cells(case_dir))

    available = [f for f in fidelities if per_fidelity.get(f, {}).get("available")]
    result = _finish_study(available, per_fidelity, cd_thresh, cl_thresh)
    result["mode"] = "fidelity"
    return result


def _finish_study(
    available: list[str],
    per_fidelity: dict[str, Any],
    cd_thresh: float,
    cl_thresh: float,
) -> dict[str, Any]:
    """Compute deltas, Richardson p and the verdict for a list of levels."""
    result: dict[str, Any] = {"available": len(available) >= 3, "per_fidelity": per_fidelity}

    # Per-step deltas (each level vs the previous, coarse->fine) and a
    # monotonicity check are useful even before three levels have data.
    step_deltas: dict[str, Any] = {}
    prev: Optional[str] = None
    for level in available:
        info = per_fidelity.get(level, {})
        cd, cl = info.get("cd"), info.get("cl")
        if prev is not None:
            prev_cd = per_fidelity.get(prev, {}).get("cd")
            prev_cl = per_fidelity.get(prev, {}).get("cl")
            step_deltas[level] = {
                "cd_pct": round(abs(cd - prev_cd) / abs(prev_cd) * 100, 3)
                if prev_cd and cd is not None else None,
                "cl_pct": round(abs(cl - prev_cl) / abs(prev_cl) * 100, 3)
                if prev_cl and cl is not None else None,
            }
        prev = level
    result["step_deltas"] = step_deltas
    result["monotonic"] = {
        "cd": _is_monotonic([per_fidelity.get(level, {}).get("cd") for level in available]),
        "cl": _is_monotonic([per_fidelity.get(level, {}).get("cl") for level in available]),
    }

    if len(available) < 3:
        result["verdict"] = "insufficient-data"
        result["converged"] = False
        result["note"] = f"need at least three levels with data; found {len(available)}"
        return result

    # Coarsest three, in the provided order.
    f_fast, f_std, f_fine = available[-3], available[-2], available[-1]
    cd_fast, cd_std, cd_fine = (per_fidelity[f]["cd"] for f in (f_fast, f_std, f_fine))
    cl_fast, cl_std, cl_fine = (per_fidelity[f]["cl"] for f in (f_fast, f_std, f_fine))

    if None in (cd_fast, cd_std, cd_fine, cl_std, cl_fine):
        result["verdict"] = "insufficient-data"
        result["converged"] = False
        result["note"] = "coefficients unavailable for one or more levels"
        return result

    cd_sf = abs(cd_fine - cd_std) / abs(cd_std) if cd_std else None
    cl_sf = abs(cl_fine - cl_std) / abs(cl_std) if cl_std else None
    result["deltas"] = {
        "cd_std_fine_pct": round((cd_sf or 0.0) * 100, 3),
        "cl_std_fine_pct": round((cl_sf or 0.0) * 100, 3),
    }
    # Aero-efficiency convergence: L/D can drift even when Cd and Cl each pass.
    ld_std = per_fidelity[f_std].get("ld")
    ld_fine = per_fidelity[f_fine].get("ld")
    if ld_std and ld_fine is not None:
        result["deltas"]["ld_std_fine_pct"] = round(abs(ld_fine - ld_std) / abs(ld_std) * 100, 3)

    # Refinement ratio (coarse->fine) from cell counts if available, else 1.5.
    n_fast = per_fidelity[f_fast].get("cells")
    n_fine = per_fidelity[f_fine].get("cells")
    r = 1.5
    if n_fast and n_fine and n_fast > 0:
        r = (n_fine / n_fast) ** (1.0 / 3.0)
    result["refinement_ratio"] = round(r, 4)

    p = _richardson_p(cd_fast, cd_std, cd_fine, r)
    if p is not None and r != 1.0:
        denom = r ** p - 1.0
        cd_extrap = cd_fine + (cd_fine - cd_std) / denom if denom else None
        cl_extrap = None
        if None not in (cl_fast, cl_std, cl_fine):
            cl_extrap = cl_fine + (cl_fine - cl_std) / denom if denom else None
        result["richardson"] = {
            "p": round(p, 3),
            "cd_extrapolated": round(cd_extrap, 5) if cd_extrap is not None else None,
            "cl_extrapolated": round(cl_extrap, 5) if cl_extrap is not None else None,
        }
        # Roache GCI: the uncertainty band on the extrapolated value.
        result["gci"] = {
            "safety_factor": GCI_SAFETY_FACTOR,
            "cd_pct": _gci_pct(cd_std, cd_fine, r, p),
            "cl_pct": _gci_pct(cl_std, cl_fine, r, p) if None not in (cl_std, cl_fine) else None,
        }

    converged = (cd_sf is not None and cd_sf < cd_thresh and cl_sf is not None and cl_sf < cl_thresh)
    result["converged"] = converged
    if converged:
        result["verdict"] = "grid-independent"
        result["note"] = (
            f"fine-level vs previous: Cd {cd_sf * 100:.2f}% (< {cd_thresh * 100:.0f}%), "
            f"Cl {cl_sf * 100:.2f}% (< {cl_thresh * 100:.0f}%)"
        )
    elif (cd_sf is not None and cd_sf < 2 * cd_thresh and cl_sf is not None and cl_sf < 2 * cl_thresh):
        result["verdict"] = "marginal"
        result["note"] = f"near convergent: Cd {cd_sf * 100:.2f}%, Cl {cl_sf * 100:.2f}%"
    else:
        result["verdict"] = "not-converged"
        result["note"] = f"forces still move with refinement: Cd {cd_sf * 100:.2f}%, Cl {cl_sf * 100:.2f}%"
    return result


def print_grid_study(report: dict[str, Any]) -> None:
    """Human-readable grid-independence summary for the CLI."""
    print("\n  Grid independence")
    for fidelity, info in report.get("per_fidelity", {}).items():
        if not info.get("available"):
            print(f"    {fidelity:<9} --  {info.get('note', 'unavailable')}")
            continue
        cd = f"{info['cd']:.5f}" if info.get("cd") is not None else "--"
        cl = f"{info['cl']:.5f}" if info.get("cl") is not None else "--"
        ld = f"{info['ld']:.3f}" if info.get("ld") is not None else "--"
        cells = info.get("cells")
        cells_txt = f"  cells {cells:,}" if cells else ""
        print(f"    {fidelity:<9} Cd {cd}  Cl {cl}  L/D {ld}  ({info.get('source')}){cells_txt}")
    deltas = report.get("deltas")
    if deltas:
        print(f"    delta std->fine:  Cd {deltas['cd_std_fine_pct']:.2f}%  Cl {deltas['cl_std_fine_pct']:.2f}%"
              + (f"  L/D {deltas['ld_std_fine_pct']:.2f}%" if "ld_std_fine_pct" in deltas else ""))
    rich = report.get("richardson")
    if rich:
        p = rich.get("p")
        print(f"    Richardson order p = {p}" + (
            f", Cd extrapolated {rich['cd_extrapolated']}" if rich.get("cd_extrapolated") is not None else ""
        ))
    gci = report.get("gci")
    if gci:
        print(f"    GCI (Fs {gci.get('safety_factor')}): Cd ±{gci.get('cd_pct')}%  Cl ±{gci.get('cl_pct')}%")
    if report.get("monotonic", {}).get("cd") is False:
        print("    warning: Cd is not monotone across levels (possible oscillation)")
    print(f"    verdict: {report.get('verdict')} — {report.get('note')}")


__all__ = [
    "grid_study", "print_grid_study", "configure_refinement_study",
    "CD_THRESHOLD", "CL_THRESHOLD",
]


# ---------------------------------------------------------------------------
# Refinement-study config generation
# ---------------------------------------------------------------------------

# Coarse -> fine background resolution. Ratios ~1.5 give a usable Richardson p.
DEFAULT_REFINEMENT_LEVELS = (20, 30, 45)
_REFINEMENT_LABELS = ("coarse", "medium", "fine")


def refinement_variant_name(base: str, cells_per_length: int) -> str:
    """Case name for one refinement level, e.g. ``wing`` + 20 -> ``wing_cpl20``.

    The ``_cpl<N>`` suffix is the contract the rest of the pipeline relies on:
    ``grid_study`` discovers variants with ``glob("<base>_cpl*")`` and the Studio
    strips the suffix to recover the base name.
    """
    return f"{base}_cpl{cells_per_length}"


def configure_refinement_study(
    base_config_path: str | Path,
    *,
    base_name: str | None = None,
    levels: tuple[int, ...] | None = None,
    out_dir: str | Path | None = None,
    co_refine_surface: bool | None = None,
    slurm_scaling: dict[str, list[str]] | None = None,
    preset: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Write three refinement-only configs from one base config.

    Every field is inherited from the base **except** ``mesh_params
    .cells_per_length`` (and ``case_name``), so the three cases differ only in
    background mesh density — the physics, near-wall layer target/count, solver
    and end time are identical. That is what makes a Richardson study valid
    (unlike switching ``fidelity``, which also changes y+/layers/end_time).

    Defaults come from ``DEFAULT_CONFIG["grid_study"]``. SLURM walltime and
    memory are scaled per level so the fine mesh is not starved by the base's
    config. With ``co_refine_surface`` the surface/edge level is also stepped
    +0/+1/+1 across the ladder (a stronger, less clean near-wall ladder).

    ``preset`` is the fidelity preset block (from
    :data:`rapidfoam.meshing.presets.FIDELITY_PRESETS`) used to pin the near-wall
    and end-time values when the base config does not set them explicitly. It is
    passed in by the caller because ``postproc`` may not import the meshing
    presets (bounded-context rule); when omitted, the local ``_PRESET_FALLBACK``
    table is used.

    Returns ``[{name, config_path, cells_per_length, label}]`` for each variant.
    """
    import json

    from rapidfoam.config import DEFAULT_CONFIG, effective_config, user_set

    gs = DEFAULT_CONFIG.get("grid_study", {})
    if levels is None:
        levels = tuple(gs.get("levels", DEFAULT_REFINEMENT_LEVELS))
    if co_refine_surface is None:
        co_refine_surface = bool(gs.get("co_refine_surface", False))
    if slurm_scaling is None:
        slurm_scaling = gs.get("slurm", {})

    base_path = Path(base_config_path)
    raw = json.loads(base_path.read_text(encoding="utf-8"))
    stem = base_name or raw.get("case_name") or base_path.stem
    out = Path(out_dir) if out_dir else base_path.parent

    # The effective fidelity of the base config. Preset values (from the caller
    # or the local fallback) pin the near-wall layers and end time so a later
    # edit to a preset cannot drift the study.
    eff = effective_config(raw)
    fidelity = eff.get("fidelity", "standard")
    if preset is None:
        preset = _PRESET_FALLBACK.get(fidelity, _PRESET_FALLBACK["standard"])

    overrides = dict(raw.get("overrides") or {})
    layers = dict(overrides.get("layers") or {})
    mesh = dict(overrides.get("mesh_params") or {})
    solver = dict(overrides.get("solver") or {})

    # A base value is authoritative only if the user set it explicitly. Values
    # that merely leaked in from DEFAULT_CONFIG (e.g. n_layers 5, end_time 800)
    # must not override the selected fidelity preset.
    def _pinned(section: str, key: str) -> Any:
        block = (raw.get(section) or {}) if isinstance(raw.get(section), dict) else {}
        if key in block:
            return block[key]
        return None

    def _resolve(current: dict[str, Any], section: str, key: str, preset_key: str) -> Any:
        if key in current:
            return current[key]
        explicit = _pinned(section, key)
        if explicit is not None:
            return explicit
        if user_set(raw, section, key):
            return eff.get(section, {}).get(key)
        return preset.get(preset_key)

    layers["y_plus_target"] = _resolve(layers, "layers", "y_plus_target", "y_plus_target")
    layers["n_layers"] = _resolve(layers, "layers", "n_layers", "n_layers")
    layers["expansion_ratio"] = _resolve(layers, "layers", "expansion_ratio", "expansion_ratio")
    solver["end_time"] = _resolve(solver, "solver", "end_time", "end_time")
    overrides["layers"] = layers
    overrides["solver"] = solver

    base_surface = list(eff.get("mesh_params", {}).get("surface_level")
                        or preset.get("surface_level") or [4, 5])
    base_edge = int(eff.get("mesh_params", {}).get("edge_level")
                    or preset.get("edge_level") or 6)

    times = list(slurm_scaling.get("time", []))
    mems = list(slurm_scaling.get("mem_per_cpu", []))

    results: list[dict[str, Any]] = []
    last_index = max(len(levels) - 1, 1)
    for index, cpl in enumerate(levels):
        variant = json.loads(json.dumps(raw))  # deep copy
        variant["case_name"] = refinement_variant_name(stem, cpl)
        variant["fidelity"] = raw.get("fidelity", "standard")
        variant = _ensure_overrides(variant)
        variant["overrides"]["layers"] = dict(layers)
        variant["overrides"]["solver"] = dict(solver)
        variant["overrides"]["mesh_params"] = {**mesh, "cells_per_length": int(cpl)}
        if co_refine_surface:
            step = 1 if index == last_index else 0
            variant["overrides"]["mesh_params"]["surface_level"] = [base_surface[0], base_surface[1] + step]
            variant["overrides"]["mesh_params"]["edge_level"] = base_edge + step
        # Scale SLURM resources per level (override any pinned base value).
        if times:
            variant["overrides"]["slurm"] = {**variant["overrides"].get("slurm", {}),
                                             "time": times[min(index, len(times) - 1)]}
        if mems:
            variant["overrides"].setdefault("slurm", {})["mem_per_cpu"] = mems[min(index, len(mems) - 1)]
        cfg_path = out / f"{variant['case_name']}.json"
        cfg_path.write_text(json.dumps(variant, indent=4) + "\n", encoding="utf-8")
        results.append({
            "name": variant["case_name"],
            "config_path": str(cfg_path),
            "cells_per_length": int(cpl),
            "label": _REFINEMENT_LABELS[index] if index < len(_REFINEMENT_LABELS) else f"level{index}",
        })
    return results


# Fallback fidelity settings used only when a base config does not carry explicit
# values (postproc must not import the meshing presets; bounded-context rule).
_PRESET_FALLBACK: dict[str, dict[str, Any]] = {
    "fast": {"y_plus_target": 50, "n_layers": 5, "expansion_ratio": 1.15,
             "end_time": 800, "surface_level": [3, 4], "edge_level": 5},
    "standard": {"y_plus_target": 30, "n_layers": 8, "expansion_ratio": 1.15,
                 "end_time": 1500, "surface_level": [4, 5], "edge_level": 6},
    "fine": {"y_plus_target": 1, "n_layers": 20, "expansion_ratio": 1.1,
             "end_time": 2500, "surface_level": [4, 5], "edge_level": 7},
}


def _ensure_overrides(variant: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(variant.get("overrides"), dict):
        variant["overrides"] = {}
    return variant



