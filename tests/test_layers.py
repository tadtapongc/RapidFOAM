"""Unit tests for y+-driven boundary-layer sizing."""

import math
import tempfile
import unittest
from pathlib import Path

from rapidfoam.config import DEFAULT_CONFIG, deep_merge
from rapidfoam.geometry import (
    FIDELITY_PRESETS,
    compute_mesh_params,
    estimate_friction_velocity,
    first_layer_height,
    resolve_layers,
)
from rapidfoam.writers.mesh import write_snappy_hex_mesh_dict

BOUNDS = ((-0.745, 0.0, -2.961), (0.745, 1.145, 0.296))
NU = 1.516e-5
U = 16.67


def base_cfg(preset="standard"):
    p = FIDELITY_PRESETS[preset]
    length = BOUNDS[1][2] - BOUNDS[0][2]
    return {
        "flow": {"velocity": U, "direction": "-z", "ground": True},
        "outputs": {"downforce_axis": "-y"},
        "fluid": {"nu": NU, "rho": 1.225},
        "mesh_params": {
            "base_cell_size": length / p["cells_per_length"],
            "surface_level": list(p["surface_level"]),
        },
        "layers": {
            "n_layers": p["n_layers"],
            "expansion_ratio": p["expansion_ratio"],
            "y_plus_target": p["y_plus_target"],
        },
        "fidelity": preset,
    }


def full_cfg(preset="standard", **overrides):
    cfg = deep_merge(DEFAULT_CONFIG, base_cfg(preset))
    cfg.update(overrides)
    return cfg


class TestFrictionVelocity(unittest.TestCase):
    def test_turbulent_branch_matches_correlation(self):
        length = 3.257
        Re = U * length / NU
        self.assertGreater(Re, 5.0e5)
        expected = U * math.sqrt(0.5 * 0.026 * Re ** (-1.0 / 7.0))
        self.assertAlmostEqual(estimate_friction_velocity(U, NU, length), expected)

    def test_laminar_branch_matches_blasius(self):
        velocity, length = 1.0, 0.05
        Re = velocity * length / NU
        self.assertLess(Re, 5.0e5)
        expected = velocity * math.sqrt(0.5 * 1.328 / math.sqrt(Re))
        self.assertAlmostEqual(estimate_friction_velocity(velocity, NU, length), expected)

    def test_monotonic_in_velocity_and_length(self):
        base = estimate_friction_velocity(10.0, NU, 1.0)
        self.assertGreater(estimate_friction_velocity(20.0, NU, 1.0), base)
        # A longer plate lowers Cf, so u_tau decreases for fixed velocity
        self.assertLess(estimate_friction_velocity(10.0, NU, 2.0), base)

    def test_invalid_inputs_return_zero(self):
        self.assertEqual(estimate_friction_velocity(0.0, NU, 1.0), 0.0)
        self.assertEqual(estimate_friction_velocity(U, 0.0, 1.0), 0.0)


class TestFirstLayerHeight(unittest.TestCase):
    def test_formula_uses_cell_centre_convention(self):
        u_tau = 0.65
        self.assertAlmostEqual(first_layer_height(40, u_tau, NU), 2.0 * 40 * NU / u_tau)

    def test_invalid_u_tau_returns_zero(self):
        self.assertEqual(first_layer_height(40, 0.0, NU), 0.0)


