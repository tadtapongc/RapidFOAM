"""Tests for postproc.fielddata (fieldMinMax / surfaceFieldValue reductions)."""

import tempfile
import unittest
from pathlib import Path

from rapidfoam.postproc.fielddata import (
    find_field_min_max_files,
    find_surface_field_value_files,
    parse_field_min_max,
    parse_surface_field_value,
    read_field_min_max,
    read_surface_field_value,
    surface_field_value_name,
)

FIELD_MIN_MAX = """\
# FieldMinMax fieldMinMax write:
# Time        	field	min        	max        	location
1	yPlus	0.0012	245.2	(1.2 0.3 -0.5)
400	yPlus	0.0009	80.14	(0.4 0.1 -1.1)
"""

SURFACE_VALUE = """\
# SurfaceFieldValue patch=geometry operation=max
# Time        	p
400	0.85
"""


class ParseFieldMinMaxTest(unittest.TestCase):
    def test_latest_row_wins_with_location(self):
        parsed = parse_field_min_max(FIELD_MIN_MAX)
        self.assertIn("yPlus", parsed)
        entry = parsed["yPlus"]
        self.assertEqual(entry["time"], 400.0)
        self.assertAlmostEqual(entry["min"], 0.0009)
        self.assertAlmostEqual(entry["max"], 80.14)
        self.assertEqual(entry["location"], [0.4, 0.1, -1.1])

    def test_garbage_is_ignored(self):
        self.assertEqual(parse_field_min_max("not a field min max file\n"), {})


class ParseSurfaceFieldValueTest(unittest.TestCase):
    def test_parses_operation_and_patch(self):
        parsed = parse_surface_field_value(SURFACE_VALUE, "wallPressure_max_geometry")
        self.assertEqual(parsed["operation"], "max")
        self.assertEqual(parsed["patch"], "geometry")
        self.assertAlmostEqual(parsed["value"], 0.85)
        self.assertEqual(parsed["time"], 400.0)

    def test_unknown_object_name_is_none(self):
        self.assertIsNone(parse_surface_field_value(SURFACE_VALUE, "someOtherObject"))


class ReadFilesTest(unittest.TestCase):
    def test_find_and_read_reductions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "postProcessing" / "fieldMinMax" / "400").mkdir(parents=True)
            (root / "postProcessing" / "fieldMinMax" / "400" / "fieldMinMax.dat").write_text(
                FIELD_MIN_MAX, encoding="utf-8"
            )
            svf = root / "postProcessing" / "wallPressure_min_geometry" / "400"
            svf.mkdir(parents=True)
            (svf / "surfaceFieldValue.dat").write_text(SURFACE_VALUE, encoding="utf-8")

            fmm_files = find_field_min_max_files(root)
            self.assertEqual(len(fmm_files), 1)
            fmm = read_field_min_max(fmm_files)
            self.assertAlmostEqual(fmm["yPlus"]["max"], 80.14)

            svf_files = find_surface_field_value_files(root)
            self.assertEqual(len(svf_files), 1)
            self.assertEqual(surface_field_value_name(svf_files[0]), "wallPressure_min_geometry")
            values = read_surface_field_value(svf_files)
            self.assertIn("min_geometry", values)
            self.assertAlmostEqual(values["min_geometry"]["value"], 0.85)


if __name__ == "__main__":
    unittest.main()
