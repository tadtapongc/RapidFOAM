"""Feature-based sizing and feature-angle derivation (moved from geometry.py)."""

from __future__ import annotations

import math
from typing import Any

from rapidfoam.geometry.stl import EdgeStats, FeatureAngleStats


def _resolve_feature_sizing(
    user_mesh: dict[str, Any],
    preset: dict[str, Any],
    base_cell: float,
    surface_level: list[int],
    edge_level: int,
    feature_stats: EdgeStats,
    extents: list[float],
) -> dict[str, Any]:
    """Widen surface/edge refinement so the smallest feature is resolved.

    The feature length scale is the smaller of a robust low-percentile triangle
    edge (rejects CAD slivers) and the thinnest geometric extent; the required
    refinement level is the one whose cell (``base_cell / 2**level``) is at most
    ``feature / feature_cells``. Levels are only ever raised above the preset and
    are capped by ``max_surface_level`` to bound the cell budget.
    """
    pct = float(user_mesh.get("feature_percentile", preset.get("feature_percentile", 10.0)))
    cells_per_feature = max(float(user_mesh.get("feature_cells", preset.get("feature_cells", 3.0))), 1.0)
    max_level = int(user_mesh.get("max_surface_level", preset.get("max_surface_level", 7)))
    max_level = max(1, min(max_level, 14))

    model_length = max(extents) if extents else base_cell
    robust_edge = feature_stats.percentile(pct) if feature_stats.n_edges else 0.0
    if robust_edge <= 0.0 and math.isfinite(feature_stats.min_edge):
        robust_edge = feature_stats.min_edge
    thin_extent = min((e for e in extents if e > 0), default=0.0)

    candidates = [v for v in (robust_edge, thin_extent) if v > 0]
    raw_small = min(candidates) if candidates else 0.0

    # Sliver floor: ignore features below 0.01% of the model length so a single
    # degenerate tessellation edge cannot explode the mesh.
    floor = max(model_length * 1e-4, 1e-9)
    small = max(raw_small, floor) if raw_small > 0 else floor

    target_cell = small / cells_per_feature
    if base_cell > 0 and 0 < target_cell < base_cell:
        required = math.ceil(math.log2(base_cell / target_cell))
    else:
        required = 0
    required = max(required, 0)

    level1 = max(int(surface_level[1]), required)
    capped = level1 > max_level
    level1 = min(level1, max_level)
    level0 = min(int(surface_level[0]), level1)
    new_edge = min(max(int(edge_level), required), max_level)

    return {
        "small_feature_m": round(raw_small, 8) if raw_small > 0 else None,
        "feature_floor_m": floor,
        "robust_edge_m": robust_edge or None,
        "thin_extent_m": thin_extent or None,
        "min_edge_m": feature_stats.min_edge if math.isfinite(feature_stats.min_edge) else None,
        "feature_percentile": pct,
        "feature_cells": cells_per_feature,
        "required_level": required,
        "max_surface_level": max_level,
        "capped": capped,
        "base_cell_size": round(base_cell, 4),
        "finest_surface_cell_m": base_cell / (2 ** level1),
        "surface_level": [level0, level1],
        "edge_level": new_edge,
    }


def _resolve_feature_angle(
    user_mesh: dict[str, Any],
    preset: dict[str, Any],
    angle_stats: FeatureAngleStats,
) -> dict[str, Any]:
    """Derive ``resolveFeatureAngle`` from the crease (normal-angle) distribution.

    snappyHexMesh treats intersections whose angle **exceeds**
    ``resolveFeatureAngle`` as features (maximum refinement + snapping). The
    statistic used here is the angle between adjacent face normals: 0 deg on a
    seamless flat join, 90 deg at a box corner, larger on a sharp fold. Real
    aero creases therefore sit in the high tail, while smooth tessellation sits
    near 0.

    Strategy: take a high percentile as the sharpest meaningful crease and set
    the threshold a safety fraction *below* it, so that crease and everything
    sharper is resolved while gentler tessellation is not. The threshold is
    capped at the preset so this can only ever *add* feature detection (sharper
    threshold), never drop features the preset already captured.
    """
    preset_angle = float(preset.get("resolveFeatureAngle", 35))
    # Normal-angle percentile marking the sharpest meaningful crease.
    crease_pct = float(user_mesh.get("crease_percentile", preset.get("crease_percentile", 99.0)))
    # Fraction of the sharpest crease kept as the threshold (<1 = sharper).
    ratio = float(user_mesh.get("feature_angle_ratio", preset.get("feature_angle_ratio", 0.75)))
    # Normal angles below this are treated as smooth tessellation, not creases.
    floor = float(user_mesh.get("crease_angle_floor", preset.get("crease_angle_floor", 15.0)))
    band = (5.0, 80.0)

    crease_normal = angle_stats.percentile(max(0.0, min(crease_pct, 100.0)))
    if crease_normal <= floor:
        # No creases sharper than the floor: keep the preset threshold.
        derived = preset_angle
    else:
        derived = crease_normal * ratio

    derived = min(max(derived, band[0]), band[1])
    resolve = min(derived, preset_angle)
    resolve = round(resolve, 1)

    # Keep feature_extract consistent: included angle just above the crease.
    included_recommended = round(min(max(180.0 - resolve, band[0]), band[1] + 100.0), 1)

    return {
        "resolveFeatureAngle": resolve,
        "preset_resolveFeatureAngle": preset_angle,
        "crease_percentile": crease_pct,
        "sharpest_crease_normal_deg": round(crease_normal, 1),
        "sharpest_crease_included_deg": round(180.0 - crease_normal, 1),
        "crease_angle_floor": floor,
        "feature_angle_ratio": ratio,
        "min_angle_deg": round(angle_stats.min_angle, 1) if angle_stats.n_angles else None,
        "n_angles": angle_stats.n_angles,
        "included_angle_recommended": included_recommended,
        "changed": resolve != preset_angle,
    }


def resolve_per_surface_levels(
    user_mesh: dict[str, Any],
    preset: dict[str, Any],
    base_cell: float,
    global_surface_level: list[int],
    global_edge_level: int,
    stats_by_stem: dict[str, EdgeStats],
    extents_by_stem: dict[str, list[float]],
) -> dict[str, dict[str, Any]]:
    """Derive a surface/edge refinement level per STL from its own feature size.

    Geometry-derived (like a size function): a part with small features gets a
    finer level, a large smooth part a coarser one — no per-part naming. Each
    part's level is computed the same way as the global auto-size, only ever
    raising above the preset and capped by ``max_surface_level``.

    Returns ``{stem: {"surface_level": [l0, l1], "edge_level": int,
    "required_level": int, "capped": bool}}``; empty when there are no per-part
    stats.
    """
    if not stats_by_stem:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for stem, stats in stats_by_stem.items():
        info = _resolve_feature_sizing(
            user_mesh,
            preset,
            base_cell,
            list(global_surface_level),
            int(global_edge_level),
            stats,
            extents_by_stem.get(stem, []),
        )
        result[stem] = {
            "surface_level": list(info["surface_level"]),
            "edge_level": int(info["edge_level"]),
            "required_level": info["required_level"],
            "capped": bool(info.get("capped")),
        }
    return result


__all__ = ["_resolve_feature_sizing", "_resolve_feature_angle", "resolve_per_surface_levels"]
