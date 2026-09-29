"""Mesh-parameter derivation funnel (moved from geometry.py, Phase 2).

``compute_mesh_params`` still mutates ``cfg["feature_extract"]`` when the
derived feature angle sharpens detection; callers that need purity go through
``meshing.plan.build_mesh_plan`` (which runs it on a private copy).
"""

from __future__ import annotations

from typing import Any

from rapidfoam.core.axes import flow_axis_index_sign, up_axis_index
from rapidfoam.core.faces import face_assignments, face_role
from rapidfoam.meshing.domain import GROUND_EMBED, compute_domain_box
from rapidfoam.meshing.grading import compute_block_grading
from rapidfoam.meshing.presets import FIDELITY_PRESETS
from rapidfoam.meshing.sizing import _resolve_feature_angle, _resolve_feature_sizing
from rapidfoam.stl_utils import BBox, EdgeStats, FeatureAngleStats


def compute_mesh_params(
    cfg: dict[str, Any],
    combined_bounds: BBox,
    feature_stats: EdgeStats | None = None,
    angle_stats: FeatureAngleStats | None = None,
    *,
    explicit_feature_angle: bool = False,
) -> dict[str, Any]:
    """Derive all mesh parameters from geometry bounds.

    Domain and wake dimensions follow geometry bounds. Cell sizes and distance
    shells use metre-valued fidelity presets unless explicitly overridden.

    When ``feature_stats`` (edge-length statistics from the STLs) is supplied,
    surface and edge refinement are widened so the smallest geometry feature is
    resolved with a target number of cells across it (see
    :func:`_resolve_feature_sizing`). This only ever adds local refinement on
    top of the fidelity preset and never coarsens it.

    When ``angle_stats`` (dihedral-angle statistics) is supplied,
    ``resolveFeatureAngle`` is derived so real creases are snapped while smooth
    tessellation is not (see :func:`_resolve_feature_angle`).

    Refinement strategy:
        - Distance-based shells around the STL surface.
          Cells are refined based on proximity to the geometry, giving smooth
          transitions that conform to the actual shape.
        - Two-stage wake region:
          1. nearWakeBox: High resolution immediately behind vehicle (diffuser,
             rear wing vortices, tire separation). Length ~ 1.2x vehicle length.
          2. farWakeBox: Low-cost downstream transport to outlet without numerical
             diffusion, avoiding millions of wasted cells far downstream.

    Fidelity levels:
        "fast"     — iterative design, quick turnaround (~2-4M cells)
        "standard" — balanced accuracy/speed for FSAE (~6-9M cells)
        "fine"     — final report quality (~12-16M cells)

    Returns dict with:
        base_cell_size, surface_level, edge_level, distance_levels,
        refinement_regions (nearWakeBox + farWakeBox), n_layers, etc.
    """
    smin, smax = combined_bounds
    extents = [smax[i] - smin[i] for i in range(3)]
    max_extent = max(extents)

    if max_extent <= 0:
        raise ValueError("STL has zero extent — check your geometry")

    # Get fidelity preset
    fidelity = cfg.get("fidelity", "standard")
    preset = FIDELITY_PRESETS.get(fidelity, FIDELITY_PRESETS["standard"])

    user_mesh = cfg.get("mesh_params", {})

    # Base cell: explicit metres, or derived from the model length and
    # the preset's cells_per_length so resolution scales with the geometry.
    base_cell_override = user_mesh.get("base_cell_size")
    if base_cell_override in (None, "auto"):
        cells_per_length = float(user_mesh.get("cells_per_length", preset.get("cells_per_length", 30)))
        base_cell = max_extent / max(cells_per_length, 1.0)
    else:
        base_cell = float(base_cell_override)

    # Surface and edge levels: respect user override or use fidelity preset
    surface_level = list(user_mesh.get("surface_level", preset["surface_level"]))
    edge_level = int(user_mesh.get("edge_level", preset["edge_level"]))

    # Feature-based auto-sizing: widen local surface/edge refinement so small
    # geometry features (thin sections, tight radii, small triangles) are
    # resolved. Never coarsens the preset; capped by max_surface_level.
    auto_size_info: dict[str, Any] | None = None
    if feature_stats is not None and user_mesh.get("auto_size", False):
        auto_size_info = _resolve_feature_sizing(
            user_mesh, preset, base_cell, surface_level, edge_level,
            feature_stats, extents,
        )
        surface_level = auto_size_info["surface_level"]
        edge_level = auto_size_info["edge_level"]

    # Distance-based refinement shells. Users specify metres via
    # distance_levels; presets store base-cell multiples via distance_shells.
    if "distance_levels" in user_mesh:
        distance_levels = user_mesh["distance_levels"]
    else:
        shells = user_mesh.get("distance_shells", preset.get("distance_shells", []))
        distance_levels = [(round(float(d) * base_cell, 6), int(l)) for d, l in shells]
    if distance_levels and isinstance(distance_levels[0], list):
        distance_levels = [tuple(x) for x in distance_levels]

    resolve_feature_angle = user_mesh.get("resolveFeatureAngle", preset.get("resolveFeatureAngle", 35))

    # Geometry-derived feature angle: pick resolveFeatureAngle from the crease
    # distribution so sharp real edges are resolved but smooth tessellation is
    # not. Never loosens below the preset (that would lose real features).
    feature_angle_info: dict[str, Any] | None = None
    if angle_stats is not None and angle_stats.n_angles > 0 \
            and user_mesh.get("auto_feature_angle", False) \
            and "resolveFeatureAngle" not in user_mesh:
        feature_angle_info = _resolve_feature_angle(user_mesh, preset, angle_stats)
        resolve_feature_angle = feature_angle_info["resolveFeatureAngle"]

        # Keep surfaceFeatureExtract consistent with snappy: surfaceFeatureExtract
        # keeps an edge as an .eMesh feature when its *included* angle is below
        # ``includedAngle`` (included = 180 - normal). snappy snaps edges whose
        # normal angle exceeds resolveFeatureAngle, i.e. included angle below
        # 180 - resolveFeatureAngle. The derived resolve angle can be sharper
        # than the preset (more features), so raise the extraction angle to the
        # recommended value to catch those creases explicitly. A user-set
        # includedAngle always wins.
        # Only when the derived angle actually sharpened detection (otherwise
        # the extraction angle should stay as configured, so smooth geometry is
        # not silently loosened).
        if not explicit_feature_angle and feature_angle_info.get("changed"):
            recommended = feature_angle_info["included_angle_recommended"]
            feat = cfg.setdefault("feature_extract", {})
            configured = feat.get("includedAngle")
            if configured is None or recommended > float(configured):
                feat["includedAngle"] = recommended

    # Wake levels (uncoupled from surface level to prevent wake bloat)
    near_wake_level = user_mesh.get("near_wake_level", preset.get("near_wake_level", 3))
    far_wake_level = user_mesh.get("far_wake_level", preset.get("far_wake_level", 1))

    # Support legacy single wake_level override if user explicitly set it
    if "wake_level" in user_mesh:
        near_wake_level = user_mesh["wake_level"]

    flow_idx, flow_sign = flow_axis_index_sign(cfg)
    up_idx = up_axis_index(cfg)
    lateral_idx = next(i for i in range(3) if i != flow_idx and i != up_idx)

    # Vertical alignment (ground for vehicles, or symmetric padding for airplanes)
    domain_faces = {d: face_role(cfg, name) for d, name in face_assignments(cfg).items()}
    up_min_key = f"-{'xyz'[up_idx]}"
    is_ground = "ground" in domain_faces.get(up_min_key, "").lower()

    # Centerline / symmetry coordinate
    lateral_min_key = f"-{'xyz'[lateral_idx]}"
    is_symmetry = "symmetry" in domain_faces.get(lateral_min_key, "").lower()

    sym_coord = cfg.get("symmetry_plane")
    if sym_coord is None:
        sym_coord = cfg.get("centerline")
    if sym_coord is not None:
        sym_x = float(sym_coord)
    elif "domain_box" in cfg and isinstance(cfg["domain_box"], dict) and "min" in cfg["domain_box"]:
        sym_x = cfg["domain_box"]["min"][lateral_idx]
    elif abs(smin[lateral_idx]) < 0.05:
        sym_x = 0.0
    else:
        sym_x = smin[lateral_idx]

    # Precompute ground coordinate once if ground plane is present
    ground_z: float | None = None
    if is_ground:
        if "ground_plane" in cfg and cfg["ground_plane"] is not None:
            ground_z = float(cfg["ground_plane"]) - GROUND_EMBED
        elif "ground_clearance" in cfg and cfg["ground_clearance"] is not None:
            ground_z = smin[up_idx] - float(cfg["ground_clearance"]) - GROUND_EMBED
        elif "domain_box" in cfg and isinstance(cfg["domain_box"], dict) and "min" in cfg["domain_box"]:
            ground_z = cfg["domain_box"]["min"][up_idx] - GROUND_EMBED
        else:
            ground_z = smin[up_idx] - GROUND_EMBED

    # --- 1. Near Wake Box (High-resolution: rear wing, diffuser, tire separation) ---
    near_pad_lat = max(0.10, extents[lateral_idx] * 0.15)
    near_pad_top = max(0.15, extents[up_idx] * 0.25)
    near_length = max(2.0, extents[flow_idx] * 1.2)

    near_min = list(smin)
    near_max = list(smax)
    if is_ground:
        assert ground_z is not None
        near_min[up_idx] = ground_z
    else:
        near_min[up_idx] = smin[up_idx] - near_pad_top

    near_max[up_idx] = smax[up_idx] + near_pad_top
    near_min[lateral_idx] = smin[lateral_idx] - near_pad_lat
    near_max[lateral_idx] = smax[lateral_idx] + near_pad_lat

    # If lateral min is symmetry plane, clip cleanly to symmetry plane
    if is_symmetry:
        near_min[lateral_idx] = sym_x

    if flow_sign > 0:
        near_min[flow_idx] = smin[flow_idx] + extents[flow_idx] * 0.6
        near_max[flow_idx] = smax[flow_idx] + near_length
    else:
        near_min[flow_idx] = smin[flow_idx] - near_length
        near_max[flow_idx] = smin[flow_idx] + extents[flow_idx] * 0.4

    # --- 2. Far Wake Box (Lower resolution: downstream transport to outlet) ---
    far_pad_lat = max(0.20, extents[lateral_idx] * 0.30)
    far_pad_top = max(0.25, extents[up_idx] * 0.40)
    far_length = max(4.0, extents[flow_idx] * 3.5)

    far_min = list(smin)
    far_max = list(smax)
    if is_ground:
        assert ground_z is not None
        far_min[up_idx] = ground_z
    else:
        far_min[up_idx] = smin[up_idx] - far_pad_top

    far_max[up_idx] = smax[up_idx] + far_pad_top
    far_min[lateral_idx] = smin[lateral_idx] - far_pad_lat
    far_max[lateral_idx] = smax[lateral_idx] + far_pad_lat

    if is_symmetry:
        far_min[lateral_idx] = sym_x

    if flow_sign > 0:
        far_min[flow_idx] = smax[flow_idx]
        far_max[flow_idx] = smax[flow_idx] + far_length
    else:
        far_min[flow_idx] = smin[flow_idx] - far_length
        far_max[flow_idx] = smin[flow_idx]

    domain_box = cfg.get("domain_box")
    if not isinstance(domain_box, dict):
        domain_box = compute_domain_box(cfg, combined_bounds)
    if domain_faces.get(f"+{'xyz'[up_idx]}") == "ground":
        for upper in (near_max, far_max):
            upper[up_idx] = domain_box["max"][up_idx] + 0.01
    if domain_faces.get(f"+{'xyz'[lateral_idx]}") == "symmetry":
        for upper in (near_max, far_max):
            upper[lateral_idx] = domain_box["max"][lateral_idx]

    default_regions = [
        {"name": "nearWakeBox", "min": [round(v, 4) for v in near_min],
         "max": [round(v, 4) for v in near_max], "level": near_wake_level},
        {"name": "farWakeBox", "min": [round(v, 4) for v in far_min],
         "max": [round(v, 4) for v in far_max], "level": far_wake_level},
    ]

    refinement_regions = user_mesh.get("refinement_regions", default_regions)

    grading_info = compute_block_grading(cfg, domain_box, combined_bounds, base_cell)

    result = {
        "base_cell_size": round(base_cell, 4),
        "surface_level": surface_level,
        "edge_level": edge_level,
        "distance_levels": distance_levels,
        "refinement_regions": refinement_regions,
        "grading": grading_info["grading"],
        "block_cells": grading_info["block_cells"],
        "nCellsBetweenLevels": user_mesh.get("nCellsBetweenLevels", preset.get("nCellsBetweenLevels", 2)),
        "maxGlobalCells": user_mesh.get("maxGlobalCells", preset.get("maxGlobalCells", 18_000_000)),
        "maxLocalCells": user_mesh.get("maxLocalCells", 2_000_000),
        "minRefinementCells": user_mesh.get("minRefinementCells", 10),
        "resolveFeatureAngle": resolve_feature_angle,
        "allowFreeStandingZoneFaces": user_mesh.get("allowFreeStandingZoneFaces", True),
    }
    result["grading_info"] = grading_info
    if auto_size_info is not None:
        result["auto_size"] = auto_size_info
    if feature_angle_info is not None:
        result["feature_angle"] = feature_angle_info
    for key in ("location_in_mesh", "locationInMesh", "maxLoadUnbalance"):
        if key in user_mesh:
            result[key] = user_mesh[key]
    return result


__all__ = ["compute_mesh_params"]
