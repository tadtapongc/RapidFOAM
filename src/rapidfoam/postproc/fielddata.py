"""Spatial field diagnostics from small function-object reductions.

Parses the compact text files some function objects write, so the telemetry can
report *where* an extremum is (e.g. the maximum y+ on the body) without ever
streaming a full field:

  * ``fieldMinMax``       -> ``postProcessing/fieldMinMax/<time>/fieldMinMax.dat``
  * ``surfaceFieldValue`` -> ``postProcessing/<object>/<time>/surfaceFieldValue.dat``

Nothing here runs OpenFOAM; it reads the files the run scripts already write.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable, Optional

# surfaceFieldValue object names emitted by casegen.solver: wallPressure_<op>_<patch>
_SURFACE_VALUE_NAME_RE = re.compile(r"^wallPressure_(min|max|average)_(.+)$")


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


def find_surface_field_value_files(base_dir: str | Path | None = None) -> list[Path]:
    """Find ``surfaceFieldValue*.dat`` files (one directory per function object)."""
    base = Path(base_dir) if base_dir else Path(".")
    return sorted(
        p for p in base.glob("postProcessing/*/*/surfaceFieldValue*.dat") if p.is_file()
    )


def surface_field_value_name(path: Path) -> str:
    """Return the function-object (directory) name for a surfaceFieldValue file."""
    # <base>/postProcessing/<object>/<time>/surfaceFieldValue.dat
    return path.parent.parent.name


def parse_surface_field_value(
    text: str, object_name: str
) -> Optional[dict[str, Any]]:
    """Parse one ``surfaceFieldValue.dat`` against its object name.

    The object name encodes operation and patch (``wallPressure_<op>_<patch>``).
    The value is the last numeric token of the last data row (the reduction).
    """
    match = _SURFACE_VALUE_NAME_RE.match(object_name)
    if not match:
        return None
    operation, patch = match.group(1), match.group(2)
    value: Optional[float] = None
    time: Optional[float] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        nums = [n for n in (_num(t) for t in line.replace("(", " ").replace(")", " ").split()) if n is not None]
        if not nums:
            continue
        value = nums[-1]
        if len(nums) >= 2:
            time = nums[0]
    if value is None:
        return None
    return {"operation": operation, "patch": patch, "value": value, "time": time}


def read_surface_field_value(files: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Merge ``surfaceFieldValue`` files into ``{operation_patch: {...}}``."""
    result: dict[str, dict[str, Any]] = {}
    for path in files:
        parsed = parse_surface_field_value(
            path.read_text(encoding="utf-8", errors="replace"),
            surface_field_value_name(path),
        )
        if parsed is None:
            continue
        key = f"{parsed['operation']}_{parsed['patch']}"
        prev = result.get(key)
        if prev is None or (parsed.get("time") or 0.0) >= (prev.get("time") or 0.0):
            result[key] = parsed
    return result


__all__ = [
    "find_field_min_max_files",
    "parse_field_min_max",
    "read_field_min_max",
    "find_surface_field_value_files",
    "surface_field_value_name",
    "parse_surface_field_value",
    "read_surface_field_value",
]
