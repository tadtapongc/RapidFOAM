"""Residual reading utilities."""

from __future__ import annotations

import math
from pathlib import Path

from rapidfoam.postproc.forces import _dir_time


def find_residual_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find solverInfo.dat files."""
    base = Path(base_dir) if base_dir else Path(".")
    all_files: list[Path] = []

    res_dir = base / "postProcessing" / "residuals"
    if res_dir.exists():
        for d in sorted(res_dir.glob("*/"), key=_dir_time):
            f = d / "solverInfo.dat"
            if f.exists():
                all_files.append(f)

    if not all_files:
        for proc_dir in sorted(base.glob("processor*")):
            pr = proc_dir / "postProcessing" / "residuals"
            if pr.exists():
                for d in sorted(pr.glob("*/"), key=_dir_time):
                    f = d / "solverInfo.dat"
                    if f.exists():
                        all_files.append(f)
                if all_files:
                    break

    return all_files


def read_residuals(files: list[Path]) -> tuple[dict[str, list[float | None]], list[str]]:
    """Read solverInfo.dat files.

    Returns:
        (data_dict, headers)
    """
    headers: list[str] = []
    rows: dict[float, dict[str, float | None]] = {}

    for path in files:
        file_headers: list[str] = []
        segment_started = False
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        if "Time" in line:
                            file_headers = line.strip("# \n").split()
                            for h in file_headers:
                                if h not in headers:
                                    headers.append(h)
                        continue
                    if not line or not file_headers:
                        continue
                    parts = line.split()
                    try:
                        t = float(parts[0])
                    except (ValueError, IndexError):
                        continue
                    if not math.isfinite(t) or len(parts) < len(file_headers):
                        continue
                    if not segment_started:
                        rows = {key: row for key, row in rows.items() if key < round(t, 8)}
                        segment_started = True
                    row = {}
                    for i, h in enumerate(file_headers):
                        if i < len(parts):
                            try:
                                val: float | None = float(parts[i])
                                if not math.isfinite(val):
                                    val = None
                            except ValueError:
                                val = None
                        else:
                            val = None
                        row[h] = val
                    rows[round(t, 8)] = row
        except (OSError, ValueError):
            pass

    data = {h: [rows[t].get(h) for t in sorted(rows)] for h in headers}
    return data, headers
