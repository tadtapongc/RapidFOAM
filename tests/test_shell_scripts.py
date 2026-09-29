"""Execute generated scripts with fake OpenFOAM commands in isolated Linux cases."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rapidfoam.config import DEFAULT_CONFIG
from rapidfoam.writers.scripts import write_scripts


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Requires Linux bash")
class ShellScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rapidfoam_scripts_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.case = self.root / "case with spaces"
        (self.case / "system").mkdir(parents=True)
        (self.case / "system/controlDict").write_text("stopAt endTime;\n")
        (self.case / "constant/triSurface").mkdir(parents=True)
        (self.case / "constant/triSurface/sample.stl").write_text(
            "solid sample\nendsolid sample\n"
        )
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        runfunctions = self.root / "foam/bin/tools/RunFunctions"
        runfunctions.parent.mkdir(parents=True)
        runfunctions.write_text('''runApplication() {
    if [ "$1" = "-s" ]; then shift 2; fi
    "$@"
}
runParallel() {
    if [ "$1" = "-s" ]; then shift 2; fi
    "$@"
}
''')
        tool = self.bin / "fake-tool"
        tool.write_text('''#!/bin/bash
name=$(basename "$0")
if [ "$name" = "$FAIL_STAGE" ]; then exit 31; fi
case "$name" in
    decomposePar)
        mkdir -p processor0
        echo recoverable > processor0/state
        ;;
    simpleFoam)
        touch "$HARNESS_ROOT/solver.started"
        if [ "$WAIT_SOLVER" = 1 ]; then
            while true; do sleep 0.1; done
        fi
        exit "${FAKE_SOLVER_STATUS:-0}"
        ;;
    reconstructPar)
        if [ "${RECONSTRUCT_STATUS:-0}" -ne 0 ]; then exit "$RECONSTRUCT_STATUS"; fi
        cp processor0/state reconstructed
        ;;
    surfaceCheck)
        case "${SURFACE_MODE:-}" in
            open)
                echo "Surface is not closed since not all edges connected to two faces:"
                echo "    connected to one face : 420"
                echo "    connected to >2 faces : 0"
                ;;
            self)
                echo "Surface is closed. All edges connected to two faces."
                echo "Surface is self-intersecting at 3 locations."
                ;;
            illegal)
                echo "Surface has 3 illegal triangles."
                ;;
            *)
                echo "Surface has no illegal triangles."
                echo "Surface is closed. All edges connected to two faces."
                echo "Number of unconnected parts : 1"
                ;;
        esac
        ;;
    snappyHexMesh)
        echo "$*" >> "$HARNESS_ROOT/snappy.log"
        ;;
    mpirun)
        shift 2
        exec "$@"
        ;;
    python3)
        echo $$ > "$HARNESS_ROOT/monitor.pid"
        trap 'exit 0' TERM INT
        while true; do sleep 0.1; done
        ;;
    rsync)
        args=("$@")
        count=${#args[@]}
        src=${args[$((count-2))]}
        dst=${args[$((count-1))]}
        if [ "$COPYBACK_FAIL" = 1 ] && [ "$dst" = "$ORIG_CASE/" ]; then exit 42; fi
        cp -a "$src/." "$dst/"
        ;;
esac
''')
        tool.chmod(0o755)
        for name in ("surfaceFeatureExtract", "surfaceCheck", "blockMesh", "decomposePar", "snappyHexMesh",
                     "reconstructParMesh", "checkMesh", "renumberMesh", "potentialFoam",
                     "simpleFoam", "reconstructPar", "mpirun", "python3", "rsync", "module"):
            (self.bin / name).symlink_to(tool)
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "WM_PROJECT_DIR": str(self.root / "foam"), "SLURM_JOB_ID": "test",
                    "SLURM_NTASKS": "2", "TMPDIR": str(self.scratch),
                    "HARNESS_ROOT": str(self.root), "ORIG_CASE": str(self.case),
                    "FOAM_INST_DIR": "", "FAIL_STAGE": "", "COPYBACK_FAIL": "0",
                    "SURFACE_MODE": "",
                    "WAIT_SOLVER": "0", "SOLVER_STATUS": "0", "RECONSTRUCT_STATUS": "0"}

    def generate(self, surface=None):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["parallel"]["n_procs"] = 2
        cfg["slurm"].update(openfoam_source=None, openfoam_module=None)
        if surface:
            cfg["surface_check"].update(surface)
        write_scripts(cfg, self.case)

    def run_script(self, name, **env):
        if "SOLVER_STATUS" in env:
            env["FAKE_SOLVER_STATUS"] = env.pop("SOLVER_STATUS")
        return subprocess.run(["bash", str(self.case/name)], cwd=self.case,
                              env={**self.env, **env}, text=True, capture_output=True, timeout=15)

    def assert_monitor_stopped(self):
        path = self.root/"monitor.pid"
        if path.exists():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(path.read_text()), 0)

    def test_all_generated_shell_syntax(self):
        self.generate()
        for name in ("Allrun", "Allrun.parallel", "Allclean", "run.sh"):
            result = subprocess.run(["bash", "-n", str(self.case/name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_serial_solver_failure_is_reported(self):
        self.generate()
        result = self.run_script("Allrun", SOLVER_STATUS="17")
        self.assertEqual(result.returncode, 17, result.stdout+result.stderr)
        self.assertNotIn("Done.", result.stdout)
        self.assert_monitor_stopped()

    def test_parallel_solver_failure_recovers_results_and_reports_failure(self):
        self.generate()
        result = self.run_script("Allrun.parallel", SOLVER_STATUS="17")
        self.assertEqual(result.returncode, 17, result.stdout+result.stderr)
        self.assertEqual((self.case/"reconstructed").read_text().strip(), "recoverable")
        self.assertFalse((self.case/"processor0").exists())
        self.assert_monitor_stopped()

    def test_parallel_reconstruction_failure_keeps_processors(self):
        self.generate()
        result = self.run_script("Allrun.parallel", RECONSTRUCT_STATUS="23")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.case/"processor0/state").read_text().strip(), "recoverable")

    def test_slurm_in_place_reconstruction_failure_keeps_processors(self):
        self.generate()
        result = self.run_script("run.sh", RECONSTRUCT_STATUS="23")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.case/"processor0/state").exists())
        self.assert_monitor_stopped()

    def test_slurm_solver_failure_survives_successful_cleanup(self):
        self.generate()
        result = self.run_script("run.sh", SOLVER_STATUS="17")
        self.assertEqual(result.returncode, 17, result.stdout+result.stderr)
        self.assertTrue((self.case/"reconstructed").exists())

    def test_slurm_success(self):
        self.generate()
        result = self.run_script("run.sh")
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertTrue((self.case/"reconstructed").exists())
        self.assert_monitor_stopped()

    def test_failed_meshing_preserves_mesh_processors(self):
        self.generate()
        result = self.run_script("run.sh", FAIL_STAGE="snappyHexMesh")
        self.assertEqual(result.returncode, 31, result.stdout+result.stderr)
        self.assertTrue((self.case/"processor0/state").exists())
        self.assertFalse((self.case/"reconstructed").exists())

    def test_surface_check_gate_passes_on_clean_geometry(self):
        self.generate()
        result = self.run_script("run.sh")
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertTrue((self.case/"log.surfaceCheck").exists())
        self.assertTrue((self.case/"reconstructed").exists())

    def test_surface_check_report_only_does_not_stop_on_open_geometry(self):
        # Default surface_check.enforce is off: the run reports but continues.
        self.generate()
        result = self.run_script("run.sh", SURFACE_MODE="open")
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertTrue((self.case/"log.surfaceCheck").exists())
        self.assertTrue((self.case/"reconstructed").exists())
        self.assertIn("report-only", result.stdout)

    def test_surface_check_enforce_aborts_on_open_geometry(self):
        self.generate(surface={"enforce": True})
        result = self.run_script("run.sh", SURFACE_MODE="open")
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse((self.case/"processor0").exists())
        self.assertIn("not closed", result.stderr)

    def test_surface_check_enforce_aborts_on_self_intersection(self):
        self.generate(surface={"enforce": True})
        result = self.run_script("Allrun", SURFACE_MODE="self")
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse((self.case/"processor0").exists())
        self.assertIn("self-intersecting", result.stderr)

    def test_surface_check_enforce_aborts_on_illegal_triangles(self):
        self.generate(surface={"enforce": True})
        result = self.run_script("Allrun.parallel", SURFACE_MODE="illegal")
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse((self.case/"processor0").exists())
        self.assertIn("illegal triangles", result.stderr)

    def test_layering_pass_runs_when_dict_present(self):
        self.generate()
        (self.case/"system/snappyHexMeshDict_layering").write_text("", encoding="utf-8")
        result = self.run_script("Allrun")
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        log = (self.root/"snappy.log").read_text(encoding="utf-8")
        self.assertIn("layering", log)

    def test_sigterm_attempts_recovery_without_losing_failed_results(self):
        self.generate()
        proc = subprocess.Popen(["bash", str(self.case/"run.sh")], cwd=self.case,
                                env={**self.env, "WAIT_SOLVER": "1", "RECONSTRUCT_STATUS": "23"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            deadline = time.monotonic()+5
            while not (self.root/"solver.started").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root/"solver.started").exists())
            os.killpg(proc.pid, signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 143, stdout+stderr)
            self.assertTrue((self.case/"processor0/state").exists())
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()


if __name__ == "__main__":
    unittest.main()
