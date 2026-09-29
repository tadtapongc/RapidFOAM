"""The mesh derivation seam: ``MeshPlan``.

Today the interface between derivation and emission is a shared mutable ``cfg``
dict. This module produces an immutable plan once and lets callers apply it back
to ``cfg`` for compatibility (``apply_plan_to_cfg``). It currently *wraps*
``compute_mesh_params`` + ``resolve_layers`` (Phase 2 step 1) so behaviour is
byte-identical; later steps move the derivation internals here and make the mesh
writers consume the plan directly.

The plan is built from a private deep copy of ``cfg``, so normalising derivation
no longer mutates the caller's config as a side effect.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from rapidfoam.geometry import compute_mesh_params, resolve_layers


@dataclass(frozen=True)
class LayerSpec:
    """Resolved boundary-layer stack (see geometry.resolve_layers)."""

    n_layers: int
    expansion_ratio: float
    relative_sizes: bool
    first_layer_thickness: float | None
    min_thickness: float | None
    ground_n_layers: int
    two_pass: bool
    resolved: Mapping[str, Any]

    @classmethod
    def from_config(
        cls, layers: Mapping[str, Any], resolved: Mapping[str, Any]
    ) -> "LayerSpec":
        return cls(
            n_layers=int(layers.get("n_layers", 0) or 0),
            expansion_ratio=float(layers.get("expansion_ratio", 1.0) or 1.0),
            relative_sizes=bool(layers.get("relativeSizes", True)),
            first_layer_thickness=layers.get("first_layer_thickness"),
            min_thickness=layers.get("min_thickness"),
            ground_n_layers=int(layers.get("ground_n_layers", 0) or 0),
            two_pass=bool(layers.get("two_pass", False)),
            resolved=resolved,
        )


@dataclass(frozen=True)
class MeshPlan:
    """Immutable result of mesh-parameter derivation."""

    domain_box: Mapping[str, Any]
    mesh_params: Mapping[str, Any]
    layers: Mapping[str, Any]
    feature_extract: Mapping[str, Any]
    layer_spec: LayerSpec

    @property
    def base_cell_size(self) -> float:
        return float(self.mesh_params["base_cell_size"])

    @property
    def surface_level(self) -> tuple[int, int]:
        level = self.mesh_params["surface_level"]
        return (int(level[0]), int(level[1]))

    @property
    def edge_level(self) -> int:
        return int(self.mesh_params["edge_level"])

    def as_config(self) -> dict[str, Any]:
        """Back-compat mapping of the ``mesh_params``/``layers`` config views."""
        return {
            "domain_box": copy.deepcopy(dict(self.domain_box)),
            "mesh_params": copy.deepcopy(dict(self.mesh_params)),
            "layers": copy.deepcopy(dict(self.layers)),
            "feature_extract": copy.deepcopy(dict(self.feature_extract)),
        }


def build_mesh_plan(
    cfg: dict[str, Any],
    bounds: Any,
    *,
    feature_stats: Any = None,
    angle_stats: Any = None,
    explicit_feature_angle: bool = False,
    explicit_first_layer: bool = False,
    explicit_min_thickness: bool = False,
) -> MeshPlan:
    """Derive a :class:`MeshPlan` without mutating ``cfg``.

    ``cfg`` must already have the fidelity preset applied (call
    ``meshing.presets.apply_fidelity_preset`` first), matching the CLI/Studio
    order. Derivation runs on a private copy.
    """
    work = copy.deepcopy(cfg)
    mesh_params = compute_mesh_params(
        work,
        bounds,
        feature_stats=feature_stats,
        angle_stats=angle_stats,
        explicit_feature_angle=explicit_feature_angle,
    )
    work["mesh_params"] = mesh_params
    resolved = resolve_layers(
        work,
        bounds,
        explicit_first_layer=explicit_first_layer,
        explicit_min_thickness=explicit_min_thickness,
    )
    return MeshPlan(
        domain_box=work.get("domain_box") if isinstance(work.get("domain_box"), dict) else {},
        mesh_params=mesh_params,
        layers=work.get("layers", {}),
        feature_extract=work.get("feature_extract", {}),
        layer_spec=LayerSpec.from_config(work.get("layers", {}), resolved),
    )


def plan_from_config(cfg: dict[str, Any]) -> MeshPlan:
    """Build a plan from an already-derived ``cfg`` (no re-derivation).

    Used by the ``writers.mesh`` compatibility shim so callers that hand-build a
    config keep working while the writers consume a plan.
    """
    layers = cfg.get("layers", {})
    if not isinstance(layers, dict):
        layers = {}
    resolved = layers.get("_resolved", {})
    return MeshPlan(
        domain_box=cfg.get("domain_box") if isinstance(cfg.get("domain_box"), dict) else {},
        mesh_params=cfg.get("mesh_params", {}),
        layers=layers,
        feature_extract=cfg.get("feature_extract", {}),
        layer_spec=LayerSpec.from_config(layers, resolved if isinstance(resolved, dict) else {}),
    )


def apply_plan_to_cfg(cfg: dict[str, Any], plan: MeshPlan) -> None:
    """Write a plan's derived values back into ``cfg`` (compatibility shim).

    Keeps the generated ``case_config.json`` and the pre-Phase-2 tests working
    until writers consume the plan directly (Phase 3).
    """
    cfg["domain_box"] = copy.deepcopy(dict(plan.domain_box))
    cfg["mesh_params"] = copy.deepcopy(dict(plan.mesh_params))
    cfg["layers"] = copy.deepcopy(dict(plan.layers))
    cfg["feature_extract"] = copy.deepcopy(dict(plan.feature_extract))


__all__ = ["LayerSpec", "MeshPlan", "build_mesh_plan", "plan_from_config", "apply_plan_to_cfg"]
