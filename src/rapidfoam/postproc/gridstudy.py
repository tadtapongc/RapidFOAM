"""Grid-independence study across fidelity meshes.

Compares the forces of the ``fast`` / ``standard`` / ``fine`` meshes of a case,
reports the discretisation deltas, a Richardson-style extrapolation, and a
mesh-convergence verdict. Reads existing case artifacts only (postproc layer);
nothing here runs OpenFOAM.
"""

from __future__ import annotations

import math
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
        per_fidelity[fidelity] = {
            "available": True,
            "case": case_dir.name,
            "cd": round(coeffs["cd"], 5) if coeffs.get("cd") is not None else None,
            "cl": round(coeffs["cl"], 5) if coeffs.get("cl") is not None else None,
            "source": coeffs.get("source"),
            "cells": cell_count,
        }

    available = [f for f in fidelities if per_fidelity.get(f, {}).get("available")]
    result: dict[str, Any] = {"available": len(available) >= 3, "per_fidelity": per_fidelity}

    if len(available) < 3:
        result["verdict"] = "insufficient-data"
        result["converged"] = False
        result["note"] = (
            f"need at least three fidelities with data; found {len(available)}"
        )
        return result

    # Use the coarsest three available, in fidelity order.
    f_fast, f_std, f_fine = available[-3], available[-2], available[-1]
    cd_fast, cd_std, cd_fine = (per_fidelity[f]["cd"] for f in (f_fast, f_std, f_fine))
    cl_fast, cl_std, cl_fine = (per_fidelity[f]["cl"] for f in (f_fast, f_std, f_fine))

    if None in (cd_fast, cd_std, cd_fine, cl_std, cl_fine):
        result["verdict"] = "insufficient-data"
        result["converged"] = False
        result["note"] = "coefficients unavailable for one or more fidelities"
        return result

    cd_sf = abs(cd_fine - cd_std) / abs(cd_std) if cd_std else None
    cl_sf = abs(cl_fine - cl_std) / abs(cl_std) if cl_std else None
    result["deltas"] = {
        "cd_std_fine_pct": round((cd_sf or 0.0) * 100, 3),
        "cl_std_fine_pct": round((cl_sf or 0.0) * 100, 3),
    }

    # Refinement ratio (coarse->fine) from cell counts if available, else 1.5 default.
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

    converged = (cd_sf is not None and cd_sf < cd_thresh and cl_sf is not None and cl_sf < cl_thresh)
    result["converged"] = converged
    if converged:
        result["verdict"] = "grid-independent"
        result["note"] = (
            f"std->fine: Cd {cd_sf * 100:.2f}% (< {cd_thresh * 100:.0f}%), "
            f"Cl {cl_sf * 100:.2f}% (< {cl_thresh * 100:.0f}%)"
        )
    elif (cd_sf is not None and cd_sf < 2 * cd_thresh and cl_sf is not None and cl_sf < 2 * cl_thresh):
        result["verdict"] = "marginal"
        result["note"] = (
            f"near convergent: Cd {cd_sf * 100:.2f}%, Cl {cl_sf * 100:.2f}%"
        )
    else:
        result["verdict"] = "not-converged"
        result["note"] = (
            f"forces still move with refinement: Cd {cd_sf * 100:.2f}%, Cl {cl_sf * 100:.2f}%"
        )
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
        cells = info.get("cells")
        cells_txt = f"  cells {cells:,}" if cells else ""
        print(f"    {fidelity:<9} Cd {cd}  Cl {cl}  ({info.get('source')}){cells_txt}")
    deltas = report.get("deltas")
    if deltas:
        print(f"    delta std->fine:  Cd {deltas['cd_std_fine_pct']:.2f}%  Cl {deltas['cl_std_fine_pct']:.2f}%")
    rich = report.get("richardson")
    if rich:
        p = rich.get("p")
        print(f"    Richardson order p = {p}" + (
            f", Cd extrapolated {rich['cd_extrapolated']}" if rich.get("cd_extrapolated") is not None else ""
        ))
    print(f"    verdict: {report.get('verdict')} — {report.get('note')}")


__all__ = ["grid_study", "print_grid_study", "CD_THRESHOLD", "CL_THRESHOLD"]