class TestResolveLayers(unittest.TestCase):
    def test_preset_target_resolves_to_absolute(self):
        cfg = base_cfg("standard")
        res = resolve_layers(cfg, BOUNDS)
        layers = cfg["layers"]
        self.assertFalse(layers["relativeSizes"])
        self.assertAlmostEqual(layers["first_layer_thickness"], res["first_layer_thickness"])
        self.assertAlmostEqual(layers.get("min_thickness"), layers["first_layer_thickness"])
        self.assertGreater(res["u_tau"], 0.0)
        self.assertIsNotNone(res["y_plus_effective"])
        self.assertIsNotNone(res["stack"])
        self.assertIn("_resolved", layers)

    def test_clamp_respects_max_face_thickness_ratio(self):
        cfg = base_cfg("standard")
        res = resolve_layers(cfg, BOUNDS)
        cell_fine = cfg["mesh_params"]["base_cell_size"] / 2 ** cfg["mesh_params"]["surface_level"][1]
        self.assertLessEqual(cfg["layers"]["first_layer_thickness"], 0.5 * cell_fine + 1e-12)
        if res["clamped"]:
            self.assertLess(res["y_plus_effective"], res["y_plus_target"])

    def test_explicit_first_layer_wins(self):
        cfg = base_cfg("standard")
        cfg["layers"].update(relativeSizes=False, first_layer_thickness=2e-5, min_thickness=2e-5)
        res = resolve_layers(cfg, BOUNDS, explicit_first_layer=True)
        self.assertEqual(cfg["layers"]["first_layer_thickness"], 2e-5)
        self.assertEqual(cfg["layers"]["min_thickness"], 2e-5)
        self.assertAlmostEqual(res["y_plus_effective"], 2e-5 * res["u_tau"] / (2.0 * NU))

    def test_no_target_is_noop(self):
        cfg = base_cfg("standard")
        cfg["layers"].pop("y_plus_target")
        cfg["layers"].update(relativeSizes=True, first_layer_thickness=0.3, min_thickness=0.05)
        res = resolve_layers(cfg, BOUNDS)
        self.assertEqual(cfg["layers"]["first_layer_thickness"], 0.3)
        self.assertTrue(cfg["layers"]["relativeSizes"])
        self.assertIsNone(res["first_layer_thickness"])

    def test_invalid_target_ignored(self):
        cfg = base_cfg("standard")
        cfg["layers"]["y_plus_target"] = "bogus"
        res = resolve_layers(cfg, BOUNDS)
        self.assertIsNone(res["first_layer_thickness"])

    def test_fine_preset_reaches_low_y_plus(self):
        cfg = base_cfg("fine")
        res = resolve_layers(cfg, BOUNDS)
        self.assertFalse(cfg["layers"]["relativeSizes"])
        self.assertIsNotNone(res["y_plus_effective"])
        self.assertLess(res["y_plus_effective"], 2.0)


class TestPresetSchema(unittest.TestCase):
    def test_all_presets_carry_fsae_fields(self):
        for name, p in FIDELITY_PRESETS.items():
            with self.subTest(preset=name):
                self.assertIsNotNone(p.get("y_plus_target"))
                self.assertIsNotNone(p.get("cells_per_length"))
                self.assertIsInstance(p.get("ground_layers"), bool)
                self.assertTrue(p.get("distance_shells"))
                self.assertIn("slurm_mem_per_cpu", p)

    def test_wall_function_and_wall_resolved_tiers(self):
        self.assertGreaterEqual(FIDELITY_PRESETS["fast"]["y_plus_target"], 30)
        self.assertGreaterEqual(FIDELITY_PRESETS["standard"]["y_plus_target"], 30)
        self.assertLessEqual(FIDELITY_PRESETS["fine"]["y_plus_target"], 5)
        for name, preset in FIDELITY_PRESETS.items():
            with self.subTest(preset=name):
                self.assertFalse(preset["ground_layers"])

    def test_fine_uses_budget_friendly_tangential_levels(self):
        self.assertLessEqual(FIDELITY_PRESETS["fine"]["maxGlobalCells"], 32_000_000)
        self.assertGreaterEqual(FIDELITY_PRESETS["fine"]["n_layers"], 10)


