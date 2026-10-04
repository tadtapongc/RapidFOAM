"""Geometry preview helpers for the Web Studio."""

from __future__ import annotations

import copy
from typing import Any

from rapidfoam.config import find_stl, user_set
from rapidfoam.meshing.plan import build_mesh_plan
from rapidfoam.meshing.presets import FIDELITY_PRESETS, apply_fidelity_preset
from rapidfoam.meshing.sizing import apply_mesh_regions, resolve_per_surface_levels
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


def per_surface_preview(
    merged: dict[str, Any],
    raw_cfg: dict[str, Any],
    stats_by_stem: dict[str, Any],
    extents_by_stem: dict[str, list[float]],
    stl_names: list[str],
) -> dict[str, Any]:
    """Effective per-surface refinement for each STL (for the Studio readout).

    Mirrors the builder: geometry-derived levels when ``auto_size`` is on, then
    manual ``mesh_regions`` overrides on top. Returns
    ``{stem: {surface_level, edge_level, n_layers, source}}``.
    """
    preview_cfg = copy.deepcopy(merged)
    apply_fidelity_preset(preview_cfg, lambda section, key: user_set(raw_cfg, section, key))
    mesh = preview_cfg.get("mesh_params", {})
    global_level = mesh.get("surface_level", FIDELITY_PRESETS["standard"]["surface_level"])
    global_edge = mesh.get("edge_level", FIDELITY_PRESETS["standard"]["edge_level"])
    n_layers = preview_cfg.get("layers", {}).get("n_layers")

    auto: dict[str, Any] = {}
    if mesh.get("auto_size"):
        base_cell = mesh.get("base_cell_size")
        if isinstance(base_cell, (int, float)) and base_cell > 0 and stats_by_stem:
            auto = resolve_per_surface_levels(
                mesh,
                FIDELITY_PRESETS.get(preview_cfg.get("fidelity", "standard"), FIDELITY_PRESETS["standard"]),
                float(base_cell),
                list(global_level),
                int(global_edge),
                stats_by_stem,
                extents_by_stem,
            )
    preview_cfg.setdefault("mesh_params", {})["surface_levels"] = auto
    apply_mesh_regions(preview_cfg, stl_names)
    resolved = preview_cfg["mesh_params"].get("surface_levels", {})
    layer_overrides = preview_cfg["mesh_params"].get("layer_overrides", {})
    # A manual override is anything present in the effective mesh_regions (top
    # level or under overrides); effective_config has already merged both.
    manual_regions = preview_cfg.get("mesh_regions") if isinstance(preview_cfg.get("mesh_regions"), dict) else {}

    out: dict[str, Any] = {}
    for stem in stl_names:
        info = resolved.get(stem, {})
        has_auto = bool(auto.get(stem))
        has_manual = stem in manual_regions
        out[stem] = {
            "surface_level": info.get("surface_level", global_level),
            "edge_level": info.get("edge_level", global_edge),
            "n_layers": layer_overrides.get(stem, n_layers),
            "source": "manual" if has_manual else ("auto" if has_auto else "preset"),
        }
    return out


__all__ = ["layer_preview", "per_surface_preview", "_local_stl_exists"]
