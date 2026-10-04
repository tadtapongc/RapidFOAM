"""Spatial field diagnostics from small function-object reductions.

Parses the compact text file the ``fieldMinMax`` function object writes so the
telemetry can report *where* an extremum is (e.g. the maximum y+ on the body)
without ever streaming a full field:

  * ``fieldMinMax`` -> ``postProcessing/fieldMinMax/<time>/fieldMinMax.dat``

Nothing here runs OpenFOAM; it reads the files the run scripts already write.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Optional


def _num(token: str) -> Optional[float]:
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def find_field_min_max_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find ``fieldMinMax.dat`` files (one per write time)."""
    base = Path(base_dir) if base_dir else Path(".")
    return sorted(
        p for p in base.glob("postProcessing/*/*/fieldMinMax.dat") if p.is_file()
    )


def parse_field_min_max(text: str) -> dict[str, dict[str, Any]]:
    """Parse a ``fieldMinMax.dat`` into ``{field: {time, min, max, location}}``.

    OpenFOAM v2606 writes one row per field per write time with the columns
    ``Time field min location(min) [processor] max location(max) [processor]``
    (the ``processor`` column only appears in parallel runs). Later rows override
    earlier ones, so the result is the latest write time; ``location`` is the
    location of the **maximum**.
    """
    result: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.replace("(", " ").replace(")", " ").split()
        if len(tokens) < 10:
            continue
        time = _num(tokens[0])
        field = tokens[1]
        lo = _num(tokens[2])

        idx = 3
        loc_min: list[float] = []
        while idx < len(tokens) and len(loc_min) < 3:
            value = _num(tokens[idx])
            if value is None:
                break
            loc_min.append(value)
            idx += 1
        # Optional processor column before the max (present when run in parallel).
        if len(tokens) - idx >= 6:
            idx += 1
        hi = _num(tokens[idx]) if idx < len(tokens) else None
        idx += 1
        loc_max: list[float] = []
        while idx < len(tokens) and len(loc_max) < 3:
            value = _num(tokens[idx])
            if value is None:
                break
            loc_max.append(value)
            idx += 1

        if time is None or lo is None or hi is None:
            continue
        result[field] = {
            "time": time,
            "min": lo,
            "max": hi,
            "location": loc_max if len(loc_max) == 3 else None,
            "location_min": loc_min if len(loc_min) == 3 else None,
        }
    return result


def read_field_min_max(files: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Merge ``fieldMinMax.dat`` files, later files overriding earlier ones."""
    merged: dict[str, dict[str, Any]] = {}
    for path in files:
        try:
            merged.update(parse_field_min_max(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return merged


__all__ = [
    "find_field_min_max_files",
    "parse_field_min_max",
    "read_field_min_max",
]
