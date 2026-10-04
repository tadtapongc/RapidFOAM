"""Fidelity presets and their application.

The preset table and the (single) application logic live here. ``geometry.py``
re-exports ``FIDELITY_PRESETS`` for existing callers.
"""

from __future__ import annotations

from typing import Any, Callable

FIDELITY_PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        # Quick turnaround for iterative design (~10-20 min on 32 cores, ~3-5M cells)
        "desc": "Quick iterative design turnaround",
        "cell_estimate": "~3-5M cells",
        "n_cells_target": 4000000,
        "runtime_estimate": "~10-20 min",
        "cells_per_length": 20,        # base cell = longest STL extent / 20
        "surface_level": [3, 4],       # 18.75mm - 9.38mm surface cells at ~3m model
        "edge_level": 5,               # 4.69mm at edges
        "n_layers": 5,
        "expansion_ratio": 1.15,
        "y_plus_target": 50,
        "ground_layers": False,
        "end_time": 800,
        "write_interval": 400,
        "maxGlobalCells": 10_000_000,
        "nCellsBetweenLevels": 2,
        "resolveFeatureAngle": 35,
        "nSolveIter": 100,             # snap iterations
        "nFeatureSnapIter": 10,
        "nLayerIter": 75,
        "nRelaxIter_layers": 15,
        "slurm_time": "04:00:00",
        "slurm_mem_per_cpu": "2G",
        # Feature-based auto-sizing: refine until the smallest feature spans
        # feature_cells, capped at max_surface_level.
        "feature_percentile": 25.0,
        "feature_cells": 3.0,
        "max_surface_level": 6,
        # Feature-angle derivation: resolve creases in the high tail of the
        # normal-angle histogram, keeping the threshold a ratio below them.
        "crease_percentile": 99.0,
        "feature_angle_ratio": 0.75,
        "crease_angle_floor": 15.0,
        # Distance-based refinement shells, as multiples of the base cell
        "distance_shells": [
            (0.25, 3),    # quarter cell -> level 3
            (0.80, 2),    # ~cell -> level 2
        ],
        "near_wake_level": 2,
        "far_wake_level": 1,
    },
    "standard": {
        # Balanced — optimal for FSAE aero (~1-2 hrs on 32 cores, sweet spot: ~9-13M cells)
        "desc": "Balanced accuracy and speed for FSAE aero",
        "cell_estimate": "~9-13M cells",
        "n_cells_target": 11000000,
        "runtime_estimate": "~1-2 hrs",
        "cells_per_length": 30,        # base cell = longest STL extent / 30
        "surface_level": [4, 5],       # 6.25mm bodywork, 3.125mm fine features
        "edge_level": 6,               # 1.56mm at sharp aero edges (wings/gurneys)
        "n_layers": 8,
        "expansion_ratio": 1.15,
        "y_plus_target": 30,
        "ground_layers": False,
        "end_time": 1500,
        "write_interval": 500,
        "maxGlobalCells": 20_000_000,
        "nCellsBetweenLevels": 2,      # 2 buffer cells (avoids massive 3D transition bloat)
        "resolveFeatureAngle": 35,     # Prevents general body curvature from ballooning to max level
        "nSolveIter": 200,
        "nFeatureSnapIter": 15,
        "nLayerIter": 75,
        "nRelaxIter_layers": 15,
        "slurm_time": "08:00:00",
        "slurm_mem_per_cpu": "2G",
        # Feature-based auto-sizing: refine until the smallest feature spans
        # feature_cells, capped at max_surface_level.
        "feature_percentile": 25.0,
        "feature_cells": 4.0,
        "max_surface_level": 7,
        # Feature-angle derivation: resolve creases in the high tail of the
        # normal-angle histogram, keeping the threshold a ratio below them.
        "crease_percentile": 99.0,
        "feature_angle_ratio": 0.75,
        "crease_angle_floor": 15.0,
        # Conforming distance shells, as multiples of the base cell
        "distance_shells": [
            (0.25, 4),    # quarter cell -> level 4 (6.25mm at ~3m model)
            (0.80, 3),    # ~cell -> level 3 (12.5mm)
        ],
        "near_wake_level": 3,          # rear wing vortex / diffuser
        "far_wake_level": 1,           # downstream transport (saves cells)
    },
    "fine": {
        # Wall-resolved low-Re tier (~4-6 hours, ~20-28M cells on 32 cores)
        "desc": "Wall-resolved tier (y+ ~ 1 first cell, fine near-wall stack); pair with a low-Re-consistent wall treatment and validate",
        "cell_estimate": "~20-28M cells",
        "n_cells_target": 24000000,
        "runtime_estimate": "~4-6 hrs",
        "cells_per_length": 37.5,      # base cell = longest STL extent / 37.5
        "surface_level": [4, 5],       # keep tangential cells, layers carry the near-wall work
        "edge_level": 7,               # 0.68mm at sharp aero edges (trailing edges, gurneys)
        "n_layers": 20,
        "expansion_ratio": 1.1,
        "y_plus_target": 1,
        "ground_layers": False,
        "end_time": 2500,
        "write_interval": 500,
        "maxGlobalCells": 32_000_000,
        "nCellsBetweenLevels": 2,
        "resolveFeatureAngle": 30,
        "nSolveIter": 300,
        "nFeatureSnapIter": 20,
        "nLayerIter": 75,
        "nRelaxIter_layers": 15,
        "slurm_time": "14:00:00",
        "slurm_mem_per_cpu": "4G",
        # Feature-based auto-sizing: refine until the smallest feature spans
        # feature_cells, capped at max_surface_level.
        "feature_percentile": 25.0,
        "feature_cells": 5.0,
        "max_surface_level": 8,
        # Feature-angle derivation: resolve creases in the high tail of the
        # normal-angle histogram, keeping the threshold a ratio below them.
        "crease_percentile": 99.0,
        "feature_angle_ratio": 0.75,
        "crease_angle_floor": 15.0,
        # Conforming distance shells, as multiples of the base cell
        "distance_shells": [
            (0.25, 5),    # 2.5mm at ~3m model
            (0.75, 4),    # 6.25mm
            (1.90, 3),    # 12.5mm
        ],
        "near_wake_level": 4,
        "far_wake_level": 2,
    },
}

