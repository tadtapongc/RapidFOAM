"""Telemetry endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Response

from rapidfoam.postproc.checkmesh import (
    check_mesh_quality,
    checkmesh_targets_from_dict,
    find_snappy_logs,
    parse_checkmesh,
    parse_layer_coverage,
    verdict_bands_from_dict,
)
from rapidfoam.postproc.forces import (
    axis_config_from_dict,
    check_convergence,
    find_coefficient_files,
    find_force_files,
    find_moment_files,
    find_per_part_force_files,
    is_symmetry_case,
    load_axis_config,
    normalize_coefficient_columns,
    normalize_component_columns,
    parse_tabular_dat,
    read_forces,
    window_stats,
)
from rapidfoam.postproc.residuals import find_residual_files, read_residuals
from rapidfoam.postproc.surfacecheck import (
    check_surface,
    find_surfacecheck_logs,
    parse_surfacecheck,
    surface_check_policy_from_dict,
)
from rapidfoam.postproc.yplus import find_yplus_files, read_yplus
from rapidfoam.postproc.fielddata import (
    find_field_min_max_files,
    parse_field_min_max,
    read_field_min_max,
)
from rapidfoam.core import caseconfig
from rapidfoam.web.services.telemetry import (
    _average_vector,
    _compute_aero_balance,
    _compute_coefficients,
    _downsample_columns,
    _latest_vector,
    _lateral_axis_index,
    _load_reference_quantities,
    _load_solver_end_time,
    _load_stl_files,
    _load_vehicle_geometry,
    _per_part_forces,
    _project_coefficient_columns_for_symmetry,
    _project_force_columns_for_symmetry,
    _project_moment_columns_for_symmetry,
    _read_remote_telemetry,
    _read_text_files,
    _read_text_tail_lines,
    _read_yplus_texts,
    remote_telemetry_status,
    _shift_moment_columns,
    _sorted_segments,
    _subsample_indices,
    _summarise_yplus,
    _time_dir_key,
    _yplus_target_from_config,
    parse_residuals_from_log,
    parse_solver_diagnostics_from_log,
    parse_solver_info_text,
)
from rapidfoam.web.state import ALLOWED_LOG_TYPES, CASE_NAME_REGEX, PROJECT_ROOT, ssh_client

log = logging.getLogger("rapidfoam.web")

router = APIRouter()


@router.get("/api/telemetry/forces")
async def api_telemetry_forces(
    case_name: str,
    aref: Optional[float] = None,
    lref: Optional[float] = None,
    rho: Optional[float] = None,
    velocity: Optional[float] = None,
    cofr: Optional[str] = None,
    wheelbase: Optional[float] = None,
    front_pct: Optional[float] = None,
    max_points: int = 400,
) -> dict[str, Any]:
    """Fetch force, coefficient, and component telemetry for a case.

    Drag/downforce histories are axis-mapped and doubled for symmetry cases.
    Coefficient histories prefer the solver's ``forceCoeffs`` output, but can
    be recomputed from raw forces using post-run reference overrides supplied
    as ``aref``/``lref``/``rho``/``velocity``/``cofr`` query parameters.
    ``max_points <= 0`` returns the full history (used by the CSV export).
    """
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    local_case = PROJECT_ROOT / "cases" / case_name
    local_cfg = PROJECT_ROOT / "configs" / f"{case_name}.json"

    # Fetch the remote bundle first (single SSH command, shared 2.5 s cache) so
    # axis mapping / symmetry can use the *remote* case_config.json when the case
    # lives only on the cluster. Falls back to the local files otherwise.
    remote_bundle: dict[str, str] = {}
    remote_status: dict[str, Any] = {}
    if ssh_client.is_connected:
        remote_bundle = await _read_remote_telemetry(case_name)
        remote_status = remote_telemetry_status(case_name)

    bundled_cfg: Optional[dict[str, Any]] = None
    for path, text in remote_bundle.items():
        if path.endswith("/case_config.json"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    bundled_cfg = parsed
            except ValueError:
                bundled_cfg = None
            break

    if bundled_cfg is not None:
        drag_idx, drag_sign, df_idx, df_sign, drag_axis_name, df_axis_name = axis_config_from_dict(bundled_cfg)
        is_sym = caseconfig.has_symmetry(bundled_cfg)
        cfg_path = None
        case_dir = None
    else:
        cfg_path = str(local_cfg) if local_cfg.is_file() else None
        case_dir = local_case if local_case.is_dir() else None
        drag_idx, drag_sign, df_idx, df_sign, drag_axis_name, df_axis_name = load_axis_config(
            config_path=cfg_path, case_dir=case_dir
        )
        is_sym = is_symmetry_case(config_path=cfg_path, case_dir=case_dir)
    sym_scale = 2.0 if is_sym else 1.0
    lateral_idx = _lateral_axis_index(drag_idx, df_idx)
    reference = _load_reference_quantities(cfg_path, case_dir)
    stl_files = _load_stl_files(cfg_path, case_dir)
    vehicle = _load_vehicle_geometry(cfg_path, case_dir)
    cofr_run = list(reference.get("CofR", [0.0, 0.0, 0.0]))

    # Post-run reference overrides from the inline telemetry editor. Blank
    # fields fall back to the case configuration.
    reference_overridden = any(
        value is not None for value in (aref, lref, rho, velocity, cofr)
    )
    if rho is not None:
        reference["rho"] = round(float(rho), 6)
    if velocity is not None:
        reference["velocity"] = round(float(velocity), 4)
    if aref is not None:
        reference["Aref"] = round(float(aref), 6)
    if lref is not None:
        reference["lRef"] = round(float(lref), 6)
    cofr_effective = cofr_run
    if cofr is not None:
        try:
            parsed_cofr = [float(part) for part in str(cofr).split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="cofr must be three comma-separated numbers")
        if len(parsed_cofr) != 3:
            raise HTTPException(status_code=400, detail="cofr must be three comma-separated numbers")
        cofr_effective = parsed_cofr
    reference["CofR"] = [round(v, 6) for v in cofr_effective]
    reference["dynamic_pressure"] = round(
        0.5 * reference["rho"] * reference["velocity"] * reference["velocity"], 4
    )

    force_segments: list[str] = []
    moment_segments: list[str] = []
    coeff_segments: list[str] = []
    parts_segments: dict[str, list[str]] = {}

    if remote_bundle:
        force_segments = _sorted_segments(remote_bundle, "/force.dat", "/forces/")
        moment_segments = _sorted_segments(remote_bundle, "/moment.dat")
        coeff_segments = _sorted_segments(remote_bundle, "/coefficient.dat")
        part_items: dict[str, list[tuple[str, str]]] = {}
        for path, text in remote_bundle.items():
            if not path.endswith("/force.dat") or "/forces_" not in path:
                continue
            for segment in path.split("/"):
                if segment.startswith("forces_"):
                    part_items.setdefault(segment[len("forces_"):], []).append((path, text))
                    break
        for part, items in part_items.items():
            items.sort(key=lambda item: _time_dir_key(item[0]))
            parts_segments[part] = [text for _, text in items]

    if not force_segments and local_case.is_dir():
        force_segments = _read_text_files(find_force_files(local_case))
        moment_segments = _read_text_files(find_moment_files(local_case))
        coeff_segments = _read_text_files(find_coefficient_files(local_case))
    if not parts_segments and local_case.is_dir():
        for part, files in find_per_part_force_files(local_case).items():
            parts_segments[part] = _read_text_files(files)

    per_part = _per_part_forces(parts_segments, drag_idx, drag_sign, df_idx, df_sign, sym_scale)

    times, force_rows, force_header = parse_tabular_dat(force_segments)
    force_cols = normalize_component_columns(force_rows, force_header)

    raw_drags: list[float] = []
    raw_downforces: list[float] = []
    if times:
        drag_key = f"total_{'xyz'[drag_idx]}"
        df_key = f"total_{'xyz'[df_idx]}"
        if drag_key in force_cols and df_key in force_cols:
            raw_drags = [v * drag_sign for v in force_cols[drag_key]]
            raw_downforces = [v * df_sign for v in force_cols[df_key]]
        else:
            legacy_files = find_force_files(local_case) if local_case.is_dir() else []
            if legacy_files:
                try:
                    times, raw_drags, raw_downforces = read_forces(
                        legacy_files, drag_idx, drag_sign, df_idx, df_sign
                    )
                except Exception as exc:
                    log.warning("Could not read forces for %s: %s", case_name, exc)

    if not times:
        case_stage = "Generated"
        has_mesh = False
        if local_case.is_dir():
            if (local_case / "constant" / "polyMesh" / "points").is_file():
                has_mesh = True
                case_stage = "Meshed"
            if (local_case / "log.simpleFoam").is_file():
                case_stage = "Solving"
            elif (local_case / "log.snappyHexMesh").is_file():
                case_stage = "Meshing"

        message = f"No force.dat found yet for case '{case_name}'. Current stage: {case_stage}."
        if ssh_client.is_connected and not remote_bundle and remote_status.get("reason"):
            message = (
                f"No telemetry fetched for case '{case_name}' from the cluster "
                f"({remote_status['reason']}). Check the remote repo path and case name."
            )
        return {
            "has_data": False,
            "case_name": case_name,
            "stage": case_stage,
            "has_mesh": has_mesh,
            "run_command": "./Allrun.parallel",
            "message": message,
            "remote_status": remote_status,
            "reference": reference,
            "stl_files": stl_files,
            "vehicle": vehicle,
            "balance": {"available": False},
            "coefficients": {"available": False, "summary": {}, "series": {}},
            "components": {"available": False},
            "per_part": per_part,
        }

    # Apply symmetry projection to full-car values: drag/downforce (in-plane)
    # double, while side force (normal to the symmetry plane) cancels.
    drags = [v * sym_scale for v in raw_drags]
    downforces = [v * sym_scale for v in raw_downforces]
    if is_sym and force_cols:
        force_cols = _project_force_columns_for_symmetry(force_cols, lateral_idx)

    converged, d_pct, f_pct, d_avg, f_avg = check_convergence(drags, downforces)
    ld_ratio = abs(f_avg / d_avg) if abs(d_avg) > 1e-3 else 0.0

    # ---- Components (force + moment), scaled to full-car if symmetric ----
    moment_times, moment_rows, moment_header = parse_tabular_dat(moment_segments)
    moment_cols = normalize_component_columns(moment_rows, moment_header)
    if is_sym and moment_cols:
        moment_cols = _project_moment_columns_for_symmetry(moment_cols, lateral_idx)
    moment_cols = _shift_moment_columns(moment_cols, force_cols, cofr_run, cofr_effective)

    # ---- Coefficients (solver output, recomputed, or config fallback) ----
    coeff_times, coeff_rows, coeff_header = parse_tabular_dat(coeff_segments)
    coeff_cols = normalize_coefficient_columns(coeff_rows, coeff_header)
    coeff_source = "forceCoeffs"
    recomputed: dict[str, list[float]] = {}
    if reference_overridden and force_cols:
        recomputed = _compute_coefficients(
            force_cols,
            moment_cols,
            drag_idx,
            drag_sign,
            df_idx,
            df_sign,
            reference["rho"],
            reference["velocity"],
            reference["Aref"],
            reference["lRef"],
        )
    if recomputed:
        coeff_cols = recomputed
        coeff_times = times
        coeff_source = "recomputed"
    elif coeff_cols:
        if is_sym:
            coeff_cols = _project_coefficient_columns_for_symmetry(coeff_cols)
    else:
        velocity_ref = reference["velocity"]
        dynamic_pressure_area = (
            0.5 * reference["rho"] * velocity_ref * velocity_ref * reference["Aref"]
        )
        if dynamic_pressure_area > 0:
            coeff_cols = {
                "Cd": [v / dynamic_pressure_area for v in drags],
                "Cl": [v / dynamic_pressure_area for v in downforces],
            }
            coeff_times = times
            coeff_source = "computed"

    coeff_summary: dict[str, dict[str, Optional[float]]] = {}
    for name in ("Cd", "Cl", "Cs", "CmPitch", "CmRoll", "CmYaw"):
        values = coeff_cols.get(name)
        if values:
            avg, pct = window_stats(values)
            coeff_summary[name] = {
                "current": round(values[-1], 5),
                "avg": round(avg, 5) if avg is not None else None,
                "pct": round(pct, 3) if pct is not None else None,
            }

    sub_indices = _subsample_indices(len(times), max_points)
    coeff_sub_indices = _subsample_indices(len(coeff_times), max_points) if coeff_times else []

    # ---- Force & moment component breakdown ----
    moment_sub_indices = _subsample_indices(len(moment_times), max_points) if moment_times else []

    force_avg_total = _average_vector(force_cols, "total")
    moment_avg_total = _average_vector(moment_cols, "total")
    effective_wheelbase = wheelbase if wheelbase is not None else vehicle.get("wheelbase")
    effective_front_pct = front_pct if front_pct is not None else vehicle.get("front_weight_pct")
    balance = _compute_aero_balance(
        force_avg_total,
        moment_avg_total,
        cofr_effective,
        drag_idx,
        drag_sign,
        df_idx,
        df_sign,
        effective_wheelbase,
        effective_front_pct,
    )

    force_components = {
        "available": bool(force_cols),
        "iterations": [times[i] for i in sub_indices],
        "series": _downsample_columns(force_cols, sub_indices),
        "latest": {
            "total": _latest_vector(force_cols, "total"),
            "pressure": _latest_vector(force_cols, "pressure"),
            "viscous": _latest_vector(force_cols, "viscous"),
        },
        "average": {
            "total": force_avg_total,
            "pressure": _average_vector(force_cols, "pressure"),
            "viscous": _average_vector(force_cols, "viscous"),
        },
    }
    moment_components = {
        "available": bool(moment_cols),
        "iterations": [moment_times[i] for i in moment_sub_indices],
        "series": _downsample_columns(moment_cols, moment_sub_indices),
        "latest": {
            "total": _latest_vector(moment_cols, "total"),
            "pressure": _latest_vector(moment_cols, "pressure"),
            "viscous": _latest_vector(moment_cols, "viscous"),
        },
        "average": {
            "total": moment_avg_total,
            "pressure": _average_vector(moment_cols, "pressure"),
            "viscous": _average_vector(moment_cols, "viscous"),
        },
    }

    return {
        "has_data": True,
        "case_name": case_name,
        "is_symmetry": is_sym,
        "remote_status": remote_status,
        "drag_axis": drag_axis_name,
        "downforce_axis": df_axis_name,
        "total_iterations": len(times),
        "latest_iteration": times[-1] if times else 0,
        "converged": converged,
        "drag_avg": round(d_avg, 3),
        "downforce_avg": round(f_avg, 3),
        "drag_pct": round(d_pct, 3),
        "downforce_pct": round(f_pct, 3),
        "ld_ratio": round(ld_ratio, 3),
        "reference": reference,
        "stl_files": stl_files,
        "vehicle": vehicle,
        "balance": balance,
        "series": {
            "iterations": [times[i] for i in sub_indices],
            "drag": [round(drags[i], 3) for i in sub_indices],
            "downforce": [round(downforces[i], 3) for i in sub_indices],
        },
        "coefficients": {
            "available": bool(coeff_cols),
            "source": coeff_source if coeff_cols else None,
            "summary": coeff_summary,
            "series": {
                "iterations": [coeff_times[i] for i in coeff_sub_indices],
                **_downsample_columns(coeff_cols, coeff_sub_indices),
            },
        },
        "components": {
            "available": bool(force_cols) or bool(moment_cols),
            "force": force_components,
            "moment": moment_components,
        },
        "per_part": per_part,
    }


@router.get("/api/telemetry/export")
async def api_telemetry_export(case_name: str, format: str = "csv") -> Response:
    """Export the telemetry histories as CSV or JSON for offline analysis."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    data = await api_telemetry_forces(case_name, max_points=0)
    if not data.get("has_data"):
        raise HTTPException(status_code=404, detail=f"No telemetry available for case '{case_name}'")

    if format.lower() == "json":
        payload = json.dumps(data, indent=2)
        return Response(
            content=payload,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{case_name}_telemetry.json"'},
        )

    series = data.get("series", {})
    iterations = series.get("iterations", [])
    drags = series.get("drag", [])
    downforces = series.get("downforce", [])

    coeff_series = (data.get("coefficients") or {}).get("series", {})
    coeff_names = [name for name in ("Cd", "Cl", "Cs", "CmPitch", "CmRoll", "CmYaw") if name in coeff_series]
    coeff_lookup: dict[str, dict[float, Optional[float]]] = {}
    for name in coeff_names:
        coeff_lookup[name] = {
            round(t, 8): v
            for t, v in zip(coeff_series.get("iterations", []), coeff_series[name])
        }

    header = ["iteration", "drag_N", "downforce_N", "L_over_D"] + coeff_names
    lines = [",".join(header)]
    for index, iteration in enumerate(iterations):
        drag = drags[index] if index < len(drags) else None
        downforce = downforces[index] if index < len(downforces) else None
        ld = abs(downforce / drag) if drag not in (None, 0) and downforce is not None else None
        row = [
            str(iteration),
            "" if drag is None else f"{drag:.4f}",
            "" if downforce is None else f"{downforce:.4f}",
            "" if ld is None else f"{ld:.4f}",
        ]
        key = round(float(iteration), 8)
        for name in coeff_names:
            value = coeff_lookup[name].get(key)
            row.append("" if value is None else f"{value:.6f}")
        lines.append(",".join(row))

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{case_name}_telemetry.csv"'},
    )




