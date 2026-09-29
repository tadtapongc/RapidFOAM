"""Telemetry config-read regression tests.

These pin the bugs described in refactor-prep/PAIN_POINTS.md #9d and fixed by
Phase 1a (``core.caseconfig`` + full-history export):

  * the telemetry reference readers ignore the ``overrides`` block and return
    universal defaults instead of the values the case was generated with;
  * telemetry prefers the raw ``configs/<case>.json`` over the effective
    ``<case>/case_config.json``, so a preset-injected ``layers.y_plus_target``
    is invisible to the Mesh Quality panel;
  * ``/api/telemetry/export`` silently serialises the downsampled UI series
    (<= 400 points) rather than the full history.
"""

import asyncio
import json
import shutil
from pathlib import Path
import unittest

from rapidfoam.web.server import (
    api_telemetry_export,
    api_telemetry_forces,
    api_telemetry_mesh,
)

FORCE_HEADER = "# Time total(fx fy fz) pressure(fx fy fz) viscous(fx fy fz)\n"


def write_force_dat(path: Path, rows: int, drag: float = 50.0, downforce: float = 200.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [FORCE_HEADER]
    for i in range(1, rows + 1):
        lines.append(f"{i} (0.0 {-downforce} {-drag}) (0 0 0) (0 0 0)\n")
    path.write_text("".join(lines), encoding="utf-8")


class TelemetryConfigTest(unittest.TestCase):
    def setUp(self):
        self.name = "p0_telemetry_cfg"
        self.case_dir = Path("cases") / self.name
        self.cfg_path = Path("configs") / f"{self.name}.json"
        self.addCleanup(lambda: shutil.rmtree(self.case_dir, ignore_errors=True))
        self.addCleanup(lambda: self.cfg_path.unlink(missing_ok=True))
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_raw_config(self, raw: dict) -> None:
        self.cfg_path.write_text(json.dumps(raw), encoding="utf-8")

    def test_reference_values_honour_overrides(self):
        self._write_raw_config({
            "case_name": self.name,
            "stl_files": ["body.stl"],
            "flow": {"velocity": 20.2, "direction": "-z", "ground": True},
            "overrides": {
                "fluid": {"rho": 1.18},
                "force_refs": {"Aref": 0.85, "lRef": 1.25, "CofR": [0.1, 0.0, 0.5]},
            },
        })
        write_force_dat(self.case_dir / "postProcessing" / "forces" / "0" / "force.dat", 30)

        res = asyncio.run(api_telemetry_forces(self.name))
        self.assertTrue(res["has_data"])
        ref = res["reference"]
        self.assertAlmostEqual(ref["rho"], 1.18)
        self.assertAlmostEqual(ref["Aref"], 0.85)
        self.assertAlmostEqual(ref["lRef"], 1.25)
        self.assertEqual(ref["CofR"], [0.1, 0.0, 0.5])

    def test_preset_yplus_target_visible_to_mesh_panel(self):
        # Raw config has no layers (the target came from the fidelity preset).
        self._write_raw_config({
            "case_name": self.name,
            "stl_files": ["body.stl"],
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
        })
        (self.case_dir / "case_config.json").write_text(
            json.dumps({"layers": {"n_layers": 8, "y_plus_target": 30}}), encoding="utf-8"
        )
        (self.case_dir / "log.checkMesh").write_text(
            "    cells:            2000\nMesh non-orthogonality Max: 40.0 average: 2.0\nMesh OK.\n",
            encoding="utf-8",
        )
        (self.case_dir / "log.snappyHexMesh").write_text(
            "patch    faces    layers    overall thickness\n"
            "                  target   mesh     [m]       [%]\n"
            "-----    -----    -----    ----     ---       ---\n"
            "geometry 100      8        8        0.0005    98.0\n",
            encoding="utf-8",
        )
        yplus_dir = self.case_dir / "postProcessing" / "yPlus" / "0"
        yplus_dir.mkdir(parents=True, exist_ok=True)
        (yplus_dir / "yPlus.dat").write_text(
            "# Time\tpatch\tmin\tmax\taverage\n0\tgeometry\t20.0\t40.0\t31.0\n",
            encoding="utf-8",
        )

        res = asyncio.run(api_telemetry_mesh(self.name))
        self.assertTrue(res["has_data"])
        self.assertTrue(res["y_plus"]["available"])
        self.assertEqual(res["y_plus"]["target"], 30.0)

    def test_export_contains_full_history(self):
        self._write_raw_config({
            "case_name": self.name,
            "stl_files": ["body.stl"],
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
        })
        write_force_dat(self.case_dir / "postProcessing" / "forces" / "0" / "force.dat", 500)

        response = asyncio.run(api_telemetry_export(self.name, "csv"))
        lines = response.body.decode("utf-8").strip().splitlines()
        data_rows = len(lines) - 1  # drop the header
        self.assertGreaterEqual(data_rows, 500)


if __name__ == "__main__":
    unittest.main()
