"""Context the mesh writers need that is not part of mesh derivation.

Keeps the writers free of raw ``cfg`` indexing: STL names, boundary patches,
the resolved face map, snappy quality controls and the flow/up/lateral axis
indices are resolved once by :func:`build_mesh_context`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from rapidfoam.core.axes import flow_axis_index_sign, up_axis_index
from rapidfoam.core.faces import face_assignments


@dataclass(frozen=True)
class MeshContext:
    stl_names: tuple[str, ...]
    patches: Mapping[str, str]
    faces: Mapping[str, str]
    snap: Mapping[str, Any]
    quality: Mapping[str, Any]
    flow_index: int
    flow_sign: int
    up_index: int
    lateral_index: int


def build_mesh_context(
    cfg: dict[str, Any],
    *,
    stl_names: Sequence[str] | None = None,
) -> MeshContext:
    flow_index, flow_sign = flow_axis_index_sign(cfg)
    up_index = up_axis_index(cfg)
    lateral_index = next(i for i in range(3) if i not in (flow_index, up_index))
    names = tuple(stl_names if stl_names is not None else cfg.get("stl_names", []))
    return MeshContext(
        stl_names=names,
        patches=dict(cfg.get("patches", {})),
        faces=dict(face_assignments(cfg)),
        snap=dict(cfg.get("snap", {})),
        quality=dict(cfg.get("mesh_quality", {})),
        flow_index=flow_index,
        flow_sign=flow_sign,
        up_index=up_index,
        lateral_index=lateral_index,
    )


__all__ = ["MeshContext", "build_mesh_context"]
