"""Compatibility shim for the plan-based mesh writers.

The canonical implementations live in ``rapidfoam.meshing.writers`` and consume a
:class:`~rapidfoam.meshing.plan.MeshPlan` + :class:`~rapidfoam.meshing.context.MeshContext`.
These wrappers accept the legacy ``cfg`` signature so existing callers/tests keep
working; they build a plan from the (already-derived) config without re-deriving.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rapidfoam.meshing.context import build_mesh_context
from rapidfoam.meshing.plan import plan_from_config
from rapidfoam.meshing.writers.block_mesh import FACE_MAP  # noqa: F401  (re-export)
from rapidfoam.meshing.writers.block_mesh import write_block_mesh_dict as _write_block_mesh_dict
from rapidfoam.meshing.writers.feature_extract import (
    write_surface_feature_extract_dict as _write_surface_feature_extract_dict,
)
from rapidfoam.meshing.writers.snappy import write_snappy_hex_mesh_dict as _write_snappy_hex_mesh_dict


def write_block_mesh_dict(cfg: dict[str, Any], case_dir: Path) -> None:
    _write_block_mesh_dict(plan_from_config(cfg), build_mesh_context(cfg), case_dir)


def write_snappy_hex_mesh_dict(cfg: dict[str, Any], case_dir: Path) -> None:
    _write_snappy_hex_mesh_dict(plan_from_config(cfg), build_mesh_context(cfg), case_dir)


def write_surface_feature_extract_dict(cfg: dict[str, Any], case_dir: Path) -> None:
    _write_surface_feature_extract_dict(plan_from_config(cfg), build_mesh_context(cfg), case_dir)


__all__ = [
    "FACE_MAP",
    "write_block_mesh_dict",
    "write_snappy_hex_mesh_dict",
    "write_surface_feature_extract_dict",
]
