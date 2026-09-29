"""Unit tests for surface-integrity parsing and verification."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.config import load_config, validate
from rapidfoam.postproc.surfacecheck import (
    check_surface,
    find_surfacecheck_logs,
    parse_surfacecheck,
    read_surfacecheck,
    surface_check_policy_from_dict,
    surface_check_report,
)
from rapidfoam.stl_utils import write_stl

BODY_TRIANGLES = [((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))]

GOOD_SURFACE = """\
Reading surface from "constant/triSurface/body.stl" ...

Statistics:
Triangles    : 95112
Vertices     : 47558
Bounding Box : (-0.5 -0.1 -0.26) (0.5 -0.04 0.26)

Region\tSize
------\t----
body\t95112

Surface has no illegal triangles.

Triangle quality (equilateral=1, collapsed=0):
    0 .. 0.05  : 0
    0.05 .. 0.1  : 0
    min 0.35 for triangle 1234
    max 1 for triangle 5678

Edges:
    min 0.0012 for edge 123 points (0 0 0)(0.0012 0 0)
    max 0.42 for edge 456 points (0 0 0)(0.42 0 0)

Checking for points less than 1e-6 of bounding box ((0.5 0.1 0.26) metre) apart.
Found 0 nearby points.

Surface is closed. All edges connected to two faces.

Number of unconnected parts : 1

Number of zones (connected area with consistent normal) : 1

Checking self-intersection.
Surface is not self-intersecting

End
"""

OPEN_SURFACE = """\
Reading surface from "constant/triSurface/half.stl" ...

Statistics:
Triangles    : 21000
Vertices     : 10600
Bounding Box : (0 -0.1 -0.26) (0.5 -0.04 0.26)

Region\tSize
------\t----
half\t21000

Surface has no illegal triangles.

Surface is not closed since not all edges connected to two faces:
    connected to one face : 420
    connected to >2 faces : 0
Conflicting face labels:420
Dumping conflicting face labels to "problemFaces"
Paste this into the input for surfaceSubset

Number of unconnected parts : 1

Number of zones (connected area with consistent normal) : 1

Checking self-intersection.
Surface is not self-intersecting

End
"""

SELF_INTERSECTING_SURFACE = """\
Reading surface from "constant/triSurface/body.stl" ...

Statistics:
Triangles    : 95112
Vertices     : 47558

Surface has no illegal triangles.

Surface is closed. All edges connected to two faces.

Number of unconnected parts : 1

Number of zones (connected area with consistent normal) : 1

Checking self-intersection.
Surface is self-intersecting at 7 locations.
Writing intersection points to selfInterPoints.obj

End
"""

ILLEGAL_SURFACE = """\
Reading surface from "constant/triSurface/body.stl" ...

Statistics:
Triangles    : 95112
Vertices     : 47558

Surface has 3 illegal triangles.
Dumping conflicting face labels to "illegalFaces"
Paste this into the input for surfaceSubset

Surface is closed. All edges connected to two faces.

Number of unconnected parts : 1

Number of zones (connected area with consistent normal) : 1
"""

MULTIPART_SURFACE = """\
Reading surface from "constant/triSurface/body.stl" ...

Statistics:
Triangles    : 95112
Vertices     : 47558

Surface has no illegal triangles.

Surface is closed. All edges connected to two faces.

Number of unconnected parts : 3

Number of zones (connected area with consistent normal) : 2
More than one normal orientation.

Checking self-intersection.
Surface is not self-intersecting

