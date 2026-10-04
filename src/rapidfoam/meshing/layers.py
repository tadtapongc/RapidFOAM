"""Near-wall sizing: y+ target to absolute snappy layer thickness.

Moved from geometry.py (Phase 2). ``resolve_layers`` still writes the resolved
values back into ``cfg["layers"]``; callers that need purity use ``MeshPlan``
(which runs it on a private copy).
"""

from __future__ import annotations

import math
from typing import Any

from rapidfoam.core.axes import flow_axis_index_sign, up_axis_index
from rapidfoam.meshing.domain import GROUND_EMBED
from rapidfoam.geometry.stl import BBox

# Upper bound on the value ``layers.y_plus_fit`` will auto-set for
# ``maxFaceThicknessRatio``. snappyHexMesh will not extrude a layer thicker than
# this fraction of the local face, and pushing it much past ~0.8 produces badly
# skewed prisms, so the fit refuses to raise it beyond this.
Y_PLUS_FIT_RATIO_CAP = 0.8


def _fit_y_plus_clamp(
    cfg: dict[str, Any],
    layers: dict[str, Any],
    requested_thickness: float,
    base_cell: float,
    levels: list[int],
    ratio_limit: float,
) -> tuple[float, int] | None:
    """Make ``requested_thickness`` buildable by snappyHexMesh (opt-in y+ fit).

    Raises ``maxFaceThicknessRatio`` up to :data:`Y_PLUS_FIT_RATIO_CAP` so the
    requested layer fits on more faces; the surface resolution is left untouched.
    Mutates ``layers`` so the chosen ratio reaches the writers.

    Returns ``(ratio, level_fine)`` or ``None`` when the cap still cannot build
    the layer (then the caller keeps the plain clamp).
    """
    if base_cell <= 0 or requested_thickness <= 0:
        return None
    level_fine = int(levels[1])
    cap = max(ratio_limit, Y_PLUS_FIT_RATIO_CAP)
    new_cell = base_cell / (2 ** level_fine)
    if new_cell <= 0:
        return None
    new_ratio = min(cap, max(ratio_limit, requested_thickness / new_cell))
    if new_ratio * new_cell + 1e-12 < requested_thickness:
        return None
    if new_ratio > ratio_limit + 1e-9:
        layers["maxFaceThicknessRatio"] = round(new_ratio, 4)
    return new_ratio, level_fine


def estimate_friction_velocity(U: float, nu: float, length: float) -> float:
    """Flat-plate friction velocity estimate for external aero.

    Uses the turbulent Prandtl-Schlichting correlation above Re=5e5 and the
    Blasius laminar correlation below. Expect roughly ±30-40% versus the
    local u_tau on a real vehicle, so treat the result as a design target
    and verify against the yPlus function object after solving.
    """
    if U <= 0 or nu <= 0:
        return 0.0
    length = max(float(length), 1e-3)
    Re = U * length / nu
    if Re > 5.0e5:
        Cf = 0.026 * Re ** (-1.0 / 7.0)
    else:
        Cf = 1.328 / math.sqrt(Re)
    return U * math.sqrt(0.5 * Cf)


def first_layer_height(y_plus: float, u_tau: float, nu: float) -> float:
    """First cell thickness whose centre sits at the requested y+."""
    if u_tau <= 0 or nu <= 0:
        return 0.0
    return 2.0 * float(y_plus) * nu / u_tau