class TestMeshParamsScaling(unittest.TestCase):
    def test_auto_base_cell_scales_with_model_length(self):
        cfg = full_cfg("standard")
        cfg["mesh_params"] = {}
        small = compute_mesh_params(cfg, ((-0.2, 0.0, -0.3), (0.2, 0.6, 0.3)))
        large = compute_mesh_params(cfg, BOUNDS)
        self.assertAlmostEqual(small["base_cell_size"], round(0.6 / 30, 4), places=4)
        self.assertAlmostEqual(large["base_cell_size"], round(3.257 / 30, 4), places=4)
        self.assertLess(small["base_cell_size"], large["base_cell_size"])

    def test_shells_are_base_cell_multiples(self):
        cfg = full_cfg("standard")
        cfg["mesh_params"] = {}
        params = compute_mesh_params(cfg, BOUNDS)
        raw_base = 3.257 / 30.0
        self.assertEqual(
            [d for d, _ in params["distance_levels"]],
            [round(0.25 * raw_base, 6), round(0.8 * raw_base, 6)],
        )

    def test_explicit_base_cell_wins(self):
        cfg = full_cfg("standard")
        params = compute_mesh_params(cfg, BOUNDS)
        self.assertAlmostEqual(params["base_cell_size"], cfg["mesh_params"]["base_cell_size"], places=4)


class TestGroundLayerEmission(unittest.TestCase):
    def _dict_text(self, ground, ground_n=None):
        cfg = deep_merge(DEFAULT_CONFIG, {
            "stl_files": ["body.stl"],
            "flow": {"velocity": U, "direction": "-z", "ground": True},
        })
        cfg["stl_names"] = ["body"]
        cfg["domain_box"] = {"min": [0.0, 0.0, -2.0], "max": [4.0, 3.0, 6.0]}
        cfg["layers"] = dict(
            cfg["layers"],
            ground_layers=ground,
            y_plus_target=40,
            n_layers=3,
            expansion_ratio=1.2,
        )
        if ground_n is not None:
            cfg["layers"]["ground_n_layers"] = ground_n
        cfg["mesh_params"] = compute_mesh_params(cfg, ((-0.5, 0.0, -1.5), (0.5, 1.0, 1.5)))
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir()
            write_snappy_hex_mesh_dict(cfg, case)
            return (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")

    def test_ground_layers_capped_by_default(self):
        text = self._dict_text(True)
        self.assertIn('"ground" { nSurfaceLayers 2; }', text)
        self.assertIn('"body" { nSurfaceLayers 3; }', text)

    def test_ground_n_layers_override(self):
        self.assertIn('"ground" { nSurfaceLayers 1; }', self._dict_text(True, ground_n=1))

    def test_ground_layers_absent_when_disabled(self):
        self.assertNotIn('"ground" { nSurfaceLayers', self._dict_text(False))


class TestGroundLayerGuard(unittest.TestCase):
    def _cfg(self, ground_clearance=None, n_layers=3):
        cfg = full_cfg("standard")
        cfg["layers"] = dict(
            cfg["layers"],
            ground_layers=True,
            y_plus_target=40,
            n_layers=n_layers,
            expansion_ratio=1.2,
        )
        if ground_clearance is not None:
            cfg["ground_clearance"] = ground_clearance
        return cfg

    def test_touching_ground_disables_optin(self):
        cfg = self._cfg()
        res = resolve_layers(cfg, BOUNDS)
        self.assertFalse(cfg["layers"]["ground_layers"])
        self.assertFalse(res["ground_layers"])
        self.assertIn("clearance", res["ground_layers_note"])

    def test_clearance_keeps_optin_capped(self):
        cfg = self._cfg(ground_clearance=0.05)
        res = resolve_layers(cfg, BOUNDS)
        self.assertTrue(cfg["layers"]["ground_layers"])
        self.assertEqual(res["ground_n_layers"], 2)
        self.assertIn("capped", res["ground_layers_note"])

    def test_ground_n_layers_override_kept(self):
        cfg = self._cfg(ground_clearance=0.05)
        cfg["layers"]["ground_n_layers"] = 1
        res = resolve_layers(cfg, BOUNDS)
        self.assertTrue(cfg["layers"]["ground_layers"])
        self.assertEqual(res["ground_n_layers"], 1)

    def test_ground_layers_off_by_default_no_note(self):
        cfg = full_cfg("standard")
        cfg["layers"].pop("ground_layers", None)
        res = resolve_layers(cfg, BOUNDS)
        self.assertFalse(res["ground_layers"])
        self.assertEqual(res["ground_layers_note"], "")


if __name__ == "__main__":
    unittest.main()
