"""Single entry point that emits all mesh dictionaries for a case."""

from __future__ import annotations

from pathlib import Path

from rapidfoam.meshing.context import MeshContext
from rapidfoam.meshing.plan import MeshPlan
from rapidfoam.meshing.writers.block_mesh import write_block_mesh_dict
from rapidfoam.meshing.writers.feature_extract import write_surface_feature_extract_dict
from rapidfoam.meshing.writers.snappy import write_snappy_hex_mesh_dict


def emit_mesh_files(plan: MeshPlan, ctx: MeshContext, case_dir: Path) -> None:
    """Write blockMeshDict, surfaceFeatureExtractDict and snappyHexMeshDict."""
    write_block_mesh_dict(plan, ctx, case_dir)
    write_surface_feature_extract_dict(plan, ctx, case_dir)
    write_snappy_hex_mesh_dict(plan, ctx, case_dir)


__all__ = ["emit_mesh_files"]
