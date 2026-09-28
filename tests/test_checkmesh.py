"""Unit tests for mesh-quality parsing and verification."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.config import load_config, validate
from rapidfoam.postproc.checkmesh import (
    QUALITY_TIERS,
    _config_has_symmetry,
    check_mesh_quality,
    checkmesh_targets_from_case,
    classify_value,
    find_checkmesh_logs,
    find_snappy_logs,
    mesh_quality_report,
    parse_boundary_patches,
    parse_checkmesh,
    parse_layer_coverage,
    read_checkmesh,
    read_layer_coverage,
    resolve_bands,
    verdict_bands_from_dict,
)
from rapidfoam.stl_utils import write_stl

BODY_TRIANGLES = [((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))]

EXTENDED_CHECKMESH = """\
    points:           9575251
    faces:            27582986
    cells:            9008844
Overall number of cells of each type:
    hexahedra:     8881583
    prisms:        15336
    wedges:        0
    pyramids:      0
    tet wedges:    203
    tetrahedra:    0
    polyhedra:     111722
Checking patch topology for multiply connected surfaces...
                   Patch    Faces   Points  Surface topology  Bounding box
                symmetry    40000    41196  ok (non-closed singly connected) (-5 -5 -5) (-5 5 5)
                geometry   124862   129704      ok (closed singly connected) (-0.5 -0.1 -0.26) (0.5 -0.04 0.26)
    Max aspect ratio = 20.738987 OK.
    Min volume = 2.8807331e-11. Max volume = 0.00012573372.  Total volume = 999.97737.
    Mesh non-orthogonality Max: 68.343121 average: 3.25209
    Max skewness = 2.3305211 OK.
    Face flatness (1 = flat, 0 = butterfly) : min = 0.70079485  average = 0.99999045
    Cell determinant (wellposedness) : minimum: 0.0054503059 average: 1.0409558
    Face interpolation weight : minimum: 0.060052616 average: 0.49565886
    Face volume ratio : minimum: 0.019349737 average: 0.98115324
  ***Concave cells (using face planes) found, number of cells: 61471
Failed 1 mesh checks.
"""

OPEN_PATCH_CHECKMESH = """\
    cells: 100
Checking patch topology for multiply connected surfaces...
                   Patch    Faces   Points  Surface topology  Bounding box
                geometry    124862   129704  ok (non-closed singly connected) (-0.5 -0.1 -0.26) (0.5 -0.04 0.26)
Mesh OK.
"""

GOOD_CHECKMESH = """\
Mesh stats
    points:           1000
    faces:            4000
    internal faces:   3800
    cells:            2000

Checking geometry...
    Max aspect ratio = 12.5 OK.
    Min volume = 1e-09. Max volume = 1e-06.  Total volume = 1.0.  Cell volumes OK.
    Mesh non-orthogonality Max: 45.2 average: 3.1
    Non-orthogonality check OK.
    Max skewness = 1.2 OK.

Mesh OK.
"""

FAILING_CHECKMESH = """\
    points:           9575251
    faces:            27582986
    internal faces:   26717334
    cells:            9008844
    Max aspect ratio = 20.738987 OK.
    Min volume = 2.8807331e-11. Max volume = 0.00012573372.  Total volume = 999.97737.  Cell volumes OK.
    Mesh non-orthogonality Max: 68.343121 average: 3.25209
    Non-orthogonality check OK.
    Max skewness = 2.3305211 OK.
   *There are 3858 faces with concave angles between consecutive edges. Max concave angle = 62.709327 degrees.
  ***Concave cells (using face planes) found, number of cells: 61471

Failed 1 mesh checks.
"""

LAYER_TABLE = """\
Some preamble
patch    faces    layers avg thickness[m]
                         near-wall overall
-----    -----    ------ --------- -------
geometry 124862   2      0.000391  0.000898

trailing text
"""

# snappyHexMesh's final per-patch table: faces, target layers, average (mesh)
# layers (fractional), overall thickness, thickness fraction [%].
FINAL_LAYER_TABLE = """\
patch             faces        layers        overall thickness
                           target   mesh     [m]       [%]
