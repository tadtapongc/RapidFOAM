"""CLI entry points for case generation and post-processing."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def setup_main() -> None:
    """Entry point for cfd-setup command."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="Generate OpenFOAM case from STL + JSON config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    python setup_case.py --init                   Create starter project structure
    python setup_case.py configs/config.json      Generate case
    python setup_case.py configs/config.json -n   Preview only (dry run)
""",
    )
    parser.add_argument("config", nargs="?", help="JSON config file")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Preview settings without generating files")
    parser.add_argument("--init", action="store_true",
                        help="Create starter project structure")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="  %(message)s",
    )

    project_dir = Path.cwd()

    if args.init:
        _do_init(project_dir)
        return

    if not args.config:
        parser.print_help()
        sys.exit(1)

    _do_generate(Path(args.config), project_dir, dry_run=args.dry_run)


def _do_init(project_dir: Path) -> None:
    """Create starter project structure."""
    cfg_dir = project_dir / "configs"
    cfg_dir.mkdir(exist_ok=True)

    # Write minimal config
    example = {
        "case_name": "my_wing",
        "stl_files": ["my_geometry.STL"],
        "flow": {
            "velocity": 16.67,
            "direction": "-z",
            "ground": True,
        },
        "outputs": {
            "drag_axis": "-z",
            "downforce_axis": "-y",
        },
    }
    out_path = cfg_dir / "config.json"
    if not out_path.exists():
        out_path.write_text(json.dumps(example, indent=4) + "\n")
        print(f"\n  ✓ Created: {out_path}")
    else:
        print(f"\n  ℹ  Kept existing: {out_path}")

    (project_dir / "stl").mkdir(exist_ok=True)
    (project_dir / "cases").mkdir(exist_ok=True)

    print("  ✓ Created: stl/ and cases/")
    print("\n  Next steps:")
    print("    1. Place STL files in stl/")
    print("    2. Edit configs/config.json")
    print("    3. python setup_case.py configs/config.json")


def _do_generate(cfg_path: Path, project_dir: Path, dry_run: bool = False) -> None:
    """Generate a complete OpenFOAM case."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from rapidfoam.config import CASE_DIR, STL_DIR, find_stl, load_config, user_set, validate
    from rapidfoam.geometry import (
        compute_domain_box,
        compute_mesh_params,
        face_assignments,
        face_role,
        resolve_layers,
        turbulence_values,
        vec_str,
        velocity_vector,
    )
    from rapidfoam.stl_utils import EdgeStats, FeatureAngleStats, copy_stl, stl_analyze_full

    if not cfg_path.exists():
        sys.exit(f"ERROR: {cfg_path} not found")

    # Load and validate config
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        sys.exit(f"ERROR: {exc}")
    
    # Load raw config to correctly handle user overrides
    with open(cfg_path, encoding="utf-8") as f:
        raw_user = json.load(f)

    def _is_set(section: str, key: str) -> bool:
        return user_set(raw_user, section, key)

    print(f"  Config: {cfg_path}")

    errors, warnings = validate(cfg, project_dir)
    for w in warnings:
        print(f"  ⚠  {w}")
    if errors:
        print("\n  ERRORS:")
        for e in errors:
            print(f"    ✗ {e}")
        sys.exit(1)

    # Resolve STL names (strip extensions for OpenFOAM patch names)
    stl_dir = project_dir / STL_DIR
    stl_pairs: list[tuple[str, Path]] = []
    for name in cfg["stl_files"]:
        stem = name.rsplit(".", 1)[0] if "." in name else name
        path = find_stl(stl_dir, name)
        if path is None:
            sys.exit(f"ERROR: STL disappeared before generation: {name}")
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
            sys.exit(f"ERROR: {exc}")
        for i in range(3):
            all_min[i] = min(all_min[i], smin[i])
            all_max[i] = max(all_max[i], smax[i])

    if all_min[0] == float("inf"):
        sys.exit("ERROR: No valid STL files found — cannot compute domain")

    combined_bounds = (tuple(all_min), tuple(all_max))

    # Auto-compute domain box if requested or missing
    if cfg.get("domain_box") in ("auto", None) or not isinstance(cfg.get("domain_box"), dict):
        cfg["domain_box"] = compute_domain_box(cfg, combined_bounds)
    if any(lo >= hi for lo, hi in zip(cfg["domain_box"]["min"], cfg["domain_box"]["max"])):
        sys.exit("ERROR: Derived domain has nonpositive dimensions; check ground and symmetry planes")

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
                print(f"  ℹ  STL crosses symmetry plane: {axis_labels[i]}_min "
                      f"({clearance_min:.3f} m) — geometry will be cut at symmetry boundary")
            else:
                print(f"  ⚠  STL penetrates outside domain: "
                      f"{axis_labels[i]}_min ({clearance_min:.3f} m)")
        elif min_face_type == "ground":
            print(f"  ℹ  Ground plane: {axis_labels[i]} = {box['min'][i]:.3f} m "
                  f"(ground clearance: {clearance_min * 1000:.1f} mm)")
        elif clearance_min < min_clearance and min_face_type != "symmetry":
            print(f"  ⚠  STL very close to domain boundary: "
                  f"{axis_labels[i]}_min (clearance: {clearance_min:.3f} m)")

        if clearance_max < -1e-4:
            print(f"  ⚠  STL penetrates outside domain: "
                  f"{axis_labels[i]}_max ({clearance_max:.3f} m)")
        elif clearance_max < min_clearance and max_face_type not in ("symmetry", "ground"):
            print(f"  ⚠  STL very close to domain boundary: "
                  f"{axis_labels[i]}_max (clearance: {clearance_max:.3f} m)")

    # Derive mesh parameters from geometry (bounds + feature statistics)
    cfg["mesh_params"] = compute_mesh_params(
        cfg, combined_bounds, feature_stats=edge_stats, angle_stats=angle_stats,
        explicit_feature_angle=_is_set("feature_extract", "includedAngle"),
    )
    # Apply fidelity presets conditionally
    from rapidfoam.geometry import FIDELITY_PRESETS
    fidelity = cfg.get("fidelity", "standard")
    preset = FIDELITY_PRESETS.get(fidelity, FIDELITY_PRESETS["standard"])
    
    if not _is_set("solver", "end_time"):
        cfg["solver"]["end_time"] = preset["end_time"]
    if not _is_set("layers", "n_layers"):
        cfg["layers"]["n_layers"] = preset["n_layers"]
    if not _is_set("layers", "expansion_ratio"):
        cfg["layers"]["expansion_ratio"] = preset["expansion_ratio"]
    if "y_plus_target" in preset:
        if not _is_set("layers", "y_plus_target") and not _is_set("layers", "first_layer_thickness"):
            cfg["layers"]["y_plus_target"] = preset["y_plus_target"]
    elif not _is_set("layers", "first_layer_thickness"):
        cfg["layers"]["first_layer_thickness"] = preset["first_layer_thickness"]
    if not _is_set("layers", "nLayerIter"):
        cfg["layers"]["nLayerIter"] = preset.get("nLayerIter", 50)
    if not _is_set("layers", "nRelaxIter"):
        cfg["layers"]["nRelaxIter"] = preset.get("nRelaxIter_layers", 10)
    if not _is_set("layers", "ground_layers"):
        cfg["layers"]["ground_layers"] = preset.get("ground_layers", False)
    if not _is_set("solver", "write_interval"):
        cfg["solver"]["write_interval"] = preset["write_interval"]
    if not _is_set("snap", "nSolveIter"):
        cfg["snap"]["nSolveIter"] = preset.get("nSolveIter", 200)
    if not _is_set("snap", "nFeatureSnapIter"):
        cfg["snap"]["nFeatureSnapIter"] = preset.get("nFeatureSnapIter", 15)
        
    # Only apply preset SLURM time if user left it as default 'auto'
    if cfg["slurm"]["time"] == "auto":
        cfg["slurm"]["time"] = preset.get("slurm_time", "04:00:00")
    if not _is_set("slurm", "mem_per_cpu"):
        cfg["slurm"]["mem_per_cpu"] = preset.get("slurm_mem_per_cpu", cfg["slurm"]["mem_per_cpu"])

    layer_resolution = resolve_layers(
        cfg,
        combined_bounds,
        explicit_first_layer=_is_set("layers", "first_layer_thickness"),
        explicit_min_thickness=_is_set("layers", "min_thickness"),
    )
    if layer_resolution.get("y_plus_target") is not None and _is_set("layers", "first_layer_thickness"):
        print("  ⚠  layers.first_layer_thickness overrides layers.y_plus_target")

    # Derived values for display
    k, omega, nut = turbulence_values(cfg)
    vel = velocity_vector(cfg)
    mesh = cfg["mesh_params"]
    end_time = cfg["solver"]["end_time"]

    print("\n  Geometry bounds:")
    print(f"    min: ({all_min[0]:.3f}, {all_min[1]:.3f}, {all_min[2]:.3f})")
    print(f"    max: ({all_max[0]:.3f}, {all_max[1]:.3f}, {all_max[2]:.3f})")
    print("  Domain box:")
    box = cfg["domain_box"]
    print(f"    min: ({box['min'][0]:.3f}, {box['min'][1]:.3f}, {box['min'][2]:.3f})")
    print(f"    max: ({box['max'][0]:.3f}, {box['max'][1]:.3f}, {box['max'][2]:.3f})")
    print("  Mesh:")
    print(f"    Base cell:      {mesh['base_cell_size']} m")
    print(f"    Surface level:  {mesh['surface_level']}")
    print(f"    Edge level:     {mesh['edge_level']}")
    dist_levels = mesh.get('distance_levels', [])
    if dist_levels:
        shells = ", ".join(f"{d*1000:.0f}mm→L{l}" for d, l in dist_levels)
        print(f"    Distance shells: {shells}")
    for r in mesh.get("refinement_regions", []):
        print(f"    Region {r['name']}: Level {r['level']}")

    sizing = mesh.get("auto_size")
    if sizing:
        small = sizing.get("small_feature_m")
        small_txt = f"{small * 1000:.2f} mm" if small else "n/a"
        print("  Auto-sizing (feature-based):")
        print(f"    small feature:  {small_txt} "
              f"({sizing['feature_percentile']:g}th pct edge)")
        print(f"    finest surface: {sizing['finest_surface_cell_m'] * 1000:.2f} mm "
              f"(level {sizing['surface_level'][1]})")
        if sizing.get("capped"):
            print(f"    ⚠  capped at max_surface_level {sizing['max_surface_level']} — "
                  f"smallest features may be under-resolved")

    fangle = mesh.get("feature_angle")
    if fangle:
        print("  Feature angle (geometry-derived):")
        print(f"    sharpest crease: {fangle['sharpest_crease_normal_deg']:g}° normal angle "
              f"({fangle['crease_percentile']:g}th pct of {fangle['n_angles']:,} edges)")
        print(f"    resolveFeatureAngle: {fangle['resolveFeatureAngle']:g}°"
              + (f" (preset {fangle['preset_resolveFeatureAngle']:g}°)" if fangle.get("changed") else ""))

    if layer_resolution.get("first_layer_thickness") is not None:
        print("  Boundary layers:")
        if layer_resolution.get("u_tau"):
            print(f"    u_tau estimate: {layer_resolution['u_tau']:.3f} m/s")
        if layer_resolution.get("y_plus_target") is not None and layer_resolution.get("mode") == "absolute":
            print(f"    y+ target:      {layer_resolution['y_plus_target']:.3g} -> "
                  f"first layer {layer_resolution['first_layer_thickness'] * 1e6:.1f} um")
        else:
            print(f"    first layer:    {layer_resolution['first_layer_thickness'] * 1e6:.1f} um (absolute)")
        if layer_resolution.get("y_plus_effective") is not None:
            suffix = " (clamped by maxFaceThicknessRatio)" if layer_resolution.get("clamped") else ""
            print(f"    effective y+:   {layer_resolution['y_plus_effective']:.1f}{suffix}")
        if layer_resolution.get("stack") is not None:
            print(f"    {cfg['layers'].get('n_layers')} layers, expansion "
                  f"{cfg['layers'].get('expansion_ratio')}, "
                  f"stack {layer_resolution['stack'] * 1000:.3f} mm")
        ground_state = "on" if cfg["layers"].get("ground_layers") else "off"
        ground_note = layer_resolution.get("ground_layers_note")
        print(f"    ground layers:  {ground_state}" + (f" ({ground_note})" if ground_note else ""))
        if (layer_resolution.get("min_thickness") is not None
                and layer_resolution.get("stack") is not None
                and layer_resolution["min_thickness"] > layer_resolution["stack"]):
            print("    WARNING: min_thickness exceeds the layer stack — snappyHexMesh will add 0 layers")

    div_u_scheme = cfg.get("schemes", {}).get("div_U", "bounded Gauss limitedLinear 1")

    case_dir = project_dir / CASE_DIR / cfg["case_name"]
    # Dry run — stop here
    if dry_run:
        print(f"\n  DRY RUN — would generate: {case_dir}")
        print(f"    Velocity:   {cfg['flow']['velocity']:.2f} m/s  U={vec_str(vel)}")
        print(f"    k={k:.5g}  ω={omega:.5g}  νt={nut:.5g}")
        print(f"    Surfaces:   {', '.join(stl_names)}")
        print(f"    Pipeline:   potentialFoam → simpleFoam ({end_time} iters, {div_u_scheme})")
        return

    # Generate case
    print(f"\n{'='*60}")
    print(f"  Generating: {case_dir}")
    print(f"  Velocity: {cfg['flow']['velocity']:.2f} m/s | Cell: {mesh['base_cell_size']} m")
    print(f"  Surfaces: {', '.join(stl_names)}")
    print(f"  Pipeline: potentialFoam → simpleFoam ({end_time} iters, {div_u_scheme})")
    print(f"{'='*60}")

    # Create directories
    for d in ("0", "constant/triSurface", "system"):
        (case_dir / d).mkdir(parents=True, exist_ok=True)

    # Copy STL files
    print("\n  STL files:")
    tri_dir = case_dir / "constant" / "triSurface"
    for stem, path in stl_pairs:
        try:
            info = stl_info_map.get(stem)
            n_tri = copy_stl(path, tri_dir / f"{stem}.stl", stem, info=info)
            print(f"    ✓ {stem} ({n_tri:,} triangles)")
        except ValueError as e:
            print(f"    ✗ {stem} ERROR: {e}")
            sys.exit(1)

    # Write all OpenFOAM files
    from rapidfoam.writers.constants import write_constant
    from rapidfoam.writers.fields import write_fields
    from rapidfoam.writers.mesh import (
        write_block_mesh_dict,
        write_snappy_hex_mesh_dict,
        write_surface_feature_extract_dict,
    )
    from rapidfoam.writers.scripts import write_scripts
    from rapidfoam.writers.solver import (
        write_control_dict,
        write_decompose_par_dict,
        write_fv_schemes,
        write_fv_solution,
    )

    write_block_mesh_dict(cfg, case_dir)
    write_surface_feature_extract_dict(cfg, case_dir)
    write_snappy_hex_mesh_dict(cfg, case_dir)
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

    print(f"\n  ✓ Case: {case_dir}")
    print(f'    cd "{case_dir}" && ./Allrun.parallel')
    print()


