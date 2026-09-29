"""MeshPlan purity and compatibility (Phase 2).

``build_mesh_plan`` must:
  * not mutate the config it is given;
  * be deterministic (two builds on the same input are equal);
  * produce the same derived values as the legacy ``compute_mesh_params`` +
    ``resolve_layers`` path, which ``apply_plan_to_cfg`` writes back.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.config import effective_config
from rapidfoam.meshing.layers import resolve_layers
from rapidfoam.meshing.params import compute_mesh_params
from rapidfoam.meshing.plan import apply_plan_to_cfg, build_mesh_plan
from rapidfoam.meshing.presets import apply_fidelity_preset
from rapidfoam.stl_utils import stl_bounds, write_stl


class MeshPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stl = self.root / "stl" / "body.stl"
        write_stl(self.stl, "body", [((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3))])
        self.bounds = stl_bounds(self.stl)
        self.cfg = effective_config({
            "case_name": "plan_test",
            "stl_files": ["body.stl"],
            "fidelity": "standard",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        })

    def test_build_does_not_mutate_config(self):
        cfg = apply_fidelity_preset(copy.deepcopy(self.cfg))
        before = json.dumps(cfg, sort_keys=True)
        build_mesh_plan(cfg, self.bounds)
        self.assertEqual(before, json.dumps(cfg, sort_keys=True))

    def test_build_is_deterministic(self):
        cfg = apply_fidelity_preset(copy.deepcopy(self.cfg))
        first = build_mesh_plan(cfg, self.bounds)
        second = build_mesh_plan(cfg, self.bounds)
        self.assertEqual(first.mesh_params, second.mesh_params)
        self.assertEqual(first.layers, second.layers)

    def test_matches_legacy_derivation(self):
        cfg = apply_fidelity_preset(copy.deepcopy(self.cfg))
        plan = build_mesh_plan(cfg, self.bounds)

        legacy_cfg = apply_fidelity_preset(copy.deepcopy(self.cfg))
        legacy_cfg["mesh_params"] = compute_mesh_params(legacy_cfg, self.bounds)
        legacy_resolved = resolve_layers(legacy_cfg, self.bounds)

        self.assertEqual(plan.mesh_params, legacy_cfg["mesh_params"])
        self.assertEqual(dict(plan.layers), legacy_cfg["layers"])
        self.assertEqual(dict(plan.layer_spec.resolved), legacy_resolved)

    def test_apply_plan_round_trip(self):
        cfg = apply_fidelity_preset(copy.deepcopy(self.cfg))
        plan = build_mesh_plan(cfg, self.bounds)
        apply_plan_to_cfg(cfg, plan)
        self.assertEqual(cfg["mesh_params"], plan.mesh_params)
        self.assertEqual(cfg["layers"], dict(plan.layers))


if __name__ == "__main__":
    unittest.main()
