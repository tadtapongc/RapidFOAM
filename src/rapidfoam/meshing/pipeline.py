"""Single entry point that emits all mesh dictionaries for a case."""

from __future__ import annotations

from pathlib import Path

from rapidfoam.meshing.context import MeshContext, build_mesh_context
from rapidfoam.meshing.plan import MeshPlan, plan_from_config
from rapidfoam.meshing.writers.block_mesh import write_block_mesh_dict
from rapidfoam.meshing.writers.feature_extract import write_surface_feature_extract_dict
from rapidfoam.meshing.writers.snappy import write_snappy_hex_mesh_dict


def emit_mesh_files(plan: MeshPlan, ctx: MeshContext, case_dir: Path) -> None:
    """Write blockMeshDict, surfaceFeatureExtractDict and snappyHexMeshDict."""
    write_block_mesh_dict(plan, ctx, case_dir)
    write_surface_feature_extract_dict(plan, ctx, case_dir)
    write_snappy_hex_mesh_dict(plan, ctx, case_dir)


def emit_mesh_files_from_config(cfg: dict, case_dir: Path) -> None:
    """Convenience wrapper for an already-derived ``cfg`` (no re-derivation)."""
    emit_mesh_files(plan_from_config(cfg), build_mesh_context(cfg), case_dir)


__all__ = ["emit_mesh_files", "emit_mesh_files_from_config"]
