"""Remote cluster case inventory.

Uploaded and executed on the cluster by ``web.ssh_client`` to summarise every
case in ``<repo>/cases`` (metadata, status, latest forces). Run as a script:
``python3 remote_cases.py <repo>``; it prints the results between
``__CASE_JSON_START__``/``__CASE_JSON_END__`` markers.
"""

import sys, json, re, statistics, datetime
from pathlib import Path

repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
cases_dir = repo / "cases"

AXIS_MAP = {"x": 0, "y": 1, "z": 2}
def parse_axis(axis_str):
    s = str(axis_str).strip().lower()
    sign = -1.0 if s.startswith("-") else 1.0
    idx = AXIS_MAP.get(s.lstrip("+-"), 0)
    return idx, sign

def read_tail(fpath, max_bytes=8192):
    try:
        sz = fpath.stat().st_size
        with open(str(fpath), "rb") as f:
            if sz > max_bytes:
                f.seek(sz - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

results = []
if cases_dir.is_dir():
    for d in sorted(cases_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        cname = d.name
        st = d.stat()
        mtime = st.st_mtime
        mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

        # Config
        cfg_file = d / "case_config.json"
        if not cfg_file.is_file():
            cfg_file = repo / "configs" / (cname + ".json")

        fidelity = "standard"
        velocity = "16.67"
        direction = "-z"
        n_procs = 32
        stl_name = "--"
        drag_idx, drag_sign = 2, -1.0
        df_idx, df_sign = 1, -1.0
        is_sym = False

        if cfg_file.is_file():
            try:
                with open(str(cfg_file), encoding="utf-8") as f:
                    cd = json.load(f)
                    fidelity = cd.get("fidelity", "standard")
                    v_val = cd.get("flow", {}).get("velocity", 16.67)
                    velocity = f"{v_val:.1f}" if isinstance(v_val, (int, float)) else str(v_val)
                    direction = cd.get("flow", {}).get("direction", "-z")
                    n_procs = cd.get("parallel", {}).get("n_procs", 32)
                    stls = cd.get("stl_files", [])
                    if stls:
                        stl_name = Path(stls[0]).name
                    outputs = cd.get("outputs", {})
                    if "drag_axis" in outputs:
                        drag_idx, drag_sign = parse_axis(outputs["drag_axis"])
                    if "downforce_axis" in outputs:
                        df_idx, df_sign = parse_axis(outputs["downforce_axis"])
                    faces = cd.get("domain_faces")
                    if not faces:
                        # No explicit faces: the generator defaults the lateral-min
                        # face to symmetry, so the case is a half-model.
                        is_sym = True
                    elif any("symmetry" in str(v).lower() for v in faces.values()):
                        is_sym = True
            except Exception:
                pass

        if not is_sym:
            boundary = d / "constant" / "polyMesh" / "boundary"
            if boundary.is_file():
                try:
                    if re.search(r"\btype\s+symmetry(?:Plane)?\s*;", boundary.read_text(encoding="utf-8", errors="replace")):
                        is_sym = True
                except Exception:
                    pass

        sym_scale = 2.0 if is_sym else 1.0

        # Read forces
        force_files = sorted(d.glob("postProcessing/forces/*/force.dat"))

        has_forces = len(force_files) > 0
        latest_iter = None
        converged = False
        downforce_val = None
        drag_val = None
        ld_val = None

        if force_files:
            samples = {}
            for ff in force_files:
                columnar_total = True
                try:
                    with open(str(ff), encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("#"):
                                low = line.lower()
                                if "total_x" in low or "total_y" in low or "total_z" in low:
                                    columnar_total = True
                                elif "pressure" in low and "viscous" in low:
                                    columnar_total = False
                                continue
                            parts = line.replace("(", " ").replace(")", " ").split()
                            if len(parts) >= 10:
                                try:
                                    t = float(parts[0])
                                    if columnar_total:
                                        drg = float(parts[1 + drag_idx]) * drag_sign * sym_scale
                                        df = float(parts[1 + df_idx]) * df_sign * sym_scale
                                    else:
                                        drg = (float(parts[1 + drag_idx]) + float(parts[4 + drag_idx])) * drag_sign * sym_scale
                                        df = (float(parts[1 + df_idx]) + float(parts[4 + df_idx])) * df_sign * sym_scale
                                    samples[t] = (t, drg, df)
                                except ValueError:
                                    continue
                except Exception:
                    pass

            if samples:
                sorted_samples = sorted(samples.values(), key=lambda s: s[0])
                times = [s[0] for s in sorted_samples]
                drags = [s[1] for s in sorted_samples]
                downforces = [s[2] for s in sorted_samples]
                latest_iter = int(times[-1])

                window = 200
                if len(drags) < window:
                    window = len(drags)

                if window >= 20:
                    d_win = drags[-window:]
                    f_win = downforces[-window:]
                    d_avg = statistics.mean(d_win)
                    f_avg = statistics.mean(f_win)
                    d_std = statistics.stdev(d_win)
                    f_std = statistics.stdev(f_win)
                    d_pct = (d_std / abs(d_avg) * 100) if d_avg != 0 else 100.0
                    f_pct = (f_std / abs(f_avg) * 100) if f_avg != 0 else 100.0
                    converged = (d_pct < 0.5 and f_pct < 0.5)
                else:
                    d_avg = statistics.mean(drags) if drags else 0.0
                    f_avg = statistics.mean(downforces) if downforces else 0.0
                    converged = False

                downforce_val = round(f_avg, 2)
                drag_val = round(d_avg, 2)
                ld_val = round(f_avg / d_avg, 2) if abs(d_avg) > 1e-3 else None

        # Check logs and status
        status = "Generated"
        log_simple = d / "log.simpleFoam"
        has_residuals = log_simple.is_file()
        has_mesh = (d / "constant" / "polyMesh" / "points").is_file()

        if log_simple.is_file():
            tail = read_tail(log_simple)
            if "End" in tail or "Finalising parallel run" in tail:
                status = "Converged" if converged else "Completed"
            elif any(err in tail for err in ["FOAM FATAL", "Fatal error", "FOAM aborting", "sigFpe", "SIGFPE", "Floating point exception"]):
                status = "Failed"
            else:
                status = "Solving"
        elif (d / "log.snappyHexMesh").is_file():
            tail = read_tail(d / "log.snappyHexMesh")
            if "End" in tail or "Finalising parallel run" in tail:
                status = "Meshed"
            elif any(err in tail for err in ["FOAM FATAL", "Fatal error", "FOAM aborting", "sigFpe", "SIGFPE"]):
                status = "Failed"
            else:
                status = "Meshing"
        elif has_mesh:
            status = "Meshed"

        is_running_loc = (d / ".running_location").is_file()

        # Use the newest activity timestamp (directory or log files) so the
        # server can reliably distinguish an actively running case from a
        # stalled one even though the directory mtime does not change while
        # an existing log file is being appended to.
        activity = st.st_mtime
        for lf in (d / "log.simpleFoam", d / "log.snappyHexMesh"):
            try:
                if lf.is_file():
                    activity = max(activity, lf.stat().st_mtime)
            except OSError:
                pass
        mtime = activity
        mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

        results.append({
            "name": cname,
            "modified": mtime_str,
            "modified_ts": mtime,
            "status": status,
            "fidelity": fidelity,
            "velocity": velocity,
            "direction": direction,
            "n_procs": n_procs,
            "stl_name": stl_name,
            "has_forces": has_forces,
            "has_residuals": has_residuals,
            "has_mesh": has_mesh,
            "latest_iter": latest_iter,
            "converged": converged,
            "downforce": downforce_val,
            "drag": drag_val,
            "ld_ratio": ld_val,
            "is_running_loc": is_running_loc,
        })

print("__CASE_JSON_START__" + json.dumps(results) + "__CASE_JSON_END__")
