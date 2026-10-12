"""``snappyHexMeshDict`` writer (plan-based; extracted from writers/mesh.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from rapidfoam.core.faces import patch_role
from rapidfoam.meshing.context import MeshContext
from rapidfoam.meshing.plan import MeshPlan
from rapidfoam.meshing.quality import resolve_quality
from rapidfoam.core.foam import FOOTER, bool_str, foam_header


def _location_in_mesh(plan: MeshPlan, ctx: MeshContext) -> list[float]:
    """Place the locationInMesh probe at the corner maximally far from geometry.

    Geometry is downstream, near ground, near symmetry, so the opposite corner is
    always outside it. An explicit ``location_in_mesh``/``locationInMesh`` on the
    plan wins.
    """
    mesh = plan.mesh_params
    box = plan.domain_box
    loc = mesh.get("location_in_mesh", mesh.get("locationInMesh"))
    if loc:
        return list(loc)

    flow_idx, flow_sign = ctx.flow_index, ctx.flow_sign
    up_idx = ctx.up_index
    lateral_idx = ctx.lateral_index
    extent = [box["max"][i] - box["min"][i] for i in range(3)]

    loc = [0.0, 0.0, 0.0]
    if flow_sign > 0:
        loc[flow_idx] = box["min"][flow_idx] + extent[flow_idx] * 0.05
    else:
        loc[flow_idx] = box["max"][flow_idx] - extent[flow_idx] * 0.05

    loc[up_idx] = box["max"][up_idx] - extent[up_idx] * 0.05
    if patch_role(ctx.patches, ctx.faces[f"+{'xyz'[up_idx]}"]) == "ground":
        loc[up_idx] = box["min"][up_idx] + extent[up_idx] * 0.05

    sym_dir = next(
        (face_dir for face_dir, patch_name in ctx.faces.items()
         if patch_role(ctx.patches, patch_name) == "symmetry"),
        None,
    )
    if sym_dir and sym_dir.endswith("xyz"[lateral_idx]):
        if sym_dir.startswith("-"):
            loc[lateral_idx] = box["max"][lateral_idx] - extent[lateral_idx] * 0.05
        else:
            loc[lateral_idx] = box["min"][lateral_idx] + extent[lateral_idx] * 0.05
    else:
        loc[lateral_idx] = (box["min"][lateral_idx] + box["max"][lateral_idx]) / 2
    return loc


def _render_quality_controls(
    limits: Mapping[str, float], relaxed: Mapping[str, float]
) -> str:
    """Render one snappyHexMesh ``meshQualityControls`` block.

    Both tiers arrive fully resolved from :func:`meshing.quality.resolve_quality`
    (the single tuning source), so there are no hidden per-call defaults here:
    what the profile says is exactly what the dictionary contains. ``limits``
    is the gate; ``relaxed`` is the sub-block snappy falls back to during its
    iterations.
    """

    def v(key: str) -> str:
        return f"{limits[key]:g}"

    def r(key: str) -> str:
        return f"{relaxed[key]:g}"

    return f"""\
meshQualityControls
{{
    maxNonOrtho         {v("maxNonOrtho")};
    maxBoundarySkewness {v("maxBoundarySkewness")};
    maxInternalSkewness {v("maxInternalSkewness")};
    maxConcave          {v("maxConcave")};
    minVol              {v("minVol")};
    minTetQuality       {v("minTetQuality")};
    minArea             {v("minArea")};
    minTwist            {v("minTwist")};
    minDeterminant      {v("minDeterminant")};
    minFaceWeight       {v("minFaceWeight")};
    minVolRatio         {v("minVolRatio")};
    minTriangleTwist    {v("minTriangleTwist")};
    nSmoothScale        {v("nSmoothScale")};
    errorReduction      {v("errorReduction")};

    relaxed
    {{
        maxNonOrtho     {r("maxNonOrtho")};
        maxBoundarySkewness {r("maxBoundarySkewness")};
        maxInternalSkewness {r("maxInternalSkewness")};
        maxConcave      {r("maxConcave")};
        minVol          {r("minVol")};
        minTetQuality   {r("minTetQuality")};
        minArea         {r("minArea")};
        minTwist        {r("minTwist")};
        minDeterminant  {r("minDeterminant")};
        minFaceWeight   {r("minFaceWeight")};
        minVolRatio     {r("minVolRatio")};
        minTriangleTwist {r("minTriangleTwist")};
    }}
}}

