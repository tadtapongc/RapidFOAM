"""Convergence monitor — CLI wrapper over the case-local runtime kernel.

The kernel (force parsing, convergence check, divergence guard and the
``stopAt writeNow`` trigger) lives in
:mod:`rapidfoam.runtime.convergence_monitor` — the file copied into generated
cases. This module only resolves the case's axis config and exposes the
``rapidfoam-monitor`` console entry point, so the local and cluster monitors
cannot drift apart.

Usage:
    python -m rapidfoam.postproc.convergence_monitor [--interval 10] [--config case_config.json]

The solver must have `runTimeModifiable true` and `writeAtEnd true` in controlDict.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rapidfoam.core import caseconfig
from rapidfoam.runtime.convergence_monitor import check_convergence as check_convergence
from rapidfoam.runtime.convergence_monitor import detect_blowup as detect_blowup
from rapidfoam.runtime.convergence_monitor import find_force_files as find_force_files
from rapidfoam.runtime.convergence_monitor import monitor as _kernel_monitor
from rapidfoam.runtime.convergence_monitor import read_forces as read_forces

__all__ = ["check_convergence", "detect_blowup", "find_force_files", "main", "monitor", "read_forces"]


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
    base = Path(case_dir) if case_dir else Path.cwd()
    axes = caseconfig.axis_config(
        caseconfig.read_case_config(config_path=config_path, case_dir=base)
    )
    return _kernel_monitor(
        case_dir=base,
        interval=interval,
        min_iters=min_iters,
        threshold=threshold,
        window=window,
        blowup_factor=blowup_factor,
        drag_idx=axes.drag_index,
        drag_sign=axes.drag_sign,
        df_idx=axes.downforce_index,
        df_sign=axes.downforce_sign,
        drag_axis=axes.drag_axis,
        df_axis=axes.downforce_axis,
    )


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
