"""``surfaceFeatureExtractDict`` writer (plan-based; extracted from writers/mesh.py)."""

from __future__ import annotations

from pathlib import Path

from rapidfoam.meshing.context import MeshContext
from rapidfoam.meshing.plan import MeshPlan
from rapidfoam.core.foam import FOOTER, foam_header


def write_surface_feature_extract_dict(plan: MeshPlan, ctx: MeshContext, case_dir: Path) -> None:
    """Generate surfaceFeatureExtractDict."""
    feat = plan.feature_extract
    method = feat.get("extractionMethod", "extractFromSurface")
    angle = feat.get("includedAngle", 150)

    entries = []
    for name in ctx.stl_names:
        entries.append(f"""\
    {name}.stl
    {{
        extractionMethod    {method};
        {method}Coeffs
        {{
            includedAngle   {angle};
        }}
        subsetFeatures
        {{
            nonManifoldEdges    yes;
            openEdges           yes;
        }}
        writeObj            no;
    }}""")

    content = "\n".join(entries) + "\n\n"
    (case_dir / "system" / "surfaceFeatureExtractDict").write_text(
        foam_header("surfaceFeatureExtractDict") + content + FOOTER
    )
