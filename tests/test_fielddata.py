"""Tests for postproc.fielddata (fieldMinMax reduction)."""

import tempfile
import unittest
from pathlib import Path

from rapidfoam.postproc.fielddata import (
    find_field_min_max_files,
    parse_field_min_max,
    read_field_min_max,
)

FIELD_MIN_MAX = """\
# Field minima and maxima
# Time          	field           	min             	location(min)   	processor       	max             	location(max)   	processor
1	yPlus	0.00000000e+00	(2.5e-02 2.3e-01 -6.9e-01)	0	2.45205080e+02	(4.8e-01 -4.2e-02 1.5e-01)	7
400	yPlus	0.00000000e+00	(2.5e-02 2.3e-01 -6.9e-01)	0	8.01400000e+01	(4.0e-01 1.0e-01 -1.1e+00)	7
"""


class ParseFieldMinMaxTest(unittest.TestCase):
    def test_latest_row_wins_with_location(self):
        parsed = parse_field_min_max(FIELD_MIN_MAX)
        self.assertIn("yPlus", parsed)
        entry = parsed["yPlus"]
        self.assertEqual(entry["time"], 400.0)
        self.assertAlmostEqual(entry["min"], 0.0)
        self.assertAlmostEqual(entry["max"], 80.14)
        self.assertEqual(entry["location"], [0.4, 0.1, -1.1])

    def test_garbage_is_ignored(self):
        self.assertEqual(parse_field_min_max("not a field min max file\n"), {})


class ReadFilesTest(unittest.TestCase):
    def test_find_and_read_reductions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "postProcessing" / "fieldMinMax" / "400").mkdir(parents=True)
            (root / "postProcessing" / "fieldMinMax" / "400" / "fieldMinMax.dat").write_text(
                FIELD_MIN_MAX, encoding="utf-8"
            )

            fmm_files = find_field_min_max_files(root)
            self.assertEqual(len(fmm_files), 1)
            fmm = read_field_min_max(fmm_files)
            self.assertAlmostEqual(fmm["yPlus"]["max"], 80.14)


if __name__ == "__main__":
    unittest.main()
