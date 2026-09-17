"""Behavioral regression checks; run with python -m unittest discover -s tests."""
from __future__ import annotations

import ast
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.cli import _do_generate, _do_init
from rapidfoam.config import load_config, validate
from rapidfoam.geometry import compute_domain_box, face_assignments
from rapidfoam.postproc.forces import check_convergence, find_force_files, read_forces, is_symmetry_case
from rapidfoam.postproc.plotting import _force_stats, _rolling_average
from rapidfoam.postproc.residuals import read_residuals
from rapidfoam.postproc.convergence_monitor import monitor
from rapidfoam.stl_utils import copy_stl, stl_bounds, stl_info, write_stl
from rapidfoam.writers.scripts import _convergence_monitor_script


def force_row(time, drag, downforce=20):
    return f"{time} (0 {-downforce} {-drag}) (0 0 0) (0 0 0)\n"


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", [
            ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
        ])

    def config(self, **extra):
        path = self.root / "config.json"
        path.write_text(json.dumps({"case_name": "test_case", "stl_files": ["body.stl"], **extra}))
        return path

    def generate(self, **extra):
        path = self.config(**extra)
        with contextlib.redirect_stdout(io.StringIO()):
            _do_generate(path, self.root)
        return self.root / extra.get("case_dir", "cases") / "test_case"

    def test_missing_geometry_fails_before_creating_case(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            _do_generate(self.config(stl_files=["absent.stl", "body.stl"]), self.root)
        self.assertFalse((self.root / "cases").exists())

    def test_multiple_geometry_names_remain_paired(self):
        write_stl(self.root / "stl" / "wing.stl", "wing", [
            ((0, 0, 1), (10, 0, 0), (11, 0, 0), (10, 1, 3)),
        ])
        case = self.generate(stl_files=["wing.stl", "body.stl"])
        from rapidfoam.stl_utils import stl_bounds
        self.assertEqual(stl_bounds(case / "constant/triSurface/wing.stl")[0][0], 10)
        self.assertEqual(stl_bounds(case / "constant/triSurface/body.stl")[0][0], 0)

    def test_minimal_config_aligns_ground_and_symmetry(self):
        case = self.generate()
        cfg = json.loads((case / "case_config.json").read_text())
        self.assertEqual(cfg["domain_box"]["min"], [0, 0, -24])
        self.assertEqual(cfg["domain_faces"]["-x"], "symmetry")
        self.assertTrue(is_symmetry_case(case_dir=case))

    def test_overrides_and_custom_output_directory(self):
        case = self.generate(
            case_dir="custom", solver={"end_time": 123}, layers={"n_layers": 2},
            slurm={"time": "04:00:00"},
            mesh_params={"locationInMesh": [0.2, 0.3, 0.4], "maxLoadUnbalance": 0.25},
        )
        cfg = json.loads((case / "case_config.json").read_text())
        self.assertFalse((self.root / "cases").exists())
        self.assertEqual(cfg["solver"]["end_time"], 123)
        self.assertEqual(cfg["layers"]["n_layers"], 2)
        self.assertEqual(cfg["slurm"]["time"], "04:00:00")
        mesh = (case / "system/snappyHexMeshDict").read_text()
        self.assertIn("locationInMesh (0.2000 0.3000 0.4000)", mesh)
        self.assertIn("maxLoadUnbalance    0.25;", mesh)

    def test_scripts_have_linux_newlines_and_standalone_monitor(self):
        case = self.generate()
        for name in ("Allrun", "Allrun.parallel", "Allclean", "run.sh", "convergence_monitor.py"):
            with self.subTest(name=name):
                self.assertNotIn(b"\r", (case / name).read_bytes())
        monitor_code = (case / "convergence_monitor.py").read_text(encoding="utf-8")
        self.assertNotIn("from __future__ import annotations", monitor_code)
        parsed = ast.parse(monitor_code)
        self.assertFalse(any(isinstance(n, ast.AnnAssign) for n in ast.walk(parsed)))
        for n in ast.walk(parsed):
            if isinstance(n, ast.FunctionDef):
                self.assertIsNone(n.returns)
                for a in getattr(n.args, "posonlyargs", []) + n.args.args + n.args.kwonlyargs:
                    self.assertIsNone(a.annotation)
        namespace = runpy.run_path(str(case / "convergence_monitor.py"))
        self.assertEqual(namespace["MIN_ITERS"], 300)
        self.assertEqual(namespace["WINDOW"], 200)
        self.assertEqual(namespace["THRESHOLD"], 0.5)

    def test_init_preserves_existing_config(self):
        path = self.root / "configs/config.json"
        path.parent.mkdir()
        path.write_text("my configuration")
        with contextlib.redirect_stdout(io.StringIO()):
            _do_init(self.root)
        self.assertEqual(path.read_text(), "my configuration")

    def test_dry_run_does_not_write_case(self):
        with contextlib.redirect_stdout(io.StringIO()):
            _do_generate(self.config(), self.root, dry_run=True)
        self.assertFalse((self.root / "cases").exists())

    def test_invalid_configs_produce_validation_errors(self):
        examples = [
            {"flow": {"velocity": "fast"}}, {"flow": {"velocity": None}},
            {"flow": {"velocity": float("nan")}}, {"flow": []},
            {"fluid": {"nu": 0}}, {"mesh_params": {"base_cell_size": 0}},
            {"parallel": {"n_procs": 0}}, {"fidelity": "typo"}, {"fidelity": []},
            {"domain_box": {"min": [0], "max": [1]}},
            {"domain_faces": {"-x": "symmetry"}}, {"domain_faces": None},
            {"outputs": {"downforce_axis": "-z"}},
            {"case_name": "../elsewhere"}, {"stl_files": "body.stl"},
            {"stl_files": ["body.stl", "body.stl"]},
            {"mesh_params": {"surface_level": [5, 4]}},
        ]
        for example in examples:
            with self.subTest(example=example):
                errors, _ = validate(load_config(self.config(**example)), self.root)
                self.assertTrue(errors)

    def test_new_section_comments_are_filtered(self):
        cfg = load_config(self.config(mesh_params={"_comment": "ignore", "base_cell_size": 0.1}))
        self.assertNotIn("_comment", cfg["mesh_params"])

    def test_non_object_configuration_and_overrides_fail_cleanly(self):
        for value in ([], {"overrides": []}):
            path = self.root / "invalid.json"
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_explicit_and_implicit_domain_faces_agree(self):
        cfg = load_config(self.config())
        implicit = compute_domain_box(cfg, ((0, 0, 0), (1, 1, 3)))
        cfg["domain_faces"] = face_assignments(cfg)
        self.assertEqual(implicit, compute_domain_box(cfg, ((0, 0, 0), (1, 1, 3))))

    def test_custom_patch_names_have_correct_types_and_alignment(self):
        case = self.generate(patches={"ground": "road", "symmetry": "centre"},
                             domain_faces={"-x": "symmetry", "+x": "farField", "-y": "ground",
                                           "+y": "farField", "+z": "inlet", "-z": "outlet"})
        mesh = (case / "system/blockMeshDict").read_text()
        self.assertIn("road\n    {\n        type wall;", mesh)
        self.assertIn("centre\n    {\n        type symmetry;", mesh)
        self.assertTrue(is_symmetry_case(case_dir=case))
        self.assertIn("    road\n", (case / "0/U").read_text())

    def test_positive_ground_and_symmetry_faces(self):
        case = self.generate(ground_clearance=0.1, symmetry_plane=1,
                             domain_faces={"+x": "symmetry", "-x": "farField", "+y": "ground",
                                           "-y": "farField", "+z": "inlet", "-z": "outlet"})
        cfg = json.loads((case / "case_config.json").read_text())
        self.assertEqual(cfg["domain_box"]["max"][:2], [1, 1.1])
        self.assertEqual(cfg["domain_box"]["min"][:2], [-4, -4])
        for region in cfg["mesh_params"]["refinement_regions"]:
            self.assertEqual(region["max"][:2], [1, 1.11])
        self.assertIn("locationInMesh (-3.7500 -3.7450", (case / "system/snappyHexMeshDict").read_text())

    def test_explicit_ground_zero_is_absolute(self):
        cfg = load_config(self.config(ground_plane=0))
        self.assertEqual(compute_domain_box(cfg, ((0, 2, 0), (1, 3, 3)))["min"][1], 0)

    def test_restart_rows_and_malformed_rows(self):
        old = self.root / "old.dat"
        new = self.root / "new.dat"
        old.write_text(force_row(100, 10) + force_row(200, 5))
        new.write_text(force_row(100, 99) + "101 (0 -20 broken) (0 0 0) (0 0 0)\n"
                       + force_row(102, 100) + force_row(103, float("nan")))
        self.assertEqual(read_forces([old, new], 2, -1, 1, -1),
                         ([100.0, 102.0], [99.0, 100.0], [20.0, 20.0]))
        cfg = load_config(self.config())
        namespace = {"__name__": "standalone_test"}
        exec(_convergence_monitor_script(cfg), namespace)
        self.assertEqual(namespace["read_forces"]([old, new], 2, -1, 1, -1),
                         read_forces([old, new], 2, -1, 1, -1))

    def test_numeric_restart_discovery_and_processor_fallback(self):
        for name in ("10", "2"):
            path = self.root / "processor1/postProcessing/forces" / name / "force.dat"
            path.parent.mkdir(parents=True)
            path.write_text(force_row(100, int(name)))
        (self.root / "processor0/postProcessing/forces").mkdir(parents=True)
        files = find_force_files(self.root)
        self.assertEqual([p.parent.name for p in files], ["2", "10"])
        self.assertEqual(read_forces(files, 2, -1, 1, -1)[1], [10])

    def test_residual_restart_headers_and_partial_rows(self):
        old = self.root / "old.dat"
        new = self.root / "new.dat"
        old.write_text("# Time p_initial\n1 0.1\n2 0.2\n50 0.001\n")
        new.write_text("# Time p_initial Ux_initial\n2 0.02 0.3\n3 0.01 0.2\n4 0.005\n")
        data, headers = read_residuals([old, new])
        self.assertEqual(data["Time"], [1, 2, 3])
        self.assertEqual(data["p_initial"], [0.1, 0.02, 0.01])
        self.assertEqual(data["Ux_initial"], [None, 0.3, 0.2])
        self.assertIn("Ux_initial", headers)

    def test_plot_stats_do_not_claim_early_or_zero_mean_convergence(self):
        self.assertGreater(_force_stats([10])["pct"], 0.5)
        self.assertEqual(_force_stats([10])["last"], 10)
        self.assertGreater(_force_stats([-1, 1] * 20)["pct"], 0.5)
        self.assertEqual(_force_stats([10] * 20)["pct"], 0)

    def test_convergence_defaults_preserved(self):
        self.assertFalse(check_convergence([10] * 19, [20] * 19)[0])
        self.assertTrue(check_convergence([10] * 20, [20] * 20)[0])
        self.assertFalse(check_convergence([0] * 200, [0] * 200)[0])

    def test_monitor_reads_axes_from_target_case(self):
        case = self.root / "remote_case"
        (case / "system").mkdir(parents=True)
        (case / "system/controlDict").write_text("stopAt endTime;\n")
        (case / "case_config.json").write_text(json.dumps({"outputs": {"drag_axis": "+x", "downforce_axis": "-y"}}))
        path = case / "postProcessing/forces/0/force.dat"
        path.parent.mkdir(parents=True)
        path.write_text("".join(f"{t} (10 -20 0) (0 0 0) (0 0 0)\n" for t in range(300)))
        # A second poll would expose incorrect axes without hanging the test.
        with patch("rapidfoam.postproc.convergence_monitor.time.sleep", side_effect=[None, RuntimeError("second poll")]), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(monitor(case_dir=case))
        self.assertIn("writeNow", (case / "system/controlDict").read_text())

    def test_stl_utf8_encoding_and_replacement(self):
        path = self.root / "stl/utf8_wing.stl"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "solid wing_α\n"
            "  facet normal 0.0 0.0 1.0\n"
            "    outer loop\n"
            "      vertex 0.0 0.0 0.0\n"
            "      vertex 1.0 0.0 0.0\n"
            "      vertex 0.0 1.0 3.0\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid wing_α\n"
        )
        path.write_bytes(content.encode("utf-8"))
        from rapidfoam.stl_utils import read_stl
        name, triangles = read_stl(path)
        self.assertEqual(name, "wing_α")
        self.assertEqual(len(triangles), 1)

    def test_plotting_headless_manager_none(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib not installed")
        from rapidfoam.postproc.plotting import live_monitor
        with patch("matplotlib.pyplot.show"), patch(
            "rapidfoam.postproc.forces.load_axis_config", return_value=(2, -1, 1, -1, "-z", "-y")
        ):
            with patch("matplotlib.pyplot.subplots") as mock_subplots:
                import matplotlib.pyplot as plt
                fig = plt.Figure()
                fig.canvas.manager = None
                ax = fig.add_subplot(111)
                mock_subplots.return_value = (fig, ax)
                with patch("matplotlib.animation.FuncAnimation"), contextlib.redirect_stdout(io.StringIO()):
                    live_monitor(config_path=None, case_dir=self.root)

    def test_cli_override_with_non_dict_section(self):
        cfg_path = self.config(overrides={"custom_extension": "non_dict_override"})
        with contextlib.redirect_stdout(io.StringIO()):
            _do_generate(cfg_path, self.root)
        self.assertTrue((self.root / "cases/test_case").exists())

    def test_postproc_unification_and_shared_helpers(self):
        from rapidfoam.postproc.forces import (
            _dir_time as forces_dir_time,
            AXIS_MAP as F_AXIS_MAP,
            axis_index_sign as forces_axis_index_sign,
        )
        from rapidfoam.postproc.residuals import _dir_time as residuals_dir_time
        from rapidfoam.geometry import (
            AXIS_MAP as G_AXIS_MAP,
            axis_index_sign as geom_axis_index_sign,
        )
        self.assertIs(forces_dir_time, residuals_dir_time)
        self.assertIs(F_AXIS_MAP, G_AXIS_MAP)
        self.assertIs(forces_axis_index_sign, geom_axis_index_sign)

    def test_forces_restart_filtering_float_rounding(self):
        f = self.root / "float_forces.dat"
        f.write_text(
            "0.10000000 (0 -10 -20) (0 0 0) (0 0 0)\n"
            "0.20000000 (0 -10 -20) (0 0 0) (0 0 0)\n"
        )
        f_restart = self.root / "float_forces_restart.dat"
        f_restart.write_text(
            "0.20000000 (0 -15 -25) (0 0 0) (0 0 0)\n"
            "0.30000000 (0 -15 -25) (0 0 0) (0 0 0)\n"
        )
        times, drags, _ = read_forces([f, f_restart], 2, -1, 1, -1)
        self.assertEqual(len(times), 3)
        self.assertEqual(times, [0.1, 0.2, 0.3])
        self.assertEqual(drags, [20.0, 25.0, 25.0])

    def test_streaming_stl_bounds_and_info(self):
        path = self.root / "stl/precision.stl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "solid precision_body\n"
            "  facet normal 0.000000e+00 0.000000e+00 1.000000e+00\n"
            "    outer loop\n"
            "      vertex -1.250000e-01  2.500000e+00  3.750000e-02\n"
            "      vertex  1.000000e+01  0.000000e+00 -5.000000e-01\n"
            "      vertex  0.000000e+00 -1.000000e+01  4.000000e+00\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid precision_body\n"
        )
        name, count, bbox = stl_info(path)
        self.assertEqual(name, "precision_body")
        self.assertEqual(count, 1)
        self.assertEqual(bbox, ((-0.125, -10.0, -0.5), (10.0, 2.5, 4.0)))
        self.assertEqual(stl_bounds(path), bbox)

    def test_copy_stl_renaming_preserves_exact_lines(self):
        src = self.root / "stl/original.stl"
        dst = self.root / "stl/renamed.stl"
        src.write_text(
            "solid cad_export_name\n"
            "  facet normal 0 0 1\n"
            "    outer loop\n"
            "      vertex 1.234567890123456 2.345678901234567 3.456789012345678\n"
            "      vertex 4.567890123456789 5.678901234567890 6.789012345678901\n"
            "      vertex 7.890123456789012 8.901234567890123 9.012345678901234\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid cad_export_name\n"
        )
        n = copy_stl(src, dst, "renamed_wing")
        self.assertEqual(n, 1)
        lines = dst.read_text().splitlines()
        self.assertEqual(lines[0], "solid renamed_wing")
        self.assertEqual(lines[-1], "endsolid renamed_wing")
        # Middle lines must be preserved verbatim without any precision truncation
        self.assertIn("      vertex 1.234567890123456 2.345678901234567 3.456789012345678", lines)

    def test_large_stl_streaming_scalability(self):
        large_stl = self.root / "stl/large.stl"
        triangles = [
            ((0.0, 0.0, 1.0), (float(i), 0.0, 0.0), (float(i + 1), 1.0, 0.0), (float(i), 1.0, 2.0))
            for i in range(1000)
        ]
        write_stl(large_stl, "large_part", triangles)
        name, count, bbox = stl_info(large_stl)
        self.assertEqual(name, "large_part")
        self.assertEqual(count, 1000)
        self.assertEqual(bbox, ((0.0, 0.0, 0.0), (1000.0, 1.0, 2.0)))

    def test_rolling_average_math_and_empty(self):
        self.assertEqual(_rolling_average([]), [])
        vals = [10.0, 20.0, 30.0]
        self.assertEqual(_rolling_average(vals, window=2), [10.0, 15.0, 25.0])
        import random, statistics
        data = [random.uniform(50, 150) for _ in range(150)]
        expected = [
            statistics.mean(data[max(0, i - 40 + 1) : i + 1])
            for i in range(len(data))
        ]
        actual = _rolling_average(data, window=40)
        for e, a in zip(expected, actual):
            self.assertAlmostEqual(e, a, places=7)

    def test_performance_defaults_and_parallel_checkmesh(self):
        case = self.generate()
        cfg = json.loads((case / "case_config.json").read_text())
        self.assertEqual(cfg["linear_solvers"]["p"]["mergeLevels"], 2)
        self.assertEqual(cfg["relaxation"]["fields"]["p"], 0.7)
        self.assertEqual(cfg["relaxation"]["equations"]["U"], 0.7)
        snappy = (case / "system/snappyHexMeshDict").read_text()
        self.assertIn("maxLoadUnbalance    0.25;", snappy)
        allrun_parallel = (case / "Allrun.parallel").read_text()
        self.assertIn("runParallel checkMesh", allrun_parallel)
        run_sh = (case / "run.sh").read_text()
        self.assertIn("checkMesh -allGeometry -allTopology -noFunctionObjects -parallel", run_sh)

    def test_classic_force_layout_sums_pressure_and_viscous(self):
        path = self.root / "classic.dat"
        path.write_text(
            "# Time forces(pressure) forces(viscous) moments(pressure) moments(viscous)\n"
            "1 (0 -50 -100) (0 -1 -30) (0 0 0) (0 0 0)\n"
        )
        times, drags, downforces = read_forces([path], 2, -1, 1, -1)
        self.assertEqual(times, [1.0])
        self.assertEqual(drags, [130.0])
        self.assertEqual(downforces, [51.0])

    def test_esi_force_layout_uses_total_without_double_counting(self):
        path = self.root / "esi.dat"
        path.write_text(
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n"
            "1 0 -50 -130 0 -50 -100 0 0 -30\n"
        )
        _, drags, downforces = read_forces([path], 2, -1, 1, -1)
        self.assertEqual(drags, [130.0])
        self.assertEqual(downforces, [50.0])

    def test_multi_solid_stl_rename_merges_all_solids(self):
        src = self.root / "stl/multi.stl"
        dst = self.root / "stl/merged.stl"
        src.write_text(
            "solid a\n  facet normal 0 0 1\n    outer loop\n      vertex 0 0 0\n"
            "      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\nendsolid a\n"
            "solid b\n  facet normal 0 0 1\n    outer loop\n      vertex 0 0 0\n"
            "      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\nendsolid b\n"
        )
        self.assertEqual(copy_stl(src, dst, "merged"), 2)
        lines = dst.read_text().splitlines()
        self.assertEqual([ln for ln in lines if ln.startswith("solid")], ["solid merged", "solid merged"])
        self.assertEqual([ln for ln in lines if ln.startswith("endsolid")], ["endsolid merged", "endsolid merged"])

    def test_symmetry_plane_null_falls_back_to_centerline(self):
        cfg = load_config(self.config(symmetry_plane=None, centerline=0.25))
        box = compute_domain_box(cfg, ((0.25, 0, 0), (1, 1, 3)))
        self.assertEqual(box["min"][0], 0.25)

    def test_pitch_axis_default_convention_is_positive_x(self):
        from rapidfoam.writers.solver import write_control_dict

        cfg = load_config(self.config())
        cfg["stl_names"] = ["body"]
        case = self.root / "pitchcase"
        (case / "system").mkdir(parents=True)
        write_control_dict(cfg, case)
        self.assertIn("pitchAxis       (1 0 0);", (case / "system/controlDict").read_text())


if __name__ == "__main__":
    unittest.main()