"""


def write_snappy_hex_mesh_dict(plan: MeshPlan, ctx: MeshContext, case_dir: Path) -> None:
    """Generate snappyHexMeshDict (and the two-pass layering dict) from the plan."""
    mesh = plan.mesh_params
    stl_names = ctx.stl_names
    patches = ctx.patches
    snap = ctx.snap
    layers = plan.layers
    quality = ctx.quality
    profile = resolve_quality(quality)

    surface_level = mesh["surface_level"]
    edge_level = mesh["edge_level"]
    regions = mesh["refinement_regions"]
    distance_levels = mesh.get("distance_levels", [])
    # Optional geometry-derived per-surface levels (mesh_params.auto_size):
    # {stem: {"surface_level": [l0, l1], "edge_level": int}}.
    per_surface = mesh.get("surface_levels") or {}

    def _surface_level_for(name):
        info = per_surface.get(name)
        return info.get("surface_level", surface_level) if isinstance(info, dict) else surface_level

    def _edge_level_for(name):
        info = per_surface.get(name)
        return info.get("edge_level", edge_level) if isinstance(info, dict) else edge_level

    geo_lines = []
    for name in stl_names:
        geo_lines.append(f"    {name}.stl {{ type triSurfaceMesh; name {name}; }}")
    for r in regions:
        geo_lines.append(
            f"    {r['name']} {{ type searchableBox;"
            f" min ({r['min'][0]} {r['min'][1]} {r['min'][2]});"
            f" max ({r['max'][0]} {r['max'][1]} {r['max'][2]}); }}")

    feat_lines = []
    for name in stl_names:
        feat_lines.append(f'        {{ file "{name}.eMesh"; level {_edge_level_for(name)}; }}')

    ref_surf_lines = []
    for name in stl_names:
        level = _surface_level_for(name)
        ref_surf_lines.append(
            f"        {name} {{ level ({level[0]} {level[1]});"
            f" patchInfo {{ type wall; }} }}")

    ref_region_lines = []
    if distance_levels:
        levels_str = " ".join(f"({d} {l})" for d, l in distance_levels)
        for name in stl_names:
            ref_region_lines.append(
                f"        {name} {{ mode distance; levels ({levels_str}); }}")
    for r in regions:
        ref_region_lines.append(
            f"        {r['name']} {{ mode inside; levels ((1e15 {r['level']})); }}")

    layer_lines = []
    n_layers = layers["n_layers"]
    # Optional per-surface layer-count overrides (mesh_regions): {stem: int}.
    layer_overrides = mesh.get("layer_overrides") or {}

    def _n_layers_for(name):
        value = layer_overrides.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return n_layers

    for name in stl_names:
        layer_lines.append(f'        "{name}" {{ nSurfaceLayers {_n_layers_for(name)}; }}')
    active_patches = set(ctx.faces.values())
    if layers.get("ground_layers", False) and patches["ground"] in active_patches:
        try:
            ground_n = int(layers.get("ground_n_layers", 2) or 2)
        except (TypeError, ValueError):
            ground_n = 2
        ground_n = max(0, min(ground_n, n_layers))
        if ground_n > 0:
            layer_lines.append(f'        "{patches["ground"]}" {{ nSurfaceLayers {ground_n}; }}')

    loc = _location_in_mesh(plan, ctx)

    quality_text = _render_quality_controls(profile.main, profile.relaxed)

    # Layering pass gate: a bounded relaxation of the main gate (the tier that
    # actually bounds the final mesh — snappy keeps a prism only while the cell
    # stays inside it). A case may override with mesh_quality.layering_relaxed.
    layering_quality_text = _render_quality_controls(profile.layering, profile.layering)

    def _render(castellated, snap_flag, add_layers, quality_block):
        return f"""\
castellatedMesh {bool_str(castellated)};
snap            {bool_str(snap_flag)};
addLayers       {bool_str(add_layers)};