def _apply_ground_layer_policy(
    cfg: dict[str, Any],
    combined_bounds: BBox,
    layers: dict[str, Any],
    resolved: dict[str, Any],
) -> None:
    """Guard ground-patch layers: opt-in only, needs clearance, count capped.

    Ground layers act on the whole road patch, including far-field cells, and
    collide with the body layer fronts when the model touches the ground, so
    they are disabled unless the config explicitly asks and the geometry has
    at least max(2mm, 2 x first layer) of clearance. The stack is capped by
    layers.ground_n_layers (default 2) regardless of the body layer count.

    Clearance is measured against the *actual* road surface used by the mesh
    (the configured plane less the small embed offset), so the guard agrees
    with where snappy puts the ground patch.
    """
    up_idx = up_axis_index(cfg)
    smin_up = float(combined_bounds[0][up_idx])
    if cfg.get("ground_plane") is not None:
        ground_surface = float(cfg["ground_plane"]) - GROUND_EMBED
        clearance = smin_up - ground_surface
    elif cfg.get("ground_clearance") is not None:
        clearance = float(cfg["ground_clearance"])
    else:
        clearance = 0.0

    requested = bool(layers.get("ground_layers", False))
    n_layers = int(layers.get("n_layers", 0) or 0)
    cap = int(layers.get("ground_n_layers", min(max(n_layers, 0), 2)) or 0)
    ground_n = max(0, min(cap, n_layers)) if n_layers > 0 else 0

    resolved["ground_layers"] = requested
    resolved["ground_n_layers"] = ground_n
    resolved["ground_clearance"] = clearance
    resolved["ground_layers_note"] = ""

    if not requested:
        return

    first = resolved.get("first_layer_thickness") if resolved.get("mode") == "absolute" else None
    try:
        first = float(first) if first is not None else 0.0
    except (TypeError, ValueError):
        first = 0.0
    required = max(2.0e-3, 2.0 * first)

    if ground_n <= 0:
        layers["ground_layers"] = False
        resolved["ground_layers"] = False
        resolved["ground_layers_note"] = "disabled: no layers configured"
        return
    if clearance < required:
        layers["ground_layers"] = False
        resolved["ground_layers"] = False
        resolved["ground_layers_note"] = (
            f"disabled: ground clearance {clearance * 1000:.1f} mm < "
            f"{required * 1000:.1f} mm required"
        )
        return
    if ground_n < n_layers:
        resolved["ground_layers_note"] = f"capped at {ground_n} of {n_layers} layers"


