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


if __name__ == "__main__":
    unittest.main()
