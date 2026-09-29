"""Background blockMesh grading (moved from geometry.py, Phase 2)."""

from __future__ import annotations

import math
from typing import Any

from rapidfoam.core.axes import flow_axis_index_sign, up_axis_index
from rapidfoam.core.faces import face_assignments, face_role
from rapidfoam.stl_utils import BBox

# Far/near cell-size ratio for the auto-graded background mesh. The near-body
# (fine) cell stays at the base cell size while cells grow toward the domain
# boundary by this factor, cutting the far-field cell count. Kept modest: the
# background is only the castellation starting point and snappy refines on top.
DEFAULT_GRADING_RATIO = 3.0


def _graded_axis(
    extent: float,
    base_cell: float,
    ratio: float,
    fine_at_start: bool = True,
) -> tuple[int, float]:
    """Cell count and ``simpleGrading`` scalar for one blockMesh axis.

    Keeps the cell at the fine end at ~``base_cell`` while cells grow toward the
    far end by ``ratio`` (end/start). Because the near-body cell is preserved,
    the graded axis needs fewer cells than a uniform axis of the same near-body
    resolution, so this cuts the far-field count rather than just redistributing
    it. Returns ``(cells, grading)``; ``grading`` is ``ratio`` when the fine end
    is the block start (+local) and its reciprocal when the fine end is the end.

    The number of cells ``n`` solves the geometric series for the fixed first
    cell; the count is small (tens), so a linear search is exact and cheap.
    """
    if base_cell <= 0:
        return 1, 1.0
    n_uniform = max(1, round(extent / base_cell))
    if ratio <= 1.0 or n_uniform <= 2:
        return n_uniform, 1.0

    best_n = n_uniform
    best_err = None
    for n in range(1, n_uniform + 1):
        if n == 1:
            first = extent
        else:
            beta = ratio ** (1.0 / (n - 1))
            first = extent * (beta - 1.0) / (beta ** n - 1.0)
        err = abs(first - base_cell)
        if best_err is None or err < best_err:
            best_err = err
            best_n = n
    return best_n, (ratio if fine_at_start else 1.0 / ratio)


def _prod(values: list[int]) -> int:
    result = 1
    for value in values:
        result *= int(value)
    return result


def compute_block_grading(
    cfg: dict[str, Any],
    domain_box: dict[str, list[float]],
    combined_bounds: BBox,
    base_cell: float,
) -> dict[str, Any]:
    """Derive the background ``simpleGrading`` and block cell counts.

    Grading is only applied on axes with an unambiguous fine side: the up axis
    toward the ground (or its positive-face variant when ground is ``+up``), and
    the lateral axis toward a symmetry plane. The flow axis is left uniform — the
    body sits mid-axis upstream/downstream, which a single block cannot
    centre-refine, and the wake boxes already resolve the downstream region.

    ``mesh_params.grading`` selects the mode:
        - ``"off"`` (default) / ``null`` / ``false``: uniform (1 1 1)
        - ``"auto"``: derive as above using ``grading_ratio``
        - ``[gx, gy, gz]``: explicit expansion ratios, uniform cell counts

    Grading is opt-in: measured on a real case it trims ~10–30% of the final
    cells but raises non-orthogonality/aspect ratio, so the quality-first default
    keeps the background uniform.
    """
    user_mesh = cfg.get("mesh_params", {})
    if not isinstance(user_mesh, dict):
        user_mesh = {}

    extents = [domain_box["max"][i] - domain_box["min"][i] for i in range(3)]
    uniform = [
        max(1, round(extents[i] / base_cell)) if base_cell and base_cell > 0 else 1
        for i in range(3)
    ]

    def _result(mode_name: str, grading: list[float], cells: list[int], ratio, axes):
        return {
            "mode": mode_name,
            "grading": [round(float(g), 6) for g in grading],
            "block_cells": [int(c) for c in cells],
            "uniform_cells": [int(c) for c in uniform],
            "ratio": ratio,
            "axes": axes,
            "cell_reduction": round(1.0 - _prod(cells) / max(_prod(uniform), 1), 4),
        }

    # Recomputing from an already-computed mesh_params dict (which carries a
    # `grading_info` provenance block) must honour the recorded mode rather than
    # mistaking the computed `grading` list for an explicit user override.
    prior = user_mesh.get("grading_info")
    if isinstance(prior, dict) and "mode" in prior:
        mode = prior.get("mode")
        if mode == "explicit" and isinstance(prior.get("grading"), (list, tuple)):
            return _result("explicit", [float(v) for v in prior["grading"]], uniform, None, {})
        mode = "off" if mode == "off" else "auto"
    else:
        mode = user_mesh.get("grading", "off")

    if isinstance(mode, (list, tuple)):
        return _result("explicit", [float(v) for v in mode], uniform, None, {})
    if mode is None or mode is False or (isinstance(mode, str)
                                         and mode.strip().lower() in ("off", "none", "uniform")):
        return _result("off", [1.0, 1.0, 1.0], uniform, None, {})

    # --- auto ---
    try:
        ratio = float(user_mesh.get("grading_ratio", DEFAULT_GRADING_RATIO))
    except (TypeError, ValueError):
        ratio = DEFAULT_GRADING_RATIO
    if not math.isfinite(ratio) or ratio <= 1.0:
        return _result("off", [1.0, 1.0, 1.0], uniform, None, {})

    flow_idx, _ = flow_axis_index_sign(cfg)
    up_idx = up_axis_index(cfg)
    lateral_idx = next(i for i in range(3) if i not in (flow_idx, up_idx))
    domain_faces = {d: face_role(cfg, name) for d, name in face_assignments(cfg).items()}

    def _role(idx: int, sign: str) -> str:
        return domain_faces.get(f"{sign}{'xyz'[idx]}", "")

    fine_side: dict[int, bool] = {}
    if _role(up_idx, "-") == "ground":
        fine_side[up_idx] = True
    elif _role(up_idx, "+") == "ground":
        fine_side[up_idx] = False
    if _role(lateral_idx, "-") == "symmetry":
        fine_side[lateral_idx] = True
    elif _role(lateral_idx, "+") == "symmetry":
        fine_side[lateral_idx] = False

    grading = [1.0, 1.0, 1.0]
    cells = list(uniform)
    axes: dict[str, Any] = {}
    for idx, fine_at_start in fine_side.items():
        n, g = _graded_axis(extents[idx], base_cell, ratio, fine_at_start)
        if n == cells[idx] and g == 1.0:
            continue
        cells[idx] = n
        grading[idx] = g
        axes["xyz"[idx]] = {
            "role": "ground" if idx == up_idx else "symmetry",
            "fine_side": "min" if fine_at_start else "max",
            "uniform_cells": uniform[idx],
            "cells": n,
            "grading": round(g, 6),
            "near_cell_m": round(base_cell, 6),
            "far_cell_m": round(base_cell * ratio, 6),
        }
    return _result("auto", grading, cells, round(ratio, 4), axes)


__all__ = ["DEFAULT_GRADING_RATIO", "_graded_axis", "_prod", "compute_block_grading"]
