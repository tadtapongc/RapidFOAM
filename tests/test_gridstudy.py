"""Tests for postproc.gridstudy (Richardson convergence / verdicts)."""

import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.postproc.gridstudy import (
    _richardson_p,
    configure_refinement_study,
    grid_study,
    refinement_variant_name,
)


def _write_case(root: Path, name: str, cd: float, cl: float, cells: int = 1_000_000):
    case = root / name
    coeff = case / "postProcessing" / "forceCoeffs" / "0"
    coeff.mkdir(parents=True, exist_ok=True)
    lines = ["# Time Cd Cl\n"]
    for i in range(1, 30):
        lines.append(f"{i} {cd} {cl}\n")
    (coeff / "coefficient.dat").write_text("".join(lines))
    import json
    (case / "case_config.json").write_text(json.dumps({
        "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        "mesh_params": {"block_cells": [cells // 1000000 or 1, 100, 10]},
    }))
    return case


def _write_unsolved_case(root: Path, name: str):
    """A generated cpl case that has not produced force/coefficient data yet."""
    import json
    case = root / name
    case.mkdir(parents=True, exist_ok=True)
    (case / "case_config.json").write_text(json.dumps({
        "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        "mesh_params": {"block_cells": [1, 100, 10]},
    }))
    return case


class GridStudyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_converged_when_std_fine_close(self):
        _write_case(self.root, "b_fast", cd=0.312, cl=1.44, cells=4_000_000)
        _write_case(self.root, "b_standard", cd=0.301, cl=1.40, cells=9_000_000)
        _write_case(self.root, "b_fine", cd=0.297, cl=1.39, cells=20_000_000)
        report = grid_study("b", cases_root=self.root)
        self.assertTrue(report["available"])
        self.assertEqual(report["verdict"], "grid-independent")
        self.assertTrue(report["converged"])
        self.assertLess(report["deltas"]["cd_std_fine_pct"], 3.0)
        self.assertIn("richardson", report)

    def test_not_converged_when_refinement_moves_forces(self):
        _write_case(self.root, "b_fast", cd=0.40, cl=2.0, cells=4_000_000)
        _write_case(self.root, "b_standard", cd=0.32, cl=1.6, cells=9_000_000)
        _write_case(self.root, "b_fine", cd=0.25, cl=1.2, cells=20_000_000)
        report = grid_study("b", cases_root=self.root)
        self.assertEqual(report["verdict"], "not-converged")
        self.assertFalse(report["converged"])

    def test_insufficient_when_fewer_than_three(self):
        _write_case(self.root, "b_fine", cd=0.297, cl=1.39, cells=20_000_000)
        _write_case(self.root, "b_standard", cd=0.30, cl=1.40, cells=9_000_000)
        report = grid_study("b", cases_root=self.root)
        self.assertFalse(report["available"])
        self.assertEqual(report["verdict"], "insufficient-data")

    def test_bare_base_resolves_as_standard(self):
        _write_case(self.root, "b_fast", cd=0.31, cl=1.44)
        _write_case(self.root, "b", cd=0.30, cl=1.40)          # bare == standard
        _write_case(self.root, "b_fine", cd=0.297, cl=1.39)
        report = grid_study("b", cases_root=self.root)
        self.assertTrue(report["available"])
        self.assertEqual(report["per_fidelity"]["standard"]["case"], "b")

    def test_richardson_handles_degenerate(self):
        self.assertIsNone(_richardson_p(0.3, 0.3, 0.3, 1.5))   # no change
        self.assertIsNone(_richardson_p(0.3, 0.2, 0.1, 1.0))   # r <= 1

    def test_convergence_stats_gci_ld_and_step_deltas(self):
        _write_case(self.root, "b_fast", cd=0.312, cl=1.44, cells=4_000_000)
        _write_case(self.root, "b_standard", cd=0.301, cl=1.40, cells=9_000_000)
        _write_case(self.root, "b_fine", cd=0.297, cl=1.39, cells=20_000_000)
        report = grid_study("b", cases_root=self.root)

        # Aero efficiency per level.
        self.assertIsNotNone(report["per_fidelity"]["fine"]["ld"])
        self.assertIn("ld_std_fine_pct", report["deltas"])

        # Per-step deltas (each level vs the previous), plus a monotonicity flag.
        self.assertIn("standard", report["step_deltas"])
        self.assertIn("fine", report["step_deltas"])
        self.assertGreater(report["step_deltas"]["fine"]["cd_pct"], 0)
        self.assertTrue(report["monotonic"]["cd"])

        # Roache GCI on the extrapolated value.
        self.assertIn("gci", report)
        self.assertEqual(report["gci"]["safety_factor"], 1.25)
        self.assertGreater(report["gci"]["cd_pct"], 0)

    def test_realised_mesh_cells_from_checkmesh(self):
        # block_cells (background grid) and the realised checkMesh count are
        # reported separately; only the former drives the refinement ratio.
        for name, cd, cl, cells, mesh in (
            ("m_fast", 0.312, 1.44, 4_000_000, 8_000_000),
            ("m_standard", 0.301, 1.40, 9_000_000, 18_000_000),
            ("m_fine", 0.297, 1.39, 20_000_000, 40_000_000),
        ):
            case = _write_case(self.root, name, cd=cd, cl=cl, cells=cells)
            (case / "log.checkMesh").write_text(f"Mesh stats\n    cells:      {mesh}\n")
        report = grid_study("m", cases_root=self.root)
        # block_cells is the small background grid; mesh_cells the realised count.
        self.assertEqual(report["per_fidelity"]["fine"]["cells"], 20000)
        self.assertEqual(report["per_fidelity"]["fine"]["mesh_cells"], 40_000_000)

    def test_partial_ladder_still_reports_step_deltas(self):
        _write_case(self.root, "q_cpl20", cd=0.312, cl=1.44, cells=20_000_000)
        _write_unsolved_case(self.root, "q_cpl30")
        _write_case(self.root, "q_cpl45", cd=0.297, cl=1.39, cells=45_000_000)
        report = grid_study("q", cases_root=self.root)
        self.assertFalse(report["available"])
        # cpl45's delta is measured against the previous *available* level (cpl20).
        self.assertIn("cpl45", report["step_deltas"])
        self.assertGreater(report["step_deltas"]["cpl45"]["cd_pct"], 0)

    def test_partial_refinement_data_degrades_instead_of_crashing(self):
        # A cpl study whose middle level has not produced force data yet (still
        # meshing/solving) must report insufficient-data, not raise KeyError.
        # Regression: the cpl branch passed every key to _finish_study, including
        # "available": False entries with no "cd"/"cl".
        _write_case(self.root, "r_cpl20", cd=0.312, cl=1.44, cells=20_000_000)
        _write_unsolved_case(self.root, "r_cpl30")
        _write_case(self.root, "r_cpl45", cd=0.297, cl=1.39, cells=45_000_000)
        report = grid_study("r", cases_root=self.root)
        self.assertEqual(report["mode"], "refinement")
        self.assertFalse(report["available"])
        self.assertEqual(report["verdict"], "insufficient-data")
        self.assertFalse(report["per_fidelity"]["cpl30"]["available"])


class RefinementStudyConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _base(self):
        import json
        base = {
            "case_name": "wing",
            "stl_files": ["wing.stl"],
            "fidelity": "standard",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "overrides": {"layers": {"y_plus_target": 30, "n_layers": 8}, "solver": {"end_time": 1500}},
        }
        p = self.root / "wing.json"
        p.write_text(json.dumps(base))
        return p

    def test_variants_only_change_cells_per_length(self):
        import json
        variants = configure_refinement_study(self._base(), out_dir=self.root)
        self.assertEqual([v["cells_per_length"] for v in variants], [20, 30, 45])
        cpls = {v["cells_per_length"] for v in variants}
        self.assertEqual(len(cpls), 3)
        for v in variants:
            cfg = json.loads(Path(v["config_path"]).read_text())
            self.assertEqual(cfg["overrides"]["layers"]["y_plus_target"], 30)
            self.assertEqual(cfg["overrides"]["layers"]["n_layers"], 8)
            self.assertEqual(cfg["overrides"]["solver"]["end_time"], 1500)
            self.assertEqual(cfg["flow"], {"velocity": 20.0, "direction": "-z", "ground": True})

    def test_variant_naming(self):
        self.assertEqual(refinement_variant_name("wing", 30), "wing_cpl30")

    def test_slurm_scaled_and_physics_pinned(self):
        import json
        variants = configure_refinement_study(self._base(), out_dir=self.root)
        times = []
        for v in variants:
            cfg = json.loads(Path(v["config_path"]).read_text())
            times.append(cfg["overrides"]["slurm"]["time"])
            self.assertEqual(cfg["overrides"]["slurm"]["mem_per_cpu"], cfg["overrides"]["slurm"]["mem_per_cpu"])
            self.assertEqual(cfg["overrides"]["layers"]["y_plus_target"], 30)
            self.assertEqual(cfg["overrides"]["layers"]["n_layers"], 8)
            self.assertEqual(cfg["overrides"]["solver"]["end_time"], 1500)
        self.assertEqual(times, ["04:00:00", "08:00:00", "14:00:00"])

    def test_co_refine_surface_steps_last_two(self):
        import json
        variants = configure_refinement_study(self._base(), out_dir=self.root, co_refine_surface=True)
        surfaces = [json.loads(Path(v["config_path"]).read_text())["overrides"]["mesh_params"].get("surface_level")
                    for v in variants]
        self.assertTrue(all(s is not None for s in surfaces))
        self.assertLess(surfaces[0][1], surfaces[-1][1])

    def test_grid_study_discovers_cpl_variants(self):
        import json
        variants = configure_refinement_study(self._base(), out_dir=self.root)
        # Fabricate solved results for the three variant names (pinned Cd/Cl).
        cds = {"cpl20": 0.312, "cpl30": 0.301, "cpl45": 0.297}
        cls = {"cpl20": 1.44, "cpl30": 1.40, "cpl45": 1.39}
        for v in variants:
            label = f"cpl{v['cells_per_length']}"
            case = self.root / v["name"]
            coeff = case / "postProcessing" / "forceCoeffs" / "0"
            coeff.mkdir(parents=True)
            coeff.joinpath("coefficient.dat").write_text(
                "# Time Cd Cl\n" + "".join(f"{i} {cds[label]} {cls[label]}\n" for i in range(1, 30))
            )
            (case / "case_config.json").write_text(json.dumps({
                "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
                "mesh_params": {"block_cells": [v["cells_per_length"], 100, 10]},
                "layers": {"y_plus_target": 30, "n_layers": 8},
                "solver": {"end_time": 1500},
            }))
        report = grid_study("wing", cases_root=self.root)
        self.assertEqual(report["mode"], "refinement")
        self.assertEqual(report["verdict"], "grid-independent")
        self.assertIn("cpl20", report["per_fidelity"])
        self.assertTrue(report["pinned_consistent"])

    def test_fidelity_preset_pins_when_base_has_no_overrides(self):
        # A Studio config with fidelity 'standard' and no explicit layers/solver
        # must pin the standard preset (y+30, 8 layers, 1500), not the global
        # DEFAULT_CONFIG values (which match fast: 5 layers, 800).
        import json
        from rapidfoam.meshing.presets import FIDELITY_PRESETS

        base = {
            "case_name": "wing",
            "stl_files": ["wing.stl"],
            "fidelity": "standard",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }
        p = self.root / "wing_bare.json"
        p.write_text(json.dumps(base))
        variants = configure_refinement_study(
            p, out_dir=self.root, preset=FIDELITY_PRESETS["standard"])
        for v in variants:
            cfg = json.loads(Path(v["config_path"]).read_text())
            self.assertEqual(cfg["overrides"]["layers"]["y_plus_target"], 30)
            self.assertEqual(cfg["overrides"]["layers"]["n_layers"], 8)
            self.assertEqual(cfg["overrides"]["solver"]["end_time"], 1500)

    def test_fast_preset_pins_when_base_has_no_overrides(self):
        # Symmetrically, fidelity 'fast' must pin 5 layers / 800, not standard.
        import json
        from rapidfoam.meshing.presets import FIDELITY_PRESETS

        base = {
            "case_name": "wing",
            "stl_files": ["wing.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }
        p = self.root / "wing_fast.json"
        p.write_text(json.dumps(base))
        variants = configure_refinement_study(
            p, out_dir=self.root, preset=FIDELITY_PRESETS["fast"])
        for v in variants:
            cfg = json.loads(Path(v["config_path"]).read_text())
            self.assertEqual(cfg["overrides"]["layers"]["n_layers"], 5)
            self.assertEqual(cfg["overrides"]["solver"]["end_time"], 800)

    def test_explicit_override_beats_preset(self):
        # A user's explicit layers/solver must win over the fidelity preset.
        import json
        from rapidfoam.meshing.presets import FIDELITY_PRESETS

        base = {
            "case_name": "wing",
            "stl_files": ["wing.stl"],
            "fidelity": "standard",
            "overrides": {"layers": {"n_layers": 12}, "solver": {"end_time": 3000}},
        }
        p = self.root / "wing_override.json"
        p.write_text(json.dumps(base))
        variants = configure_refinement_study(
            p, out_dir=self.root, preset=FIDELITY_PRESETS["standard"])
        for v in variants:
            cfg = json.loads(Path(v["config_path"]).read_text())
            self.assertEqual(cfg["overrides"]["layers"]["n_layers"], 12)
            self.assertEqual(cfg["overrides"]["solver"]["end_time"], 3000)

    def test_only_cells_per_length_differs_from_bare_base(self):
        import json
        from rapidfoam.meshing.presets import FIDELITY_PRESETS

        base = {
            "case_name": "wing",
            "stl_files": ["wing.stl"],
            "fidelity": "standard",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }
        p = self.root / "wing_diff.json"
        p.write_text(json.dumps(base))
        variants = configure_refinement_study(
            p, out_dir=self.root, preset=FIDELITY_PRESETS["standard"],
            slurm_scaling={"time": [], "mem_per_cpu": []})

        def flat(d, prefix=""):
            out = {}
            for k, val in d.items():
                if isinstance(val, dict):
                    out.update(flat(val, prefix + k + "."))
                else:
                    out[prefix + k] = val
            return out

        cfgs = [flat(json.loads(Path(v["config_path"]).read_text())) for v in variants]
        keys = set().union(*(c.keys() for c in cfgs))
        differing = {k for k in keys if len({json.dumps(c.get(k)) for c in cfgs}) > 1}
        self.assertEqual(differing, {"case_name", "overrides.mesh_params.cells_per_length"})


if __name__ == "__main__":
    unittest.main()