geometry
{{
{chr(10).join(geo_lines)}
}}

castellatedMeshControls
{{
    maxLocalCells       {mesh.get("maxLocalCells", 2000000)};
    maxGlobalCells      {mesh.get("maxGlobalCells", 30000000)};
    minRefinementCells  {mesh.get("minRefinementCells", 10)};
    maxLoadUnbalance    {mesh.get("maxLoadUnbalance", 0.25)};
    nCellsBetweenLevels {mesh.get("nCellsBetweenLevels", 3)};

    features
    (
{chr(10).join(feat_lines)}
    );

    refinementSurfaces
    {{
{chr(10).join(ref_surf_lines)}
    }}

    resolveFeatureAngle {mesh.get("resolveFeatureAngle", 15)};

    refinementRegions
    {{
{chr(10).join(ref_region_lines)}
    }}

    locationInMesh ({loc[0]:.4f} {loc[1]:.4f} {loc[2]:.4f});
    allowFreeStandingZoneFaces {bool_str(mesh.get("allowFreeStandingZoneFaces", True))};
}}

snapControls
{{
    nSmoothPatch        {snap.get("nSmoothPatch", 5)};
    tolerance           {snap.get("tolerance", 2.0)};
    nSolveIter          {snap.get("nSolveIter", 200)};
    nRelaxIter          {snap.get("nRelaxIter", 8)};
    nFeatureSnapIter    {snap.get("nFeatureSnapIter", 15)};
    implicitFeatureSnap {bool_str(snap.get("implicitFeatureSnap", True))};
    explicitFeatureSnap {bool_str(snap.get("explicitFeatureSnap", True))};
    multiRegionFeatureSnap {bool_str(snap.get("multiRegionFeatureSnap", False))};
}}

addLayersControls
{{
    relativeSizes       {bool_str(layers.get("relativeSizes", True))};
    layers
    {{
{chr(10).join(layer_lines)}
    }}
    expansionRatio          {layers.get("expansion_ratio", 1.2)};
    firstLayerThickness     {layers.get("first_layer_thickness", 0.3)};
    minThickness            {layers.get("min_thickness", 0.05)};
    nGrow                   {layers.get("nGrow", 0)};
    featureAngle            {layers.get("featureAngle", 170)};
    slipFeatureAngle        {layers.get("slipFeatureAngle", 30)};
    maxFaceThicknessRatio   {layers.get("maxFaceThicknessRatio", 0.5)};
    nSmoothSurfaceNormals   {layers.get("nSmoothSurfaceNormals", 3)};
    nSmoothThickness        {layers.get("nSmoothThickness", 10)};
    nSmoothNormals          {layers.get("nSmoothNormals", 3)};
    nRelaxIter              {layers.get("nRelaxIter", 10)};
    nBufferCellsNoExtrude   {layers.get("nBufferCellsNoExtrude", 0)};
    nLayerIter              {layers.get("nLayerIter", 50)};
    maxAlignedCells         {layers.get("maxAlignedCells", 200000)};
    minMedialAxisAngle      {layers.get("minMedialAxisAngle", 90)};
    maxThicknessToMedialRatio {layers.get("maxThicknessToMedialRatio", 0.3)};
    nMedialAxisIter         {layers.get("nMedialAxisIter", 10)};
    nSmoothDisplacement     {layers.get("nSmoothDisplacement", 0)};
    detectExtrusionIsland   {bool_str(layers.get("detectExtrusionIsland", True))};
    nRelaxedIter            {layers.get("nRelaxedIter", 20)};
}}

{quality_block}
writeFlags ( scalarLevels layerSets layerFields );
mergeTolerance 1e-6;

"""
    (case_dir / "system" / "snappyHexMeshDict").write_text(
        foam_header("snappyHexMeshDict") + _render(True, True, not layers.get("two_pass", False), quality_text) + FOOTER
    )
    if layers.get("two_pass", False):
        (case_dir / "system" / "snappyHexMeshDict_layering").write_text(
            foam_header("snappyHexMeshDict_layering")
            + _render(False, False, True, layering_quality_text)
            + FOOTER
        )
