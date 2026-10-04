"""Unit tests for feature-based mesh auto-sizing.

Covers the streaming edge statistics (EdgeStats / stl_analyze) and their use
in compute_mesh_params to widen snappy surface/edge refinement for small
geometry features.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.config import DEFAULT_CONFIG, deep_merge, load_config, validate
from rapidfoam.meshing.params import compute_mesh_params
from rapidfoam.meshing.presets import FIDELITY_PRESETS
from rapidfoam.meshing.sizing import apply_mesh_regions, resolve_per_surface_levels
from rapidfoam.geometry.stl import (
    EdgeStats,
    FeatureAngleStats,
    _dihedral_angle_deg,
    stl_analyze,
    stl_analyze_full,
    stl_info,
    write_stl,
)

BOUNDS = ((-0.745, 0.0, -2.961), (0.745, 1.145, 0.296))
BODY_TRIANGLES = [((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))]


class TestEdgeStats(unittest.TestCase):
    def test_min_max_and_count(self):
        stats = EdgeStats()
        for length in (0.001, 0.01, 0.1, 1.0, 10.0):
            stats.add(length)
        self.assertEqual(stats.n_edges, 5)
        self.assertAlmostEqual(stats.min_edge, 0.001)
        self.assertAlmostEqual(stats.max_edge, 10.0)

    def test_degenerate_edges_ignored(self):
        stats = EdgeStats()
        stats.add(0.0)
        stats.add(-1.0)
        stats.add(float("nan"))
        self.assertEqual(stats.n_edges, 0)
        self.assertEqual(stats.n_degenerate, 3)

    def test_percentile_is_bounded_by_data(self):
        stats = EdgeStats()
        for length in (0.001, 0.01, 0.1, 1.0, 10.0):
            stats.add(length)
        self.assertAlmostEqual(stats.percentile(0), stats.min_edge)
        self.assertGreaterEqual(stats.percentile(50), 0.1)
        self.assertGreaterEqual(stats.percentile(100), stats.max_edge)

    def test_empty_percentile_is_zero(self):
        self.assertEqual(EdgeStats().percentile(5), 0.0)

    def test_merge_accumulates(self):
        a = EdgeStats()
        a.add(0.1)
        b = EdgeStats()
        b.add(0.001)
        a.merge(b)
        self.assertEqual(a.n_edges, 2)
        self.assertAlmostEqual(a.min_edge, 0.001)
        self.assertAlmostEqual(a.max_edge, 0.1)

    def test_add_triangle_measures_three_edges(self):
        stats = EdgeStats()
        stats.add_triangle((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 4.0, 0.0))
        self.assertEqual(stats.n_edges, 3)
        self.assertAlmostEqual(stats.min_edge, 3.0)
        self.assertAlmostEqual(stats.max_edge, 5.0)


class TestStlAnalyze(unittest.TestCase):
    def test_bbox_count_and_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tri.stl"
            write_stl(path, "tri", [((0, 0, 1), (0, 0, 0), (3, 0, 0), (0, 4, 0))])
            name, n_triangles, bbox, stats = stl_analyze(path)
            self.assertEqual(name, "tri")
            self.assertEqual(n_triangles, 1)
            self.assertEqual(bbox, ((0, 0, 0), (3, 4, 0)))
            self.assertEqual(stats.n_edges, 3)
            self.assertAlmostEqual(stats.min_edge, 3.0)
            self.assertAlmostEqual(stats.max_edge, 5.0)

    def test_stl_info_matches_analyze(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tri.stl"
            write_stl(path, "tri", [((0, 0, 1), (0, 0, 0), (3, 0, 0), (0, 4, 0))])
            info = stl_info(path)
            name, n_triangles, bbox, _ = stl_analyze(path)
            self.assertEqual(info, (name, n_triangles, bbox))

    def test_multiple_triangles_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "two.stl"
            write_stl(path, "two", [
                ((0, 0, 1), (0, 0, 0), (3, 0, 0), (0, 4, 0)),
                ((0, 0, 1), (10, 0, 0), (13, 0, 0), (10, 4, 0)),
            ])
            _, n_triangles, bbox, stats = stl_analyze(path)
            self.assertEqual(n_triangles, 2)
            self.assertEqual(bbox, ((0, 0, 0), (13, 4, 0)))
            self.assertEqual(stats.n_edges, 6)


class TestFeatureSizing(unittest.TestCase):
    def _cfg(self, preset="standard", **mesh):
        cfg = deep_merge(DEFAULT_CONFIG, {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "outputs": {"downforce_axis": "-y"},
            "fidelity": preset,
        })
        base = {"auto_size": True}
        base.update(mesh)
        cfg["mesh_params"] = base
        return cfg

    def _stats(self, length, count=50):
        stats = EdgeStats()
        for _ in range(count):
            stats.add(length)
        return stats

    def test_small_feature_raises_surface_level(self):
        params = compute_mesh_params(self._cfg(), BOUNDS, feature_stats=self._stats(0.01))
        self.assertIn("auto_size", params)
        self.assertEqual(params["auto_size"]["required_level"], 6)
        self.assertEqual(params["surface_level"], [4, 6])
        self.assertEqual(params["edge_level"], 6)
        self.assertFalse(params["auto_size"]["capped"])

    def test_cap_limits_refinement(self):
        params = compute_mesh_params(self._cfg(), BOUNDS, feature_stats=self._stats(0.0005))
        info = params["auto_size"]
        self.assertGreater(info["required_level"], info["max_surface_level"])
        self.assertTrue(info["capped"])
        self.assertEqual(params["surface_level"][1], FIDELITY_PRESETS["standard"]["max_surface_level"])
        self.assertEqual(params["edge_level"], FIDELITY_PRESETS["standard"]["max_surface_level"])

    def test_auto_size_can_be_disabled(self):
        params = compute_mesh_params(
            self._cfg(auto_size=False), BOUNDS, feature_stats=self._stats(0.0005)
        )
        self.assertNotIn("auto_size", params)
        self.assertEqual(params["surface_level"], list(FIDELITY_PRESETS["standard"]["surface_level"]))

    def test_auto_size_off_by_default(self):
        cfg = deep_merge(DEFAULT_CONFIG, {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "outputs": {"downforce_axis": "-y"},
            "fidelity": "standard",
        })
        params = compute_mesh_params(cfg, BOUNDS, feature_stats=self._stats(0.0005))
        self.assertNotIn("auto_size", params)
        self.assertEqual(params["surface_level"], list(FIDELITY_PRESETS["standard"]["surface_level"]))

    def test_no_stats_keeps_preset_behaviour(self):
        params = compute_mesh_params(self._cfg(), BOUNDS)
        self.assertNotIn("auto_size", params)
        self.assertEqual(params["surface_level"], list(FIDELITY_PRESETS["standard"]["surface_level"]))

    def test_large_feature_never_coarsens(self):
        params = compute_mesh_params(self._cfg(), BOUNDS, feature_stats=self._stats(0.5))
        self.assertEqual(params["surface_level"], list(FIDELITY_PRESETS["standard"]["surface_level"]))

    def test_user_max_surface_level_override(self):
        params = compute_mesh_params(
            self._cfg(max_surface_level=6), BOUNDS, feature_stats=self._stats(0.0005)
        )
        self.assertEqual(params["surface_level"][1], 6)
        self.assertEqual(params["auto_size"]["max_surface_level"], 6)

    def test_presets_carry_feature_fields(self):
        for name, preset in FIDELITY_PRESETS.items():
            with self.subTest(preset=name):
                self.assertIn("feature_percentile", preset)
                self.assertIn("feature_cells", preset)
                self.assertIsInstance(preset["max_surface_level"], int)


class TestFeatureAngleStats(unittest.TestCase):
    def test_dihedral_flat_join_is_zero(self):
        self.assertAlmostEqual(_dihedral_angle_deg((0, 0, 1), (0, 0, 1)), 0.0, places=4)

    def test_dihedral_perpendicular_is_90(self):
        self.assertAlmostEqual(_dihedral_angle_deg((0, 0, 1), (1, 0, 0)), 90.0, places=4)

    def test_dihedral_inverted_is_180(self):
        self.assertAlmostEqual(_dihedral_angle_deg((0, 0, 1), (0, 0, -1)), 180.0, places=4)

    def test_histogram_and_percentile(self):
        stats = FeatureAngleStats()
        for _ in range(10):
            stats.add(90.0)
        for _ in range(10):
            stats.add(10.0)
        self.assertEqual(stats.n_angles, 20)
        self.assertAlmostEqual(stats.min_angle, 10.0)
        self.assertAlmostEqual(stats.max_angle, 90.0)
        self.assertLessEqual(stats.percentile(25), 10.0)
        self.assertGreaterEqual(stats.percentile(75), 90.0)

    def test_merge_accumulates(self):
        a = FeatureAngleStats()
        a.add(90.0)
        b = FeatureAngleStats()
        b.add(10.0)
        a.merge(b)
        self.assertEqual(a.n_angles, 2)
        self.assertAlmostEqual(a.min_angle, 10.0)


class TestStlAnalyzeFull(unittest.TestCase):
    def test_detects_shared_edge_angle(self):
        # Two triangles sharing an edge, folded 90 deg: their normals differ by 90.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fold.stl"
            write_stl(path, "fold", [
                ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 0)),   # normal +z
                ((0, -1, 0), (0, 0, 0), (1, 0, 0), (0, 0, 1)),  # normal -y
            ])
            _, n_triangles, _, _, angles = stl_analyze_full(path)
            self.assertEqual(n_triangles, 2)
            self.assertEqual(angles.n_angles, 1)
            self.assertAlmostEqual(angles.min_angle, 90.0, places=3)

    def test_flat_pair_is_seamless(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flat.stl"
            write_stl(path, "flat", [
                ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 0)),
                ((0, 0, 1), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
            ])
            _, _, _, _, angles = stl_analyze_full(path)
            self.assertEqual(angles.n_angles, 1)
            self.assertAlmostEqual(angles.min_angle, 0.0, places=3)


class TestFeatureAngleDerivation(unittest.TestCase):
    def _cfg(self, preset="standard", **mesh):
        cfg = deep_merge(DEFAULT_CONFIG, {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "outputs": {"downforce_axis": "-y"},
            "fidelity": preset,
        })
        base = {"auto_feature_angle": True}
        base.update(mesh)
        cfg["mesh_params"] = base
        return cfg

    def _angles(self, normal_angle, count=30):
        stats = FeatureAngleStats()
        for _ in range(count):
            stats.add(normal_angle)
        return stats

    def test_gentle_creases_sharpen_threshold(self):
        # 160 deg normal crease -> 0.75*160 = 120 -> capped at band 80 -> min(80,35)=35
        params = compute_mesh_params(self._cfg(), BOUNDS, angle_stats=self._angles(160.0))
        self.assertEqual(params["resolveFeatureAngle"], 35.0)

    def test_shallow_crease_keeps_preset(self):
        # 20 deg normal crease -> 0.75*20 = 15 -> min(15,35)=15 (sharper)
        params = compute_mesh_params(self._cfg(), BOUNDS, angle_stats=self._angles(20.0))
        self.assertEqual(params["resolveFeatureAngle"], 15.0)
        self.assertTrue(params["feature_angle"]["changed"])

    def test_smooth_only_keeps_preset(self):
        # 5 deg normal -> below the floor -> preset kept
        params = compute_mesh_params(self._cfg(), BOUNDS, angle_stats=self._angles(5.0))
        self.assertEqual(params["resolveFeatureAngle"], 35.0)
        self.assertFalse(params["feature_angle"]["changed"])

    def test_user_override_wins(self):
        cfg = self._cfg(resolveFeatureAngle=50)
        params = compute_mesh_params(cfg, BOUNDS, angle_stats=self._angles(20.0))
        self.assertEqual(params["resolveFeatureAngle"], 50)
        self.assertNotIn("feature_angle", params)

    def test_auto_feature_angle_can_be_disabled(self):
        cfg = self._cfg(auto_feature_angle=False)
        params = compute_mesh_params(cfg, BOUNDS, angle_stats=self._angles(20.0))
        self.assertEqual(params["resolveFeatureAngle"], 35)
        self.assertNotIn("feature_angle", params)

    def test_auto_feature_angle_off_by_default(self):
        cfg = deep_merge(DEFAULT_CONFIG, {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "outputs": {"downforce_axis": "-y"},
            "fidelity": "standard",
        })
        params = compute_mesh_params(cfg, BOUNDS, angle_stats=self._angles(20.0))
        self.assertEqual(params["resolveFeatureAngle"], 35.0)
        self.assertNotIn("feature_angle", params)

    def test_no_angle_stats_keeps_preset(self):
        params = compute_mesh_params(self._cfg(), BOUNDS)
        self.assertEqual(params["resolveFeatureAngle"], 35)


class TestFeatureExtractAlignment(unittest.TestCase):
    def _cfg(self, preset="standard"):
        cfg = deep_merge(DEFAULT_CONFIG, {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "outputs": {"downforce_axis": "-y"},
            "fidelity": preset,
        })
        cfg["mesh_params"] = {"auto_feature_angle": True}
        return cfg

    def _angles(self, normal_angle, count=30):
        stats = FeatureAngleStats()
        for _ in range(count):
            stats.add(normal_angle)
        return stats

    def test_sharp_crease_raises_extraction_angle(self):
        # 20 deg normal crease -> resolve 15 -> included recommended 165.
        cfg = self._cfg()
        compute_mesh_params(cfg, BOUNDS, angle_stats=self._angles(20.0))
        self.assertEqual(cfg["feature_extract"]["includedAngle"], 165.0)

    def test_smooth_geometry_keeps_default_extraction_angle(self):
        cfg = self._cfg()
        compute_mesh_params(cfg, BOUNDS, angle_stats=self._angles(5.0))
        self.assertEqual(cfg["feature_extract"]["includedAngle"], 140)

    def test_explicit_extraction_angle_wins(self):
        cfg = self._cfg()
        cfg["feature_extract"]["includedAngle"] = 130
        compute_mesh_params(
            cfg, BOUNDS, angle_stats=self._angles(20.0), explicit_feature_angle=True
        )
        self.assertEqual(cfg["feature_extract"]["includedAngle"], 130)

    def test_auto_feature_angle_disabled_leaves_extraction(self):
        cfg = self._cfg()
        cfg["mesh_params"] = {"auto_feature_angle": False}
        compute_mesh_params(cfg, BOUNDS, angle_stats=self._angles(20.0))
        self.assertEqual(cfg["feature_extract"]["includedAngle"], 140)

    def test_no_angle_stats_leaves_extraction(self):
        cfg = self._cfg()
        compute_mesh_params(cfg, BOUNDS)
        self.assertEqual(cfg["feature_extract"]["includedAngle"], 140)


class TestAutoSizeValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", BODY_TRIANGLES)

    def _errors(self, mesh_params):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "auto_size_case",
            "stl_files": ["body.stl"],
            "mesh_params": mesh_params,
        }))
        errors, _ = validate(load_config(path), self.root)
        return errors

    def _warnings(self, config_overrides):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "auto_size_case",
            "stl_files": ["body.stl"],
            **config_overrides,
        }))
        _, warnings = validate(load_config(path), self.root)
        return warnings

    def test_invalid_feature_params_rejected(self):
        for bad in (
            {"auto_size": "yes"},
            {"feature_percentile": 0},
            {"feature_percentile": 150},
            {"feature_cells": -1},
            {"max_surface_level": 0},
            {"max_surface_level": 3.5},
        ):
            with self.subTest(bad=bad):
                self.assertTrue(self._errors(bad))

    def test_valid_feature_params_accepted(self):
        self.assertFalse(self._errors({
            "auto_size": True,
            "feature_percentile": 5.0,
            "feature_cells": 4,
            "max_surface_level": 7,
        }))

    def test_absolute_layer_thicker_than_cell_warns(self):
        warnings = self._warnings({
            "mesh_params": {"base_cell_size": 0.1, "surface_level": [4, 5]},
            "layers": {"relativeSizes": False, "first_layer_thickness": 0.01},
        })
        self.assertTrue(any("finest" in w and "boundary layers" in w for w in warnings))

    def test_reasonable_absolute_layer_does_not_warn(self):
        warnings = self._warnings({
            "mesh_params": {"base_cell_size": 0.1, "surface_level": [4, 5]},
            "layers": {"relativeSizes": False, "first_layer_thickness": 1e-5},
        })
        self.assertFalse(any("finest" in w for w in warnings))


class TestPerSurfaceSizing(unittest.TestCase):
    """Geometry-derived per-surface levels (auto_size): size follows features."""

    def _stats(self, length, count=500):
        stats = EdgeStats()
        for _ in range(count):
            stats.add(length)
        return stats

    def test_small_part_finer_than_large_part(self):
        preset = FIDELITY_PRESETS["standard"]
        res = resolve_per_surface_levels(
            {"auto_size": True},
            preset,
            base_cell=0.1,
            global_surface_level=list(preset["surface_level"]),
            global_edge_level=preset["edge_level"],
            stats_by_stem={"big": self._stats(0.2), "small": self._stats(0.005)},
            extents_by_stem={"big": [3.0, 1.0, 5.0], "small": [0.3, 0.1, 0.4]},
        )
        self.assertGreater(res["small"]["surface_level"][1], res["big"]["surface_level"][1])
        self.assertGreaterEqual(res["small"]["edge_level"], res["big"]["edge_level"])

    def test_large_part_never_coarsens_below_preset(self):
        preset = FIDELITY_PRESETS["standard"]
        res = resolve_per_surface_levels(
            {"auto_size": True},
            preset,
            base_cell=0.1,
            global_surface_level=list(preset["surface_level"]),
            global_edge_level=preset["edge_level"],
            stats_by_stem={"big": self._stats(0.2)},
            extents_by_stem={"big": [3.0, 1.0, 5.0]},
        )
        self.assertEqual(res["big"]["surface_level"][1], preset["surface_level"][1])

    def test_capped_at_max_surface_level(self):
        preset = FIDELITY_PRESETS["standard"]
        res = resolve_per_surface_levels(
            {"auto_size": True, "max_surface_level": 5},
            preset,
            base_cell=0.1,
            global_surface_level=list(preset["surface_level"]),
            global_edge_level=preset["edge_level"],
            stats_by_stem={"tiny": self._stats(0.0005)},
            extents_by_stem={"tiny": [0.05, 0.05, 0.05]},
        )
        self.assertLessEqual(res["tiny"]["surface_level"][1], 5)
        self.assertTrue(res["tiny"]["capped"])

    def test_empty_stats_returns_empty(self):
        preset = FIDELITY_PRESETS["standard"]
        self.assertEqual(
            resolve_per_surface_levels(
                {"auto_size": True}, preset, 0.1, list(preset["surface_level"]),
                preset["edge_level"], {}, {},
            ),
            {},
        )


class TestMeshRegions(unittest.TestCase):
    """Manual per-surface overrides (mesh_regions) folded into mesh_params."""

    def test_apply_mesh_regions_sets_levels_and_layers(self):
        cfg = {"mesh_regions": {"wing": {"surface_level": [4, 7], "edge_level": 8, "n_layers": 12}}}
        apply_mesh_regions(cfg, ["body", "wing"])
        self.assertEqual(cfg["mesh_params"]["surface_levels"]["wing"]["surface_level"], [4, 7])
        self.assertEqual(cfg["mesh_params"]["surface_levels"]["wing"]["edge_level"], 8)
        self.assertEqual(cfg["mesh_params"]["layer_overrides"]["wing"], 12)

    def test_unknown_stem_ignored(self):
        cfg = {"mesh_regions": {"not_a_part": {"n_layers": 0}}}
        apply_mesh_regions(cfg, ["body"])
        self.assertEqual(cfg["mesh_params"]["layer_overrides"], {})

    def test_manual_overrides_geometry_derived(self):
        cfg = {
            "mesh_regions": {"wing": {"surface_level": [2, 3]}},
            "mesh_params": {"surface_levels": {"wing": {"surface_level": [4, 7], "edge_level": 8}}},
        }
        apply_mesh_regions(cfg, ["wing"])
        self.assertEqual(cfg["mesh_params"]["surface_levels"]["wing"]["surface_level"], [2, 3])
        # edge_level from the auto pass survives when the manual block omits it.
        self.assertEqual(cfg["mesh_params"]["surface_levels"]["wing"]["edge_level"], 8)

    def test_validation(self):
        cfg = deep_merge(DEFAULT_CONFIG, {"stl_files": ["body.stl"]})
        cfg["mesh_regions"] = {"body": {"surface_level": [5, 4]}}
        errors, _ = validate(cfg, Path("."))
        self.assertTrue(any("surface_level" in e for e in errors))
        cfg["mesh_regions"] = {"body": {"bogus": 1}}
        errors, _ = validate(cfg, Path("."))
        self.assertTrue(any("unknown keys" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
