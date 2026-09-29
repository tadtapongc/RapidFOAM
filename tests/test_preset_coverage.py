"""Fidelity-preset coverage regression tests.

Pins PAIN_POINTS.md #9c: the shipped ``configs/config.json`` used to write
concrete ``slurm.time`` / ``slurm.mem_per_cpu`` values, silently defeating the
fidelity-preset SLURM time/memory in ``run.sh``. The shipped config now omits
them and the generator resolves both with the same ``user_set`` guard.
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
from rapidfoam.stl_utils import write_stl

REPO_ROOT = Path(__file__).resolve().parents[1]


class PresetCoverageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_stl(self.root / "stl" / "body.stl", "body", [
            ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
        ])

    def _generate(self, cfg: dict) -> Path:
        path = self.root / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            _do_generate(path, self.root)
        return self.root / "cases" / cfg["case_name"]

    def test_minimal_config_uses_preset_slurm(self):
        case = self._generate({
            "case_name": "preset_min",
            "stl_files": ["body.stl"],
            "fidelity": "fast",
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        })
        run_sh = (case / "run.sh").read_text(encoding="utf-8")
        self.assertIn("--time=04:00:00", run_sh)

    def test_shipped_config_still_uses_preset_slurm(self):
        shipped = json.loads((REPO_ROOT / "configs" / "config.json").read_text(encoding="utf-8"))
        shipped.update({
            "case_name": "preset_shipped",
            "stl_files": ["body.stl"],
            "fidelity": "fine",
        })
        case = self._generate(shipped)
        run_sh = (case / "run.sh").read_text(encoding="utf-8")
        self.assertIn("--time=14:00:00", run_sh)
        self.assertIn("--mem-per-cpu=4G", run_sh)


if __name__ == "__main__":
    unittest.main()
