"""Quality-tier tuning contract (meshing/quality.py single source).

Pins the robustness retune: the tiers used by the emitter come from one module,
``DEFAULT_CONFIG`` carries exactly those tiers, and the two-pass layering gate
is a *bounded* relaxation of the main gate (a looser gate previously pinned the
final mesh at ~80 deg non-orthogonality and "Bad" verdicts on every real case).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.config import DEFAULT_CONFIG, load_config, validate
from rapidfoam.geometry.stl import write_stl
from rapidfoam.meshing.quality import (
    LAYERING_LIMITS,
    QUALITY_LIMITS,
    RELAXED_LIMITS,
    resolve_quality,
)


class QualityTierDefaultsTest(unittest.TestCase):
    def test_default_config_carries_the_tiers(self):
        quality = DEFAULT_CONFIG["mesh_quality"]
        for key, value in QUALITY_LIMITS.items():
            self.assertEqual(quality[key], value, key)
        self.assertEqual(quality["relaxed"], dict(RELAXED_LIMITS))
        self.assertEqual(quality["layering_relaxed"], dict(LAYERING_LIMITS))

    def test_layering_gate_is_bounded_by_design(self):
        # The layering pass is the final quality bound: it must be a real
        # relaxation of the main gate, but not a collapse.
        self.assertGreater(LAYERING_LIMITS["maxNonOrtho"], QUALITY_LIMITS["maxNonOrtho"])
        self.assertLess(LAYERING_LIMITS["maxNonOrtho"], RELAXED_LIMITS["maxNonOrtho"])
        self.assertGreater(
            LAYERING_LIMITS["maxInternalSkewness"], QUALITY_LIMITS["maxInternalSkewness"]
        )
        self.assertLessEqual(LAYERING_LIMITS["maxInternalSkewness"], 5)

    def test_main_gate_sits_at_practical_limits(self):
        self.assertEqual(QUALITY_LIMITS["maxNonOrtho"], 60)
        self.assertEqual(QUALITY_LIMITS["maxBoundarySkewness"], 20)
        self.assertEqual(QUALITY_LIMITS["maxInternalSkewness"], 3.5)


class ResolveQualityTest(unittest.TestCase):
    def test_missing_config_resolves_to_defaults(self):
        profile = resolve_quality(None)
        self.assertEqual(profile.main, {k: float(v) for k, v in QUALITY_LIMITS.items()})
        for key, value in LAYERING_LIMITS.items():
            self.assertEqual(profile.layering[key], value, key)
        # Keys not overridden inherit the strict main values, never a hidden default.
        self.assertEqual(profile.layering["minVolRatio"], QUALITY_LIMITS["minVolRatio"])

    def test_user_override_is_coerced_and_honoured(self):
        profile = resolve_quality(
            {"maxNonOrtho": 55, "minVol": "bad", "relaxed": {"maxNonOrtho": "bad"}}
        )
        self.assertEqual(profile.main["maxNonOrtho"], 55)
        self.assertEqual(profile.main["minVol"], QUALITY_LIMITS["minVol"])
        self.assertEqual(profile.relaxed["maxNonOrtho"], RELAXED_LIMITS["maxNonOrtho"])

    def test_tiers_are_independent_when_main_is_tightened(self):
        # Predictability: tightening the main gate does not silently move the
        # other tiers; the user sets layering_relaxed explicitly if wanted.
        profile = resolve_quality({"maxNonOrtho": 50})
        self.assertEqual(profile.main["maxNonOrtho"], 50)
        self.assertEqual(profile.layering["maxNonOrtho"], LAYERING_LIMITS["maxNonOrtho"])
        overridden = resolve_quality({"maxNonOrtho": 50, "layering_relaxed": {"maxNonOrtho": 62}})
        self.assertEqual(overridden.layering["maxNonOrtho"], 62)

    def test_layering_override_keeps_other_tiers_strict(self):
        profile = resolve_quality({"layering_relaxed": {"maxNonOrtho": 70, "minDeterminant": 1e-6}})
        self.assertEqual(profile.layering["maxNonOrtho"], 70)
        self.assertEqual(profile.layering["minDeterminant"], 1e-6)
        self.assertEqual(profile.layering["minVolRatio"], QUALITY_LIMITS["minVolRatio"])


class LayerGateValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", [
            ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
        ])

    def _warnings(self, mesh_quality):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "case_name": "quality_case",
            "stl_files": ["body.stl"],
            "mesh_quality": mesh_quality,
        }), encoding="utf-8")
        _, warnings = validate(load_config(path), self.root)
        return [w for w in warnings if "layering_relaxed.maxNonOrtho" in w]

    def test_default_margin_does_not_warn(self):
        self.assertEqual([], self._warnings({}))

    def test_tightened_main_gate_warns_when_layering_stays_loose(self):
        self.assertEqual(1, len(self._warnings({"maxNonOrtho": 50})))

    def test_matched_layering_gate_does_not_warn(self):
        self.assertEqual([], self._warnings({
            "maxNonOrtho": 50,
            "layering_relaxed": {"maxNonOrtho": 55},
        }))

    def test_relaxed_override_must_be_numeric(self):
        path = self.root / "bad_relaxed.json"
        path.write_text(json.dumps({
            "case_name": "quality_case",
            "stl_files": ["body.stl"],
            "mesh_quality": {"relaxed": {"maxNonOrtho": "tight"}},
        }), encoding="utf-8")
        errors, _ = validate(load_config(path), self.root)
        self.assertTrue(any("mesh_quality.relaxed.maxNonOrtho" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
