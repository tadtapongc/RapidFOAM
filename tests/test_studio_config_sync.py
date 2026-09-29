"""Studio config-sync regression tests.

Pins PAIN_POINTS.md #9b: two symmetry detectors used to disagree on the same
``case_config.json``. ``checkmesh._config_has_symmetry`` returned True whenever
``symmetry_plane`` was numeric (which the Studio always writes, even for a full
car), while ``forces.is_symmetry_case`` decides from ``domain_faces``. Phase 1a
canonicalized both on ``core.caseconfig.has_symmetry`` (face assignment first).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.postproc.checkmesh import _config_has_symmetry
from rapidfoam.postproc.forces import is_symmetry_case

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


if __name__ == "__main__":
    unittest.main()