def resolve_layers(
    cfg: dict[str, Any],
    combined_bounds: BBox,
    *,
    explicit_first_layer: bool = False,
    explicit_min_thickness: bool = False,
) -> dict[str, Any]:
    """Resolve layers.y_plus_target into an absolute snappy layer spec.

    Writes the resolved values (relativeSizes false, metre-valued
    first_layer_thickness and min_thickness) back into cfg["layers"] and
    stores provenance under layers["_resolved"] (ignored by deep_merge on
    reload). No-op when no target is set or when the user supplied an
    explicit first_layer_thickness, in which case the latter wins.
    """
    layers = cfg.setdefault("layers", {})
    nu = float(cfg.get("fluid", {}).get("nu", 0.0) or 0.0)
    U = float(cfg.get("flow", {}).get("velocity", 0.0) or 0.0)
    flow_idx, _ = flow_axis_index_sign(cfg)
    extent = float(combined_bounds[1][flow_idx]) - float(combined_bounds[0][flow_idx])
    u_tau = estimate_friction_velocity(U, nu, extent)

    target = layers.get("y_plus_target")
    first = layers.get("first_layer_thickness")
    relative = bool(layers.get("relativeSizes", True))
    n_layers = int(layers.get("n_layers", 0) or 0)
    ratio = float(layers.get("expansion_ratio", 1.0) or 1.0)
    min_ratio = float(layers.get("min_thickness_ratio", 1.0) or 1.0)

    def _stack(t: float) -> float:
        if n_layers >= 1 and ratio > 1.0:
            return t * (ratio ** n_layers - 1) / (ratio - 1)
        return t * max(n_layers, 1)

    def _min_thickness(t: float) -> float:
        """Minimum layer thickness snappy may keep.

        ruler-scaled by ``layers.min_thickness_ratio`` (default 1.0 = the full
        first layer). Lowering it lets snappy keep partial stacks instead of
        dropping them entirely, materially improving coverage on hard geometry
        at the cost of thinner/partial prisms. Never exceeds the total stack.
        """
        return min(max(min_ratio, 0.0) * t, _stack(t))

    resolved: dict[str, Any] = {
        "u_tau": u_tau,
        "y_plus_target": target,
        "y_plus_effective": None,
        "first_layer_thickness": None,
        "min_thickness": None,
        "stack": None,
        "mode": "relative" if relative else "absolute",
        "clamped": False,
        # Clamp provenance, so the CLI/Studio can explain a shortfall.
        "requested_thickness": None,
        "thickness_max": None,
        "clamp_level": None,
        "clamp_cell_m": None,
        # y+ fit provenance (only populated when layers.y_plus_fit resolves a clamp).
        "fit_applied": False,
        "fit_ratio": None,
        "fit_level": None,
    }
    layers["_resolved"] = resolved

    if target is not None and not explicit_first_layer:
        try:
            target_value = float(target)
        except (TypeError, ValueError):
            target_value = None
        if target_value is not None and math.isfinite(target_value) and target_value > 0 and u_tau > 0:
            thickness = first_layer_height(target_value, u_tau, nu)

            mesh = cfg.get("mesh_params", {})
            base_cell = float(mesh.get("base_cell_size", 0.0) or 0.0)
            levels = mesh.get("surface_level") or [0, 0]
            ratio_limit = float(layers.get("maxFaceThicknessRatio", 0.5) or 0.5)
            if base_cell > 0 and len(levels) == 2:
                # Clamp against the *finest* surface cell (level1). snappy will
                # not build a layer thicker than maxFaceThicknessRatio of the
                # local cell, and because min_thickness tracks the first layer it
                # DROPS the whole stack (rather than thinning) on any face finer
                # than that. Requesting the thin, always-buildable thickness keeps
                # layer coverage high; the y+ target is then simply unreachable at
                # this surface resolution, which the provenance + CLI warning
                # report honestly instead of silently degrading the mesh.
                level_fine = int(levels[1])
                cell_fine = base_cell / (2 ** level_fine)
                thickness_max = ratio_limit * cell_fine
                resolved["requested_thickness"] = thickness
                resolved["thickness_max"] = thickness_max
                resolved["clamp_level"] = level_fine
                resolved["clamp_cell_m"] = cell_fine
                if thickness > thickness_max:
                    # Opt-in: instead of only clamping + warning, raise
                    # maxFaceThicknessRatio (within the cap) so the requested y+
                    # is actually met. Falls back to the plain clamp when the cap
                    # still cannot build the layer. Default off.
                    if bool(layers.get("y_plus_fit", False)):
                        fitted = _fit_y_plus_clamp(
                            cfg, layers, thickness, base_cell, levels, ratio_limit,
                        )
                        if fitted is not None:
                            ratio_limit, level_fine = fitted
                            cell_fine = base_cell / (2 ** level_fine)
                            thickness_max = ratio_limit * cell_fine
                            resolved["thickness_max"] = thickness_max
                            resolved["clamp_level"] = level_fine
                            resolved["clamp_cell_m"] = cell_fine
                            resolved["fit_applied"] = True
                            resolved["fit_ratio"] = round(ratio_limit, 4)
                            resolved["fit_level"] = level_fine
                    if thickness > thickness_max and not resolved["fit_applied"]:
                        thickness = thickness_max
                        resolved["clamped"] = True

            layers["relativeSizes"] = False
            layers["first_layer_thickness"] = thickness
            if not explicit_min_thickness:
                layers["min_thickness"] = _min_thickness(thickness)
            resolved.update(
                y_plus_effective=thickness * u_tau / (2.0 * nu),
                first_layer_thickness=thickness,
                min_thickness=layers.get("min_thickness"),
                stack=_stack(thickness),
                mode="absolute",
            )
    elif not relative and first is not None:
        try:
            thickness = float(first)
        except (TypeError, ValueError):
            thickness = None
        if thickness is not None and math.isfinite(thickness) and thickness > 0:
            if not explicit_min_thickness:
                layers["min_thickness"] = _min_thickness(thickness)
            resolved.update(
                y_plus_effective=(thickness * u_tau / (2.0 * nu)) if u_tau > 0 else None,
                first_layer_thickness=thickness,
                min_thickness=layers.get("min_thickness"),
                stack=_stack(thickness),
                mode="absolute",
            )

    _apply_ground_layer_policy(cfg, combined_bounds, layers, resolved)
    return resolved


__all__ = [
    "estimate_friction_velocity",
    "first_layer_height",
    "resolve_layers",
    "Y_PLUS_FIT_RATIO_CAP",
    "_apply_ground_layer_policy",
]
