"""Convergence monitor — stops OpenFOAM solver when forces converge.

Runs alongside simpleFoam, periodically checks force data, and writes
a trigger file to stop the solver cleanly when both drag and downforce
variation drop below the threshold.

Usage:
    python -m rapidfoam.postproc.convergence_monitor [--interval 10] [--config case_config.json]

The solver must have `runTimeModifiable true` and `writeAtEnd true` in controlDict.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from pathlib import Path

from rapidfoam.postproc.forces import (
    check_convergence,
    find_force_files,
    load_axis_config,
    read_forces,
)


def _write_stop_trigger(case_dir: Path) -> None:
    """Write trigger to stop OpenFOAM solver cleanly.

    Modifies controlDict to set stopAt=writeNow, which causes the solver
    to write the current time step and exit gracefully.
    """
    control_dict = case_dir / "system" / "controlDict"
    if not control_dict.exists():
        return

    text = control_dict.read_text(encoding="utf-8")

    # Replace stopAt line
    lines = text.split("\n")
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("stopAt"):
            new_lines.append("stopAt          writeNow;")
        else:
            new_lines.append(line)

    control_dict.write_text("\n".join(new_lines), encoding="utf-8")


def detect_blowup(
    drags: list[float],
    downforces: list[float],
    factor: float = 50.0,
    window: int = 50,
) -> tuple[str, float, float] | None:
    """Return (name, value, reference) when a force has blown up, else None.

    The latest sample is compared against the median of the trailing window: a
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


def monitor(
    case_dir: Path | None = None,
    config_path: str | None = None,
    interval: float = 10,
    min_iters: int = 300,
    threshold: float = 0.5,
    window: int = 200,
    blowup_factor: float = 50.0,
) -> bool:
    """Monitor forces and stop solver when converged (or blown up).

    Args:
        case_dir: Path to the OpenFOAM case (default: cwd)
        config_path: Path to config JSON for axis info
        interval: Check interval in seconds
        min_iters: Minimum iterations before checking convergence
        threshold: Convergence threshold (% variation)
        window: Number of iterations to average over
        blowup_factor: Stop when a force exceeds this multiple of its rolling
            median (divergence guard); <= 0 disables the guard

    Returns:
        True if convergence was detected and stop triggered; False when the
        divergence guard (or an explicit stop) ended the run.
    """
    if case_dir is None:
        case_dir = Path.cwd()
    else:
        case_dir = Path(case_dir)

    if interval <= 0 or not math.isfinite(interval) or min_iters < 1 or window < 2:
        raise ValueError("interval must be finite and > 0, min_iters >= 1, and window >= 2")
    if threshold <= 0 or not math.isfinite(threshold):
        raise ValueError("threshold must be finite and > 0")
    if not math.isfinite(blowup_factor):
        raise ValueError("blowup_factor must be finite")
    drag_idx, drag_sign, df_idx, df_sign, drag_axis, df_axis = load_axis_config(
        config_path, case_dir=case_dir
    )

    print("  Convergence monitor started")
    print(f"    Threshold: ±{threshold}% | Window: {window} iters | Min: {min_iters} iters")
    print(f"    Check interval: {interval}s")
    print(f"    Drag axis: {drag_axis} | Downforce axis: {df_axis}")
    if blowup_factor > 0:
        print(f"    Divergence guard: |force| > {blowup_factor:g}x rolling median")
    print()

    while True:
        time.sleep(interval)

        files = find_force_files(case_dir)
        if not files:
            continue

        times, drags, downforces = read_forces(files, drag_idx, drag_sign, df_idx, df_sign)

        blowup = detect_blowup(drags, downforces, factor=blowup_factor)
        if blowup is not None:
            name, value, reference = blowup
            print()
            print(f"  ✗ DIVERGING at iteration {len(times)}: {name} = {value:.4g} N "
                  f"(rolling median {reference:.4g} N)")
            print("  → Triggering solver stop (writeNow) to preserve the state...")
            _write_stop_trigger(case_dir)
            print("  → Done. Solver will write current state and exit.")
            return False

        if len(times) < min_iters:
            continue

        converged, d_pct, f_pct, d_avg, f_avg = check_convergence(
            drags, downforces, window=window, threshold=threshold
        )

        iter_count = len(times)
        status = "✓" if converged else "…"
        print(
            f"  [{status}] iter {iter_count:>5} | "
            f"drag: {d_avg:>8.3f} N (±{d_pct:.3f}%) | "
            f"df: {f_avg:>8.3f} N (±{f_pct:.3f}%)",
            flush=True,
        )

        if converged:
            print()
            print(f"  ✓ CONVERGED at iteration {iter_count}")
            print(f"    Drag:      {d_avg:.3f} N (±{d_pct:.3f}%)")
            print(f"    Downforce: {f_avg:.3f} N (±{f_pct:.3f}%)")
            print(f"    L/D:       {abs(f_avg / d_avg):.3f}" if d_avg != 0 else "")
            print()
            print("  → Triggering solver stop (writeNow)...")

            _write_stop_trigger(case_dir)

            print("  → Done. Solver will write current state and exit.")
            return True


def main() -> None:
    """CLI entry point for convergence monitor."""
    parser = argparse.ArgumentParser(
        description="Monitor OpenFOAM forces and stop solver on convergence.",
    )
    parser.add_argument("--config", "-c", default=None,
                        help="Config JSON for axis info (default: case_config.json)")
    parser.add_argument("--interval", "-i", type=float, default=10,
                        help="Check interval in seconds (default: 10)")
    parser.add_argument("--min-iters", "-m", type=int, default=300,
                        help="Minimum iterations before checking (default: 300)")
    parser.add_argument("--threshold", "-t", type=float, default=0.5,
                        help="Convergence threshold %% (default: 0.5)")
    parser.add_argument("--window", "-w", type=int, default=200,
                        help="Averaging window (default: 200)")
    parser.add_argument("--blowup-factor", type=float, default=50.0,
                        help="Divergence guard: stop when |force| exceeds this multiple "
                             "of its rolling median (default: 50; 0 disables)")
    args = parser.parse_args()

    try:
        monitor(
            config_path=args.config,
            interval=args.interval,
            min_iters=args.min_iters,
            threshold=args.threshold,
            window=args.window,
            blowup_factor=args.blowup_factor,
        )
    except KeyboardInterrupt:
        print("\n  Monitor stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