@router.get("/api/telemetry/residuals")
async def api_telemetry_residuals(case_name: str) -> dict[str, Any]:
    """Parse solver residuals from solverInfo.dat or log.simpleFoam with aligned iterations."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    rows: dict[float, dict[str, float]] = {}

    # 1. Check remote cluster first if connected
    if ssh_client.is_connected:
        bundle = await _read_remote_telemetry(case_name)
        solverinfo = _sorted_segments(bundle, "/solverInfo.dat") or _sorted_segments(
            bundle, "/residuals.dat"
        )
        if solverinfo:
            rows = parse_solver_info_text("\n".join(solverinfo))

        # Fallback to the solver log if no solverInfo.dat is present
        if not rows:
            log_text = next(
                (value for path, value in bundle.items() if path.endswith("/log.simpleFoam")),
                "",
            )
            if log_text:
                rows = parse_residuals_from_log(log_text)

    # 2. Check local case directory if no rows yet
    local_case = PROJECT_ROOT / "cases" / case_name
    if not rows and local_case.is_dir():
        res_files = find_residual_files(local_case)
        if res_files:
            try:
                data_dict, headers = read_residuals(res_files)
                times = data_dict.get("Time", [])
                for idx, t in enumerate(times):
                    if t is not None:
                        t_key = round(float(t), 8)
                        row = {}
                        for h in headers:
                            val = data_dict.get(h, [])[idx] if idx < len(data_dict.get(h, [])) else None
                            if val is not None and math.isfinite(val) and val > 0:
                                row[h] = float(val)
                        rows[t_key] = row
            except Exception as exc:
                log.warning("Could not read local residual files: %s", exc)

        # Fallback to local log.simpleFoam
        if not rows:
            local_log = local_case / "log.simpleFoam"
            if local_log.is_file():
                try:
                    with open(local_log, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        log_content = "".join(lines[-5000:])
                        rows = parse_residuals_from_log(log_content)
                except Exception as exc:
                    log.warning("Could not read local log.simpleFoam: %s", exc)

    if not rows:
        return {
            "has_data": False,
            "message": f"No residual data or log.simpleFoam found for case '{case_name}'.",
        }

    sorted_iters = sorted(rows.keys())
    if not sorted_iters:
        return {"has_data": False, "message": "No iterations found in residual data."}

    var_candidates = {
        "p": ["p_initial", "p"],
        "Ux": ["Ux_initial", "Ux"],
        "Uy": ["Uy_initial", "Uy"],
        "Uz": ["Uz_initial", "Uz"],
        "k": ["k_initial", "k"],
        "omega": ["omega_initial", "omega", "epsilon_initial", "epsilon", "nuTilda_initial", "nuTilda"],
    }

    all_vars = ["p", "Ux", "Uy", "Uz", "k", "omega"]
    residuals_aligned: dict[str, list[Optional[float]]] = {v: [] for v in all_vars}

    for it in sorted_iters:
        row = rows[it]
        for v in all_vars:
            val = None
            for cand in var_candidates[v]:
                if cand in row and row[cand] is not None:
                    val = round(row[cand], 8)
                    break
            residuals_aligned[v].append(val)

    # Downsample if iterations > 400 to keep UI rendering fast while showing entire run
    max_pts = 400
    if len(sorted_iters) > max_pts:
        step = math.ceil(len(sorted_iters) / max_pts)
        sub_indices = list(range(0, len(sorted_iters), step))
        if (len(sorted_iters) - 1) not in sub_indices:
            sub_indices.append(len(sorted_iters) - 1)

        iters_sub = [int(sorted_iters[i]) for i in sub_indices]
        residuals_sub = {v: [residuals_aligned[v][i] for i in sub_indices] for v in all_vars}
    else:
        iters_sub = [int(it) for it in sorted_iters]
        residuals_sub = residuals_aligned

    return {
        "has_data": True,
        "total_iterations": len(sorted_iters),
        "latest_iteration": int(sorted_iters[-1]),
        "iterations": iters_sub,
        "residuals": residuals_sub,
    }


@router.get("/api/telemetry/solver")
async def api_telemetry_solver(case_name: str) -> dict[str, Any]:
    """Return solver-health diagnostics: continuity, linear effort, timing, and ETA."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    content = ""
    if ssh_client.is_connected:
        bundle = await _read_remote_telemetry(case_name)
        content = next(
            (value for path, value in bundle.items() if path.endswith("/log.simpleFoam")),
            "",
        )

    local_case = PROJECT_ROOT / "cases" / case_name
    if not content and local_case.is_dir():
        local_log = local_case / "log.simpleFoam"
        if local_log.is_file():
            content = _read_text_tail_lines(local_log)

    rows = parse_solver_diagnostics_from_log(content) if content else {}
    if not rows:
        return {
            "has_data": False,
            "case_name": case_name,
            "message": f"No solver diagnostics found for case '{case_name}'.",
        }

    sorted_iters = sorted(rows.keys())
    iterations = [int(it) for it in sorted_iters]
    continuity_global = [rows[it]["continuity_global"] for it in sorted_iters]
    continuity_cumulative = [rows[it]["continuity_cumulative"] for it in sorted_iters]
    linear_iters = [rows[it]["linear_iters"] for it in sorted_iters]
    linear_iters_max = [rows[it]["linear_iters_max"] for it in sorted_iters]
    execution_time = [rows[it]["execution_time"] for it in sorted_iters]

    # Iteration rate from the two most recent fully timed steps.
    timed = [(it, rows[it]["execution_time"]) for it in sorted_iters if rows[it]["execution_time"]]
    iterations_per_second: Optional[float] = None
    if len(timed) >= 2:
        (it0, et0), (it1, et1) = timed[-2], timed[-1]
        if et1 > et0:
            iterations_per_second = (it1 - it0) / (et1 - et0)
    elif timed:
        it1, et1 = timed[-1]
        if et1 > 0:
            iterations_per_second = it1 / et1

    elapsed = execution_time[-1] if execution_time and execution_time[-1] is not None else None
    cfg_path = str(PROJECT_ROOT / "configs" / f"{case_name}.json")
    if not Path(cfg_path).is_file():
        cfg_path = None
    end_time = _load_solver_end_time(cfg_path, local_case if local_case.is_dir() else None)
    remaining = (end_time - iterations[-1]) if end_time is not None else None
    eta_seconds = (
        remaining / iterations_per_second
        if remaining is not None and remaining > 0 and iterations_per_second
        else (0.0 if remaining is not None and remaining <= 0 else None)
    )

    sub_indices = _subsample_indices(len(iterations))
    latest_continuity = next(
        (value for value in reversed(continuity_global) if value is not None), None
    )
    return {
        "has_data": True,
        "case_name": case_name,
        "total_iterations": len(iterations),
        "latest_iteration": iterations[-1],
        "iterations_per_second": round(iterations_per_second, 4) if iterations_per_second is not None else None,
        "elapsed_seconds": round(elapsed, 2) if elapsed is not None else None,
        "end_time": end_time,
        "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
        "latest_continuity_global": latest_continuity,
        "latest_linear_iters": linear_iters[-1],
        "series": {
            "iterations": [iterations[i] for i in sub_indices],
            "continuity_global": [continuity_global[i] for i in sub_indices],
            "continuity_cumulative": [continuity_cumulative[i] for i in sub_indices],
            "linear_iters": [linear_iters[i] for i in sub_indices],
            "linear_iters_max": [linear_iters_max[i] for i in sub_indices],
        },
    }


