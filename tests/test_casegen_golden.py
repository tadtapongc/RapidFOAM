"""Golden case-generation tests (Phase 0 safety net).

Generates a fixed case from a fixed STL and compares the emitted OpenFOAM
dictionaries and run scripts against committed snapshots in ``tests/golden/``.
This is the contract that the writer extraction in Phases 3–5 must preserve.

The snapshots were generated from the current (pre-refactor) code; a diff is a
behaviour-change decision, not an automatic accept.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.cli import _do_generate
from rapidfoam.stl_utils import write_stl

GOLDEN = Path(__file__).resolve().parent / "golden"

TEXT_FILES = {
    "system/blockMeshDict": "blockMeshDict.golden",
    "system/snappyHexMeshDict": "snappyHexMeshDict.golden",
    "system/surfaceFeatureExtractDict": "surfaceFeatureExtractDict.golden",
    "Allrun.parallel": "Allrun.parallel.golden",
    "run.sh": "run.sh.golden",
}


class CasegenGoldenTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", [
            ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
        ])
        cfg = {
            "case_name": "golden_test",
            "stl_files": ["body.stl"],
            "fidelity": "standard",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }
        path = self.root / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            _do_generate(path, self.root)
        self.case = self.root / "cases" / "golden_test"

    def test_generated_dicts_match_golden(self):
        for rel, golden_name in TEXT_FILES.items():
            with self.subTest(file=rel):
                actual = (self.case / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
                expected = (GOLDEN / golden_name).read_text(encoding="utf-8")
                self.assertEqual(expected, actual)

    def test_case_config_matches_golden(self):
        actual = json.loads((self.case / "case_config.json").read_text(encoding="utf-8"))
        expected = json.loads((GOLDEN / "case_config.golden.json").read_text(encoding="utf-8"))
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
