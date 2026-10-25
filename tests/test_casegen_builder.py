"""casegen.builder tests (Phase 4).

``build_case`` is the shared generation entry point used by both the CLI and
the Web Studio. It returns the case directory, reports through its ``reporter``
callback, and raises ``CaseGenerationError`` (not ``SystemExit``) on failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.casegen.builder import CaseGenerationError, build_case
from rapidfoam.casegen.solver import write_control_dict
from rapidfoam.config import DEFAULT_CONFIG, deep_merge, load_config, validate
from rapidfoam.geometry.stl import write_stl


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

    def test_two_pass_layering_generates_second_dict(self):
        self.cfg.write_text(json.dumps({
            "case_name": "twopass",
            "stl_files": ["body.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "overrides": {"layers": {"two_pass": True}},
        }), encoding="utf-8")
        case = build_case(self.cfg, self.root, reporter=lambda _: None)
        self.assertTrue((case / "system" / "snappyHexMeshDict_layering").is_file())
        self.assertIn("snappyHexMeshDict_layering", (case / "Allrun.parallel").read_text())

    def test_surface_enforce_exempts_default_half_model(self):
        # Default config gets a symmetry face from the generator; no symmetry_plane.
        self.cfg.write_text(json.dumps({
            "case_name": "half_enforce",
            "stl_files": ["body.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "surface_check": {"enabled": True, "enforce": True},
        }), encoding="utf-8")
        case = build_case(self.cfg, self.root, reporter=lambda _: None)
        allrun = (case / "Allrun").read_text()
        self.assertNotIn("Surface is not closed", allrun)
        self.assertIn("Open surface allowed", allrun)

    def test_surface_enforce_checks_closure_on_full_car(self):
        self.cfg.write_text(json.dumps({
            "case_name": "full_enforce",
            "stl_files": ["body.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "domain_faces": {
                "-x": "farField", "+x": "farField", "-y": "ground",
                "+y": "farField", "+z": "inlet", "-z": "outlet",
            },
            "surface_check": {"enabled": True, "enforce": True},
        }), encoding="utf-8")
        case = build_case(self.cfg, self.root, reporter=lambda _: None)
        allrun = (case / "Allrun").read_text()
        self.assertIn("Surface is not closed", allrun)
        self.assertNotIn("Open surface allowed", allrun)


class MeshRegionEmissionTest(unittest.TestCase):
    """Per-surface refinement must reach the emitted dicts via build_case.

    Regression: auto_size per-surface levels and mesh_regions overrides were
    folded into ``cfg["mesh_params"]`` after ``apply_plan_to_cfg`` had already
    deep-copied the plan, so the writers (which consume the plan) emitted the
    global values while ``case_config.json`` and the console report showed the
    overrides. Tests only covered the ``emit_mesh_files_from_config`` shim,
    which aliases ``cfg["mesh_params"]`` by reference, hiding the divergence.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        body_triangle = ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))
        # A deliberately tiny part: auto_size must give it a finer level than
        # the large body, otherwise the regression below cannot distinguish the
        # plan re-point from the old global-level output.
        tiny_triangle = ((0, 0, 1), (2, 0, 0.01), (2.01, 0, 0.01), (2, 0.01, 0.01))
        write_stl(self.root / "stl" / "body.stl", "body", [body_triangle])
        write_stl(self.root / "stl" / "bracket.stl", "bracket", [tiny_triangle])

    def _generate(self, name, **extra):
        cfg = {
            "case_name": name,
            "stl_files": ["body.stl", "bracket.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }
        cfg.update(extra)
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return build_case(path, self.root, reporter=lambda _: None)

    def test_mesh_regions_override_reaches_snappy_dict(self):
        case = self._generate("regions", mesh_regions={
            "bracket": {"surface_level": [5, 7], "edge_level": 8, "n_layers": 0},
        })
        text = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
        self.assertIn("body { level (3 4);", text)
        self.assertIn("bracket { level (5 7);", text)
        self.assertIn('file "bracket.eMesh"; level 8;', text)
        self.assertIn('"bracket" { nSurfaceLayers 0; }', text)

    def test_layers_only_region_does_not_crash(self):
        case = self._generate("layers_only", mesh_regions={"bracket": {"n_layers": 0}})
        text = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
        self.assertIn('"bracket" { nSurfaceLayers 0; }', text)
        case_cfg = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
        mesh = case_cfg["mesh_params"]
        self.assertEqual(mesh["layer_overrides"]["bracket"], 0)
        self.assertNotIn("bracket", mesh.get("surface_levels", {}))

    def test_auto_size_levels_reach_snappy_dict(self):
        case = self._generate("autosize", mesh_params={"auto_size": True})
        case_cfg = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
        per_surface = case_cfg["mesh_params"]["surface_levels"]
        self.assertTrue(per_surface)
        # The tiny bracket must be refined beyond the large body — otherwise
        # this test would pass even if the writer kept the stale global levels.
        self.assertNotEqual(
            per_surface["body"]["surface_level"],
            per_surface["bracket"]["surface_level"],
        )
        self.assertGreater(
            per_surface["bracket"]["edge_level"], per_surface["body"]["edge_level"]
        )
        text = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
        for stem, info in per_surface.items():
            level = info["surface_level"]
            self.assertIn(f"{stem} {{ level ({level[0]} {level[1]});", text)
            self.assertIn(f'file "{stem}.eMesh"; level {info["edge_level"]};', text)


class PhysicsConfigTest(unittest.TestCase):
    """Physics-related robustness: outlet backflow BCs and turbulence guards."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", [
            ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
        ])

    def _generate(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "physics_test",
            "stl_files": ["body.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }), encoding="utf-8")
        return build_case(path, self.root, reporter=lambda _: None)

    def test_outlet_bcs_are_backflow_robust(self):
        case = self._generate()
        u = (case / "0/U").read_text(encoding="utf-8")
        k = (case / "0/k").read_text(encoding="utf-8")
        omega = (case / "0/omega").read_text(encoding="utf-8")
        # Outlet: pressure-driven velocity + freestream-clamped turbulence on
        # backflow, instead of a dead-end zero velocity / zero-gradient scalar.
        self.assertIn("type            pressureInletOutletVelocity;", u)
        self.assertIn("type            inletOutlet;", k)
        self.assertIn("type            inletOutlet;", omega)
        k_val = 1.5 * (20.0 * 0.005) ** 2
        self.assertIn(f"inletValue      uniform {k_val:.6g}", k)
        # The slip far field keeps zero-gradient scalars (no normal flux there).
        self.assertIn("type            slip;", u)
        self.assertIn("type            zeroGradient;", k)
        self.assertIn("type            zeroGradient;", omega)

    def test_turbulence_guardrails(self):
        path = self.root / "guard.json"
        path.write_text(json.dumps({
            "case_name": "guard",
            "stl_files": ["body.stl"],
            "turbulence": {"model": "kEpsilon", "intensity": 0.5},
        }), encoding="utf-8")
        _, warnings = validate(load_config(path), self.root)
        self.assertTrue(any("not kOmegaSST" in w for w in warnings))
        self.assertTrue(any("fraction" in w for w in warnings))

    def test_default_turbulence_does_not_warn(self):
        path = self.root / "default.json"
        path.write_text(json.dumps({
            "case_name": "default",
            "stl_files": ["body.stl"],
        }), encoding="utf-8")
        _, warnings = validate(load_config(path), self.root)
        self.assertFalse(any("kOmegaSST" in w or "fraction" in w for w in warnings))


class SolverFieldOutputsTest(unittest.TestCase):
    """controlDict diagnostic surface fields (Phase: ParaView load map)."""

    def _write(self, **field_outputs):
        cfg = deep_merge(DEFAULT_CONFIG, {"stl_files": ["body.stl"]})
        cfg["stl_names"] = ["body"]
        if field_outputs:
            cfg["field_outputs"] = field_outputs
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir()
            write_control_dict(cfg, case)
            return (case / "system" / "controlDict").read_text(encoding="utf-8")

    def test_default_writes_wall_shear_and_yplus_field(self):
        text = self._write()
        self.assertIn("type            wallShearStress;", text)
        self.assertIn("patches         (body);", text)
        self.assertIn("writeFields     true;", text)
        # Extrema location reduction, but no surfaceFieldValue wall-pressure FO.
        self.assertIn("type            fieldMinMax;", text)
        self.assertIn("writeLocation   true;", text)
        self.assertNotIn("wallPressure_min_body", text)
        self.assertNotIn("type            surfaceFieldValue;", text)
        # Opt-in vorticity is off by default, and there is no wallPressure FO
        # (the type does not exist in v2606; wall pressure is the p boundary).
        self.assertNotIn("type            vorticity;", text)
        self.assertNotIn("type            wallPressure;", text)

    def test_vorticity_opt_in(self):
        self.assertIn("type            vorticity;", self._write(vorticity=True))

    def test_flags_disable_outputs(self):
        text = self._write(
            wall_shear_stress=False, y_plus=False,
            field_min_max=False, vorticity=False,
        )
        self.assertNotIn("wallShearStress", text)
        self.assertNotIn("wallPressure", text)
        self.assertNotIn("writeFields", text)
        self.assertNotIn("fieldMinMax", text)
        self.assertNotIn("surfaceFieldValue", text)
        # The base diagnostic objects are always present.
        self.assertIn("type            forces;", text)
        self.assertIn("type            yPlus;", text)


if __name__ == "__main__":
    unittest.main()