@router.get("/api/telemetry/mesh")
async def api_telemetry_mesh(case_name: str) -> dict[str, Any]:
    """Verify mesh quality from the checkMesh and snappyHexMesh logs.

    Returns the parsed checkMesh metrics, the per-patch boundary-layer coverage
    and an overall verdict, using the case's configured limits where available.
    """
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    checkmesh_text = ""
    snappy_text = ""
    config_dict: Optional[dict[str, Any]] = None
    yplus_files: list[Path] = []
    yplus_data: dict[str, dict[str, float]] = {}
    field_min_max: dict[str, Any] = {}

    # 1. Remote cluster first if connected (shared telemetry bundle).
    if ssh_client.is_connected:
        bundle = await _read_remote_telemetry(case_name)
        checkmesh_text = next(
            (value for path, value in bundle.items() if path.endswith("/log.checkMesh")), ""
        )
        snappy_text = "\n".join(
            value for path, value in bundle.items() if "/log.snappyHexMesh" in path
        )
        yplus_data = _read_yplus_texts(_sorted_segments(bundle, "/yPlus.dat"))
        for path, text in bundle.items():
            if path.endswith("/fieldMinMax.dat"):
                field_min_max.update(parse_field_min_max(text))
        cfg_text = next(
            (value for path, value in bundle.items() if path.endswith("/case_config.json")), ""
        )
        if cfg_text:
            try:
                parsed_cfg = json.loads(cfg_text)
                if isinstance(parsed_cfg, dict):
                    config_dict = parsed_cfg
            except ValueError:
                config_dict = None

    # 2. Local case directory fallback.
    local_case = PROJECT_ROOT / "cases" / case_name
    if not checkmesh_text and (local_case / "log.checkMesh").is_file():
        checkmesh_text = _read_text_tail_lines(local_case / "log.checkMesh")
    if not snappy_text:
        snappy_logs = find_snappy_logs(local_case)
        if snappy_logs:
            snappy_text = "\n".join(_read_text_tail_lines(p) for p in snappy_logs)
    if not yplus_data:
        yplus_files = find_yplus_files(local_case)
        if yplus_files:
            yplus_data = read_yplus(yplus_files)
    if local_case.is_dir() and not field_min_max:
        field_min_max = read_field_min_max(find_field_min_max_files(local_case))
    if config_dict is None:
        config_dict = caseconfig.read_case_config(
            config_path=str(PROJECT_ROOT / "configs" / f"{case_name}.json"),
            case_dir=local_case,
        )

    if not checkmesh_text and not snappy_text:
        return {
            "has_data": False,
            "case_name": case_name,
            "message": f"No checkMesh or snappyHexMesh log found for case '{case_name}'.",
        }

    stats = parse_checkmesh(checkmesh_text)
    layers = parse_layer_coverage(snappy_text)
    targets = checkmesh_targets_from_dict(config_dict)
    summary = check_mesh_quality(
        stats,
        layers,
        int(targets["target_layers"]) if "target_layers" in targets else None,
        max_non_ortho=targets.get("max_non_ortho", 65.0),
        max_skewness=targets.get("max_skewness", 4.0),
        bands=verdict_bands_from_dict(config_dict),
        has_symmetry=caseconfig.has_symmetry(config_dict),
    )

    # Cross-reference the realised near-wall y+ (from the yPlus function object)
    # against the sizing target so "layers all present" is not mistaken for
    # "layers at the right height".
    yplus_target = _yplus_target_from_config(config_dict)
    summary["y_plus"] = _summarise_yplus(yplus_data, yplus_target)
    summary["field_min_max"] = field_min_max

    return {"has_data": True, "case_name": case_name, **summary}