-----             -----    -----    ----     ---       ---
geometry          124862   3        2.84     0.00134   90.6
"""


class TestParseCheckmesh(unittest.TestCase):
    def test_parses_core_metrics(self):
        stats = parse_checkmesh(FAILING_CHECKMESH)
        self.assertEqual(stats["cells"], 9008844)
        self.assertAlmostEqual(stats["max_non_ortho"], 68.343121)
        self.assertAlmostEqual(stats["avg_non_ortho"], 3.25209)
        self.assertAlmostEqual(stats["max_skewness"], 2.3305211)
        self.assertAlmostEqual(stats["max_aspect_ratio"], 20.738987)
        self.assertEqual(stats["concave_faces"], 3858)
        self.assertAlmostEqual(stats["max_concave_angle"], 62.709327)
        self.assertEqual(stats["concave_cells"], 61471)
        self.assertEqual(stats["failed_checks"], 1)
        self.assertFalse(stats["ok"])

    def test_pass_sets_ok(self):
        stats = parse_checkmesh(GOOD_CHECKMESH)
        self.assertEqual(stats["cells"], 2000)
        self.assertEqual(stats["failed_checks"], 0)
        self.assertTrue(stats["ok"])

    def test_unrelated_text_returns_empty(self):
        self.assertEqual(parse_checkmesh("Adding layers...\nsome noise\n"), {})


class TestParseExtendedCheckmesh(unittest.TestCase):
    def test_averages_parsed(self):
        stats = parse_checkmesh(EXTENDED_CHECKMESH)
        self.assertAlmostEqual(stats["avg_non_ortho"], 3.25209)
        self.assertAlmostEqual(stats["avg_flatness"], 0.99999045)
        self.assertAlmostEqual(stats["avg_determinant"], 1.0409558)
        self.assertAlmostEqual(stats["avg_interp_weight"], 0.49565886)
        self.assertAlmostEqual(stats["avg_volume_ratio"], 0.98115324)

    def test_cell_types_parsed(self):
        stats = parse_checkmesh(EXTENDED_CHECKMESH)
        self.assertEqual(stats["cell_types"]["hexahedra"], 8881583)
        self.assertEqual(stats["cell_types"]["polyhedra"], 111722)


class TestParseBoundaryPatches(unittest.TestCase):
    def test_closure_flags(self):
        patches = parse_boundary_patches(EXTENDED_CHECKMESH)
        self.assertIn("geometry", patches)
        self.assertTrue(patches["geometry"]["closed"])
        self.assertEqual(patches["geometry"]["faces"], 124862)
        self.assertFalse(patches["symmetry"]["closed"])

    def test_kind_classification(self):
        result = check_mesh_quality(parse_checkmesh(OPEN_PATCH_CHECKMESH))
        self.assertIn("geometry", result["open_patches"])
        self.assertFalse(result["ok"])

    def test_domain_patches_not_flagged(self):
        result = check_mesh_quality(parse_checkmesh(EXTENDED_CHECKMESH))
        self.assertEqual(result["open_patches"], [])

    def test_ground_and_farfield_never_flagged(self):
        text = (
            "    cells: 10\n"
            "Checking patch topology for multiply connected surfaces...\n"
            "                   Patch    Faces   Points  Surface topology\n"
            "                  ground    15522    16614  ok (non-closed singly connected) (0 0 0) (1 1 1)\n"
            "                farField     5040     5422  ok (non-closed singly connected) (0 0 0) (1 1 1)\n"
            "Mesh OK.\n"
        )
        result = check_mesh_quality(parse_checkmesh(text))
        self.assertEqual(result["open_patches"], [])

    def test_symmetry_case_suppresses_open_patches(self):
        # A half model cut on symmetry is legitimately non-closed everywhere.
        result = check_mesh_quality(parse_checkmesh(OPEN_PATCH_CHECKMESH), has_symmetry=True)
        self.assertEqual(result["open_patches"], [])
        self.assertFalse(any("open (non-closed)" in i for i in result["issues"]))

    def test_full_model_still_flags_open_body_patch(self):
        result = check_mesh_quality(parse_checkmesh(OPEN_PATCH_CHECKMESH), has_symmetry=False)
        self.assertIn("geometry", result["open_patches"])


class TestConfigHasSymmetry(unittest.TestCase):
    def test_explicit_symmetry_plane(self):
        self.assertTrue(_config_has_symmetry({"symmetry_plane": 0.0}))
        self.assertTrue(_config_has_symmetry({"centerline": 0.0}))

    def test_domain_faces_symmetry_patch(self):
        cfg = {
            "patches": {"symmetry": "symmetry"},
            "domain_faces": {"-x": "symmetry", "+x": "farField"},
        }
        self.assertTrue(_config_has_symmetry(cfg))

    def test_no_symmetry(self):
        self.assertFalse(_config_has_symmetry({}))
        self.assertFalse(_config_has_symmetry({"symmetry_plane": None}))
        self.assertFalse(_config_has_symmetry({"domain_faces": {"-x": "farField", "+x": "farField"}}))


class TestParseLayerCoverage(unittest.TestCase):
    def test_parses_table(self):
        coverage = parse_layer_coverage(LAYER_TABLE)
        self.assertIn("geometry", coverage)
        self.assertEqual(coverage["geometry"]["layers"], 2)
        self.assertEqual(coverage["geometry"]["faces"], 124862)
        self.assertAlmostEqual(coverage["geometry"]["near_wall_thickness"], 0.000391)
        self.assertAlmostEqual(coverage["geometry"]["overall_thickness"], 0.000898)

    def test_later_table_overrides(self):
        text = LAYER_TABLE + LAYER_TABLE.replace("geometry 124862   2", "geometry 124862   3")
        coverage = parse_layer_coverage(text)
        self.assertEqual(coverage["geometry"]["layers"], 3)

    def test_unrelated_text_returns_empty(self):
        self.assertEqual(parse_layer_coverage("no tables here\n"), {})

    def test_final_table_keeps_fractional_layers(self):
        coverage = parse_layer_coverage(FINAL_LAYER_TABLE)
        entry = coverage["geometry"]
        # 2.84 must not be truncated to 2 (the historical bug).
        self.assertAlmostEqual(entry["layers"], 2.84)
        self.assertEqual(entry["target_layers"], 3)
        self.assertAlmostEqual(entry["coverage"], 2.84 / 3.0)
        self.assertAlmostEqual(entry["percent"], 90.6)


class TestFindLogs(unittest.TestCase):
    def test_finds_root_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "log.checkMesh").write_text(GOOD_CHECKMESH, encoding="utf-8")
            (root / "log.snappyHexMesh").write_text(LAYER_TABLE, encoding="utf-8")
            self.assertEqual(len(find_checkmesh_logs(root)), 1)
            self.assertEqual(len(find_snappy_logs(root)), 1)

    def test_falls_back_to_processor_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "processor0"
            proc.mkdir()
            (proc / "log.checkMesh").write_text(GOOD_CHECKMESH, encoding="utf-8")
            self.assertEqual(len(find_checkmesh_logs(root)), 1)

    def test_empty_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_checkmesh_logs(Path(tmp)), [])
            self.assertEqual(find_snappy_logs(Path(tmp)), [])


class TestReadLogs(unittest.TestCase):
    def test_read_checkmesh_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "log.checkMesh").write_text(GOOD_CHECKMESH, encoding="utf-8")
            stats = read_checkmesh(find_checkmesh_logs(root))
            self.assertEqual(stats["cells"], 2000)

    def test_read_layer_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "log.snappyHexMesh").write_text(LAYER_TABLE, encoding="utf-8")
            coverage = read_layer_coverage(find_snappy_logs(root))
            self.assertEqual(coverage["geometry"]["layers"], 2)


class TestCheckMeshQuality(unittest.TestCase):
    def test_ok(self):
        result = check_mesh_quality(parse_checkmesh(GOOD_CHECKMESH))
        self.assertTrue(result["available"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])

    def test_non_ortho_flagged(self):
        result = check_mesh_quality(parse_checkmesh(FAILING_CHECKMESH))
        self.assertFalse(result["ok"])
        self.assertTrue(any("non-orthogonality" in i for i in result["issues"]))
        self.assertTrue(any("concave cells" in i for i in result["issues"]))
        self.assertTrue(any("failed mesh check" in i for i in result["issues"]))

    def test_negative_volume_flagged(self):
        stats = dict(parse_checkmesh(GOOD_CHECKMESH))
        stats["min_volume"] = -1.0
        result = check_mesh_quality(stats)
        self.assertFalse(result["ok"])
        self.assertTrue(any("cell volume" in i for i in result["issues"]))

    def test_layer_dropout_flagged(self):
        layers = {"geometry": {"faces": 100, "layers": 1,
                               "near_wall_thickness": 1e-4, "overall_thickness": 2e-4}}
        result = check_mesh_quality(parse_checkmesh(GOOD_CHECKMESH), layers, target_layers=3)
        self.assertFalse(result["ok"])
        self.assertAlmostEqual(result["layers"]["geometry"]["coverage"], 1 / 3)
        self.assertTrue(any("dropout" in i for i in result["issues"]))

    def test_layer_coverage_ok(self):
        layers = {"geometry": {"faces": 100, "layers": 3,
                               "near_wall_thickness": 1e-4, "overall_thickness": 2e-4}}
        result = check_mesh_quality(parse_checkmesh(GOOD_CHECKMESH), layers, target_layers=3)
        self.assertTrue(result["ok"])

    def test_fractional_final_coverage_is_not_dropout(self):
        # snappy's 2.84/3 (~95%) must not be flagged as dropout, and the
        # fractional coverage must survive into the report.
        result = check_mesh_quality(
            parse_checkmesh(GOOD_CHECKMESH),
            parse_layer_coverage(FINAL_LAYER_TABLE),
            target_layers=3,
        )
        self.assertAlmostEqual(result["layers"]["geometry"]["coverage"], 2.84 / 3.0)
        self.assertFalse(any("dropout" in i for i in result["issues"]))
        self.assertTrue(result["ok"])

    def test_low_fractional_final_coverage_is_dropout(self):
        table = FINAL_LAYER_TABLE.replace("2.84     0.00134   90.6", "1.50     0.00070   50.0")
        result = check_mesh_quality(
            parse_checkmesh(GOOD_CHECKMESH),
            parse_layer_coverage(table),
            target_layers=3,
        )
        self.assertAlmostEqual(result["layers"]["geometry"]["coverage"], 0.5)
        self.assertTrue(any("dropout" in i for i in result["issues"]))
        self.assertFalse(result["ok"])

    def test_metrics_table_has_pass_flags(self):
        result = check_mesh_quality(parse_checkmesh(EXTENDED_CHECKMESH))
        by_key = {m["key"]: m for m in result["metrics"]}
        self.assertFalse(by_key["max_non_ortho"]["pass"])
        self.assertTrue(by_key["max_skewness"]["pass"])
        self.assertEqual(by_key["min_volume"]["limit_text"], "> 0")
        self.assertIn("concave_cells", by_key)

    def test_cell_type_fractions(self):
        result = check_mesh_quality(parse_checkmesh(EXTENDED_CHECKMESH))
        hexes = result["cell_types"]["hexahedra"]
        self.assertAlmostEqual(hexes["fraction"], 8881583 / sum(
            v["count"] for v in result["cell_types"].values()
        ), places=4)

    def test_unavailable_without_data(self):
        result = check_mesh_quality({})
        self.assertFalse(result["available"])

    def test_custom_thresholds(self):
        stats = dict(parse_checkmesh(GOOD_CHECKMESH))
        stats["max_non_ortho"] = 70.0
        self.assertTrue(check_mesh_quality(stats, max_non_ortho=75.0)["ok"])
        self.assertFalse(check_mesh_quality(stats, max_non_ortho=65.0)["ok"])


class TestClassifyValue(unittest.TestCase):
    def test_max_kind_bands(self):
        self.assertEqual(classify_value("max_non_ortho", 10.0), "good")
        self.assertEqual(classify_value("max_non_ortho", 65.0), "usable")
        self.assertEqual(classify_value("max_non_ortho", 75.0), "marginal")

    def test_min_kind_bands(self):
        self.assertEqual(classify_value("min_determinant", 0.5), "good")
        self.assertEqual(classify_value("min_determinant", 0.005), "usable")
        self.assertEqual(classify_value("min_determinant", 0.0001), "marginal")

    def test_zero_kind(self):
        self.assertEqual(classify_value("concave_cells", 0.0), "good")
        self.assertEqual(classify_value("concave_cells", 5.0), "marginal")

    def test_unknown_key_is_good(self):
        self.assertEqual(classify_value("not_a_metric", 9999.0), "good")


class TestVerdict(unittest.TestCase):
    def test_clean_mesh_is_good(self):
        result = check_mesh_quality(parse_checkmesh(GOOD_CHECKMESH))
        self.assertEqual(result["verdict"], "good")
        self.assertEqual(result["verdict_label"], "Good")

    def test_marginal_but_passing_is_not_good(self):
        # Non-orthogonality 65 passes the configured 65 limit but is > the 60
        # "good" band, so the verdict should be usable, not good.
        text = GOOD_CHECKMESH.replace(
            "Mesh non-orthogonality Max: 45.2 average: 3.1",
            "Mesh non-orthogonality Max: 65.0 average: 3.1",
        ).replace("Mesh OK.", "Mesh OK.")
        result = check_mesh_quality(parse_checkmesh(text))
        self.assertEqual(result["verdict"], "usable")

    def test_hard_failure_is_bad(self):
        result = check_mesh_quality(parse_checkmesh(FAILING_CHECKMESH))
        self.assertEqual(result["verdict"], "bad")
        self.assertTrue(result["note"].startswith("[Bad]"))

    def test_open_patch_forces_bad(self):
        result = check_mesh_quality(parse_checkmesh(OPEN_PATCH_CHECKMESH))
        self.assertEqual(result["verdict"], "bad")

    def test_layer_dropout_forces_bad(self):
        layers = {"geometry": {"faces": 100, "layers": 1, "coverage": 0.5}}
        result = check_mesh_quality(
            parse_checkmesh(GOOD_CHECKMESH), layers, target_layers=2
        )
        self.assertEqual(result["verdict"], "bad")

    def test_metric_entries_carry_level_and_bands(self):
        result = check_mesh_quality(parse_checkmesh(EXTENDED_CHECKMESH))
        by_key = {m["key"]: m for m in result["metrics"]}
        non_ortho = by_key["max_non_ortho"]  # 68.34: > 60 good band, <= 70 caution
        self.assertEqual(non_ortho["level"], "usable")
        self.assertEqual(non_ortho["good"], 60.0)
        self.assertEqual(non_ortho["caution"], 70.0)

    def test_tiers_cover_expected_keys(self):
        for key in ("max_non_ortho", "max_skewness", "max_aspect_ratio",
                    "min_determinant", "concave_cells"):
            self.assertIn(key, QUALITY_TIERS)


class TestVerdictBands(unittest.TestCase):
    def test_resolve_bands_defaults_when_empty(self):
        tiers = resolve_bands(None)
        self.assertEqual(tiers["max_non_ortho"][:2], (60.0, 70.0))
        self.assertEqual(tiers["max_non_ortho"][2], "max")

    def test_resolve_bands_overrides_good_and_caution(self):
        tiers = resolve_bands({"max_non_ortho": {"good": 30.0, "caution": 40.0}})
        self.assertEqual(tiers["max_non_ortho"][:2], (30.0, 40.0))
        # kind/label come from the built-in table, not the override
        self.assertEqual(tiers["max_non_ortho"][2], "max")

    def test_resolve_bands_ignores_unknown_and_malformed(self):
        tiers = resolve_bands({"nope": {"good": 1, "caution": 2}, "max_skewness": "x"})
        self.assertEqual(tiers["max_non_ortho"][:2], (60.0, 70.0))
        self.assertNotIn("nope", tiers)

    def test_override_changes_verdict(self):
        # 45 is "good" by default but "usable" with a tighter band.
        args = (parse_checkmesh(GOOD_CHECKMESH),)
        default = check_mesh_quality(*args)
        tight = check_mesh_quality(*args, bands={"max_non_ortho": {"good": 30.0, "caution": 40.0}})
        by_default = {m["key"]: m for m in default["metrics"]}
        by_tight = {m["key"]: m for m in tight["metrics"]}
        self.assertEqual(by_default["max_non_ortho"]["level"], "good")
        self.assertEqual(by_tight["max_non_ortho"]["level"], "marginal")

    def test_verdict_bands_from_dict(self):
        cfg = {"mesh_quality": {"verdict_bands": {
            "max_non_ortho": {"good": 50, "caution": 60},
            "bogus": {"good": 1},
        }}}
        bands = verdict_bands_from_dict(cfg)
        self.assertEqual(bands["max_non_ortho"], {"good": 50.0, "caution": 60.0})
        self.assertNotIn("bogus", bands)

    def test_default_config_carries_bands(self):
        from rapidfoam.config import DEFAULT_CONFIG
        bands = verdict_bands_from_dict(DEFAULT_CONFIG)
        for key in ("max_non_ortho", "max_skewness", "max_aspect_ratio", "concave_cells"):
            self.assertIn(key, bands)


class TestBandValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", BODY_TRIANGLES)

    def _errors(self, mesh_quality):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "bands_case",
            "stl_files": ["body.stl"],
            "mesh_quality": mesh_quality,
        }))
        errors, _ = validate(load_config(path), self.root)
        return errors

    def test_valid_bands_accepted(self):
        self.assertFalse(self._errors({"verdict_bands": {
            "max_non_ortho": {"good": 55, "caution": 65},
        }}))

    def test_partial_band_merges_with_defaults(self):
        # A lone 'good' deep-merges onto the default caution, so it stays valid.
        self.assertFalse(self._errors({"verdict_bands": {"max_non_ortho": {"good": 55}}}))

    def test_non_numeric_band_rejected(self):
        self.assertTrue(self._errors({"verdict_bands": {
            "max_non_ortho": {"good": "high", "caution": 65},
        }}))

    def test_non_object_band_rejected(self):
        self.assertTrue(self._errors({"verdict_bands": "tight"}))


class TestCheckmeshTargets(unittest.TestCase):
    def test_reads_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "case_config.json").write_text(json.dumps({
                "layers": {"n_layers": 3},
                "mesh_quality": {"maxNonOrtho": 70, "maxInternalSkewness": 5},
            }), encoding="utf-8")
            targets = checkmesh_targets_from_case(case_dir=case)
            self.assertEqual(targets["target_layers"], 3.0)
            self.assertEqual(targets["max_non_ortho"], 70.0)
            self.assertEqual(targets["max_skewness"], 5.0)

    def test_missing_config_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(checkmesh_targets_from_case(case_dir=Path(tmp)), {})


class TestMeshQualityReport(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "log.checkMesh").write_text(GOOD_CHECKMESH, encoding="utf-8")
            (case / "log.snappyHexMesh").write_text(LAYER_TABLE, encoding="utf-8")
            (case / "case_config.json").write_text(json.dumps({
                "layers": {"n_layers": 2},
            }), encoding="utf-8")
            report = mesh_quality_report(case_dir=case)
            self.assertTrue(report["available"])
            self.assertTrue(report["ok"])
            self.assertEqual(report["layers"]["geometry"]["layers"], 2)
            self.assertAlmostEqual(report["layers"]["geometry"]["coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
