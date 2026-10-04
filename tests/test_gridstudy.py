"""Tests for postproc.gridstudy (Richardson convergence / verdicts)."""

import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.postproc.gridstudy import _richardson_p, grid_study


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


if __name__ == "__main__":
    unittest.main()