@router.get("/api/telemetry/surface")
async def api_telemetry_surface(case_name: str) -> dict[str, Any]:
    """Report surface integrity from the surfaceCheck log.

    Report-only: the pipeline only aborts on a defect when
    ``surface_check.enforce`` is set. A symmetry half model is open along the
    cut and is exempt from the closure requirement.
    """
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")

    surface_text = ""
    config_dict: Optional[dict[str, Any]] = None

    # 1. Remote cluster first if connected (shared telemetry bundle).
    if ssh_client.is_connected:
        bundle = await _read_remote_telemetry(case_name)
        surface_text = "\n".join(
            value for path, value in bundle.items() if "/log.surfaceCheck" in path
        )
        cfg_text = next(
            (value for path, value in bundle.items() if path.endswith("/case_config.json")), ""
        )
        if cfg_text:
            try:
                parsed_cfg = json.loads(cfg_text)
                if isinstance(parsed_cfg, dict):
                    config_dict = parsed_cfg
            except ValueError:
                config_dict = None

    # 2. Local case directory fallback.
    local_case = PROJECT_ROOT / "cases" / case_name
    if not surface_text:
        surface_logs = find_surfacecheck_logs(local_case)
        if surface_logs:
            surface_text = "\n".join(_read_text_tail_lines(p) for p in surface_logs)
    if config_dict is None:
        config_dict = caseconfig.read_case_config(
            config_path=str(PROJECT_ROOT / "configs" / f"{case_name}.json"),
            case_dir=local_case,
        )

    if not surface_text:
        return {
            "has_data": False,
            "case_name": case_name,
            "message": f"No surfaceCheck log found for case '{case_name}'.",
        }

    summary = check_surface(
        parse_surfacecheck(surface_text),
        has_symmetry=caseconfig.has_symmetry(config_dict),
        **surface_check_policy_from_dict(config_dict),
    )
    return {"has_data": True, "case_name": case_name, **summary}


