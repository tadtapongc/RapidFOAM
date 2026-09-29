"""Case generation shared by the CLI and the Web Studio.

``build_case`` holds the generation pipeline (previously ``cli._do_generate``)
so the transport layers no longer depend on each other. Console output goes
through a ``reporter`` callable; user-facing failures raise
:class:`CaseGenerationError` instead of calling ``sys.exit``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

Reporter = Callable[[str], None]


class CaseGenerationError(Exception):
    """A user-facing case-generation failure (message ready to display)."""


def _default_reporter(message: str) -> None:
    """Print, degrading non-ASCII glyphs when stdout cannot encode them."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"))


def build_case(
    cfg_path: Path,
    project_dir: Path,
    dry_run: bool = False,
    reporter: Reporter | None = None,
) -> Path | None:
    """Generate a complete OpenFOAM case from ``cfg_path``.

    Returns the case directory, or ``None`` for a dry run. Raises
    :class:`CaseGenerationError` on a user-facing error.
    """
    report: Reporter = reporter if reporter is not None else _default_reporter

    from rapidfoam.config import CASE_DIR, STL_DIR, find_stl, load_config, user_set, validate
    from rapidfoam.core.faces import face_assignments, face_role
    from rapidfoam.core.fields import turbulence_values, velocity_vector
    from rapidfoam.core.axes import vec_str
    from rapidfoam.meshing.domain import compute_domain_box
    from rapidfoam.meshing.context import build_mesh_context
    from rapidfoam.meshing.pipeline import emit_mesh_files
    from rapidfoam.meshing.plan import apply_plan_to_cfg, build_mesh_plan
    from rapidfoam.meshing.presets import apply_fidelity_preset
    from rapidfoam.geometry.stl import EdgeStats, FeatureAngleStats, copy_stl, stl_analyze_full

    if not cfg_path.exists():
        raise CaseGenerationError(f"ERROR: {cfg_path} not found")

    # Load and validate config
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        raise CaseGenerationError(f"ERROR: {exc}")

    # Load raw config to correctly handle user overrides
    with open(cfg_path, encoding="utf-8") as f:
        raw_user = json.load(f)

    def _is_set(section: str, key: str) -> bool:
        return user_set(raw_user, section, key)

    report(f"  Config: {cfg_path}")

    errors, warnings = validate(cfg, project_dir)
    for w in warnings:
        report(f"  ⚠  {w}")
    if errors:
        report("\n  ERRORS:")
        for e in errors:
            report(f"    ✗ {e}")
        raise CaseGenerationError("")

    # Resolve STL names (strip extensions for OpenFOAM patch names)
    stl_dir = project_dir / STL_DIR
    stl_pairs: list[tuple[str, Path]] = []
    for name in cfg["stl_files"]:
        stem = name.rsplit(".", 1)[0] if "." in name else name
        path = find_stl(stl_dir, name)
        if path is None:
            raise CaseGenerationError(f"ERROR: STL disappeared before generation: {name}")
        stl_pairs.append((stem, path))

    stl_names = [stem for stem, _ in stl_pairs]
    cfg["stl_names"] = stl_names
    cfg["domain_faces"] = face_assignments(cfg)

    # Compute combined STL bounds and edge statistics for feature-based sizing
    all_min = [float("inf")] * 3
    all_max = [float("-inf")] * 3
    stl_info_map: dict[str, tuple[str, int, tuple[tuple[float, float, float], tuple[float, float, float]]]] = {}
    edge_stats = EdgeStats()
    angle_stats = FeatureAngleStats()
    for stem, path in stl_pairs:
        try:
            solid_name, n_triangles, bbox, stats, angles = stl_analyze_full(path)
            stl_info_map[stem] = (solid_name, n_triangles, bbox)
            edge_stats.merge(stats)
            angle_stats.merge(angles)
            smin, smax = bbox
        except (OSError, ValueError) as exc:
            raise CaseGenerationError(f"ERROR: {exc}")
        for i in range(3):
            all_min[i] = min(all_min[i], smin[i])
            all_max[i] = max(all_max[i], smax[i])

    if all_min[0] == float("inf"):
        raise CaseGenerationError("ERROR: No valid STL files found — cannot compute domain")

    combined_bounds = (tuple(all_min), tuple(all_max))

    # Auto-compute domain box if requested or missing
    if cfg.get("domain_box") in ("auto", None) or not isinstance(cfg.get("domain_box"), dict):
        cfg["domain_box"] = compute_domain_box(cfg, combined_bounds)
    if any(lo >= hi for lo, hi in zip(cfg["domain_box"]["min"], cfg["domain_box"]["max"])):
        raise CaseGenerationError(
            "ERROR: Derived domain has nonpositive dimensions; check ground and symmetry planes"
        )

    # Check STL clearance relative to domain boundaries
    box = cfg["domain_box"]
    domain_faces = {d: face_role(cfg, p) for d, p in cfg["domain_faces"].items()}
    axis_labels = ["x", "y", "z"]
    for i in range(3):
        clearance_min = all_min[i] - box["min"][i]
        clearance_max = box["max"][i] - all_max[i]
        stl_extent = all_max[i] - all_min[i]
        min_clearance = max(0.1, stl_extent * 0.1)  # at least 10% of geometry size

        min_face_type = domain_faces.get(f"-{axis_labels[i]}", "").lower()
        max_face_type = domain_faces.get(f"+{axis_labels[i]}", "").lower()

        if clearance_min < -1e-4:
            if min_face_type == "symmetry":
                report(f"  ℹ  STL crosses symmetry plane: {axis_labels[i]}_min "
                       f"({clearance_min:.3f} m) — geometry will be cut at symmetry boundary")
            else:
                report(f"  ⚠  STL penetrates outside domain: "
                       f"{axis_labels[i]}_min ({clearance_min:.3f} m)")
        elif min_face_type == "ground":
            report(f"  ℹ  Ground plane: {axis_labels[i]} = {box['min'][i]:.3f} m "
                   f"(ground clearance: {clearance_min * 1000:.1f} mm)")
        elif clearance_min < min_clearance and min_face_type != "symmetry":
            report(f"  ⚠  STL very close to domain boundary: "
                   f"{axis_labels[i]}_min (clearance: {clearance_min:.3f} m)")

        if clearance_max < -1e-4:
            report(f"  ⚠  STL penetrates outside domain: "
                   f"{axis_labels[i]}_max ({clearance_max:.3f} m)")
        elif clearance_max < min_clearance and max_face_type not in ("symmetry", "ground"):
            report(f"  ⚠  STL very close to domain boundary: "
                   f"{axis_labels[i]}_max (clearance: {clearance_max:.3f} m)")

    # Derive all mesh parameters through the shared plan seam, then apply the
    # result back to cfg (keeps case_config.json and writers unchanged).
    apply_fidelity_preset(cfg, _is_set)
    plan = build_mesh_plan(
        cfg,
        combined_bounds,
        feature_stats=edge_stats,
        angle_stats=angle_stats,
        explicit_feature_angle=_is_set("feature_extract", "includedAngle"),
        explicit_first_layer=_is_set("layers", "first_layer_thickness"),
        explicit_min_thickness=_is_set("layers", "min_thickness"),
    )
    apply_plan_to_cfg(cfg, plan)
    layer_resolution = dict(plan.layer_spec.resolved)
    if layer_resolution.get("y_plus_target") is not None and _is_set("layers", "first_layer_thickness"):
        report("  ⚠  layers.first_layer_thickness overrides layers.y_plus_target")

    # Derived values for display
    k, omega, nut = turbulence_values(cfg)
    vel = velocity_vector(cfg)
    mesh = cfg["mesh_params"]
    end_time = cfg["solver"]["end_time"]

    report("\n  Geometry bounds:")
    report(f"    min: ({all_min[0]:.3f}, {all_min[1]:.3f}, {all_min[2]:.3f})")
    report(f"    max: ({all_max[0]:.3f}, {all_max[1]:.3f}, {all_max[2]:.3f})")
    report("  Domain box:")
    box = cfg["domain_box"]
    report(f"    min: ({box['min'][0]:.3f}, {box['min'][1]:.3f}, {box['min'][2]:.3f})")
    report(f"    max: ({box['max'][0]:.3f}, {box['max'][1]:.3f}, {box['max'][2]:.3f})")
    report("  Mesh:")
    report(f"    Base cell:      {mesh['base_cell_size']} m")
    report(f"    Surface level:  {mesh['surface_level']}")
    report(f"    Edge level:     {mesh['edge_level']}")
    dist_levels = mesh.get('distance_levels', [])
    if dist_levels:
        shells = ", ".join(f"{d*1000:.0f}mm→L{l}" for d, l in dist_levels)
        report(f"    Distance shells: {shells}")
    for r in mesh.get("refinement_regions", []):
        report(f"    Region {r['name']}: Level {r['level']}")

    grading = mesh.get("grading_info")
    if grading:
        mode = grading.get("mode", "off")
        cells = grading.get("block_cells")
        uniform = grading.get("uniform_cells")
        if mode == "auto" and cells:
            ratios = " ".join(f"{g:g}" for g in grading["grading"])
            reduction = grading.get("cell_reduction", 0.0) * 100.0
            report(f"    Block grading:  ({ratios})")
            report(f"    Block cells:    {cells[0]}×{cells[1]}×{cells[2]} "
                   f"(uniform {uniform[0]}×{uniform[1]}×{uniform[2]}, "
                   f"-{reduction:.0f}% background cells)")
        elif mode == "explicit":
            ratios = " ".join(f"{g:g}" for g in grading["grading"])
            report(f"    Block grading:  ({ratios}) (explicit)")

    sizing = mesh.get("auto_size")
    if sizing:
        small = sizing.get("small_feature_m")
        small_txt = f"{small * 1000:.2f} mm" if small else "n/a"
        report("  Auto-sizing (feature-based):")
        report(f"    small feature:  {small_txt} "
               f"({sizing['feature_percentile']:g}th pct edge)")
        report(f"    finest surface: {sizing['finest_surface_cell_m'] * 1000:.2f} mm "
               f"(level {sizing['surface_level'][1]})")
        if sizing.get("capped"):
            report(f"    ⚠  capped at max_surface_level {sizing['max_surface_level']} — "
                   f"smallest features may be under-resolved")

    fangle = mesh.get("feature_angle")
    if fangle:
        report("  Feature angle (geometry-derived):")
        report(f"    sharpest crease: {fangle['sharpest_crease_normal_deg']:g}° normal angle "
               f"({fangle['crease_percentile']:g}th pct of {fangle['n_angles']:,} edges)")
        report(f"    resolveFeatureAngle: {fangle['resolveFeatureAngle']:g}°"
               + (f" (preset {fangle['preset_resolveFeatureAngle']:g}°)" if fangle.get("changed") else ""))

    if layer_resolution.get("first_layer_thickness") is not None:
        report("  Boundary layers:")
        if layer_resolution.get("u_tau"):
            report(f"    u_tau estimate: {layer_resolution['u_tau']:.3f} m/s")
        if layer_resolution.get("y_plus_target") is not None and layer_resolution.get("mode") == "absolute":
            report(f"    y+ target:      {layer_resolution['y_plus_target']:.3g} -> "
                   f"first layer {layer_resolution['first_layer_thickness'] * 1e6:.1f} um")
        else:
            report(f"    first layer:    {layer_resolution['first_layer_thickness'] * 1e6:.1f} um (absolute)")
        if layer_resolution.get("y_plus_effective") is not None:
            suffix = " (clamped by maxFaceThicknessRatio)" if layer_resolution.get("clamped") else ""
            report(f"    effective y+:   {layer_resolution['y_plus_effective']:.1f}{suffix}")
        # Only flag a *significant* shortfall: the flat-plate u_tau is itself
        # ~30-40% off, so a minor clamp is not worth alarming the user about.
        requested = layer_resolution.get("y_plus_target")
        effective = layer_resolution.get("y_plus_effective")
        if (layer_resolution.get("clamped") and requested is not None and effective is not None
                and effective < 0.75 * float(requested)):
            level = layer_resolution.get("clamp_level")
            cell_mm = float(layer_resolution.get("clamp_cell_m") or 0.0) * 1000.0
            report(f"    ⚠  y+ target {requested:g} not achievable: the level-{level} "
                   f"surface cell ({cell_mm:.2f} mm) floors it at "
                   f"y+ {effective:.1f}. Defeature, lower "
                   f"surface_level, or raise maxFaceThicknessRatio.")
        if layer_resolution.get("stack") is not None:
            report(f"    {cfg['layers'].get('n_layers')} layers, expansion "
                   f"{cfg['layers'].get('expansion_ratio')}, "
                   f"stack {layer_resolution['stack'] * 1000:.3f} mm")
        ground_state = "on" if cfg["layers"].get("ground_layers") else "off"
        ground_note = layer_resolution.get("ground_layers_note")
        report(f"    ground layers:  {ground_state}" + (f" ({ground_note})" if ground_note else ""))
        if (layer_resolution.get("min_thickness") is not None
                and layer_resolution.get("stack") is not None
                and layer_resolution["min_thickness"] > layer_resolution["stack"]):
            report("    WARNING: min_thickness exceeds the layer stack — snappyHexMesh will add 0 layers")

    div_u_scheme = cfg.get("schemes", {}).get("div_U", "bounded Gauss limitedLinear 1")

    case_dir = project_dir / CASE_DIR / cfg["case_name"]
    # Dry run — stop here
    if dry_run:
        report(f"\n  DRY RUN — would generate: {case_dir}")
        report(f"    Velocity:   {cfg['flow']['velocity']:.2f} m/s  U={vec_str(vel)}")
        report(f"    k={k:.5g}  ω={omega:.5g}  νt={nut:.5g}")
        report(f"    Surfaces:   {', '.join(stl_names)}")
        report(f"    Pipeline:   potentialFoam → simpleFoam ({end_time} iters, {div_u_scheme})")
        return None

    # Generate case
    report(f"\n{'='*60}")
    report(f"  Generating: {case_dir}")
    report(f"  Velocity: {cfg['flow']['velocity']:.2f} m/s | Cell: {mesh['base_cell_size']} m")
    report(f"  Surfaces: {', '.join(stl_names)}")
    report(f"  Pipeline: potentialFoam → simpleFoam ({end_time} iters, {div_u_scheme})")
    report(f"{'='*60}")

    # Create directories
    for d in ("0", "constant/triSurface", "system"):
        (case_dir / d).mkdir(parents=True, exist_ok=True)

    # Copy STL files
    report("\n  STL files:")
    tri_dir = case_dir / "constant" / "triSurface"
    for stem, path in stl_pairs:
        try:
            info = stl_info_map.get(stem)
            n_tri = copy_stl(path, tri_dir / f"{stem}.stl", stem, info=info)
            report(f"    ✓ {stem} ({n_tri:,} triangles)")
        except ValueError as e:
            report(f"    ✗ {stem} ERROR: {e}")
            raise CaseGenerationError(f"ERROR: {stem}: {e}")

    # Write all OpenFOAM files
    from rapidfoam.casegen.constants import write_constant
    from rapidfoam.casegen.fields import write_fields
    from rapidfoam.casegen.scripts import write_scripts
    from rapidfoam.casegen.solver import (
        write_control_dict,
        write_decompose_par_dict,
        write_fv_schemes,
        write_fv_solution,
    )

    emit_mesh_files(plan, build_mesh_context(cfg), case_dir)
    write_control_dict(cfg, case_dir)
    write_fv_schemes(cfg, case_dir)
    write_fv_solution(cfg, case_dir)
    write_decompose_par_dict(cfg, case_dir)
    write_constant(cfg, case_dir)
    write_fields(cfg, case_dir)
    write_scripts(cfg, case_dir)

    # Backup 0/ as 0.orig
    orig_dir = case_dir / "0.orig"
    if orig_dir.exists():
        shutil.rmtree(orig_dir)
    shutil.copytree(case_dir / "0", orig_dir)

    # Save config snapshot (for post-processing)
    (case_dir / "case_config.json").write_text(json.dumps(cfg, indent=2) + "\n")

    report(f"\n  ✓ Case: {case_dir}")
    report(f'    cd "{case_dir}" && ./Allrun.parallel')
    report("")
    return case_dir


__all__ = ["build_case", "CaseGenerationError"]
