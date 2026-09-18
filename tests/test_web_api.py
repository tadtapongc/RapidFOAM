"""Automated tests for Web API endpoints and Cluster SSH client using standard unittest."""

import asyncio
import json
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch, PropertyMock
from fastapi import HTTPException

from rapidfoam.web.server import (
    DomainBoxRequest,
    GenerateCaseRequest,
    JobCancelRequest,
    api_get_saved_config,
    api_config_defaults,
    api_config_load_file,
    api_config_templates,
    api_stl_list,
    api_get_stl_file,
    api_case_generate_and_submit,
    api_case_cancel,
    api_geometry_domain_box,
    api_telemetry_forces,
    api_telemetry_residuals,
    api_telemetry_logs,
    api_telemetry_export,
    api_telemetry_solver,
    parse_solver_diagnostics_from_log,
    api_list_cases,
    api_case_delete,
    api_stl_check_exists,
    api_case_check_exists,
    merge_config_with_defaults,
    ssh_client,
)
from rapidfoam.web import server as web_server
from rapidfoam.web.ssh_client import ClusterSSHClient
from rapidfoam.web.ssh_client import FILE_BEGIN, FILE_END, _parse_marked_bundle
from rapidfoam.postproc.forces import is_symmetry_case


class TestWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample_stl = Path("stl/sample_wing.stl")
        if not cls.sample_stl.exists():
            cls.sample_stl.parent.mkdir(parents=True, exist_ok=True)
            cls.sample_stl.write_text(
                "solid sample_wing\n"
                "  facet normal 0 0 1\n"
                "    outer loop\n"
                "      vertex 0 0 0\n"
                "      vertex 1 0 0\n"
                "      vertex 0 1 0\n"
                "    endloop\n"
                "  endfacet\n"
                "endsolid sample_wing\n"
            )
            cls.created_stl = True
        else:
            cls.created_stl = False

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "created_stl", False) and cls.sample_stl.exists():
            cls.sample_stl.unlink(missing_ok=True)

    def setUp(self):
        # Remote telemetry is cached briefly; never leak state across tests.
        web_server._remote_telemetry_cache.clear()

    def test_saved_cluster_config(self):
        """Test retrieving cached cluster config with password redacted."""
        res = asyncio.run(api_get_saved_config())
        self.assertIn("host", res)
        self.assertIn("username", res)
        self.assertIn("remote_repo_path", res)
        self.assertIn("has_saved_password", res)
        self.assertNotIn("saved_password", res)

    def test_config_schema_defaults(self):
        """Test schema defaults endpoint."""
        res = asyncio.run(api_config_defaults())
        self.assertIn("default_config", res)
        self.assertIn("fidelity_presets", res)
        self.assertIn("fast", res["fidelity_presets"])
        self.assertIn("standard", res["fidelity_presets"])
        standard = res["fidelity_presets"]["standard"]
        self.assertEqual(standard.get("layers", {}).get("y_plus_target"), 40)
        self.assertFalse(standard.get("layers", {}).get("ground_layers"))
        self.assertEqual(standard.get("mesh", {}).get("cells_per_length"), 30)
        self.assertIn("mesh", standard)
        self.assertIn("solver", standard)

    def test_config_templates(self):
        """Test templates list endpoint."""
        templates = asyncio.run(api_config_templates())
        self.assertIsInstance(templates, list)
        self.assertTrue(any(t["filename"] == "config.json" for t in templates))

    def test_config_load_file(self):
        """Test loading config.json."""
        res = asyncio.run(api_config_load_file("config.json"))
        self.assertEqual(res["filename"], "config.json")
        self.assertIn("raw_config", res)
        self.assertEqual(res["raw_config"]["case_name"], "my_case")

    def test_config_load_file_traversal_blocked(self):
        """Test that directory traversal in load-file is blocked."""
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api_config_load_file("../secret.json"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_stl_list(self):
        """Test listing STLs in stl/ directory without duplicate paths."""
        stls = asyncio.run(api_stl_list())
        self.assertIsInstance(stls, list)
        filenames = [s["filename"] for s in stls]
        self.assertEqual(len(filenames), len(set(filenames)))

    def test_stl_file_serving(self):
        """Test serving raw STL file."""
        res = asyncio.run(api_get_stl_file("sample_wing.stl"))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(Path(res.path).exists())

    def test_validation_endpoint(self):
        """Test generating/validating a config."""
        test_cfg_path = Path("configs/test_case_web.json")
        self.addCleanup(lambda: test_cfg_path.unlink(missing_ok=True))

        valid_cfg = {
            "case_name": "test_case_web",
            "stl_files": ["sample_wing.stl"],
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "parallel": {"n_procs": 16},
        }

        req = GenerateCaseRequest(
            config=valid_cfg,
            upload_to_cluster=False,
            generate_remotely=False,
            submit_slurm=False,
            generate_locally=False,
        )
        res = asyncio.run(api_case_generate_and_submit(req))
        self.assertTrue(res["success"])
        self.assertEqual(res["case_name"], "test_case_web")

    def test_case_name_validation_injection(self):
        """Test that malicious case names are rejected."""
        invalid_cfgs = [
            {"case_name": "bad;name", "stl_files": ["sample_wing.stl"]},
            {"case_name": "bad'name", "stl_files": ["sample_wing.stl"]},
            {"case_name": "bad name", "stl_files": ["sample_wing.stl"]},
        ]
        for cfg in invalid_cfgs:
            req = GenerateCaseRequest(
                config=cfg,
                upload_to_cluster=False,
                generate_remotely=False,
                submit_slurm=False,
                generate_locally=False,
            )
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_case_generate_and_submit(req))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_job_id_validation_injection(self):
        """Test that non-numeric job IDs are rejected in job cancel."""
        req = JobCancelRequest(job_id="123; rm -rf /")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api_case_cancel(req))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_local_case_generation(self):
        """Test local case generation through the API endpoint."""
        case_name = "test_case_local_gen"
        test_cfg_path = Path(f"configs/{case_name}.json")
        test_case_dir = Path(f"cases/{case_name}")
        self.addCleanup(lambda: test_cfg_path.unlink(missing_ok=True))
        self.addCleanup(lambda: shutil.rmtree(test_case_dir, ignore_errors=True))

        valid_cfg = {
            "case_name": case_name,
            "stl_files": ["sample_wing.stl"],
            "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "parallel": {"n_procs": 8},
        }

        req = GenerateCaseRequest(
            config=valid_cfg,
            upload_to_cluster=False,
            generate_remotely=False,
            submit_slurm=False,
            generate_locally=True,
        )
        res = asyncio.run(api_case_generate_and_submit(req))
        self.assertTrue(res["success"])
        self.assertTrue(res["local_actions"].get("generated_locally"))
        self.assertTrue((test_case_dir / "system" / "controlDict").is_file())

    def test_telemetry_logs_whitelist(self):
        """Test that arbitrary log_types are rejected."""
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api_telemetry_logs("my_case", log_type="malicious_type"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_telemetry_forces_and_convergence(self):
        """Test telemetry forces calculation and symmetry scaling."""
        case_name = "test_case_telemetry"
        case_dir = Path(f"cases/{case_name}")
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        # Write force.dat with total(fx fy fz)
        force_lines = ["# Time total(fx fy fz) pressure(fx fy fz) viscous(fx fy fz)\n"]
        for i in range(1, 60):
            # t, total(0.0 -200.0 -50.0) -> fy=-200, fz=-50
            force_lines.append(f"{i} (0.0 -200.0 -50.0) (0 0 0) (0 0 0)\n")
        (forces_dir / "force.dat").write_text("".join(force_lines))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertTrue(res["has_data"])
        self.assertEqual(res["case_name"], case_name)
        self.assertEqual(res["total_iterations"], 59)
        # Default axes: drag = -fz = 50.0, df = -fy = 200.0
        self.assertAlmostEqual(res["drag_avg"], 50.0, places=1)
        self.assertAlmostEqual(res["downforce_avg"], 200.0, places=1)

    def test_telemetry_residuals_alignment(self):
        """Test telemetry residuals alignment where variable arrays have equal length."""
        case_name = "test_case_residuals"
        case_dir = Path(f"cases/{case_name}")
        case_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        log_lines = []
        for it in range(1, 15):
            log_lines.append(f"Time = {it}\n")
            log_lines.append("Solving for Ux, Initial residual = 0.05, Final residual = 0.001, No Iterations 5\n")
            log_lines.append("Solving for Uy, Initial residual = 0.04, Final residual = 0.001, No Iterations 5\n")
            log_lines.append("Solving for Uz, Initial residual = 0.03, Final residual = 0.001, No Iterations 5\n")
            # Multiple p correctors
            log_lines.append("Solving for p, Initial residual = 0.02, Final residual = 0.001, No Iterations 10\n")
            log_lines.append("Solving for p, Initial residual = 0.005, Final residual = 0.0001, No Iterations 10\n")
            log_lines.append("Solving for k, Initial residual = 0.01, Final residual = 0.0005, No Iterations 3\n")
            log_lines.append("Solving for omega, Initial residual = 0.008, Final residual = 0.0002, No Iterations 3\n")
        (case_dir / "log.simpleFoam").write_text("".join(log_lines))

        res = asyncio.run(api_telemetry_residuals(case_name))
        self.assertTrue(res["has_data"])
        n_iters = len(res["iterations"])
        self.assertEqual(n_iters, 14)
        for var, series in res["residuals"].items():
            self.assertEqual(len(series), n_iters, f"Residuals for {var} must match iteration count")
        # Ensure initial residual for p was captured (0.02), not the second corrector (0.005)
        self.assertAlmostEqual(res["residuals"]["p"][0], 0.02)

    def test_telemetry_residuals_solver_info(self):
        """Test telemetry residuals reading from postProcessing/residuals/0/solverInfo.dat."""
        case_name = "test_case_solver_info"
        case_dir = Path(f"cases/{case_name}")
        res_dir = case_dir / "postProcessing" / "residuals" / "0"
        res_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        header = "# Time\tUx_initial\tUy_initial\tUz_initial\tp_initial\tk_initial\tomega_initial\n"
        lines = [header]
        for it in range(1, 21):
            lines.append(f"{it}\t{0.01/it}\t{0.02/it}\t{0.03/it}\t{1.0/it}\t{0.5/it}\t{0.001/it}\n")
        (res_dir / "solverInfo.dat").write_text("".join(lines))

        res = asyncio.run(api_telemetry_residuals(case_name))
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_iterations"], 20)
        self.assertEqual(res["latest_iteration"], 20)
        self.assertAlmostEqual(res["residuals"]["p"][0], 1.0)
        self.assertAlmostEqual(res["residuals"]["Ux"][0], 0.01)
        self.assertAlmostEqual(res["residuals"]["omega"][0], 0.001)

    def test_ssh_client_parse_squeue(self):
        """Test SLURM squeue parsing logic."""
        c = ClusterSSHClient()
        mock_squeue_output = (
            "1234567|aero_sim|cpu|RUNNING|00:15:22|08:00:00|1|None\n"
            "1234568|Wing_V2|cpu|PENDING|00:00:00|04:00:00|1|Priority\n"
        )
        with patch.object(c, "run_command", return_value=(0, mock_squeue_output, "")):
            jobs = c.get_slurm_queue("testuser")
            self.assertEqual(len(jobs), 2)
            self.assertEqual(jobs[0]["job_id"], "1234567")
            self.assertEqual(jobs[0]["name"], "aero_sim")
            self.assertEqual(jobs[0]["state"], "RUNNING")
            self.assertEqual(jobs[1]["state"], "PENDING")

    def test_ssh_client_submit_job(self):
        """Test SLURM sbatch submission parsing."""
        c = ClusterSSHClient()
        c.remote_repo_path = "/work/home/testuser/repo"
        mock_sbatch_out = "Submitted batch job 987654\n"
        with patch.object(c, "run_command", return_value=(0, mock_sbatch_out, "")):
            res = c.submit_job("case_test")
            self.assertTrue(res["success"])
            self.assertEqual(res["job_id"], "987654")

    def test_geometry_domain_box(self):
        """Test computing wind tunnel domain box from geometry bounds."""
        req = DomainBoxRequest(
            config={
                "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
                "fidelity": "standard",
                "symmetry_plane": -0.1185,
                "ground_clearance": 0.035,
            },
            bounds={"min": [-0.7, 0.035, -1.8], "max": [0.7, 1.1, 1.2]},
        )
        res = asyncio.run(api_geometry_domain_box(req))
        self.assertIn("domain_box", res)
        self.assertIn("min", res["domain_box"])
        self.assertIn("max", res["domain_box"])
        self.assertEqual(len(res["domain_box"]["min"]), 3)
        self.assertEqual(len(res["domain_box"]["max"]), 3)

    def test_ground_clearance_styles(self):
        """Test 2 + 1 ground clearance styles: None (touching CAD bottom), Relative, and Absolute."""
        bounds = {"min": [-0.7, 0.035, -1.8], "max": [0.7, 1.1, 1.2]}
        base_cfg = {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "fidelity": "standard",
            "symmetry_plane": -0.1185,
        }

        # Style 0 / None (Default): road touches lowest CAD vertex smin[1] = 0.035
        res_none = asyncio.run(api_geometry_domain_box(DomainBoxRequest(config=base_cfg, bounds=bounds)))
        self.assertAlmostEqual(res_none["domain_box"]["min"][1], 0.035)

        # Style 1: Relative ride height gap (0.020m below smin[1] -> 0.035 - 0.020 = 0.015)
        cfg_rel = dict(base_cfg, ground_clearance=0.020)
        res_rel = asyncio.run(api_geometry_domain_box(DomainBoxRequest(config=cfg_rel, bounds=bounds)))
        self.assertAlmostEqual(res_rel["domain_box"]["min"][1], 0.015)

        # Style 2: Absolute CAD ground plane (fixed at 0.0)
        cfg_abs = dict(base_cfg, ground_plane=0.0)
        res_abs = asyncio.run(api_geometry_domain_box(DomainBoxRequest(config=cfg_abs, bounds=bounds)))
        self.assertAlmostEqual(res_abs["domain_box"]["min"][1], 0.0)

    def test_auto_symmetry_plane_calculation(self):
        """Test calculation of auto symmetry plane from geometry bounds."""
        # bounds with asymmetric X min = -0.8639691, max = 0.6265309
        bounds = {"min": [-0.8640, 0.0, -1.71], "max": [0.6266, 1.0, 1.44]}
        cfg = {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
        }
        res = asyncio.run(api_geometry_domain_box(DomainBoxRequest(config=cfg, bounds=bounds)))
        self.assertIn("auto_symmetry_plane", res)
        self.assertEqual(res["lateral_axis"], "x")
        # (-0.8640 + 0.6266) / 2 = -0.2374 / 2 = -0.1187
        self.assertAlmostEqual(res["auto_symmetry_plane"], -0.1187, places=4)

    def test_geometry_domain_box_layer_preview(self):
        """Preset y+ target is resolved to an absolute first-layer thickness."""
        bounds = {"min": [-0.745, 0.0, -2.961], "max": [0.745, 1.145, 0.296]}
        cfg = {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "fluid": {"nu": 1.516e-5, "rho": 1.225},
            "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
            "fidelity": "standard",
        }
        res = asyncio.run(api_geometry_domain_box(DomainBoxRequest(config=cfg, bounds=bounds)))
        preview = res["layer_preview"]
        self.assertIsNotNone(preview["first_layer_thickness"])
        self.assertGreater(preview["u_tau"], 0.0)
        self.assertGreater(preview["y_plus_effective"], 1.0)
        base_cell = round(3.257 / 30.0, 4)
        unclamped = 2.0 * 40 * 1.516e-5 / preview["u_tau"]
        clamped = 0.5 * base_cell / 2 ** 5
        self.assertAlmostEqual(preview["first_layer_thickness"], min(unclamped, clamped), places=9)

    def test_geometry_domain_box_layer_preview_explicit_absolute(self):
        """Explicit absolute thickness wins and reports its effective y+."""
        bounds = {"min": [-0.745, 0.0, -2.961], "max": [0.745, 1.145, 0.296]}
        cfg = {
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
            "fluid": {"nu": 1.516e-5, "rho": 1.225},
            "fidelity": "standard",
            "layers": {"relativeSizes": False, "first_layer_thickness": 2e-5, "min_thickness": 2e-5},
        }
        res = asyncio.run(api_geometry_domain_box(DomainBoxRequest(config=cfg, bounds=bounds)))
        preview = res["layer_preview"]
        self.assertAlmostEqual(preview["first_layer_thickness"], 2e-5)
        self.assertIsNotNone(preview["y_plus_effective"])
        self.assertLess(preview["y_plus_effective"], 2.0)

    def test_list_cases_and_delete(self):
        """Test listing cases archive and deleting a case."""
        # Create a dummy case in cases/
        dummy_case = Path("cases/test_case_archive_dummy")
        dummy_case.mkdir(parents=True, exist_ok=True)
        (dummy_case / "case_config.json").write_text('{"case_name": "test_case_archive_dummy", "fidelity": "standard"}')

        try:
            cases = asyncio.run(api_list_cases())
            self.assertTrue(any(c["name"] == "test_case_archive_dummy" for c in cases))
            matching = next(c for c in cases if c["name"] == "test_case_archive_dummy")
            self.assertEqual(matching["location"], "Local")
            self.assertEqual(matching["status"], "Generated")
            self.assertIn("modified", matching)
        finally:
            del_res = asyncio.run(api_case_delete("test_case_archive_dummy"))
            self.assertTrue(del_res["success"])
            self.assertFalse(dummy_case.exists())

    def test_list_cases_failed_detection(self):
        """Test that solver fatal error in log.simpleFoam results in status 'Failed'."""
        failed_case = Path("cases/test_case_failed_dummy")
        failed_case.mkdir(parents=True, exist_ok=True)
        (failed_case / "case_config.json").write_text('{"case_name": "test_case_failed_dummy"}')
        (failed_case / "log.simpleFoam").write_text(
            "Time = 10\n"
            "FOAM FATAL ERROR:\n"
            "Continuity error cannot be resolved\n"
            "FOAM aborting\n"
        )
        try:
            cases = asyncio.run(api_list_cases())
            match = next((c for c in cases if c["name"] == "test_case_failed_dummy"), None)
            self.assertIsNotNone(match)
            self.assertEqual(match["status"], "Failed")
        finally:
            del_res = asyncio.run(api_case_delete("test_case_failed_dummy"))
            self.assertTrue(del_res["success"])

    def test_list_cases_cluster_merge_and_slurm(self):
        """Test merging remote cases and mapping SLURM queue states into case status."""
        mock_slurm = [
            {"job_id": "1001", "name": "case_queued_sim", "state": "PENDING"},
            {"job_id": "1002", "name": "case_running_sim", "state": "RUNNING"},
        ]
        mock_remote_cases = [
            {
                "name": "case_queued_sim",
                "modified": "2026-09-07 04:00:00",
                "modified_ts": 100.0,
                "status": "Generated",
                "fidelity": "standard",
                "velocity": "20.0",
                "direction": "-z",
                "n_procs": 16,
                "stl_name": "wing.stl",
                "has_forces": False,
                "has_residuals": False,
                "has_mesh": False,
                "latest_iter": None,
                "converged": False,
                "downforce": None,
                "drag": None,
                "ld_ratio": None,
            },
            {
                "name": "case_running_sim",
                "modified": "2026-09-07 05:00:00",
                "modified_ts": 200.0,
                "status": "Solving",
                "fidelity": "standard",
                "velocity": "16.7",
                "direction": "-z",
                "n_procs": 32,
                "stl_name": "car.stl",
                "has_forces": True,
                "has_residuals": True,
                "has_mesh": True,
                "latest_iter": 120,
                "converged": False,
                "downforce": 450.2,
                "drag": 210.5,
                "ld_ratio": 2.14,
            },
            {
                "name": "RP14",
                "modified": "2026-09-07 06:00:00",
                "modified_ts": 300.0,
                "status": "Converged",
                "fidelity": "standard",
                "velocity": "16.7",
                "direction": "-z",
                "n_procs": 32,
                "stl_name": "body.stl",
                "has_forces": True,
                "has_residuals": True,
                "has_mesh": True,
                "latest_iter": 880,
                "converged": True,
                "downforce": 579.1,
                "drag": 247.95,
                "ld_ratio": 2.34,
            },
        ]

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ssh_client, "get_slurm_queue", return_value=mock_slurm):
                with patch.object(ssh_client, "list_remote_cases_detailed", return_value=mock_remote_cases):
                    cases = asyncio.run(api_list_cases())

                    by_name = {c["name"]: c for c in cases}
                    self.assertIn("case_queued_sim", by_name)
                    self.assertIn("case_running_sim", by_name)
                    self.assertIn("RP14", by_name)

                    # PENDING job should map to Queued
                    self.assertEqual(by_name["case_queued_sim"]["status"], "Queued")
                    self.assertEqual(by_name["case_queued_sim"]["location"], "Cluster")

                    # RUNNING job should map to Solving with forces
                    self.assertEqual(by_name["case_running_sim"]["status"], "Solving")
                    self.assertEqual(by_name["case_running_sim"]["latest_iter"], 120)
                    self.assertEqual(by_name["case_running_sim"]["downforce"], 450.2)
                    self.assertEqual(by_name["case_running_sim"]["drag"], 210.5)

                    # Completed converged job
                    self.assertEqual(by_name["RP14"]["status"], "Converged")
                    self.assertTrue(by_name["RP14"]["converged"])
                    self.assertEqual(by_name["RP14"]["latest_iter"], 880)
                    self.assertEqual(by_name["RP14"]["downforce"], 579.1)
                    self.assertEqual(by_name["RP14"]["drag"], 247.95)
                    self.assertEqual(by_name["RP14"]["ld_ratio"], 2.34)

    def test_ssh_client_list_remote_cases_detailed(self):
        """Test ClusterSSHClient.list_remote_cases_detailed execution and parsing."""
        c = ClusterSSHClient()
        # When disconnected, should return empty list
        self.assertEqual(c.list_remote_cases_detailed(), [])

        mock_payload = [
            {
                "name": "case_remote_1",
                "modified": "2026-09-07 05:00:00",
                "modified_ts": 12345.0,
                "status": "Converged",
                "latest_iter": 500,
                "converged": True,
                "downforce": 300.0,
                "drag": 150.0,
                "ld_ratio": 2.0,
            }
        ]
        import json
        mock_out = f"__CASE_JSON_START__{json.dumps(mock_payload)}__CASE_JSON_END__"
        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(c, "run_command", return_value=(0, mock_out, "")):
                res = c.list_remote_cases_detailed()
                self.assertEqual(len(res), 1)
                self.assertEqual(res[0]["name"], "case_remote_1")
                self.assertEqual(res[0]["latest_iter"], 500)
                self.assertEqual(res[0]["downforce"], 300.0)

    def test_merge_config_with_defaults_selective_overrides(self):
        """Test that selective overrides only change specified keys and leave others at defaults."""
        user_cfg = {
            "case_name": "test_selective",
            "stl_files": ["sample_wing.stl"],
            "overrides": {
                "fluid": {
                    "rho": 1.18,
                },
                "solver": {
                    "end_time": 999,
                },
            },
        }
        merged = merge_config_with_defaults(user_cfg)
        # Overridden fields
        self.assertEqual(merged["fluid"]["rho"], 1.18)
        self.assertEqual(merged["solver"]["end_time"], 999)
        # Un-overridden fields in same sections remain at universal defaults
        self.assertEqual(merged["fluid"]["nu"], 1.516e-5)
        self.assertEqual(merged["solver"]["write_interval"], 400)
        # Un-overridden sections remain at universal defaults
        self.assertEqual(merged["turbulence"]["model"], "kOmegaSST")
        self.assertEqual(merged["force_refs"]["lRef"], 1.0)

    def test_merge_config_with_defaults_all_five_sections(self):
        """Test overrides across all 5 sections matching configs/config.json."""
        user_cfg = {
            "case_name": "test_full_overrides",
            "stl_files": ["sample_wing.stl"],
            "overrides": {
                "mesh_params": {
                    "base_cell_size": 0.08,
                    "surface_level": [5, 6],
                    "edge_level": 7,
                    "near_wake_level": 4,
                    "far_wake_level": 2,
                },
                "fluid": {
                    "nu": 1.45e-5,
                    "rho": 1.20,
                },
                "turbulence": {
                    "model": "SpalartAllmaras",
                    "intensity": 0.01,
                    "nut_ratio": 12,
                },
                "solver": {
                    "end_time": 1200,
                    "write_interval": 300,
                    "purge_write": 3,
                },
                "force_refs": {
                    "lRef": 1.25,
                    "Aref": 0.85,
                    "CofR": [0.1, 0.0, 0.5],
                },
                "layers": {
                    "n_layers": 7,
                    "expansion_ratio": 1.22,
                    "first_layer_thickness": 0.25,
                    "min_thickness": 0.04,
                },
            },
        }
        merged = merge_config_with_defaults(user_cfg)
        self.assertEqual(merged["mesh_params"]["base_cell_size"], 0.08)
        self.assertEqual(merged["mesh_params"]["surface_level"], [5, 6])
        self.assertEqual(merged["fluid"]["nu"], 1.45e-5)
        self.assertEqual(merged["fluid"]["rho"], 1.20)
        self.assertEqual(merged["turbulence"]["model"], "SpalartAllmaras")
        self.assertEqual(merged["solver"]["end_time"], 1200)
        self.assertEqual(merged["solver"]["write_interval"], 300)
        self.assertEqual(merged["force_refs"]["lRef"], 1.25)
        self.assertEqual(merged["force_refs"]["CofR"], [0.1, 0.0, 0.5])
        self.assertEqual(merged["layers"]["n_layers"], 7)
        self.assertEqual(merged["layers"]["expansion_ratio"], 1.22)
        self.assertEqual(merged["layers"]["first_layer_thickness"], 0.25)

    def test_case_generate_with_overrides(self):
        """Test that api_case_generate_and_submit applies overrides to generated case."""
        case_name = "test_case_override_gen"
        req = GenerateCaseRequest(
            config={
                "case_name": case_name,
                "stl_files": ["sample_wing.stl"],
                "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
                "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
                "overrides": {
                    "fluid": {"rho": 1.15},
                    "solver": {"end_time": 600},
                    "layers": {"n_layers": 7, "expansion_ratio": 1.25},
                },
            },
            upload_to_cluster=False,
            generate_remotely=False,
            submit_slurm=False,
            generate_locally=True,
        )
        res = asyncio.run(api_case_generate_and_submit(req))
        self.assertTrue(res.get("success"))
        case_path = Path("cases") / case_name
        self.assertTrue(case_path.is_dir())
        try:
            control_dict = (case_path / "system" / "controlDict").read_text(encoding="utf-8")
            self.assertIn("endTime         600;", control_dict)
            self.assertIn("rhoInf          1.15;", control_dict)
            snappy_dict = (case_path / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
            self.assertIn("nSurfaceLayers 7;", snappy_dict)
            self.assertIn("expansionRatio          1.25;", snappy_dict)
        finally:
            shutil.rmtree(case_path, ignore_errors=True)
    def test_cli_launcher_parser(self):
        """Test server CLI argument parsing supports --restart, --port, --no-browser."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        parser.add_argument("--no-browser", action="store_true")
        parser.add_argument("--restart", action="store_true")
        args = parser.parse_args(["--port", "8888", "--no-browser", "--restart"])
        self.assertEqual(args.port, 8888)
        self.assertTrue(args.no_browser)
        self.assertTrue(args.restart)
    def test_stl_and_case_check_exists(self):
        """Test STL and Case existence checking APIs."""
        # Check existing STL
        res_stl_exist = asyncio.run(api_stl_check_exists("sample_wing.stl"))
        self.assertTrue(res_stl_exist["exists"])
        self.assertTrue(res_stl_exist["local_exists"])

        # Check non-existing STL
        res_stl_no = asyncio.run(api_stl_check_exists("definitely_nonexistent_stl_file.stl"))
        self.assertFalse(res_stl_no["exists"])
        self.assertFalse(res_stl_no["local_exists"])

        # Check non-existing case
        res_case_no = asyncio.run(api_case_check_exists("fake_case_12345"))
        self.assertFalse(res_case_no["exists"])

        # Create temporary dummy config to test case existence
        cfg_file = Path("configs") / "fake_case_12345.json"
        try:
            cfg_file.write_text("{}", encoding="utf-8")
            res_case_yes = asyncio.run(api_case_check_exists("fake_case_12345"))
            self.assertTrue(res_case_yes["exists"])
            self.assertTrue(res_case_yes["local_exists"])
        finally:
            if cfg_file.exists():
                cfg_file.unlink()

    def test_telemetry_logs_api_contract(self):
        """Test that api_telemetry_logs returns log_file and size_bytes metadata."""
        case_dir = Path("cases") / "test_log_contract"
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            log_file = case_dir / "log.simpleFoam"
            log_file.write_text("Iteration 1\nIteration 2\nDone.\n", encoding="utf-8")
            res = asyncio.run(api_telemetry_logs("test_log_contract", "simpleFoam"))
            self.assertEqual(res["case_name"], "test_log_contract")
            self.assertEqual(res["log_type"], "simpleFoam")
            self.assertEqual(res["log_file"], "log.simpleFoam")
            self.assertGreater(res["size_bytes"], 0)
            self.assertIn("Iteration 2", res["content"])
        finally:
            shutil.rmtree(case_dir, ignore_errors=True)

    def test_telemetry_forces_downsampling_preserves_last_point(self):
        """Test that downsampling preserves the final iteration point when len > 400."""
        case_dir = Path("cases") / "test_downsampling_case"
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Generate 550 lines of force data
            lines = ["# Time (f_px f_py f_pz) (f_vx f_vy f_vz) (f_porx f_pory f_porz)"]
            for i in range(1, 551):
                lines.append(f"{i} (0 -50 20) (0 -5 2) (0 0 0)")
            (forces_dir / "force.dat").write_text("\n".join(lines), encoding="utf-8")

            res = asyncio.run(api_telemetry_forces("test_downsampling_case"))
            self.assertTrue(res["has_data"])
            self.assertEqual(res["total_iterations"], 550)
            self.assertEqual(res["latest_iteration"], 550)
            # Downsampled series must end with the final iteration 550
            series_times = res["series"]["iterations"]
            self.assertLessEqual(len(series_times), 401)
            self.assertEqual(series_times[-1], 550)
        finally:
            shutil.rmtree(case_dir, ignore_errors=True)

    def test_telemetry_coefficients_and_components(self):
        """Telemetry exposes solver coefficients, force/moment components, and references."""
        case_name = "test_case_coeff_components"
        case_dir = Path(f"cases/{case_name}")
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        coeff_dir = case_dir / "postProcessing" / "forceCoeffs" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        coeff_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 20.0},
            "fluid": {"rho": 1.2},
            "force_refs": {"Aref": 0.5, "lRef": 1.2},
            "domain_faces": {"-x": "farField"},
        }))

        force_lines = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        moment_lines = list(force_lines)
        coeff_lines = [
            "# Force and moment coefficients\n",
            "# Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch CmRoll CmYaw Cs Cs(f) Cs(r)\n",
        ]
        for i in range(1, 25):
            force_lines.append(f"{i} 10 -200 -60 9 -195 -50 1 -5 -10\n")
            moment_lines.append(f"{i} 5 -20 -8 4 -19 -7 1 -1 -1\n")
            coeff_lines.append(f"{i} 0.5 0.45 0.05 -1.2 -1.1 -0.1 -0.4 0.1 0.05 0.2 0.15 0.05\n")
        (forces_dir / "force.dat").write_text("".join(force_lines))
        (forces_dir / "moment.dat").write_text("".join(moment_lines))
        (coeff_dir / "coefficient.dat").write_text("".join(coeff_lines))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertTrue(res["has_data"])
        self.assertEqual(res["coefficients"]["source"], "forceCoeffs")
        self.assertAlmostEqual(res["coefficients"]["summary"]["Cd"]["current"], 0.5, places=4)
        self.assertAlmostEqual(res["coefficients"]["summary"]["Cl"]["current"], -1.2, places=4)
        self.assertTrue(res["components"]["available"])
        self.assertEqual(res["components"]["force"]["latest"]["total"], [10.0, -200.0, -60.0])
        self.assertEqual(res["components"]["force"]["latest"]["viscous"], [1.0, -5.0, -10.0])
        self.assertEqual(res["components"]["moment"]["latest"]["total"], [5.0, -20.0, -8.0])
        self.assertAlmostEqual(res["reference"]["velocity"], 20.0)
        self.assertAlmostEqual(res["reference"]["Aref"], 0.5)

    def test_telemetry_computed_coefficients_fallback(self):
        """Without forceCoeffs output, coefficients are computed from reference refs."""
        case_name = "test_case_computed_coeff"
        case_dir = Path(f"cases/{case_name}")
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 10.0},
            "fluid": {"rho": 1.0},
            "force_refs": {"Aref": 1.0},
            "domain_faces": {"-x": "farField"},
        }))

        lines = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        for i in range(1, 25):
            lines.append(f"{i} 0 -50 -20 0 -50 -20 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(lines))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertEqual(res["coefficients"]["source"], "computed")
        # q = 0.5 * 1.0 * 10^2 * 1.0 = 50; Cd = 20/50, Cl = 50/50
        self.assertAlmostEqual(res["coefficients"]["summary"]["Cd"]["current"], 0.4, places=4)
        self.assertAlmostEqual(res["coefficients"]["summary"]["Cl"]["current"], 1.0, places=4)

    def test_telemetry_reference_overrides(self):
        """Post-run reference query params recompute coefficients from raw data."""
        case_name = "test_case_ref_override"
        case_dir = Path(f"cases/{case_name}")
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 10.0},
            "fluid": {"rho": 1.0},
            "force_refs": {"Aref": 1.0, "lRef": 1.0, "CofR": [0.0, 0.0, 0.0]},
            "domain_faces": {"-x": "farField"},
        }))

        force_lines = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        moment_lines = list(force_lines)
        for i in range(1, 25):
            force_lines.append(f"{i} 0 -50 -20 0 -50 -20 0 0 0\n")
            moment_lines.append(f"{i} 0 0 0 0 0 0 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(force_lines))
        (forces_dir / "moment.dat").write_text("".join(moment_lines))

        base = asyncio.run(api_telemetry_forces(case_name))
        self.assertEqual(base["coefficients"]["source"], "computed")
        self.assertAlmostEqual(base["coefficients"]["summary"]["Cd"]["current"], 0.4, places=4)

        # Doubling Aref halves Cd; source becomes recomputed.
        over = asyncio.run(api_telemetry_forces(case_name, aref=2.0))
        self.assertEqual(over["coefficients"]["source"], "recomputed")
        self.assertAlmostEqual(over["coefficients"]["summary"]["Cd"]["current"], 0.2, places=4)
        self.assertAlmostEqual(over["reference"]["Aref"], 2.0)

        # q scales with V^2: 10 -> 20 m/s divides Cd by 4.
        faster = asyncio.run(api_telemetry_forces(case_name, velocity=20.0))
        self.assertAlmostEqual(faster["coefficients"]["summary"]["Cd"]["current"], 0.1, places=4)

        # Shifting CofR moves the pitching moment: M_new = M_old + (r_run - r_new) x F.
        shifted = asyncio.run(api_telemetry_forces(case_name, cofr="0,0.5,0"))
        # (0,-0.5,0) x (0,-50,-20) = (10, 0, 0); q=50, A=1, lRef=1 -> CmPitch = 0.2
        self.assertAlmostEqual(shifted["coefficients"]["summary"]["CmPitch"]["current"], 0.2, places=4)
        # The displayed moment vector must also be shifted to the effective CofR.
        self.assertEqual(shifted["components"]["moment"]["latest"]["total"], [10.0, 0.0, 0.0])

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api_telemetry_forces(case_name, cofr="1,2"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_is_symmetry_case_defaults_to_half_model(self):
        """A config without domain_faces still generates a half-model and must double."""
        case_dir = Path("cases") / "test_sym_default_unit"
        case_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({"case_name": "x"}))
        self.assertTrue(is_symmetry_case(case_dir=case_dir))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": "x",
            "domain_faces": {
                "-x": "farField", "+x": "farField", "-y": "ground",
                "+y": "farField", "+z": "inlet", "-z": "outlet",
            },
        }))
        self.assertFalse(is_symmetry_case(case_dir=case_dir))

    def test_telemetry_doubles_without_domain_faces(self):
        """Telemetry doubles forces for a minimal config that defaults to symmetry."""
        case_name = "test_case_default_symmetry"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 16.67, "direction": "-z", "ground": True},
        }))

        rows = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        for i in range(1, 25):
            rows.append(f"{i} 0 -100 -40 0 -100 -40 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(rows))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertTrue(res["is_symmetry"])
        # downforce = -(-100) * 2 = 200 N; drag = -(-40) * 2 = 80 N
        self.assertAlmostEqual(res["downforce_avg"], 200.0, places=1)
        self.assertAlmostEqual(res["drag_avg"], 80.0, places=1)

    def test_symmetry_projection_cancels_out_of_plane(self):
        """Half-car symmetry cancels side force and roll/yaw for the full car."""
        case_name = "test_case_sym_projection"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        coeff_dir = case_dir / "postProcessing" / "forceCoeffs" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        coeff_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 10.0},
            "fluid": {"rho": 1.0},
            "force_refs": {"Aref": 1.0, "lRef": 1.0, "CofR": [0.0, 0.0, 0.0]},
            "domain_faces": {"-x": "symmetry", "+x": "farField", "-y": "ground",
                             "+y": "farField", "+z": "inlet", "-z": "outlet"},
        }))
        force_lines = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        moment_lines = list(force_lines)
        coeff_lines = [
            "# Force and moment coefficients\n",
            "# Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch CmRoll CmYaw Cs Cs(f) Cs(r)\n",
        ]
        for i in range(1, 25):
            force_lines.append(f"{i} 10 -50 -20 10 -50 -20 0 0 0\n")
            moment_lines.append(f"{i} 5 2 3 5 2 3 0 0 0\n")
            coeff_lines.append(f"{i} 0.4 0.45 -0.05 1.0 1.1 -0.1 2.0 0.3 0.2 0.1 0.15 -0.05\n")
        (forces_dir / "force.dat").write_text("".join(force_lines))
        (forces_dir / "moment.dat").write_text("".join(moment_lines))
        (coeff_dir / "coefficient.dat").write_text("".join(coeff_lines))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertTrue(res["is_symmetry"])
        # Lateral (x) force cancels; in-plane forces double.
        self.assertEqual(res["components"]["force"]["latest"]["total"], [0.0, -100.0, -40.0])
        # Pitch moment (about lateral x) doubles; roll/yaw cancel.
        self.assertEqual(res["components"]["moment"]["latest"]["total"], [10.0, 0.0, 0.0])

        summary = res["coefficients"]["summary"]
        self.assertEqual(res["coefficients"]["source"], "forceCoeffs")
        self.assertAlmostEqual(summary["Cd"]["current"], 0.8, places=5)
        self.assertAlmostEqual(summary["Cl"]["current"], 2.0, places=5)
        self.assertAlmostEqual(summary["CmPitch"]["current"], 4.0, places=5)
        self.assertAlmostEqual(summary["Cs"]["current"], 0.0, places=5)
        self.assertAlmostEqual(summary["CmRoll"]["current"], 0.0, places=5)
        self.assertAlmostEqual(summary["CmYaw"]["current"], 0.0, places=5)

    def test_telemetry_component_window_average(self):
        """Component vectors expose a trailing-window average matching the KPI basis."""
        case_name = "test_case_avg_components"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 10.0},
            "fluid": {"rho": 1.0},
            "force_refs": {"Aref": 1.0},
            "domain_faces": {"-x": "farField", "+x": "farField", "-y": "ground",
                             "+y": "farField", "+z": "inlet", "-z": "outlet"},
        }))
        rows = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        for i in range(1, 101):
            rows.append(f"{i} 0 0 {-i} 0 0 {-i} 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(rows))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertFalse(res["is_symmetry"])
        self.assertEqual(res["components"]["force"]["latest"]["total"], [0.0, 0.0, -100.0])
        self.assertEqual(res["components"]["force"]["average"]["total"], [0.0, 0.0, -50.5])
        self.assertAlmostEqual(res["drag_avg"], 50.5, places=2)

    def test_telemetry_solver_diagnostics(self):
        """Solver-health endpoint reports continuity, linear effort, timing, and ETA."""
        case_name = "test_case_solver_diag"
        case_dir = Path("cases") / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))
        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "solver": {"end_time": 20},
        }))

        lines = []
        for it in range(1, 4):
            lines.append(f"Time = {it}\n")
            lines.append(
                "GAMG:  Solving for p, Initial residual = 1e-1, Final residual = 1e-3, No Iterations 8\n"
            )
            for var in ("Ux", "Uy", "Uz", "k", "omega"):
                lines.append(
                    f"DILUPBiCGStab:  Solving for {var}, Initial residual = 1e-2, "
                    "Final residual = 1e-4, No Iterations 1\n"
                )
            lines.append(
                "time step continuity errors : sum local = 1e-5, global = 3e-6, cumulative = -1e-3\n"
            )
            lines.append(f"ExecutionTime = {it * 10} s  ClockTime = {it * 12} s\n")
        (case_dir / "log.simpleFoam").write_text("".join(lines))

        res = asyncio.run(api_telemetry_solver(case_name))
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_iterations"], 3)
        self.assertEqual(res["latest_iteration"], 3)
        self.assertEqual(res["latest_linear_iters"], 13)  # p=8 + 5 momentum/turbulence solves
        self.assertAlmostEqual(res["latest_continuity_global"], 3e-6)
        self.assertAlmostEqual(res["iterations_per_second"], 0.1, places=4)
        self.assertAlmostEqual(res["elapsed_seconds"], 30.0, places=2)
        self.assertAlmostEqual(res["eta_seconds"], 170.0, places=1)
        self.assertEqual(len(res["series"]["iterations"]), 3)
        self.assertEqual(len(res["series"]["continuity_global"]), 3)

    def test_parse_solver_diagnostics_restart(self):
        """A restarted run replaces the previous trajectory in the diagnostics parser."""
        text = (
            "Time = 1\nExecutionTime = 5 s\n"
            "Time = 2\nExecutionTime = 10 s\n"
            "Time = 1\nExecutionTime = 4 s\n"
            "Time = 2\nExecutionTime = 8 s\n"
        )
        rows = parse_solver_diagnostics_from_log(text)
        self.assertEqual(sorted(rows), [1.0, 2.0])
        self.assertAlmostEqual(rows[2.0]["execution_time"], 8.0)
        self.assertAlmostEqual(rows[1.0]["execution_time"], 4.0)

    def test_telemetry_includes_stl_files(self):
        """Telemetry payload exposes STL basenames for the 3D aero-load view."""
        case_name = "test_case_stl_files"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "stl_files": ["some/path/wing.STL", "rear.stl"],
            "flow": {"velocity": 16.67},
            "domain_faces": {"-x": "farField", "+x": "farField", "-y": "ground",
                             "+y": "farField", "+z": "inlet", "-z": "outlet"},
        }))
        rows = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        for i in range(1, 10):
            rows.append(f"{i} 0 -50 -20 0 -50 -20 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(rows))

        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertEqual(res["stl_files"], ["wing.STL", "rear.stl"])

    def test_telemetry_aero_balance_cop(self):
        """CoP + front/rear aero split from force, moment, wheelbase and static %."""
        case_name = "test_case_balance"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 10.0},
            "fluid": {"rho": 1.0},
            "force_refs": {"Aref": 1.0, "lRef": 1.0, "CofR": [0, 0, 0]},
            "vehicle": {"wheelbase": 2.0, "front_weight_pct": 50.0},
            "domain_faces": {"-x": "farField", "+x": "farField", "-y": "ground",
                             "+y": "farField", "+z": "inlet", "-z": "outlet"},
        }))
        force_rows = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        moment_rows = list(force_rows)
        for i in range(1, 25):
            force_rows.append(f"{i} 0 -100 -40 0 -100 -40 0 0 0\n")
            moment_rows.append(f"{i} 50 0 0 50 0 0 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(force_rows))
        (forces_dir / "moment.dat").write_text("".join(moment_rows))

        res = asyncio.run(api_telemetry_forces(case_name))
        balance = res["balance"]
        self.assertTrue(balance["available"])
        # D=100 N; CoP 0.5 m ahead of CoG -> 75% front.
        self.assertAlmostEqual(balance["front_pct"], 75.0, places=1)
        self.assertAlmostEqual(balance["front_load"], 75.0, places=1)
        self.assertAlmostEqual(balance["rear_load"], 25.0, places=1)
        self.assertAlmostEqual(balance["cop_pct_wheelbase"], 25.0, places=1)
        self.assertAlmostEqual(balance["cop_behind_front_axle"], 0.5, places=2)

    def test_telemetry_aero_balance_requires_vehicle(self):
        """Balance is unavailable when wheelbase/weight distribution are not configured."""
        case_name = "test_case_balance_novehicle"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))
        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 10.0},
            "force_refs": {"Aref": 1.0},
            "domain_faces": {"-x": "farField", "+x": "farField", "-y": "ground",
                             "+y": "farField", "+z": "inlet", "-z": "outlet"},
        }))
        rows = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        for i in range(1, 20):
            rows.append(f"{i} 0 -100 -40 0 -100 -40 0 0 0\n")
        (forces_dir / "force.dat").write_text("".join(rows))
        res = asyncio.run(api_telemetry_forces(case_name))
        self.assertFalse(res["balance"]["available"])

    def test_telemetry_export_csv(self):
        """CSV export includes force history and aligned coefficient columns."""
        case_name = "test_case_export"
        case_dir = Path(f"cases/{case_name}")
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        coeff_dir = case_dir / "postProcessing" / "forceCoeffs" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        coeff_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        (case_dir / "case_config.json").write_text(json.dumps({
            "case_name": case_name,
            "flow": {"velocity": 20.0},
            "fluid": {"rho": 1.2},
            "force_refs": {"Aref": 0.5},
            "domain_faces": {"-x": "farField"},
        }))
        force_lines = [
            "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
            "viscous_x viscous_y viscous_z\n"
        ]
        coeff_lines = [
            "# Force and moment coefficients\n",
            "# Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch CmRoll CmYaw Cs Cs(f) Cs(r)\n",
        ]
        for i in range(1, 25):
            force_lines.append(f"{i} 10 -200 -60 10 -200 -60 0 0 0\n")
            coeff_lines.append(f"{i} 0.5 0.45 0.05 -1.2 -1.1 -0.1 -0.4 0.1 0.05 0.2 0.15 0.05\n")
        (forces_dir / "force.dat").write_text("".join(force_lines))
        (coeff_dir / "coefficient.dat").write_text("".join(coeff_lines))

        response = asyncio.run(api_telemetry_export(case_name, "csv"))
        text = response.body.decode("utf-8")
        self.assertIn("iteration,drag_N,downforce_N,L_over_D,Cd,Cl", text)
        self.assertIn("attachment", response.headers.get("content-disposition", ""))
        self.assertEqual(len(text.strip().splitlines()), 25)

    def test_api_list_cases_remote_modified_timestamp_update(self):
        """Test that remote cases with newer modified_ts update local_entry and sort properly."""
        case_dir = Path("cases") / "test_sort_case"
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Mock remote case with future timestamp
            remote_case = {
                "name": "test_sort_case",
                "status": "Solving",
                "latest_iter": 700,
                "modified": "2026-09-08 12:00:00",
                "modified_ts": 1900000000.0,
                "fidelity": "standard",
                "velocity": "20.0",
                "direction": "-z",
                "n_procs": 32,
                "stl": "wing.stl",
                "has_forces": True,
                "has_residuals": True,
                "converged": False,
                "downforce": 120.5,
                "drag": 45.2,
                "ld_ratio": 2.66,
            }
            with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
                with patch.object(ClusterSSHClient, "list_remote_cases_detailed", return_value=[remote_case]):
                    cases = asyncio.run(api_list_cases())
                    matched = next((c for c in cases if c["name"] == "test_sort_case"), None)
                    self.assertIsNotNone(matched)
                    self.assertEqual(matched["location"], "Local & Cluster")
                    self.assertEqual(matched["status"], "Solving")
                    self.assertEqual(matched["latest_iter"], 700)
                    self.assertEqual(matched["modified_ts"], 1900000000.0)
                    self.assertEqual(matched["modified"], "2026-09-08 12:00:00")
                    # Should be sorted first due to high modified_ts
                    self.assertEqual(cases[0]["name"], "test_sort_case")
        finally:
            shutil.rmtree(case_dir, ignore_errors=True)

    def test_parse_marked_bundle(self):
        """The batching stream marker parser recovers each file's content."""
        text = (
            f"{FILE_BEGIN}cases/a/postProcessing/forces/0/force.dat\n"
            "# Time total_x total_y total_z\n1 0 -1 -2\n"
            f"\n{FILE_END}\n"
            f"{FILE_BEGIN}cases/a/log.simpleFoam\nTime = 1\n"
            f"\n{FILE_END}\n"
        )
        files = _parse_marked_bundle(text)
        self.assertEqual(
            set(files),
            {"cases/a/postProcessing/forces/0/force.dat", "cases/a/log.simpleFoam"},
        )
        self.assertIn("1 0 -1 -2", files["cases/a/postProcessing/forces/0/force.dat"])
        self.assertIn("Time = 1", files["cases/a/log.simpleFoam"])

    def test_read_remote_bundle_single_command(self):
        """read_remote_bundle reads all specs with one SSH command."""
        c = ClusterSSHClient()
        c.remote_repo_path = "/repo"
        captured = []

        def mock_run(cmd, timeout=30.0):
            captured.append(cmd)
            out = f"{FILE_BEGIN}cases/x/postProcessing/forces/0/force.dat\nrow1\n{FILE_END}\n"
            return (0, out, "")

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(c, "run_command", side_effect=mock_run):
                res = c.read_remote_bundle([
                    ("cases/x/postProcessing/forces/*/force.dat", None),
                    ("cases/x/log.simpleFoam", 100),
                ])
        self.assertEqual(len(captured), 1)
        self.assertIn("tail -n 100", captured[0])
        self.assertEqual(res["cases/x/postProcessing/forces/0/force.dat"], "row1")

    def test_remote_forces_single_batched_command(self):
        """Force telemetry reads force.dat, moment.dat and coefficient.dat in one command."""
        captured = []

        def mock_run(cmd, timeout=30.0):
            captured.append(cmd)
            return (0, "", "")

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ClusterSSHClient, "run_command", side_effect=mock_run):
                asyncio.run(api_telemetry_forces("test_case"))

        self.assertEqual(len(captured), 1, "force telemetry should use a single SSH command")
        self.assertIn("postProcessing/forces", captured[0])
        self.assertIn("forceCoeffs", captured[0])

    def test_remote_telemetry_cache_shares_reads(self):
        """The three telemetry endpoints share a single remote read within the TTL."""
        captured = []

        def mock_run(cmd, timeout=30.0):
            captured.append(cmd)
            out = (
                f"{FILE_BEGIN}cases/cache_case/postProcessing/forces/0/force.dat\n"
                "# Time total_x total_y total_z pressure_x pressure_y pressure_z "
                "viscous_x viscous_y viscous_z\n"
                "1 0 -10 -5 0 -10 -5 0 0 0\n"
                f"{FILE_END}\n"
            )
            return (0, out, "")

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ClusterSSHClient, "run_command", side_effect=mock_run):
                forces = asyncio.run(api_telemetry_forces("cache_case"))
                asyncio.run(api_telemetry_residuals("cache_case"))
                asyncio.run(api_telemetry_solver("cache_case"))

        self.assertEqual(len(captured), 1, "telemetry endpoints must share one remote read")
        self.assertTrue(forces["has_data"])

    def test_remote_residuals_batched_command_no_processor_dirs(self):
        """Remote residual reads are batched into one command without processor* globs."""
        captured_cmds = []

        def mock_run_command(cmd, timeout=5):
            captured_cmds.append(cmd)
            return (1, "", "")

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ClusterSSHClient, "run_command", side_effect=mock_run_command):
                asyncio.run(api_telemetry_residuals("test_case"))

        self.assertEqual(len(captured_cmds), 1, "residual telemetry should use a single SSH command")
        cmd = captured_cmds[0]
        self.assertNotIn("processor*", cmd)
        self.assertIn("cases/test_case/postProcessing/residuals", cmd)
        self.assertIn("log.simpleFoam", cmd)

    def test_slurm_job_matching_no_short_prefix_collision(self):
        """Test that a short SLURM job name ('EV') does not match a longer case name ('EV_TUR50')."""
        remote_case = {
            "name": "EV_TUR50",
            "status": "Generated",
            "latest_iter": 0,
            "modified": "2026-09-08 07:00:00",
            "modified_ts": 1000.0,
            "fidelity": "standard",
            "velocity": "20.0",
            "direction": "-z",
            "n_procs": 32,
            "stl": "wing.stl",
            "has_forces": False,
            "has_residuals": False,
            "converged": False,
            "downforce": None,
            "drag": None,
            "ld_ratio": None,
        }
        # SLURM queue has an active job named 'EV', not 'EV_TUR50'
        slurm_queue = [
            {"job_id": "12345", "name": "EV", "state": "RUNNING"}
        ]
        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ClusterSSHClient, "get_slurm_queue", return_value=slurm_queue):
                with patch.object(ClusterSSHClient, "list_remote_cases_detailed", return_value=[remote_case]):
                    cases = asyncio.run(api_list_cases())
                    matched = next((c for c in cases if c["name"] == "EV_TUR50"), None)
                    self.assertIsNotNone(matched)
                    # Must NOT have been hijacked by the 'EV' job into 'Solving'
                    self.assertNotEqual(matched["status"], "Solving")
                    self.assertNotEqual(matched["status"], "Queued")
                    self.assertEqual(matched["status"], "Generated")

    def test_inactive_remote_case_zombie_solving_transition(self):
        """Test that an inactive remote case without a SLURM job transitions from Solving to Completed."""
        remote_case = {
            "name": "dead_solving_case",
            "status": "Solving",
            "latest_iter": 650,
            "modified": "2026-09-08 00:00:00",
            "modified_ts": 1000.0,  # Old timestamp (> 3 mins ago)
            "fidelity": "standard",
            "velocity": "20.0",
            "direction": "-z",
            "n_procs": 32,
            "stl": "wing.stl",
            "has_forces": True,
            "has_residuals": True,
            "converged": False,
            "downforce": 150.0,
            "drag": 60.0,
            "ld_ratio": 2.5,
        }
        # Empty SLURM queue (job finished or killed)
        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ClusterSSHClient, "get_slurm_queue", return_value=[]):
                with patch.object(ClusterSSHClient, "list_remote_cases_detailed", return_value=[remote_case]):
                    cases = asyncio.run(api_list_cases())
                    matched = next((c for c in cases if c["name"] == "dead_solving_case"), None)
                    self.assertIsNotNone(matched)
                    # Must transition to Completed rather than being stuck on Solving
                    self.assertEqual(matched["status"], "Completed")

    def test_is_case_running_rejects_short_substring_match(self):
        """A short case name must not match an unrelated longer SLURM job name."""
        c = ClusterSSHClient()
        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(c, "get_slurm_queue", return_value=[{"name": "RP14", "state": "RUNNING"}]):
                self.assertFalse(c.is_case_running("R"))
                self.assertTrue(c.is_case_running("RP14"))
            with patch.object(c, "get_slurm_queue", return_value=[{"name": "cfd_RP14", "state": "PENDING"}]):
                self.assertTrue(c.is_case_running("RP14"))

    def test_remote_merge_does_not_regress_local_progress(self):
        """A stale remote snapshot must not overwrite newer local iteration data."""
        case_name = "test_merge_no_regress"
        case_dir = Path("cases") / case_name
        forces_dir = case_dir / "postProcessing" / "forces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))
        (case_dir / "case_config.json").write_text(json.dumps({"case_name": case_name}))
        rows = ["# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n"]
        for i in range(1, 61):
            rows.append(f"{i} (0 -50 20) (0 0 0) (0 0 0)\n")
        (forces_dir / "force.dat").write_text("".join(rows))

        stale_remote = {
            "name": case_name, "status": "Solving", "latest_iter": 5, "converged": False,
            "modified": "2026-01-01 00:00:00", "modified_ts": 1.0, "fidelity": "standard",
            "velocity": "16.7", "direction": "-z", "n_procs": 32, "stl_name": "geometry.stl",
            "has_forces": True, "has_residuals": True, "downforce": 1.0, "drag": 1.0, "ld_ratio": 1.0,
        }
        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ssh_client, "get_slurm_queue", return_value=[]):
                with patch.object(ssh_client, "list_remote_cases_detailed", return_value=[stale_remote]):
                    cases = asyncio.run(api_list_cases())
        matched = next(c for c in cases if c["name"] == case_name)
        self.assertEqual(matched["latest_iter"], 60)
        self.assertEqual(matched["location"], "Local & Cluster")

    def test_validation_only_does_not_write_config(self):
        """A validate-only request (no generate/upload) must not persist a config."""
        case_name = "test_validate_no_write"
        cfg_path = Path("configs") / f"{case_name}.json"
        try:
            req = GenerateCaseRequest(
                config={"case_name": case_name, "stl_files": ["sample_wing.stl"]},
                upload_to_cluster=False, generate_remotely=False,
                submit_slurm=False, generate_locally=False,
            )
            asyncio.run(api_case_generate_and_submit(req))
            self.assertFalse(cfg_path.exists())
        finally:
            if cfg_path.exists():
                cfg_path.unlink()

    def test_delete_missing_remote_case_returns_404(self):
        """Deleting a case that exists neither locally nor remotely must 404."""
        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ssh_client, "run_command", return_value=(1, "", "")):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(api_case_delete("missing_case_xyz"))
                self.assertEqual(ctx.exception.status_code, 404)
            with patch.object(ssh_client, "run_command", return_value=(0, "__DELETED__", "")):
                res = asyncio.run(api_case_delete("present_case_xyz"))
                self.assertTrue(res["success"])

    def test_cancel_job_graceful_stop(self):
        """A running job is asked to write the current iteration and exit."""
        c = ClusterSSHClient()
        c.remote_repo_path = "/repo"
        calls = []

        def fake_run(cmd, timeout=60.0):
            calls.append(cmd)
            if "-o '%T|%j'" in cmd:
                return (0, "RUNNING|case_x", "")
            if cmd.startswith("[ -f "):
                return (0, "", "")
            if "sed -i" in cmd:
                return (0, "", "")
            if "squeue -h -j" in cmd:
                return (0, "", "")  # job has exited after the graceful stop
            return (0, "", "")

        with patch.object(c, "run_command", side_effect=fake_run):
            res = c.cancel_job("12345", timeout=1.0, poll=0.01)
        self.assertTrue(res["success"])
        self.assertEqual(res["mode"], "graceful")
        self.assertFalse(any(cmd.startswith("scancel") for cmd in calls))
        self.assertTrue(any("sed -i" in cmd and "writeNow" in cmd for cmd in calls))

    def test_cancel_job_falls_back_to_scancel(self):
        """A job that ignores writeNow is force-cancelled after the timeout."""
        c = ClusterSSHClient()
        c.remote_repo_path = "/repo"
        calls = []

        def fake_run(cmd, timeout=60.0):
            calls.append(cmd)
            if "-o '%T|%j'" in cmd:
                return (0, "RUNNING|case_y", "")
            if cmd.startswith("[ -f "):
                return (0, "", "")
            if "sed -i" in cmd:
                return (0, "", "")
            if "squeue -h -j" in cmd:
                return (0, "12345 still running", "")
            return (0, "", "")

        with patch.object(c, "run_command", side_effect=fake_run):
            res = c.cancel_job("12345", timeout=0.0, poll=0.01)
        self.assertEqual(res["mode"], "graceful-cancel")
        self.assertTrue(any(cmd.startswith("scancel") for cmd in calls))

    def test_download_directory_streams_remote_tar(self):
        """download_directory extracts a streamed remote tar (not per-file SFTP)."""
        import io
        import tarfile
        import tempfile

        def make_tar():
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as t:
                dinfo = tarfile.TarInfo("system")
                dinfo.type = tarfile.DIRTYPE
                t.addfile(dinfo)
                for name, data in (("log.simpleFoam", b"hello"), ("system/controlDict", b"abc")):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    t.addfile(info, io.BytesIO(data))
            return buf.getvalue()

        class FakeChannel:
            def recv_exit_status(self):
                return 0

        class FakeStream(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.channel = FakeChannel()

        class FakeClient:
            def __init__(self, payload):
                self.payload = payload
                self.commands = []

            def exec_command(self, command, timeout=None):
                self.commands.append(command)
                return io.BytesIO(), FakeStream(self.payload), io.BytesIO()

        client = FakeClient(make_tar())
        c = ClusterSSHClient()
        c._client = client
        snapshots = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
                stats = c.download_directory("/repo/cases/c1", tmp, lambda s: snapshots.append(dict(s)))
            self.assertTrue(any("tar -C" in cmd for cmd in client.commands))
            self.assertEqual(stats["files"], 2)
            self.assertEqual(stats["dirs"], 1)
            self.assertEqual(stats["bytes"], 8)
            self.assertEqual(snapshots[0]["files"], 0)
            self.assertEqual(snapshots[-1]["files"], 2)
            self.assertEqual((Path(tmp) / "log.simpleFoam").read_bytes(), b"hello")
            self.assertEqual((Path(tmp) / "system" / "controlDict").read_bytes(), b"abc")

    def test_download_case_endpoint(self):
        """Starting a download returns immediately and progress is tracked."""
        from rapidfoam.web.server import (
            CaseDownloadRequest,
            api_case_download,
            api_case_download_progress,
        )

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ssh_client, "remote_file_exists", return_value=True):
                with patch.object(ssh_client, "download_directory",
                                  return_value={"files": 3, "dirs": 1, "bytes": 42, "total_bytes": 42}):
                    res = asyncio.run(api_case_download(CaseDownloadRequest(case_name="remote_case_zzz")))
                    prog = asyncio.run(api_case_download_progress("remote_case_zzz"))
        self.assertTrue(res["success"])
        self.assertTrue(res["started"])
        self.assertFalse(prog["active"])
        self.assertEqual(prog["files"], 3)
        self.assertEqual(prog["bytes"], 42)

    def test_download_case_requires_connection(self):
        """Downloading without an SSH session is rejected."""
        from rapidfoam.web.server import CaseDownloadRequest, api_case_download

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_case_download(CaseDownloadRequest(case_name="remote_case_zzz")))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_download_case_rejects_existing_local(self):
        """Re-downloading a case that already exists locally is rejected (409)."""
        from rapidfoam.web.server import CaseDownloadRequest, api_case_download

        case_name = "already_local_probe"
        case_dir = Path("cases") / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "marker").write_text("x")
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ssh_client, "remote_file_exists", return_value=True):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(api_case_download(CaseDownloadRequest(case_name=case_name)))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_download_case_overwrite_allows_redownload(self):
        """overwrite=true permits re-downloading an existing local case."""
        from rapidfoam.web.server import CaseDownloadRequest, api_case_download

        case_name = "already_local_overwrite_probe"
        case_dir = Path("cases") / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "marker").write_text("x")
        self.addCleanup(lambda: shutil.rmtree(case_dir, ignore_errors=True))

        with patch.object(ClusterSSHClient, "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(ssh_client, "remote_file_exists", return_value=True):
                with patch.object(ssh_client, "download_directory",
                                  return_value={"files": 0, "dirs": 0, "bytes": 0, "total_bytes": 0}):
                    res = asyncio.run(api_case_download(
                        CaseDownloadRequest(case_name=case_name, overwrite=True)
                    ))
        self.assertTrue(res["started"])

    def test_download_progress_endpoint(self):
        """The progress endpoint reports live stats and defaults to inactive."""
        from rapidfoam.web.server import _set_download_progress, api_case_download_progress

        _set_download_progress("progress_probe", active=True, files=5, dirs=2, bytes=1000, total_bytes=2000)
        res = asyncio.run(api_case_download_progress("progress_probe"))
        self.assertTrue(res["active"])
        self.assertEqual(res["files"], 5)
        self.assertEqual(res["total_bytes"], 2000)

        res_unknown = asyncio.run(api_case_download_progress("no_such_progress_case"))
        self.assertFalse(res_unknown["active"])

    def test_download_active_endpoint(self):
        """The active endpoint lists in-progress downloads for UI restore."""
        from rapidfoam.web.server import _set_download_progress, api_case_download_active

        _set_download_progress("active_probe", active=True, files=1, bytes=2, total_bytes=4)
        res = asyncio.run(api_case_download_active())
        names = [d["case_name"] for d in res["downloads"]]
        self.assertIn("active_probe", names)


if __name__ == "__main__":
    unittest.main()



