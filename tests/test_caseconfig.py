"""core.caseconfig typed axis accessor + the single config read path.

The postproc readers used to load raw JSON (bypassing ``overrides``) with a
different precedence than every other consumer. They now go through
``caseconfig.read_case_config`` / ``caseconfig.axis_config`` like the mesh,
y+, and surface checks do.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.core import caseconfig
from rapidfoam.postproc.forces import axis_config_from_dict, load_axis_config


class AxisConfigTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(caseconfig.axis_config({}).as_tuple(), (2, -1, 1, -1, "-z", "-y"))
        self.assertEqual(caseconfig.axis_config(None).as_tuple(), (2, -1, 1, -1, "-z", "-y"))

    def test_outputs_section_wins(self):
        cfg = {"outputs": {"drag_axis": "+x", "downforce_axis": "-z"}}
        axes = caseconfig.axis_config(cfg)
        self.assertEqual((axes.drag_index, axes.drag_sign), (0, 1))
        self.assertEqual((axes.downforce_index, axes.downforce_sign), (2, -1))

    def test_top_level_fallback(self):
        cfg = {"drag_axis": "+y", "downforce_axis": "+z"}
        self.assertEqual(caseconfig.axis_config(cfg).as_tuple(), (1, 1, 2, 1, "+y", "+z"))

    def test_non_dict_sections_are_safe(self):
        self.assertEqual(caseconfig.axis_config({"outputs": []}).as_tuple(), (2, -1, 1, -1, "-z", "-y"))


class LoadAxisConfigReadPathTest(unittest.TestCase):
    """load_axis_config applies overrides and prefers the case snapshot."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = self.root / "cases" / "demo"
        self.case.mkdir(parents=True)

    def _write(self, path: Path, cfg: dict) -> None:
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_overrides_applied_from_case_snapshot(self):
        self._write(self.case / "case_config.json", {
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "overrides": {"outputs": {"drag_axis": "+x"}},
        })
        self.assertEqual(load_axis_config(case_dir=self.case), (0, 1, 1, -1, "+x", "-y"))

    def test_case_snapshot_wins_over_explicit_path(self):
        self._write(self.case / "case_config.json", {
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        })
        other = self.root / "other.json"
        self._write(other, {"outputs": {"drag_axis": "+x", "downforce_axis": "-y"}})
        self.assertEqual(
            load_axis_config(str(other), case_dir=self.case), (2, -1, 1, -1, "-z", "-y")
        )

    def test_explicit_path_used_without_snapshot(self):
        other = self.root / "other.json"
        self._write(other, {"overrides": {"outputs": {"drag_axis": "+x"}}})
        self.assertEqual(load_axis_config(str(other)), (0, 1, 1, -1, "+x", "-y"))

    def test_raw_dict_helper_matches_typed_accessor(self):
        self.assertEqual(axis_config_from_dict(None), (2, -1, 1, -1, "-z", "-y"))
        cfg = {"outputs": {"drag_axis": "+x"}}
        self.assertEqual(axis_config_from_dict(cfg), caseconfig.axis_config(cfg).as_tuple())


if __name__ == "__main__":
    unittest.main()