End
"""

NO_SELF_CHECK = """\
Surface has no illegal triangles.
Surface is closed. All edges connected to two faces.
Number of unconnected parts : 1
Number of zones (connected area with consistent normal) : 1
End
"""


class TestParseSurfacecheck(unittest.TestCase):
    def test_parses_core_metrics(self):
        stats = parse_surfacecheck(GOOD_SURFACE)
        self.assertEqual(stats["triangles"], 95112)
        self.assertEqual(stats["vertices"], 47558)
        self.assertEqual(stats["illegal_triangles"], 0)
        self.assertIs(stats["closed"], True)
        self.assertEqual(stats["unconnected_parts"], 1)
        self.assertEqual(stats["normal_zones"], 1)
        self.assertTrue(stats["consistent_normals"])
        self.assertIs(stats["self_intersection_checked"], True)
        self.assertIs(stats["self_intersecting"], False)
        self.assertEqual(stats["self_intersection_locations"], 0)
        self.assertAlmostEqual(stats["min_quality"], 0.35)
        self.assertAlmostEqual(stats["max_quality"], 1.0)
        self.assertAlmostEqual(stats["min_edge"], 0.0012)
        self.assertAlmostEqual(stats["max_edge"], 0.42)

    def test_bounding_box(self):
        stats = parse_surfacecheck(GOOD_SURFACE)
        self.assertEqual(stats["bounding_box"]["min"], [-0.5, -0.1, -0.26])
        self.assertEqual(stats["bounding_box"]["max"], [0.5, -0.04, 0.26])

    def test_region_table(self):
        stats = parse_surfacecheck(OPEN_SURFACE)
        self.assertEqual(stats["regions"], {"half": 21000})

    def test_open_reports_edge_counts(self):
        stats = parse_surfacecheck(OPEN_SURFACE)
        self.assertIs(stats["closed"], False)
        self.assertEqual(stats["single_edges"], 420)
        self.assertEqual(stats["multi_edges"], 0)

    def test_self_intersection_count(self):
        stats = parse_surfacecheck(SELF_INTERSECTING_SURFACE)
        self.assertIs(stats["self_intersecting"], True)
        self.assertEqual(stats["self_intersection_locations"], 7)

    def test_illegal_triangles(self):
        stats = parse_surfacecheck(ILLEGAL_SURFACE)
        self.assertEqual(stats["illegal_triangles"], 3)

    def test_inconsistent_normals(self):
        stats = parse_surfacecheck(MULTIPART_SURFACE)
        self.assertEqual(stats["unconnected_parts"], 3)
        self.assertIs(stats["consistent_normals"], False)

    def test_self_check_absent_is_not_claimed(self):
        stats = parse_surfacecheck(NO_SELF_CHECK)
        self.assertNotIn("self_intersection_checked", stats)
        self.assertNotIn("self_intersecting", stats)

    def test_garbage_returns_empty(self):
        self.assertEqual(parse_surfacecheck("not a surfaceCheck log"), {})
        self.assertEqual(parse_surfacecheck(""), {})


class TestCheckSurface(unittest.TestCase):
    def test_clean_is_good(self):
        result = check_surface(parse_surfacecheck(GOOD_SURFACE))
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "good")
        self.assertEqual(result["verdict_label"], "Good")

    def test_unavailable_when_empty(self):
        result = check_surface({})
        self.assertFalse(result["available"])
        self.assertEqual(result["verdict"], "unknown")

    def test_open_without_symmetry_is_bad(self):
        result = check_surface(parse_surfacecheck(OPEN_SURFACE))
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "bad")
        self.assertTrue(any("not closed" in issue for issue in result["issues"]))

    def test_open_with_symmetry_is_ok(self):
        result = check_surface(parse_surfacecheck(OPEN_SURFACE), has_symmetry=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "good")
        self.assertIn("open", result["note"])
        self.assertFalse(result["issues"])

    def test_allow_open_is_ok(self):
        result = check_surface(parse_surfacecheck(OPEN_SURFACE), allow_open=True)
        self.assertTrue(result["ok"])

    def test_self_intersection_is_bad(self):
        result = check_surface(parse_surfacecheck(SELF_INTERSECTING_SURFACE))
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "bad")

    def test_illegal_triangles_are_bad(self):
        result = check_surface(parse_surfacecheck(ILLEGAL_SURFACE))
        self.assertFalse(result["ok"])
        self.assertTrue(any("illegal" in issue for issue in result["issues"]))

    def test_illegal_threshold_tolerates(self):
        result = check_surface(
            parse_surfacecheck(ILLEGAL_SURFACE), max_illegal_triangles=3
        )
        self.assertTrue(result["ok"])

    def test_multipart_and_normals_are_concerns(self):
        result = check_surface(parse_surfacecheck(MULTIPART_SURFACE))
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "concern")
        self.assertGreaterEqual(len(result["warnings"]), 2)


class TestSurfaceCheckPolicy(unittest.TestCase):
    def test_policy_from_dict(self):
        policy = surface_check_policy_from_dict({
            "surface_check": {
                "allow_open": True,
                "max_illegal_triangles": 2,
                "max_unconnected_parts": 4,
            }
        })
        self.assertEqual(policy, {
            "allow_open": True,
            "max_illegal_triangles": 2,
            "max_unconnected_parts": 4,
        })

    def test_policy_ignores_malformed(self):
        policy = surface_check_policy_from_dict({
            "surface_check": {"max_illegal_triangles": "lots", "allow_open": "yes"}
        })
        self.assertEqual(policy, {"allow_open": True})

    def test_policy_empty_without_section(self):
        self.assertEqual(surface_check_policy_from_dict({}), {})


class TestSurfaceCheckReport(unittest.TestCase):
    def test_finds_and_merges_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "log.surfaceCheck").write_text(GOOD_SURFACE, encoding="utf-8")
            (case / "log.surfaceCheck.1").write_text(OPEN_SURFACE, encoding="utf-8")
            logs = find_surfacecheck_logs(case)
            self.assertEqual(len(logs), 2)
            merged = read_surfacecheck(logs)
            self.assertIs(merged["closed"], False)  # later file wins

    def test_end_to_end_open_symmetry_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "log.surfaceCheck").write_text(OPEN_SURFACE, encoding="utf-8")
            (case / "case_config.json").write_text(
                json.dumps({"symmetry_plane": 0.0}), encoding="utf-8"
            )
            report = surface_check_report(case_dir=case)
            self.assertTrue(report["available"])
            self.assertTrue(report["ok"])
            self.assertTrue(report["has_symmetry"])

    def test_end_to_end_open_without_symmetry_bad(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "log.surfaceCheck").write_text(OPEN_SURFACE, encoding="utf-8")
            report = surface_check_report(case_dir=case)
            self.assertFalse(report["ok"])
            self.assertFalse(report["has_symmetry"])

    def test_end_to_end_missing_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = surface_check_report(case_dir=Path(tmp))
            self.assertFalse(report["available"])


class TestSurfaceCheckValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", BODY_TRIANGLES)

    def _errors(self, surface_check):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "surface_case",
            "stl_files": ["body.stl"],
            "surface_check": surface_check,
        }))
        errors, _ = validate(load_config(path), self.root)
        return errors

    def test_valid_section_accepted(self):
        self.assertFalse(self._errors({
            "enabled": True,
            "check_self_intersection": False,
            "allow_open": True,
            "max_illegal_triangles": 2,
            "max_unconnected_parts": 3,
        }))

    def test_non_boolean_rejected(self):
        self.assertTrue(self._errors({"enabled": "yes"}))

    def test_negative_int_rejected(self):
        self.assertTrue(self._errors({"max_illegal_triangles": -1}))

    def test_default_config_carries_section(self):
        from rapidfoam.config import DEFAULT_CONFIG
        section = DEFAULT_CONFIG["surface_check"]
        self.assertTrue(section["enabled"])
        self.assertFalse(section["enforce"])  # report-only by default
        self.assertTrue(section["check_self_intersection"])


if __name__ == "__main__":
    unittest.main()
