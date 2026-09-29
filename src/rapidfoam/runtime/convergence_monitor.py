#!/usr/bin/env python3
"""Auto-stop monitor: stops simpleFoam when forces converge.

This file is the canonical source copied verbatim into generated cases (with a
small generated constants header). It is deliberately dependency-free and
annotation-free so it runs on the cluster's Python (3.6+).

Checks force.dat every INTERVAL seconds. When both drag and downforce variation
drop below THRESHOLD over the last WINDOW iterations, modifies controlDict to set
stopAt=writeNow for a clean exit.
"""

import math
import statistics
import sys
import time
from pathlib import Path

# Axis indices/signs are injected by the generator (casegen/writers.scripts.py)
# ahead of this file; see DRAG_IDX/DRAG_SIGN/DF_IDX/DF_SIGN.
THRESHOLD = 0.5     # percent
WINDOW = 200        # iterations to average
MIN_ITERS = 300     # minimum before checking
INTERVAL = 10       # seconds between checks


def _dir_time(path):
    try:
        return float(path.name)
    except ValueError:
        return 0.0


def find_force_files(base_dir=None):
    base = Path(base_dir) if base_dir else Path(".")
    all_files = []
    forces_dir = base / "postProcessing" / "forces"
    if forces_dir.exists():
        for d in sorted(forces_dir.glob("*/"), key=_dir_time):
            f = d / "force.dat"
            if f.exists():
                all_files.append(f)
    if not all_files:
        for proc_dir in sorted(base.glob("processor*")):
            pf = proc_dir / "postProcessing" / "forces"
            if pf.exists():
                for d in sorted(pf.glob("*/"), key=_dir_time):
                    f = d / "force.dat"
                    if f.exists():
                        all_files.append(f)
                if all_files:
                    break
    return all_files


def read_forces(files, drag_idx, drag_sign, df_idx, df_sign):
    samples = {}
    if not isinstance(files, (list, tuple)):
        files = [files]
    for path in files:
        segment_started = False
        columnar_total = True
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
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
                    parts = line.replace("(", "").replace(")", "").split()
                    if len(parts) < 10:
                        continue
                    try:
                        values = [float(v) for v in parts[:10]]
                    except ValueError:
                        continue
                    if not all(math.isfinite(v) for v in values):
                        continue
                    t = values[0]
                    t_key = round(t, 8)
                    if not segment_started:
                        samples = {k: s for k, s in samples.items() if k < t_key}
                        segment_started = True
                    if columnar_total:
                        drag_value = values[1 + drag_idx]
                        df_value = values[1 + df_idx]
                    else:
                        drag_value = values[1 + drag_idx] + values[4 + drag_idx]
                        df_value = values[1 + df_idx] + values[4 + df_idx]
                    samples[t_key] = (t, drag_value * drag_sign, df_value * df_sign)
        except OSError:
            pass
    combined = sorted(samples.values())
    return ([c[0] for c in combined], [c[1] for c in combined], [c[2] for c in combined])


def check_convergence(drags, downforces, window=WINDOW, threshold=THRESHOLD):
    if window < 2 or threshold <= 0 or not math.isfinite(threshold):
        raise ValueError("window must be >= 2 and threshold must be finite and > 0")
    if len(drags) != len(downforces):
        raise ValueError("Drag and downforce histories must have equal lengths")
    if len(drags) < window:
        window = len(drags)
    if window < 20:
        if drags and all(math.isfinite(v) for v in drags + downforces):
            return False, 100.0, 100.0, statistics.mean(drags), statistics.mean(downforces)
        return False, 100.0, 100.0, 0.0, 0.0

    d_win = drags[-window:]
    f_win = downforces[-window:]
    if not all(math.isfinite(v) for v in d_win + f_win):
        return False, 100.0, 100.0, 0.0, 0.0
    d_avg = statistics.mean(d_win)
    f_avg = statistics.mean(f_win)
    d_pct = (statistics.stdev(d_win) / abs(d_avg) * 100) if d_avg != 0 else 100.0
    f_pct = (statistics.stdev(f_win) / abs(f_avg) * 100) if f_avg != 0 else 100.0
    return (d_pct < threshold and f_pct < threshold), d_pct, f_pct, d_avg, f_avg


def trigger_stop():
    """Modify controlDict to stop the solver cleanly."""
    control_dict = Path("system/controlDict")
    if not control_dict.exists():
        return
    text = control_dict.read_text(encoding="utf-8")
    new_lines = []
    for line in text.split("\n"):
        if line.strip().startswith("stopAt"):
            new_lines.append("stopAt          writeNow;")
        else:
            new_lines.append(line)
    control_dict.write_text("\n".join(new_lines), encoding="utf-8")


def main():
    print("  Convergence monitor started (threshold: +/-%s%%, window: %s, min: %s)"
          % (THRESHOLD, WINDOW, MIN_ITERS))
    sys.stdout.flush()

    while True:
        time.sleep(INTERVAL)

        files = find_force_files()
        if not files:
            continue

        times, drags, downforces = read_forces(
            files, DRAG_IDX, DRAG_SIGN, DF_IDX, DF_SIGN  # noqa: F821  (injected by the generator)
        )
        if len(times) < MIN_ITERS:
            continue

        converged, d_pct, f_pct, d_avg, f_avg = check_convergence(
            drags, downforces, window=WINDOW, threshold=THRESHOLD
        )

        n = len(times)
        status = "OK" if converged else ".."
        print("  [%s] iter %5d | drag: %8.3f N (+/-%.3f%%) | df: %8.3f N (+/-%.3f%%)"
              % (status, n, d_avg, d_pct, f_avg, f_pct))
        sys.stdout.flush()

        if converged:
            ld = abs(f_avg / d_avg) if d_avg != 0 else 0
            print("\n  CONVERGED at iteration %d" % n)
            print("    Drag:      %.3f N (+/-%.3f%%)" % (d_avg, d_pct))
            print("    Downforce: %.3f N (+/-%.3f%%)" % (f_avg, f_pct))
            print("    L/D:       %.3f" % ld)
            print("  -> Triggering solver stop (writeNow)...")
            sys.stdout.flush()
            trigger_stop()
            print("  -> Done. Solver will write and exit.")
            sys.stdout.flush()
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
