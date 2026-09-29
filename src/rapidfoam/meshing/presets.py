"""Fidelity-preset resolution — the one place preset fields are applied.

Previously the CLI applied ten preset fields by hand (``cli._do_generate``)
while the Studio preview applied a three-field subset, so the preview could
disagree with the generated case. Both now call :func:`apply_fidelity_preset`.
"""

from __future__ import annotations

from typing import Any, Callable

from rapidfoam.geometry import FIDELITY_PRESETS

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
        layers["nLayerIter"] = preset.get("nLayerIter", 50)
    if not is_set("layers", "nRelaxIter"):
        layers["nRelaxIter"] = preset.get("nRelaxIter_layers", 10)
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


__all__ = ["apply_fidelity_preset", "FIDELITY_PRESETS"]