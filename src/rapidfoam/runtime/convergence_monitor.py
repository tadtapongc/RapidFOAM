#!/usr/bin/env python3
"""Auto-stop monitor: stops simpleFoam on convergence or divergence.

This file is the canonical kernel, copied verbatim into generated cases (with a
small generated constants header) and imported by
``rapidfoam.postproc.convergence_monitor`` for the local ``rapidfoam-monitor``
CLI — keeping it the only copy of the force-parsing/convergence/guard logic so
the local and cluster monitors cannot drift. It is deliberately
dependency-free and annotation-free so it runs on the cluster's Python (3.6+).

Checks force.dat every INTERVAL seconds. When both drag and downforce variation
drop below THRESHOLD over the last WINDOW iterations, modifies controlDict to set
stopAt=writeNow for a clean exit. Independently, it watches for divergence: when
a force outruns the median of its trailing BLOWUP_WINDOW samples by
BLOWUP_FACTOR, the solver is stopped gracefully (writeNow) so the run does not
waste walltime and lose every result to a floating-point exception.
"""

import math
import statistics
import sys
import time
from pathlib import Path

# Axis indices/signs are injected by the generator (casegen/scripts.py) ahead of
# this file when it is copied into a case; main() reads them. Importing this
# module (e.g. from the postproc CLI) does not require them.
THRESHOLD = 0.5     # percent
WINDOW = 200        # iterations to average
MIN_ITERS = 300     # minimum before checking
INTERVAL = 10       # seconds between checks
BLOWUP_FACTOR = 50.0    # stop when |force| exceeds this multiple of its rolling median
BLOWUP_WINDOW = 50      # rolling reference window (iterations)


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


def trigger_stop(case_dir=None):
    """Modify <case>/system/controlDict to stop the solver cleanly."""
    base = Path(case_dir) if case_dir else Path(".")
    control_dict = base / "system" / "controlDict"
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


def detect_blowup(drags, downforces, factor=BLOWUP_FACTOR, window=BLOWUP_WINDOW):
    """Return (name, value, reference) when a force has blown up, else None.

    Compares the latest sample against the median of the trailing window: a
    smooth transient tracks its own median, while an exponential divergence
    outruns it by orders of magnitude within a few iterations. The 1 N floor
    keeps a near-zero reference from turning numerical noise into a trigger.
    """
    if factor <= 0 or window < 2 or len(drags) < window:
        return None
    for name, series in (("drag", drags), ("downforce", downforces)):
        recent = series[-window:]
        if not all(math.isfinite(v) for v in recent):
            return name, float("nan"), 0.0
        value = recent[-1]
        reference = statistics.median(recent)
        if abs(value) > factor * max(abs(reference), 1.0):
            return name, value, reference
    return None


def monitor(case_dir=None, interval=INTERVAL, min_iters=MIN_ITERS, threshold=THRESHOLD,
            window=WINDOW, blowup_factor=BLOWUP_FACTOR,
            drag_idx=2, drag_sign=-1.0, df_idx=1, df_sign=-1.0,
            drag_axis=None, df_axis=None):
    """Poll forces until convergence, divergence, or Ctrl-C.

    Returns True on convergence and False when the divergence guard fired.
    ``drag_idx``/``drag_sign``/``df_idx``/``df_sign`` default to the FSAE
    convention (drag -z, downforce -y); the generated case script and the
    postproc CLI both pass explicit values. ``drag_axis``/``df_axis`` are only
    used for the banner. A ``blowup_factor`` <= 0 disables the divergence guard.
    """
    base = Path(case_dir) if case_dir else Path(".")
    if interval <= 0 or not math.isfinite(interval) or min_iters < 1 or window < 2:
        raise ValueError("interval must be finite and > 0, min_iters >= 1, and window >= 2")
    if threshold <= 0 or not math.isfinite(threshold):
        raise ValueError("threshold must be finite and > 0")
    if not math.isfinite(blowup_factor):
        raise ValueError("blowup_factor must be finite")

    print("  Convergence monitor started")
    print("    Threshold: +/-%s%% | Window: %s iters | Min: %s iters"
          % (threshold, window, min_iters))
    print("    Check interval: %ss" % interval)
    if drag_axis and df_axis:
        print("    Drag axis: %s | Downforce axis: %s" % (drag_axis, df_axis))
    if blowup_factor > 0:
        print("    Divergence guard: |force| > %sx rolling median" % blowup_factor)
    print()
    sys.stdout.flush()

    while True:
        time.sleep(interval)

        files = find_force_files(base)
        if not files:
            continue

        times, drags, downforces = read_forces(files, drag_idx, drag_sign, df_idx, df_sign)

        blowup = detect_blowup(drags, downforces, factor=blowup_factor)
        if blowup is not None:
            name, value, reference = blowup
            print()
            print("  DIVERGING at iteration %d: %s = %.4g N (rolling median %.4g N)"
                  % (len(times), name, value, reference))
            print("  -> Triggering solver stop (writeNow) to preserve the state...")
            print("     Next: check the mesh (read_forces.py --mesh) and the y+ tail;")
            print("     if quality is fine, try simple.nNonOrthogonalCorrectors 3,")
            print("     then relaxation.equations.U 0.6, then div_U upwind.")
            sys.stdout.flush()
            trigger_stop(base)
            print("  -> Done. Solver will write current state and exit.")
            sys.stdout.flush()
            return False

        if len(times) < min_iters:
            continue

        converged, d_pct, f_pct, d_avg, f_avg = check_convergence(
            drags, downforces, window=window, threshold=threshold
        )

        n = len(times)
        status = "OK" if converged else ".."
        print("  [%s] iter %5d | drag: %8.3f N (+/-%.3f%%) | df: %8.3f N (+/-%.3f%%)"
              % (status, n, d_avg, d_pct, f_avg, f_pct))
        sys.stdout.flush()

        if converged:
            ld = abs(f_avg / d_avg) if d_avg != 0 else 0
            print()
            print("  CONVERGED at iteration %d" % n)
            print("    Drag:      %.3f N (+/-%.3f%%)" % (d_avg, d_pct))
            print("    Downforce: %.3f N (+/-%.3f%%)" % (f_avg, f_pct))
            print("    L/D:       %.3f" % ld)
            print()
            print("  -> Triggering solver stop (writeNow)...")
            sys.stdout.flush()
            trigger_stop(base)
            print("  -> Done. Solver will write current state and exit.")
            sys.stdout.flush()
            return True


def main():
    monitor(
        drag_idx=DRAG_IDX,      # noqa: F821  (injected by the generator)
        drag_sign=DRAG_SIGN,    # noqa: F821
        df_idx=DF_IDX,          # noqa: F821
        df_sign=DF_SIGN,        # noqa: F821
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
