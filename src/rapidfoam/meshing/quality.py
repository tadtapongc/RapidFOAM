"""snappyHexMesh quality-control tiers — the single tuning source of truth.

snappyHexMesh consumes three tiers of ``meshQualityControls``:

``main``
    The castellated + snap gate. Written into ``system/snappyHexMeshDict``; a
    user overrides it with the top-level ``mesh_quality`` keys.
``relaxed``
    The in-iteration fallback snappy applies when it cannot meet ``main`` inside
    its iteration budget. Bounded above ``main`` so the quality loop does not
    collapse the mesh; it is not the final bound.
``layering``
    The gate for the two-pass layering pass
    (``system/snappyHexMeshDict_layering``). This one *does* bound the mesh the
    solver sees: snappy keeps a prism only while the cell stays inside its
    limits, so a loose gate here is a loose final mesh.

Robustness rationale (measured on the Rapidamente aero-map cases): a layering
gate at ``maxNonOrtho 80`` pinned the final mesh just under 80 deg
non-orthogonality with skewness up to ~14 and a "Bad" verdict on every case.
The layering tier is therefore a *bounded* relaxation of the main gate — enough
margin to keep boundary-layer coverage on complex geometry, bounded enough to
stay inside the practical good/caution bands (``postproc.checkmesh``
``QUALITY_TIERS``). The main gate stays at the CFD-practical limits so pass 1
does the heavy lifting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

# Main gate (system/snappyHexMeshDict). Keys map 1:1 to snappyHexMesh
# meshQualityControls entries.
QUALITY_LIMITS: dict[str, float] = {
    "maxNonOrtho": 60,
    "maxBoundarySkewness": 20,
    "maxInternalSkewness": 3.5,
    "maxConcave": 80,
    "minVol": 1e-13,
    "minTetQuality": 1e-15,
    "minArea": -1,
    "minTwist": 0.02,
    "minDeterminant": 0.001,
    "minFaceWeight": 0.05,
    "minVolRatio": 0.01,
    "minTriangleTwist": -1,
    "nSmoothScale": 6,
    "errorReduction": 0.75,
}

# In-iteration relaxation of the main gate. Snappy falls back to these when the
# main limits cannot be met; keeping them finite (instead of effectively
# disabled) preserves solver-usable cells during the quality loop.
RELAXED_LIMITS: dict[str, float] = {
    "maxNonOrtho": 70,
    "maxBoundarySkewness": 25,
    "maxInternalSkewness": 5,
    "maxConcave": 85,
    "minVol": 1e-13,
    "minTetQuality": 1e-30,
    "minArea": -1,
    "minTwist": 0.001,
    "minDeterminant": 0.0005,
    "minFaceWeight": 0.02,
    "minVolRatio": 0.005,
    "minTriangleTwist": -1,
}

# Bounded relaxation of the main gate for the two-pass layering pass. Every key
# absent here inherits the *main* (strict) value, so raising one limit never
# silently loosens the others. The skewness limit matches the measured-good
# F10_R20_mq experiment (final max skewness 4.93 at a 5 gate); the
# non-orthogonality cap is one step tighter than that experiment's 70 so the
# final mesh stays inside the verdict "caution" band and the solver keeps margin.
LAYERING_LIMITS: dict[str, float] = {
    "maxNonOrtho": 65,
    "maxBoundarySkewness": 22,
    "maxInternalSkewness": 5,
    "maxConcave": 82,
}


@dataclass(frozen=True)
class QualityProfile:
    """Resolved ``meshQualityControls`` tiers (every entry present)."""

    main: Mapping[str, float]
    relaxed: Mapping[str, float]
    layering: Mapping[str, float]


def _numeric(value: Any, default: float) -> float:
    """Coerce a config value to a finite number, else use the default.

    Protects the writer from strings/NaN/bools that JSON or a hand-edited
    ``case_config.json`` may carry; validation reports the same problems.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def resolve_quality(quality_cfg: Mapping[str, Any] | None = None) -> QualityProfile:
    """Resolve the three quality tiers from a ``mesh_quality`` config block.

    Unknown keys are ignored and missing keys fall back to the built-in tiers.
    The tiers are *independent*: ``relaxed`` and ``layering_relaxed`` keep their
    own defaults even if the main gate is tightened (predictability — one knob
    changes one thing), except that keys the layering tier does not set
    explicitly inherit the resolved main value (e.g. ``minVolRatio``), so raising
    the bounded-relaxation limits never silently loosens the others. ``validate``
    warns when a tightened main gate leaves the layering gate looser than the
    default bounded margin, which would make the layer pass the final bound.
    """
    cfg = quality_cfg if isinstance(quality_cfg, Mapping) else {}

    main = {key: _numeric(cfg.get(key), default) for key, default in QUALITY_LIMITS.items()}

    relaxed = dict(RELAXED_LIMITS)
    relaxed_override = cfg.get("relaxed")
    if isinstance(relaxed_override, Mapping):
        for key in relaxed:
            if key in relaxed_override:
                relaxed[key] = _numeric(relaxed_override[key], relaxed[key])

    layering = {**main, **LAYERING_LIMITS}
    layering_override = cfg.get("layering_relaxed")
    if isinstance(layering_override, Mapping):
        for key in layering:
            if key in layering_override:
                layering[key] = _numeric(layering_override[key], layering[key])

    return QualityProfile(main=main, relaxed=relaxed, layering=layering)


__all__ = [
    "QUALITY_LIMITS",
    "RELAXED_LIMITS",
    "LAYERING_LIMITS",
    "QualityProfile",
    "resolve_quality",
]
