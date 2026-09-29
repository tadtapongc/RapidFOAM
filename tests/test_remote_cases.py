"""Parity between the cluster inventory script and postproc.forces (Phase 5c).

The runtime script used to be an unsearchable string embedded in ssh_client.
It now lives in ``runtime/remote_cases.py``; this test runs it against a fixture
case and checks its force summary matches the canonical reader.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.postproc.forces import check_convergence, load_axis_config, read_forces

RUNTIME = Path(__file__).resolve().parents[1] / "src" / "rapidfoam" / "runtime" / "remote_cases.py"

FORCE_HEADER = "# Time total(fx fy fz) pressure(fx fy fz) viscous(fx fy fz)\n"


def run_inventory(repo: Path) -> list[dict]:
    argv = sys.argv
    sys.argv = ["remote_cases.py", str(repo)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            runpy.run_path(str(RUNTIME), run_name="__main__")
    finally:
        sys.argv = argv
    out = buf.getvalue()
    payload = out.split("__CASE_JSON_START__")[1].split("__CASE_JSON_END__")[0]
    return json.loads(payload)


class RemoteCasesParityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        case = self.repo / "cases" / "demo"
        forces = case / "postProcessing" / "forces" / "0"
        forces.mkdir(parents=True, exist_ok=True)
        lines = [FORCE_HEADER]
        for i in range(1, 41):
            lines.append(f"{i} (0.0 -200.0 -50.0) (0 0 0) (0 0 0)\n")
        (forces / "force.dat").write_text("".join(lines), encoding="utf-8")
        (case / "case_config.json").write_text(json.dumps({
            "flow": {"velocity": 20.0, "direction": "-z"},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "fidelity": "standard",
            "domain_faces": {
                "-x": "farField", "+x": "farField", "-y": "ground",
                "+y": "farField", "+z": "inlet", "-z": "outlet",
            },
        }), encoding="utf-8")
        self.case = case

    def test_force_summary_matches_canonical_reader(self):
        result = {c["name"]: c for c in run_inventory(self.repo)}["demo"]

        di, ds, fi, fs, _, _ = load_axis_config(
            str(self.case / "case_config.json"), case_dir=self.case
        )
        times, drags, downforces = read_forces(
            [self.case / "postProcessing" / "forces" / "0" / "force.dat"], di, ds, fi, fs
        )
        _, _, _, d_avg, f_avg = check_convergence(drags, downforces)

        self.assertEqual(result["latest_iter"], int(times[-1]))
        self.assertAlmostEqual(result["drag"], round(d_avg, 2), places=2)
        self.assertAlmostEqual(result["downforce"], round(f_avg, 2), places=2)


if __name__ == "__main__":
    unittest.main()