# ============================================================
# FORCES CLI
# ============================================================

def _yplus_target_from_case(config_path: str | None, case_dir: Path) -> float | None:
    """Read layers.y_plus_target from a config or case_config.json, if present."""
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    candidates.append(case_dir / "case_config.json")
    candidates.append(Path("case_config.json"))
    for path in candidates:
        try:
            if not path.is_file():
                continue
            with open(path, encoding="utf-8") as handle:
                cfg = json.load(handle)
            layers = cfg.get("layers", {})
            target = layers.get("y_plus_target")
            if target is not None and float(target) > 0:
                return float(target)
            # Fall back to a resolved value written by the generator.
            resolved = layers.get("_resolved", {})
            t = resolved.get("y_plus_target")
            if t is not None and float(t) > 0:
                return float(t)
        except (OSError, ValueError, TypeError):
            continue
    return None


def forces_main() -> None:
    """Entry point for cfd-forces command."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="OpenFOAM force post-processing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "case",
        nargs="?",
        default=None,
        help="Case directory or name (default: current directory, or auto-detect from cases/)",
    )
    parser.add_argument("--config", "-c", default=None, help="Config JSON for axis info")
    parser.add_argument("--plot", "-p", action="store_true", help="Convergence plot")
    parser.add_argument("--save", "-s", action="store_true", help="Save plot as PNG")
    parser.add_argument("--live", "-l", action="store_true", help="Real-time monitor")
    parser.add_argument("--compare", action="store_true", help="Multi-case comparison")
    parser.add_argument("--check", action="store_true", help="Exit 0 if converged, 1 if not")
    parser.add_argument("--yplus", action="store_true", help="Verify near-wall y+ against the target")
    parser.add_argument("--mesh", action="store_true", help="Verify mesh quality (checkMesh + boundary layers)")
    parser.add_argument("--interval", "-i", type=float, default=3, help="Live update interval (s)")
    args = parser.parse_args()

    from rapidfoam.postproc.compare import compare_cases
    from rapidfoam.postproc.forces import (
        check_convergence,
        find_force_files,
        is_symmetry_case,
        load_axis_config,
        print_summary,
        read_forces,
    )
    from rapidfoam.postproc.plotting import live_monitor, plot_forces

    # Compare mode
    if args.compare:
        compare_cases()
        return

    # Resolve target case directory
    case_dir: Path
    if args.case:
        p = Path(args.case)
        if p.is_dir():
            case_dir = p
        elif (Path("cases") / args.case).is_dir():
            case_dir = Path("cases") / args.case
        else:
            sys.exit(f"ERROR: Case directory '{args.case}' not found.")
    else:
        cwd = Path.cwd()
        if (
            (cwd / "postProcessing").exists()
            or (cwd / "case_config.json").exists()
            or (cwd / "system" / "controlDict").exists()
        ):
            case_dir = cwd
        elif (cwd / "cases").is_dir():
            candidates = sorted(
                [d for d in (cwd / "cases").iterdir() if d.is_dir() and not d.name.startswith(".")],
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                case_dir = candidates[0]
            else:
                case_dir = cwd
        else:
            case_dir = cwd

    # Live mode
    if args.live:
        live_monitor(args.config, args.interval, case_dir=case_dir)
        return

    # Standard modes
    drag_idx, drag_sign, df_idx, df_sign, drag_axis, df_axis = load_axis_config(
        args.config, case_dir=case_dir
    )

    # y+ verification (independent of force data)
    if args.yplus:
        from rapidfoam.postproc.yplus import (
            check_yplus_target,
            find_yplus_files,
            read_yplus,
        )
        yp_files = find_yplus_files(case_dir)
        data = read_yplus(yp_files)
        target = _yplus_target_from_case(args.config, case_dir)
        summary = check_yplus_target(data, target)
        if not summary.get("available"):
            sys.exit(f"No yPlus output found in {case_dir}. Did the case run yPlus function object?")
        print("\n  Near-wall y+ verification"
              + (f" (target {target:g})" if target else ""))
        for patch, stats in sorted(summary["patches"].items()):
            avg = stats["average"] if stats["average"] is not None else float("nan")
            lo = stats["min"] if stats["min"] is not None else float("nan")
            hi = stats["max"] if stats["max"] is not None else float("nan")
            print(f"    {patch:<24} min {lo:>7.2f}  max {hi:>8.2f}  avg {avg:>7.2f}  [{stats['status']}]")
        print(f"    {summary['note']}")
        sys.exit(0 if not summary.get("off_target") else 2)

    # Mesh-quality verification (independent of force data)
    if args.mesh:
        from rapidfoam.postproc.checkmesh import mesh_quality_report
        summary = mesh_quality_report(case_dir, args.config)
        if not summary.get("available"):
            sys.exit(f"No checkMesh log found in {case_dir}. Did the case mesh?")

        def _fmt(value: object, spec: str = ".4g") -> str | None:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return format(value, spec)
            return None

        stats = summary["stats"]
        verdict_label = summary.get("verdict_label", "")
        print(f"\n  Mesh quality (checkMesh) — verdict: {verdict_label}")
        cells = _fmt(stats.get("cells"), ",d")
        if cells is not None:
            print(f"    cells            {cells}")
        nonortho = _fmt(stats.get("max_non_ortho"))
        if nonortho is not None:
            avg = _fmt(stats.get("avg_non_ortho"))
            suffix = f"  (avg {avg})" if avg is not None else ""
            print(f"    max non-ortho    {nonortho}{suffix}")
        skew = _fmt(stats.get("max_skewness"))
        if skew is not None:
            print(f"    max skewness     {skew}")
        aspect = _fmt(stats.get("max_aspect_ratio"))
        if aspect is not None:
            print(f"    max aspect ratio {aspect}")
        concave = _fmt(stats.get("concave_cells"), ",d")
        if concave is not None:
            print(f"    concave cells    {concave}")
        failed = stats.get("failed_checks")
        if isinstance(failed, int) and not isinstance(failed, bool):
            print(f"    failed checks    {failed}")
        ranked = [m for m in summary.get("metrics", []) if m.get("level") in ("usable", "marginal")]
        for metric in ranked:
            tag = "borderline" if metric["level"] == "usable" else "poor"
            print(f"    {metric['label']}: {metric['value']:g} ({tag})")
        for patch, info in sorted(summary["layers"].items()):
            achieved = info.get("layers", "?")
            coverage = info.get("coverage")
            cov_txt = f" ({coverage * 100:.0f}%)" if isinstance(coverage, (int, float)) else ""
            print(f"    layers {patch:<16} {achieved}{cov_txt}")
        print(f"    {summary['note']}")
        sys.exit(0 if summary.get("ok") else 2)

    files = find_force_files(case_dir)
    if not files:
        sys.exit(f"ERROR: No force.dat found in {case_dir}. Run from inside the case directory or specify case path.")

    times, drags, downforces = read_forces(files, drag_idx, drag_sign, df_idx, df_sign)

    # Check mode
    if args.check:
        conv, dp, fp, da, fa = check_convergence(drags, downforces)
        if conv:
            print(f"CONVERGED: drag={da:.3f}N df={fa:.3f}N")
            sys.exit(0)
        else:
            print(f"NOT CONVERGED: drag ±{dp:.3f}% df ±{fp:.3f}%")
            sys.exit(1)

    # Summary
    is_sym = is_symmetry_case(args.config, case_dir=case_dir)
    print_summary(times, drags, downforces, drag_axis, df_axis, is_symmetry=is_sym)

    # Near-wall y+ note (silent unless there is output to report)
    from rapidfoam.postproc.yplus import check_yplus_target, find_yplus_files, read_yplus
    yp_summary = check_yplus_target(
        read_yplus(find_yplus_files(case_dir)),
        _yplus_target_from_case(args.config, case_dir),
    )
    if yp_summary.get("available"):
        print(f"\n  Near-wall y+: {yp_summary['note']}")

    # Plot
    if args.plot or args.save:
        plot_forces(times, drags, downforces, drag_axis, df_axis, save=args.save)
