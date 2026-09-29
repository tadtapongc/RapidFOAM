"""CLI entry points for case generation and post-processing."""

from __future__ import annotations

import argparse
import json
import logging
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
    """Entry-point wrapper around casegen.builder.build_case (keeps console UX)."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from rapidfoam.casegen.builder import CaseGenerationError, build_case

    try:
        build_case(cfg_path, project_dir, dry_run=dry_run)
    except CaseGenerationError as exc:
        message = str(exc)
        sys.exit(1 if message == "" else message)


# ============================================================
# FORCES CLI
# ============================================================

def _yplus_target_from_case(config_path: str | None, case_dir: Path) -> float | None:
    """Read layers.y_plus_target from the effective case config, if present."""
    from rapidfoam.core.caseconfig import yplus_target_from_case

    return yplus_target_from_case(config_path=config_path, case_dir=case_dir)


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
    parser.add_argument("--surface", action="store_true", help="Verify surface integrity (surfaceCheck log)")
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

    # Surface-integrity verification (independent of force data)
    if args.surface:
        from rapidfoam.postproc.surfacecheck import surface_check_report
        summary = surface_check_report(case_dir, args.config)
        if not summary.get("available"):
            sys.exit(f"No surfaceCheck log found in {case_dir}. Did the case run surfaceCheck?")

        def _sfmt(value: object, spec: str = ".4g") -> str | None:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return format(value, spec)
            return None

        stats = summary["stats"]
        print(f"\n  Surface integrity (surfaceCheck) — verdict: {summary.get('verdict_label', '')}")
        triangles = _sfmt(stats.get("triangles"), ",d")
        if triangles is not None:
            print(f"    triangles        {triangles}")
        vertices = _sfmt(stats.get("vertices"), ",d")
        if vertices is not None:
            print(f"    vertices         {vertices}")
        closed = summary.get("closed")
        if closed is not None:
            state = "closed" if closed else "open"
            if summary.get("has_symmetry") and closed is False:
                state += " (symmetry half model)"
            print(f"    surface          {state}")
        illegal = _sfmt(stats.get("illegal_triangles"), ",d")
        if illegal is not None:
            print(f"    illegal tris     {illegal}")
        if stats.get("self_intersection_checked"):
            print(f"    self-intersects  {'yes' if stats.get('self_intersecting') else 'no'}")
        parts = _sfmt(stats.get("unconnected_parts"), ",d")
        if parts is not None:
            print(f"    parts            {parts}")
        for issue in summary.get("issues", []):
            print(f"    ✗ {issue}")
        for warning in summary.get("warnings", []):
            print(f"    ⚠  {warning}")
        print(f"    {summary['note']}")
        sys.exit(0 if summary.get("ok") else 2)

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
            cov_txt = f" ({coverage * 100:.0f}% layers)" if isinstance(coverage, (int, float)) else ""
            thickness = info.get("thickness_fraction")
            th_txt = f", {thickness * 100:.0f}% thickness" if isinstance(thickness, (int, float)) else ""
            print(f"    layers {patch:<16} {achieved}{cov_txt}{th_txt}")
        for warning in summary.get("warnings", []):
            print(f"    ⚠  {warning}")
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
