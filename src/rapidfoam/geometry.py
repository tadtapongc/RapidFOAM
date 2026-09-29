"""Geometry utilities — re-export facade.

The implementations moved during the Phase 1c/2 refactor:

  * axis/face/field helpers → ``rapidfoam.core.{axes,faces,fields}``
  * domain sizing / feature sizing / grading / layers / mesh params / presets
    → ``rapidfoam.meshing.*``

New code should import from those modules. This facade keeps the existing
``from rapidfoam.geometry import ...`` call sites working.
"""

from __future__ import annotations

from rapidfoam.core.axes import (  # noqa: F401
    AXIS_MAP,
    axis_index_sign,
    flow_axis_index_sign,
    parse_axis,
    up_axis_index,
    vec_str,
)
from rapidfoam.core.faces import face_assignments, face_role  # noqa: F401
from rapidfoam.core.fields import turbulence_values, velocity_vector  # noqa: F401
from rapidfoam.meshing.domain import GROUND_EMBED, compute_domain_box  # noqa: F401
from rapidfoam.meshing.grading import (  # noqa: F401
    DEFAULT_GRADING_RATIO,
    _graded_axis,
    _prod,
    compute_block_grading,
)
from rapidfoam.meshing.layers import (  # noqa: F401
    _apply_ground_layer_policy,
    estimate_friction_velocity,
    first_layer_height,
    resolve_layers,
)
from rapidfoam.meshing.params import compute_mesh_params  # noqa: F401
from rapidfoam.meshing.presets import FIDELITY_PRESETS  # noqa: F401
from rapidfoam.meshing.sizing import _resolve_feature_angle, _resolve_feature_sizing  # noqa: F401
from rapidfoam.stl_utils import BBox, EdgeStats, FeatureAngleStats  # noqa: F401