@router.get("/api/telemetry/logs")
async def api_telemetry_logs(case_name: str, log_type: str = "simpleFoam", lines: int = 100) -> dict[str, Any]:
    """Tail log files with strict type whitelisting and length limits."""
    if not CASE_NAME_REGEX.match(case_name):
        raise HTTPException(status_code=400, detail="Invalid case_name")
    if log_type not in ALLOWED_LOG_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported log_type: {log_type}")

    safe_lines = max(1, min(int(lines), 2000))
    filename = f"log.{log_type}"
    content = ""

    if ssh_client.is_connected:
        remote_path = f"{ssh_client.remote_repo_path}/cases/{case_name}/{filename}"
        content = await asyncio.to_thread(ssh_client.read_remote_text, remote_path, max_lines=safe_lines)

    if not content:
        local_file = PROJECT_ROOT / "cases" / case_name / filename
        if local_file.is_file():
            try:
                with open(local_file, encoding="utf-8", errors="replace") as f:
                    raw_lines = f.readlines()
                    content = "".join(raw_lines[-safe_lines:])
            except Exception:
                pass

    return {
        "case_name": case_name,
        "log_type": log_type,
        "content": content or f"No entries in {filename} yet.",
        "log_file": filename,
        "size_bytes": len((content or "").encode("utf-8")),
    }


