"""Studio config-sync pinning tests (Phase 0 safety net).

Pins PAIN_POINTS.md #9b: two symmetry detectors disagree on the same
``case_config.json``. ``checkmesh._config_has_symmetry`` returns True whenever
``symmetry_plane`` is numeric (which the Studio always writes, even for a full
car), while ``forces.is_symmetry_case`` decides from ``domain_faces``. The
expectedFailure test documents the disagreement; Phase 1 canonicalizes both on
one override-aware reader.
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
    @unittest.expectedFailure  # PAIN_POINTS #9b: symmetry_plane alone marks a half model
    def test_full_car_config_has_no_symmetry(self):
        cfg = {
            "case_name": "full_car",
            "symmetry_plane": 0.0,  # the Studio always writes this
            "domain_faces": FULL_CAR_FACES,
        }
        self.assertFalse(_config_has_symmetry(cfg))

    @unittest.expectedFailure  # PAIN_POINTS #9b: the two detectors disagree
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
