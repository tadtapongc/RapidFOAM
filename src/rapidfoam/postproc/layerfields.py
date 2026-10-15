"""snappyHexMesh layer fields -> first-layer coverage.

snappyHexMesh's ``writeFlags layerFields`` writes ``nSurfaceLayers`` (the realised
layer count on every boundary face) next to the mesh. The generated pipeline
preserves those files into ``meshDiagnostics/`` before the processor directories
are removed, and this module turns them into the metric that actually predicts
the near-wall y+ tail: the fraction of wetted faces that carry a prism layer at
all (the per-patch *average* in snappy's log cannot show that).

Files are OpenFOAM ``volScalarField`` dictionaries in the case's write format
(usually binary). The binary reader tries the two data offsets OpenFOAM can emit
and validates the decoded values against the declared count and a plausible
range, so an unexpected layout degrades to "unavailable" instead of returning
garbage.
"""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path
from typing import Any, Iterable

# Boundary patches that are not wetted body surfaces (same set the checkMesh
# verdict treats as domain boundaries).
_DOMAIN_PATCHES = {
    "symmetry", "inlet", "outlet", "ground", "farfield", "front", "back", ".*",
}
_DOMAIN_PREFIXES = ("inlet", "outlet", "symmetry", "ground", "farfield")

# nSurfaceLayers is a small non-negative count; the range also disambiguates the
# binary data offset (a one-byte shift produces implausible doubles).
_LAYER_BOUNDS = (-0.5, 1000.0)

_PATCH_HEADER_RE = re.compile(r'\s*"?([A-Za-z_][A-Za-z0-9_.()\-]*)"?\s*\{')
_ENTRY_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s+")
_UNIFORM_RE = re.compile(r"([-+0-9.eE]+)\s*;")
_NONUNIFORM_RE = re.compile(r"List<scalar>\s*\n\s*(\d+)\s*\n\s*\(")


def find_layer_field_files(base_dir: str | Path | None = None) -> list[Path]:
    """Locate preserved ``nSurfaceLayers`` files (meshDiagnostics first)."""
    base = Path(base_dir) if base_dir else Path(".")
    files = sorted((base / "meshDiagnostics").glob("*/nSurfaceLayers"))
    if files:
        return files
    for candidate in (base / "constant" / "nSurfaceLayers", base / "0" / "nSurfaceLayers"):
        if candidate.is_file():
            files.append(candidate)
    return files


def _valid(values: list[float], bounds: tuple[float, float]) -> bool:
    lo, hi = bounds
    return all(math.isfinite(v) and lo <= v <= hi for v in values)


def _read_binary_scalars(
    text: str, start: int, count: int, bounds: tuple[float, float]
) -> tuple[list[float] | None, int]:
    """Decode ``count`` float64 values at the first layout that validates.

    OpenFOAM emits either ``(<data>)`` or ``(\\n<data>)`` for a binary list, so
    try both offsets. A candidate must decode to finite, plausible values *and*
    be followed by the closing ``)`` — a one-byte shift of mostly-zero data can
    look plausible but never lands on the closing token. Returns
    ``(values, end_index)`` or ``(None, start)``.
    """
    candidates = [start]
    if text[start:start + 1] == "\n":
        candidates.append(start + 1)
    elif text[start:start + 2] == "\r\n":
        candidates.append(start + 2)
    for offset in candidates:
        blob = text[offset:offset + 8 * count]
        if len(blob) < 8 * count:
            continue
        after = text[offset + 8 * count:offset + 8 * count + 32].lstrip(" \t\r\n")
        if not after.startswith(")"):
            continue
        try:
            values = list(struct.unpack(f"<{count}d", blob.encode("latin-1")))
        except struct.error:
            continue
        if _valid(values, bounds):
            return values, offset + 8 * count
    return None, start


def _read_ascii_scalars(
    text: str, start: int, count: int, bounds: tuple[float, float]
) -> tuple[list[float] | None, int]:
    values: list[float] = []
    cursor = start
    while len(values) < count:
        match = re.compile(r"\s*([-+0-9.eE]+)").match(text, cursor)
        if not match:
            return None, start
        try:
            values.append(float(match.group(1)))
        except ValueError:
            return None, start
        cursor = match.end()
    if not _valid(values, bounds):
        return None, start
    return values, cursor


def _parse_value(
    text: str, cursor: int, binary: bool, bounds: tuple[float, float]
) -> tuple[dict[str, Any] | None, int]:
    """Parse a ``value`` entry (uniform or nonuniform) starting at ``cursor``."""
    kind = re.compile(r"(uniform|nonuniform)\s+").match(text, cursor)
    if not kind:
        return None, cursor
    cursor = kind.end()
    if kind.group(1) == "uniform":
        match = _UNIFORM_RE.match(text, cursor)
        if not match:
            return None, cursor
        return {"uniform": float(match.group(1))}, match.end()

    header = _NONUNIFORM_RE.match(text, cursor)
    if not header:
        return None, cursor
    count = int(header.group(1))
    start = header.end()
    values, end = (_read_binary_scalars(text, start, count, bounds) if binary
                   else _read_ascii_scalars(text, start, count, bounds))
    if values is None:
        return None, cursor
    close = text.find(")", end)
    cursor = close + 1 if close != -1 else end
    semi = re.compile(r"\s*;").match(text, cursor)
    if semi:
        cursor = semi.end()
    return {"values": values}, cursor


