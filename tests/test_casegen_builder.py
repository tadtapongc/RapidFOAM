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
from rapidfoam.casegen.solver import write_control_dict
from rapidfoam.config import DEFAULT_CONFIG, deep_merge
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

    def test_default_writes_wall_fields_and_yplus_field(self):
        text = self._write()
        self.assertIn("type            wallShearStress;", text)
        self.assertIn("type            wallPressure;", text)
        self.assertIn("patches         (body);", text)
        self.assertIn("writeFields     true;", text)
        # Extrema location + per-patch wall-pressure stats.
        self.assertIn("type            fieldMinMax;", text)
        self.assertIn("writeLocation   true;", text)
        self.assertIn("wallPressure_min_body", text)
        self.assertIn("wallPressure_max_body", text)
        self.assertIn("wallPressure_average_body", text)
        self.assertIn("type            surfaceFieldValue;", text)
        # Opt-in vorticity is off by default.
        self.assertNotIn("type            vorticity;", text)

    def test_vorticity_opt_in(self):
        self.assertIn("type            vorticity;", self._write(vorticity=True))

    def test_flags_disable_outputs(self):
        text = self._write(
            wall_pressure=False, wall_shear_stress=False, y_plus=False,
            field_min_max=False, surface_field_value=False, vorticity=False,
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
