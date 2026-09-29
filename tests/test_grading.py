"""Unit tests for geometry-adaptive background grading (blockMesh simpleGrading)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.config import DEFAULT_CONFIG, deep_merge, load_config, validate
from rapidfoam.meshing.domain import compute_domain_box
from rapidfoam.meshing.grading import (
    DEFAULT_GRADING_RATIO,
    _graded_axis,
    compute_block_grading,
)
from rapidfoam.meshing.params import compute_mesh_params
from rapidfoam.geometry.stl import write_stl
from rapidfoam.meshing.context import build_mesh_context
from rapidfoam.meshing.plan import plan_from_config
from rapidfoam.meshing.writers.block_mesh import write_block_mesh_dict

BOUNDS = ((-0.745, 0.0, -1.5), (0.745, 1.145, 1.5))

BODY_TRIANGLES = [((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))]


def base_cfg():
    return {
        "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
        "outputs": {"downforce_axis": "-y"},
        "fluid": {"nu": 1.516e-5, "rho": 1.225},
        "fidelity": "standard",
        "mesh_params": {"base_cell_size": 0.1, "surface_level": [4, 5]},
    }


def full_cfg(**overrides):
    cfg = deep_merge(DEFAULT_CONFIG, base_cfg())
    cfg.update(overrides)
    return cfg


class TestGradedAxis(unittest.TestCase):
    def test_uniform_when_ratio_one(self):
        cells, grading = _graded_axis(5.0, 0.1, 1.0, True)
        self.assertEqual(cells, 50)
        self.assertEqual(grading, 1.0)

    def test_fine_at_start_uses_ratio(self):
        cells, grading = _graded_axis(5.0, 0.1, 3.0, True)
        self.assertEqual(grading, 3.0)
        self.assertLess(cells, 50)  # fewer cells than uniform at same near-cell

    def test_fine_at_end_uses_reciprocal(self):
        cells, grading = _graded_axis(5.0, 0.1, 3.0, False)
        self.assertAlmostEqual(grading, 1.0 / 3.0)
        self.assertLess(cells, 50)

    def test_near_cell_preserved(self):
        extent, base, ratio = 5.0, 0.1, 3.0
        cells, _ = _graded_axis(extent, base, ratio, True)
        beta = ratio ** (1.0 / (cells - 1))
        near = extent * (beta - 1.0) / (beta ** cells - 1.0)
        self.assertAlmostEqual(near, base, delta=base * 0.05)

    def test_short_axis_kept_uniform(self):
        cells, grading = _graded_axis(0.2, 0.1, 3.0, True)
        self.assertEqual(cells, 2)
        self.assertEqual(grading, 1.0)


class TestComputeBlockGrading(unittest.TestCase):
    def test_auto_grades_ground_and_symmetry_axes(self):
        cfg = full_cfg(mesh_params={"base_cell_size": 0.1, "grading": "auto"})
        box = compute_domain_box(cfg, BOUNDS)
        info = compute_block_grading(cfg, box, BOUNDS, 0.1)
        self.assertEqual(info["mode"], "auto")
        # x = lateral (symmetry, fine at min), y = up (ground, fine at min)
        self.assertGreater(info["grading"][0], 1.0)
        self.assertGreater(info["grading"][1], 1.0)
        self.assertEqual(info["grading"][2], 1.0)  # flow axis uniform
        self.assertLess(info["block_cells"][0], info["uniform_cells"][0])
        self.assertLess(info["block_cells"][1], info["uniform_cells"][1])
        self.assertGreater(info["cell_reduction"], 0.0)
        self.assertIn("y", info["axes"])
        self.assertEqual(info["axes"]["y"]["fine_side"], "min")

    def test_off_mode_is_uniform(self):
        cfg = full_cfg(mesh_params={"base_cell_size": 0.1, "grading": "off"})
        info = compute_block_grading(cfg, {"min": [-4, -4, -4], "max": [1, 1, 4]}, BOUNDS, 0.1)
        self.assertEqual(info["mode"], "off")
        self.assertEqual(info["grading"], [1.0, 1.0, 1.0])
        self.assertEqual(info["block_cells"], info["uniform_cells"])
        self.assertEqual(info["cell_reduction"], 0.0)

    def test_explicit_grading_keeps_uniform_counts(self):
        cfg = full_cfg(mesh_params={"base_cell_size": 0.1, "grading": [1.5, 1.0, 2.0]})
        info = compute_block_grading(cfg, {"min": [-4, -4, -4], "max": [1, 1, 4]}, BOUNDS, 0.1)
        self.assertEqual(info["mode"], "explicit")
        self.assertEqual(info["grading"], [1.5, 1.0, 2.0])
        self.assertEqual(info["block_cells"], info["uniform_cells"])

    def test_positive_faces_fine_at_max(self):
        cfg = full_cfg(
            mesh_params={"base_cell_size": 0.1, "grading": "auto"},
            domain_faces={
                "+x": "symmetry", "-x": "farField", "+y": "ground", "-y": "farField",
                "+z": "inlet", "-z": "outlet",
            },
        )
        info = compute_block_grading(
            cfg, {"min": [-4, -4, -4], "max": [1, 1, 4]}, BOUNDS, 0.1
        )
        self.assertLess(info["grading"][0], 1.0)  # fine at +x
        self.assertLess(info["grading"][1], 1.0)  # fine at +y
        self.assertEqual(info["axes"]["y"]["fine_side"], "max")

    def test_invalid_ratio_falls_back_to_off(self):
        cfg = full_cfg(mesh_params={"base_cell_size": 0.1, "grading": "auto", "grading_ratio": 0.5})
        info = compute_block_grading(cfg, {"min": [-4, -4, -4], "max": [1, 1, 4]}, BOUNDS, 0.1)
        self.assertEqual(info["mode"], "off")
        self.assertEqual(info["grading"], [1.0, 1.0, 1.0])

    def test_default_is_off(self):
        # Quality-first default: grading must be opted in.
        info = compute_block_grading(
            full_cfg(), {"min": [-4, -4, -4], "max": [1, 1, 4]}, BOUNDS, 0.1
        )
        self.assertEqual(info["mode"], "off")
        self.assertEqual(info["grading"], [1.0, 1.0, 1.0])
        self.assertEqual(info["block_cells"], info["uniform_cells"])


class TestComputeMeshParamsGrading(unittest.TestCase):
    def test_result_carries_grading_and_cells(self):
        params = compute_mesh_params(
            full_cfg(mesh_params={"base_cell_size": 0.1, "grading": "auto"}), BOUNDS
        )
        self.assertIn("grading", params)
        self.assertIn("block_cells", params)
        self.assertIn("grading_info", params)
        self.assertEqual(len(params["grading"]), 3)
        self.assertEqual(len(params["block_cells"]), 3)
        self.assertGreater(params["grading"][1], 1.0)

    def test_grading_off_leaves_uniform(self):
        cfg = full_cfg(mesh_params={"base_cell_size": 0.1, "grading": "off"})
        params = compute_mesh_params(cfg, BOUNDS)
        self.assertEqual(params["grading"], [1.0, 1.0, 1.0])


class TestWriteBlockMeshDict(unittest.TestCase):
    def _write(self, cfg):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir(parents=True)
            cfg["domain_box"] = compute_domain_box(cfg, BOUNDS)
            cfg["mesh_params"] = compute_mesh_params(cfg, BOUNDS)
            write_block_mesh_dict(plan_from_config(cfg), build_mesh_context(cfg), case)
            return (case / "system" / "blockMeshDict").read_text(encoding="utf-8")

    def test_emits_graded_block(self):
        cfg = full_cfg(mesh_params={"base_cell_size": 0.1, "grading": "auto"})
        text = self._write(cfg)
        cells = cfg["mesh_params"]["block_cells"]
        self.assertIn(f"({cells[0]} {cells[1]} {cells[2]})", text)
        self.assertIn("simpleGrading (", text)
        self.assertNotIn("simpleGrading (1 1 1)", text)

    def test_falls_back_to_uniform_without_block_cells(self):
        cfg = full_cfg()
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir(parents=True)
            cfg["domain_box"] = compute_domain_box(cfg, BOUNDS)
            cfg["mesh_params"] = {"base_cell_size": 0.1}  # no block_cells/grading
            write_block_mesh_dict(plan_from_config(cfg), build_mesh_context(cfg), case)
            text = (case / "system" / "blockMeshDict").read_text(encoding="utf-8")
        self.assertIn("simpleGrading (1 1 1)", text)


class TestGradingValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", BODY_TRIANGLES)

    def _errors(self, mesh_params):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "grading_case",
            "stl_files": ["body.stl"],
            "mesh_params": mesh_params,
        }))
        errors, _ = validate(load_config(path), self.root)
        return errors

    def test_auto_and_off_accepted(self):
        self.assertFalse(self._errors({"grading": "auto"}))
        self.assertFalse(self._errors({"grading": "off"}))

    def test_explicit_triplet_accepted(self):
        self.assertFalse(self._errors({"grading": [1.0, 2.0, 1.5]}))

    def test_bad_string_rejected(self):
        self.assertTrue(self._errors({"grading": "steep"}))

    def test_bad_triplet_rejected(self):
        self.assertTrue(self._errors({"grading": [1.0, -2.0, 1.0]}))
        self.assertTrue(self._errors({"grading": [1.0, 2.0]}))

    def test_ratio_bounds(self):
        self.assertFalse(self._errors({"grading_ratio": 3.0}))
        self.assertTrue(self._errors({"grading_ratio": 0.5}))
        self.assertTrue(self._errors({"grading_ratio": 50}))


class TestGradingDefaults(unittest.TestCase):
    def test_default_ratio_is_modest(self):
        self.assertGreaterEqual(DEFAULT_GRADING_RATIO, 1.5)
        self.assertLessEqual(DEFAULT_GRADING_RATIO, 6.0)


if __name__ == "__main__":
    unittest.main()
