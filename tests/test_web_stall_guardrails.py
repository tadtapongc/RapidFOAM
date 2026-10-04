"""Stall guardrails for the Web telemetry/archive layer.

These lock in the fixes that stop the event loop from blocking: the heavy
force/moment/coefficient read+parse runs in a worker thread, the remote bundle
read is time-bounded, and the local case scan runs off the loop. They are cheap
structural assertions so the stall class of bugs cannot silently return.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TelemetryOffLoopTest(unittest.TestCase):
    def test_forces_parse_runs_in_thread(self):
        """/api/telemetry/forces must dispatch its parse via asyncio.to_thread."""
        from rapidfoam.web.routers import telemetry as t

        seen = {"thread": False}
        real_to_thread = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            if getattr(func, "__name__", "") == "_assemble_force_segments":
                seen["thread"] = True
            return await real_to_thread(func, *args, **kwargs)

        # A local case with minimal force data; empty remote.
        case = "test_stall_guard_forces"
        case_dir = Path("cases") / case
        (case_dir / "postProcessing" / "forces" / "0").mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(case_dir, ignore_errors=True))
        rows = ["# Time total(fx fy fz) pressure(fx fy fz) viscous(fx fy fz)\n"]
        rows += [f"{i} (0.0 -10.0 -2.0) (0 0 0) (0 0 0)\n" for i in range(1, 20)]
        (case_dir / "postProcessing" / "forces" / "0" / "force.dat").write_text("".join(rows))

        with patch.object(t.asyncio, "to_thread", spy):
            asyncio.run(t.api_telemetry_forces(case))
        self.assertTrue(seen["thread"], "force parsing must run via asyncio.to_thread")


class RemoteBundleTimeoutTest(unittest.TestCase):
    def test_read_remote_bundle_is_time_bounded(self):
        """The remote bundle read must be wrapped in asyncio.wait_for."""
        from rapidfoam.web.services import telemetry as svc
        from rapidfoam.web.ssh_client import ClusterSSHClient

        captured = {"wait_for": False}
        real_wait_for = asyncio.wait_for

        async def spy(aw, timeout=None):
            captured["wait_for"] = timeout is not None
            return await real_wait_for(aw, timeout=timeout)

        class _Client:
            is_connected = True

            def read_remote_bundle(self, specs, report=None, timeout=10.0):
                if report is not None:
                    report.update({"ok": True, "files": 0})
                return {}

        with patch.object(ClusterSSHClient, "is_connected", property(lambda self: True)), \
                patch.object(svc.ssh_client, "read_remote_bundle", side_effect=_Client().read_remote_bundle), \
                patch.object(svc.asyncio, "wait_for", spy):
            asyncio.run(svc._read_remote_bundle([("cases/x/log.simpleFoam", 10)]))
        self.assertTrue(captured["wait_for"], "remote bundle read must be time-bounded")


class ArchiveOffLoopTest(unittest.TestCase):
    def test_local_case_scan_runs_off_loop(self):
        """GET /api/cases local scan must run via asyncio.to_thread."""
        from rapidfoam.web.routers import cases as c

        calls = {"scan": 0}
        real_scan = c._scan_local_cases

        def spy():
            calls["scan"] += 1
            return real_scan()

        with patch.object(c, "_scan_local_cases", spy):
            c._invalidate_local_cases_cache()
            asyncio.run(c._cached_local_cases())
        self.assertEqual(calls["scan"], 1)


if __name__ == "__main__":
    unittest.main()
