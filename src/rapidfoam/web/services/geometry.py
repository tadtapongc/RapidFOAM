"""Geometry preview helpers for the Web Studio."""

from __future__ import annotations

import copy
from typing import Any

from rapidfoam.config import find_stl, user_set
from rapidfoam.meshing.plan import build_mesh_plan
from rapidfoam.meshing.presets import apply_fidelity_preset
from rapidfoam.web.state import PROJECT_ROOT


def _local_stl_exists(name: str) -> bool:
    """True when the named STL resolves inside the project's local stl/ folder."""
    return find_stl(PROJECT_ROOT / "stl", name) is not None


def layer_preview(
    merged: dict[str, Any],
    raw_cfg: dict[str, Any],
    bounds: tuple,
    feature_stats: Any = None,
    angle_stats: Any = None,
) -> dict[str, Any]:
    """Resolve the near-wall layer spec for the Studio preview without mutating it."""
    preview_cfg = copy.deepcopy(merged)
    apply_fidelity_preset(preview_cfg, lambda section, key: user_set(raw_cfg, section, key))
    plan = build_mesh_plan(
        preview_cfg,
        bounds,
        feature_stats=feature_stats,
        angle_stats=angle_stats,
        explicit_feature_angle=user_set(raw_cfg, "feature_extract", "includedAngle"),
        explicit_first_layer=user_set(raw_cfg, "layers", "first_layer_thickness"),
        explicit_min_thickness=user_set(raw_cfg, "layers", "min_thickness"),
    )
    resolved = dict(plan.layer_spec.resolved)
    resolved["auto_size"] = plan.mesh_params.get("auto_size")
    resolved["feature_angle"] = plan.mesh_params.get("feature_angle")
    resolved["surface_level"] = plan.mesh_params.get("surface_level")
    resolved["edge_level"] = plan.mesh_params.get("edge_level")
    # Effective layer controls the readout shows alongside the resolved spec.
    layers_cfg = preview_cfg.get("layers", {})
    resolved["n_layers"] = layers_cfg.get("n_layers")
    resolved["expansion_ratio"] = layers_cfg.get("expansion_ratio")
    resolved["two_pass"] = layers_cfg.get("two_pass")
    resolved["maxFaceThicknessRatio"] = layers_cfg.get("maxFaceThicknessRatio")
    resolved["min_thickness_ratio"] = layers_cfg.get("min_thickness_ratio")
    resolved["y_plus_fit"] = layers_cfg.get("y_plus_fit")
    return resolved


__all__ = ["layer_preview", "_local_stl_exists"]