IsSetFn = Callable[[str, str], bool]


def _never_set(section: str, key: str) -> bool:
    return False


def apply_fidelity_preset(
    cfg: dict[str, Any],
    is_set: IsSetFn | None = None,
) -> dict[str, Any]:
    """Apply the active fidelity preset to fields the user did not set.

    Mutates ``cfg`` in place (the pre-Phase-2 contract) and returns it. ``is_set``
    is a ``(section, key) -> bool`` predicate used to tell a user-provided value
    from a default; callers pass a partial of :func:`rapidfoam.config.user_set`.
    """
    is_set = is_set or _never_set
    fidelity = cfg.get("fidelity", "standard")
    preset = FIDELITY_PRESETS.get(fidelity, FIDELITY_PRESETS["standard"])

    solver = cfg.setdefault("solver", {})
    layers = cfg.setdefault("layers", {})
    snap = cfg.setdefault("snap", {})
    slurm = cfg.setdefault("slurm", {})

    if not is_set("solver", "end_time"):
        solver["end_time"] = preset.get("end_time", solver.get("end_time"))
    if not is_set("layers", "n_layers"):
        layers["n_layers"] = preset.get("n_layers", layers.get("n_layers"))
    if not is_set("layers", "expansion_ratio"):
        layers["expansion_ratio"] = preset.get("expansion_ratio", layers.get("expansion_ratio"))

    if "y_plus_target" in preset:
        if not is_set("layers", "y_plus_target") and not is_set("layers", "first_layer_thickness"):
            layers["y_plus_target"] = preset["y_plus_target"]
    elif not is_set("layers", "first_layer_thickness"):
        layers["first_layer_thickness"] = preset.get(
            "first_layer_thickness", layers.get("first_layer_thickness")
        )

    if not is_set("layers", "nLayerIter"):
        layers["nLayerIter"] = preset.get("nLayerIter", 75)
    if not is_set("layers", "nRelaxIter"):
        layers["nRelaxIter"] = preset.get("nRelaxIter_layers", 15)
    if not is_set("layers", "ground_layers"):
        layers["ground_layers"] = preset.get("ground_layers", False)
    if not is_set("solver", "write_interval"):
        solver["write_interval"] = preset.get("write_interval", solver.get("write_interval"))
    if not is_set("snap", "nSolveIter"):
        snap["nSolveIter"] = preset.get("nSolveIter", 200)
    if not is_set("snap", "nFeatureSnapIter"):
        snap["nFeatureSnapIter"] = preset.get("nFeatureSnapIter", 15)
    if not is_set("slurm", "time"):
        slurm["time"] = preset.get("slurm_time", "04:00:00")
    if not is_set("slurm", "mem_per_cpu"):
        slurm["mem_per_cpu"] = preset.get("slurm_mem_per_cpu", slurm.get("mem_per_cpu"))
    return cfg


__all__ = ["FIDELITY_PRESETS", "apply_fidelity_preset"]