def _skip_entry(text: str, cursor: int) -> int:
    """Skip a non-``value`` entry (a ``;`` statement or a nested block)."""
    semi = text.find(";", cursor)
    brace = text.find("{", cursor)
    if brace != -1 and (semi == -1 or brace < semi):
        depth = 1
        pos = brace + 1
        while depth and pos < len(text):
            char = text[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            pos += 1
        return pos
    return semi + 1 if semi != -1 else len(text)


def parse_layer_field(
    data: bytes, bounds: tuple[float, float] = _LAYER_BOUNDS
) -> dict[str, dict[str, Any]]:
    """Parse a volScalarField into ``{patch: {"values": [...]}}``.

    ``uniform`` patch values are reported as ``{"uniform": value}`` (their face
    count is not in the field file). Returns ``{}`` when the file cannot be
    parsed confidently.
    """
    text = data.decode("latin-1")  # 1:1 byte mapping keeps offsets exact
    binary = bool(re.search(r"^\s*format\s+binary\s*;", text[:2000], re.M))

    internal = re.compile(r"internalField\s+").search(text)
    if not internal:
        return {}
    _, cursor = _parse_value(text, internal.end(), binary, bounds)

    boundary = text.find("boundaryField", cursor)
    if boundary == -1:
        return {}
    brace = text.find("{", boundary)
    if brace == -1:
        return {}
    cursor = brace + 1

    patches: dict[str, dict[str, Any]] = {}
    while True:
        if text[cursor:].lstrip().startswith("}"):
            break
        header = _PATCH_HEADER_RE.match(text, cursor)
        if header is None:
            break
        name = header.group(1)
        cursor = header.end()
        entry: dict[str, Any] = {}
        while True:
            if text[cursor:].lstrip().startswith("}"):
                cursor = text.find("}", cursor) + 1
                break
            key = _ENTRY_RE.match(text, cursor)
            if key is None:
                break
            if key.group(1) == "value":
                parsed, next_cursor = _parse_value(text, key.end(), binary, bounds)
                if parsed is None:
                    break
                entry.update(parsed)
                cursor = next_cursor
            else:
                cursor = _skip_entry(text, key.end())
        if entry:
            patches[name] = entry
    return patches


def read_layer_fields(sources: Iterable[Path | bytes]) -> dict[str, dict[str, Any]]:
    """Merge layer fields from every processor into one per-patch view.

    ``sources`` may be file paths (a downloaded case) or raw bytes (a remote
    bundle entry), so the same aggregation serves both.
    """
    merged: dict[str, dict[str, Any]] = {}
    for source in sources:
        try:
            data = source.read_bytes() if isinstance(source, Path) else source
        except OSError:
            continue
        try:
            parsed = parse_layer_field(data)
        except (ValueError, struct.error):
            continue
        for name, entry in parsed.items():
            slot = merged.setdefault(name, {"values": [], "uniform": None})
            if "values" in entry:
                slot["values"].extend(entry["values"])
            elif "uniform" in entry:
                slot["uniform"] = entry["uniform"]
    return merged


def _is_domain_patch(name: str) -> bool:
    key = name.strip('"').lower()
    return key in _DOMAIN_PATCHES or key.startswith(_DOMAIN_PREFIXES)


def first_layer_coverage(sources: Iterable[Path | bytes]) -> dict[str, Any]:
    """Per-patch and wetted-body first-layer coverage from ``nSurfaceLayers``.

    ``sources`` are ``Path`` objects or raw field bytes. Returns
    ``available: False`` when no preserved field is found (cases generated
    before the pipeline preserved it, or a parser that declined).
    """
    merged = read_layer_fields(list(sources))
    if not merged:
        return {"available": False, "note": "no snappy layer field preserved"}

    patches: dict[str, dict[str, Any]] = {}
    body_faces = 0
    body_layered = 0
    body_histogram: dict[int, int] = {}
    for name, entry in merged.items():
        values = entry.get("values") or []
        stats: dict[str, Any] = {}
        if entry.get("uniform") is not None and not values:
            stats["uniform"] = entry["uniform"]
        if values:
            layered = sum(1 for v in values if v >= 0.5)
            histogram: dict[int, int] = {}
            for value in values:
                key = int(round(value))
                histogram[key] = histogram.get(key, 0) + 1
            stats.update({
                "faces": len(values),
                "layered": layered,
                "coverage": round(layered / len(values), 4),
                "mean": round(sum(values) / len(values), 3),
                "histogram": dict(sorted(histogram.items())),
            })
            if not _is_domain_patch(name):
                body_faces += len(values)
                body_layered += layered
                for key, count in histogram.items():
                    body_histogram[key] = body_histogram.get(key, 0) + count
        patches[name] = stats

    if body_faces == 0:
        return {"available": False, "patches": patches,
                "note": "layer field found but no wetted patch values"}

    body = {
        "faces": body_faces,
        "layered": body_layered,
        "coverage": round(body_layered / body_faces, 4),
        "histogram": dict(sorted(body_histogram.items())),
    }
    missing = body_faces - body_layered
    note = (f"first-layer coverage {body['coverage'] * 100:.0f}% of {body_faces:,} wetted faces "
            f"({missing * 100.0 / body_faces:.0f}% carry no prism layer)")
    return {"available": True, "patches": patches, "body": body, "note": note}


__all__ = [
    "find_layer_field_files",
    "parse_layer_field",
    "read_layer_fields",
    "first_layer_coverage",
]
