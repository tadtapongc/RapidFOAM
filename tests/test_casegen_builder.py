"""casegen.builder tests (Phase 4).

``build_case`` is the shared generation entry point used by both the CLI and
the Web Studio. It returns the case directory, reports through its ``reporter``
callback, and raises ``CaseGenerationError`` (not ``SystemExit``) on failure.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.casegen.builder import CaseGenerationError, build_case
from rapidfoam.stl_utils import write_stl


class BuildCaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", [
            ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
        ])
        self.cfg = self.root / "config.json"
        self.cfg.write_text(json.dumps({
            "case_name": "builder_test",
            "stl_files": ["body.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }), encoding="utf-8")

    def test_build_case_generates_and_reports(self):
        lines: list[str] = []
        case = build_case(self.cfg, self.root, reporter=lines.append)
        self.assertIsNotNone(case)
        self.assertTrue((case / "system" / "snappyHexMeshDict").is_file())
        self.assertTrue((case / "case_config.json").is_file())
        self.assertTrue(any("Generating" in line for line in lines))

    def test_dry_run_writes_nothing(self):
        case = build_case(self.cfg, self.root, dry_run=True, reporter=lambda _: None)
        self.assertIsNone(case)
        self.assertFalse((self.root / "cases").exists())

    def test_missing_config_raises_typed_error(self):
        with self.assertRaises(CaseGenerationError):
            build_case(self.root / "nope.json", self.root, reporter=lambda _: None)

    def test_invalid_config_raises_typed_error(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({
            "case_name": "bad",
            "stl_files": ["body.stl"],
            "flow": {"velocity": "fast"},
        }), encoding="utf-8")
        with self.assertRaises(CaseGenerationError):
            build_case(bad, self.root, reporter=lambda _: None)


if __name__ == "__main__":
    unittest.main()
