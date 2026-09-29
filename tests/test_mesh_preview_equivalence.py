"""Preview == generation equivalence (PAIN_POINTS #1).

The Studio preview used to resolve only three of the ten preset fields the
generator applies, so the effective layer stack it displayed could differ from
the generated case. Both now call ``meshing.presets.apply_fidelity_preset``;
this test pins that contract across the fidelity presets and a layer override.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.cli import _do_generate
from rapidfoam.config import effective_config
from rapidfoam.stl_utils import stl_bounds, write_stl
from rapidfoam.web.server import layer_preview


class MeshPreviewEquivalenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stl = self.root / "stl" / "body.stl"
        write_stl(self.stl, "body", [((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))])

    def _compare(self, name: str, extra: dict) -> None:
        raw = {
            "case_name": name,
            "stl_files": ["body.stl"],
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            **extra,
        }
        path = self.root / "config.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        merged = effective_config(raw)
        bounds = stl_bounds(self.stl)
        preview = layer_preview(merged, raw, bounds)

        with contextlib.redirect_stdout(io.StringIO()):
            _do_generate(path, self.root)
        generated = json.loads((self.root / "cases" / name / "case_config.json").read_text(encoding="utf-8"))
        resolved = generated["layers"]["_resolved"]

        for key in ("mode", "y_plus_target"):
            self.assertEqual(preview.get(key), resolved.get(key), f"{name}: {key}")
        for key in ("first_layer_thickness", "min_thickness", "stack"):
            self.assertAlmostEqual(
                float(preview.get(key) or 0.0), float(resolved.get(key) or 0.0), places=9,
                msg=f"{name}: {key}",
            )
        self.assertEqual(preview.get("surface_level"), generated["mesh_params"]["surface_level"])
        self.assertEqual(preview.get("edge_level"), generated["mesh_params"]["edge_level"])

    def test_matches_for_each_fidelity(self):
        for fidelity in ("fast", "standard", "fine"):
            with self.subTest(fidelity=fidelity):
                self._compare(f"equiv_{fidelity}", {"fidelity": fidelity})

    def test_matches_with_explicit_layer_override(self):
        self._compare("equiv_override", {
            "fidelity": "standard",
            "overrides": {"layers": {"n_layers": 4, "expansion_ratio": 1.3}},
        })

    def test_matches_with_yplus_override(self):
        self._compare("equiv_yplus", {
            "fidelity": "fast",
            "overrides": {"layers": {"y_plus_target": 80}},
        })


if __name__ == "__main__":
    unittest.main()