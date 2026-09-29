"""``blockMeshDict`` writer (plan-based; extracted from writers/mesh.py)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rapidfoam.core.faces import patch_role
from rapidfoam.meshing.context import MeshContext
from rapidfoam.meshing.plan import MeshPlan
from rapidfoam.core.foam import FOOTER, foam_header

log = logging.getLogger(__name__)

# hex vertex ordering to face string
FACE_MAP: dict[str, str] = {
    "+x": "(1 2 6 5)", "-x": "(0 4 7 3)",
    "+y": "(2 3 7 6)", "-y": "(0 1 5 4)",
    "+z": "(4 5 6 7)", "-z": "(0 3 2 1)",
}

_PATCH_TYPES = {
    "inlet": "patch",
    "outlet": "patch",
    "ground": "wall",
    "symmetry": "symmetry",
}


def _grading_str(grading: Any) -> str:
    """Format a 3-component simpleGrading tuple, defaulting to uniform."""
    if isinstance(grading, (list, tuple)) and len(grading) == 3:
        try:
            return "(" + " ".join(f"{float(g):g}" for g in grading) + ")"
        except (TypeError, ValueError):
            pass
    return "(1 1 1)"


def write_block_mesh_dict(plan: MeshPlan, ctx: MeshContext, case_dir: Path) -> None:
    """Generate blockMeshDict from the plan's domain box and base cell size."""
    box = plan.domain_box
    bmin, bmax = box["min"], box["max"]
    mesh = plan.mesh_params
    cell_size = mesh["base_cell_size"]

    cells = mesh.get("block_cells")
    if isinstance(cells, (list, tuple)) and len(cells) == 3 \
            and all(isinstance(c, int) and not isinstance(c, bool) and c >= 1 for c in cells):
        nx, ny, nz = (int(c) for c in cells)
    else:
        log.warning("MeshPlan has no valid block_cells; falling back to a uniform cell count")
        nx = max(1, round((bmax[0] - bmin[0]) / cell_size))
        ny = max(1, round((bmax[1] - bmin[1]) / cell_size))
        nz = max(1, round((bmax[2] - bmin[2]) / cell_size))

    grading_str = _grading_str(mesh.get("grading"))

    patch_faces: dict[str, list[str]] = {}
    for direction, patch_name in ctx.faces.items():
        patch_faces.setdefault(patch_name, []).append(FACE_MAP[direction])

    boundary_lines = []
    for patch_name, faces in patch_faces.items():
        patch_type = _PATCH_TYPES.get(patch_role(ctx.patches, patch_name), "patch")
        faces_str = " ".join(faces)
        boundary_lines.append(f"""\
    {patch_name}
    {{
        type {patch_type};
        faces ( {faces_str} );
    }}""")

    content = f"""\
scale   1;

vertices
(
    ({bmin[0]} {bmin[1]} {bmin[2]})
    ({bmax[0]} {bmin[1]} {bmin[2]})
    ({bmax[0]} {bmax[1]} {bmin[2]})
    ({bmin[0]} {bmax[1]} {bmin[2]})
    ({bmin[0]} {bmin[1]} {bmax[2]})
    ({bmax[0]} {bmin[1]} {bmax[2]})
    ({bmax[0]} {bmax[1]} {bmax[2]})
    ({bmin[0]} {bmax[1]} {bmax[2]})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading {grading_str}
);

edges ();

boundary
(
{chr(10).join(boundary_lines)}
);

mergePatchPairs ();

"""
    (case_dir / "system" / "blockMeshDict").write_text(
        foam_header("blockMeshDict") + content + FOOTER
    )
