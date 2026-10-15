"""Studio config-sync regression tests.

Pins PAIN_POINTS.md #9b: two symmetry detectors used to disagree on the same
``case_config.json``. ``checkmesh._config_has_symmetry`` returned True whenever
``symmetry_plane`` was numeric (which the Studio always writes, even for a full
car), while ``forces.is_symmetry_case`` decides from ``domain_faces``. Phase 1a
canonicalized both on ``core.caseconfig.has_symmetry`` (face assignment first).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.meshing.presets import FIDELITY_PRESETS
from rapidfoam.postproc.checkmesh import _config_has_symmetry
from rapidfoam.postproc.forces import is_symmetry_case

REPO_ROOT = Path(__file__).resolve().parents[1]

FULL_CAR_FACES = {
    "-x": "farField",
    "+x": "farField",
    "-y": "ground",
    "+y": "farField",
    "+z": "inlet",
    "-z": "outlet",
}


class SymmetryDetectorTest(unittest.TestCase):
    def test_full_car_config_has_no_symmetry(self):
        cfg = {
            "case_name": "full_car",
            "symmetry_plane": 0.0,  # the Studio always writes this
            "domain_faces": FULL_CAR_FACES,
        }
        self.assertFalse(_config_has_symmetry(cfg))

    def test_detectors_agree_for_full_car(self):
        cfg = {
            "case_name": "full_car",
            "symmetry_plane": 0.0,
            "domain_faces": FULL_CAR_FACES,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case_config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            from_faces = is_symmetry_case(config_path=str(path))
        from_config = _config_has_symmetry(cfg)
        self.assertEqual(from_config, from_faces)
        self.assertFalse(from_faces)


class StudioPresetSyncTest(unittest.TestCase):
    """The Studio must not drift from the backend presets/defaults.

    The preset endpoint feeds the form placeholders, the JS fallback map backs
    them when the endpoint is unreachable, and the static hints quote values —
    all three have drifted before (expansion 1.2/1.15 vs the backend 1.1).
    """

    def test_schema_defaults_endpoint_matches_presets(self):
        from rapidfoam.web.routers.config import api_config_defaults

        payload = asyncio.run(api_config_defaults())
        for name, preset in FIDELITY_PRESETS.items():
            with self.subTest(fidelity=name):
                exposed = payload["fidelity_presets"][name]
                self.assertEqual(exposed["layers"]["expansion_ratio"], preset["expansion_ratio"])
                self.assertEqual(exposed["layers"]["n_layers"], preset["n_layers"])
                self.assertEqual(exposed["layers"]["y_plus_target"], preset["y_plus_target"])
                self.assertEqual(exposed["mesh"]["cells_per_length"], preset["cells_per_length"])
                self.assertEqual(exposed["mesh"]["surface_level"], preset["surface_level"])
                self.assertEqual(exposed["mesh"]["edge_level"], preset["edge_level"])
                self.assertEqual(
                    exposed["mesh"]["nCellsBetweenLevels"], preset["nCellsBetweenLevels"]
                )
                self.assertEqual(exposed["solver"]["end_time"], preset["end_time"])
                self.assertEqual(exposed["slurm"]["time"], preset["slurm_time"])

    def test_js_fallback_placeholders_match_presets(self):
        js = (REPO_ROOT / "src/rapidfoam/web/static/js/app.js").read_text(encoding="utf-8")
        for name, preset in FIDELITY_PRESETS.items():
            with self.subTest(fidelity=name):
                match = re.search(rf"{name}: \{{[^}}]*expansion: '([\d.]+)'", js)
                self.assertIsNotNone(match, f"{name} fallback entry missing")
                self.assertEqual(float(match.group(1)), float(preset["expansion_ratio"]))

    def test_html_hints_quote_current_presets(self):
        html = (REPO_ROOT / "src/rapidfoam/web/static/index.html").read_text(encoding="utf-8")
        fast = float(FIDELITY_PRESETS["fast"]["expansion_ratio"])
        standard = float(FIDELITY_PRESETS["standard"]["expansion_ratio"])
        fine = float(FIDELITY_PRESETS["fine"]["expansion_ratio"])
        expansion_hint = next(
            line for line in html.splitlines() if "Geometric growth ratio" in line
        )
        self.assertIn(f"fast {fast:.2f}", expansion_hint)
        self.assertIn(f"standard/fine {standard:.2f}", expansion_hint)
        self.assertEqual(standard, fine)
        # The two-pass hint quotes the bounded layering gate it actually emits.
        gate_hint = next(line for line in html.splitlines() if "Pass 1 castellates" in line)
        self.assertIn("maxNonOrtho 65", gate_hint)
        self.assertIn("maxInternalSkewness 5", gate_hint)
        # `fine` is a low-y+ wall-function-bridged mesh, not classical wall-resolved.
        yplus_hint = next(line for line in html.splitlines() if "Preset: fast 50" in line)
        self.assertIn("low-y+", yplus_hint)
        self.assertNotIn("wall-resolved", yplus_hint)


if __name__ == "__main__":
    unittest.main()
