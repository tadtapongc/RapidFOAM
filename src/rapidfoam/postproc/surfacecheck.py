"""Pre-mesh surface-integrity reading and verification.

Parses the ``surfaceCheck`` log (``log.surfaceCheck``) so a surface can be
screened for the defects that make snappyHexMesh fail or leak: open
(non-manifold) edges, self-intersections, illegal/degenerate triangles,
multiple unconnected parts and inconsistent normal orientation. Nothing here
runs OpenFOAM; it reads the log the run scripts already write.

A half model cut on a symmetry plane is *legitimately* open along the cut, so
open-surface findings are suppressed when the case has a symmetry plane
(mirroring ``checkmesh``'s open-patch suppression).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rapidfoam.postproc.checkmesh import _config_has_symmetry, _load_config_dict

# --- surfaceCheck metric patterns (tolerant of spacing) ----------------------
# Statistics: the surfaceCheck/triSurface::writeStats block.
_TRIANGLES_RE = re.compile(
    r"^\s*(?:Triangles|Number of triangles)\s*[:=]\s*(\d+)", re.MULTILINE
)
_VERTICES_RE = re.compile(
    r"^\s*(?:Vertices|Number of vertices)\s*[:=]\s*(\d+)", re.MULTILINE
)
_BBOX_RE = re.compile(r"Bounding Box\s*:\s*\(([^)]+)\)\s*\(([^)]+)\)")

# "Region<TAB>Size" table.
_REGION_HEADER_RE = re.compile(r"^\s*Region\s+Size\s*$")
_REGION_RULE_RE = re.compile(r"^\s*-+\s+-+\s*$")
_REGION_ROW_RE = re.compile(r"^\s*(\S+)\s+(\d+)\s*$")

# Triangle validity.
_ILLEGAL_RE = re.compile(r"Surface has (\d+) illegal triangles")
_NO_ILLEGAL_RE = re.compile(r"Surface has no illegal triangles")

# Triangle quality / edge length min-max lines.
_QUALITY_MIN_RE = re.compile(r"^\s*min\s+([-+0-9.eE]+)\s+for triangle", re.MULTILINE)
_QUALITY_MAX_RE = re.compile(r"^\s*max\s+([-+0-9.eE]+)\s+for triangle", re.MULTILINE)
_EDGE_MIN_RE = re.compile(r"^\s*min\s+([-+0-9.eE]+)\s+for edge", re.MULTILINE)
_EDGE_MAX_RE = re.compile(r"^\s*max\s+([-+0-9.eE]+)\s+for edge", re.MULTILINE)

# Nearby points / small edges.
_NEARBY_RE = re.compile(r"Found (\d+) nearby points")
_CLOSE_UNCONNECTED_RE = re.compile(r"close unconnected points")

# Manifold / closure.
_CLOSED_RE = re.compile(r"Surface is closed\. All edges connected to two faces")
_NOT_CLOSED_RE = re.compile(r"Surface is not closed since not all edges connected")
_SINGLE_EDGES_RE = re.compile(r"connected to one face\s*:\s*(\d+)")
_MULTI_EDGES_RE = re.compile(r"connected to >?2 faces\s*:\s*(\d+)")

# Connectivity / normals.
_UNCONNECTED_RE = re.compile(r"Number of unconnected parts\s*:\s*(\d+)")
_NORMAL_ZONES_RE = re.compile(
    r"Number of zones \(connected area with consistent normal\)\s*:\s*(\d+)"
)

# Self-intersection (only present with -checkSelfIntersection).
_SELF_CHECKED_RE = re.compile(r"Checking self-intersection\.")
_SELF_NONE_RE = re.compile(r"Surface is not self-intersecting")
_SELF_SOME_RE = re.compile(r"Surface is self-intersecting at (\d+) locations")


def _num(token: str) -> float | None:
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _vector(text: str) -> list[float] | None:
    try:
        values = [float(part) for part in text.split()]
    except (TypeError, ValueError):
        return None
    return values if len(values) == 3 else None


def _parse_regions(text: str) -> dict[str, int]:
    """Parse the ``Region / Size`` table (one row per solid/region)."""
    regions: dict[str, int] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _REGION_HEADER_RE.match(line):
            continue
        cursor = index + 1
        while cursor < len(lines) and _REGION_RULE_RE.match(lines[cursor]):
            cursor += 1
        while cursor < len(lines):
            row = _REGION_ROW_RE.match(lines[cursor])
            if not row:
                break
            regions[row.group(1)] = int(row.group(2))
            cursor += 1
        break
    return regions


def parse_surfacecheck(text: str) -> dict[str, Any]:
    """Parse a ``surfaceCheck`` log into a metrics dictionary.

    Returns an empty dict when the text carries no recognisable surfaceCheck
    output, so it is safe to call on an arbitrary file.
    """
    stats: dict[str, Any] = {}
    if not text:
        return stats

    def _int(pattern: re.Pattern[str], key: str) -> None:
        match = pattern.search(text)
        if match:
            stats[key] = int(match.group(1))

    def _float(pattern: re.Pattern[str], key: str) -> None:
        match = pattern.search(text)
        if match:
            value = _num(match.group(1))
            if value is not None:
                stats[key] = value

    _int(_TRIANGLES_RE, "triangles")
    _int(_VERTICES_RE, "vertices")

    match = _BBOX_RE.search(text)
    if match:
        low = _vector(match.group(1))
        high = _vector(match.group(2))
        if low is not None and high is not None:
            stats["bounding_box"] = {"min": low, "max": high}

    regions = _parse_regions(text)
    if regions:
        stats["regions"] = regions

    match = _ILLEGAL_RE.search(text)
    if match:
        stats["illegal_triangles"] = int(match.group(1))
    elif _NO_ILLEGAL_RE.search(text):
        stats["illegal_triangles"] = 0

    _float(_QUALITY_MIN_RE, "min_quality")
    _float(_QUALITY_MAX_RE, "max_quality")
    _float(_EDGE_MIN_RE, "min_edge")
    _float(_EDGE_MAX_RE, "max_edge")

    _int(_NEARBY_RE, "nearby_points")
    close_unconnected = len(_CLOSE_UNCONNECTED_RE.findall(text))
    if close_unconnected:
        stats["nearby_unconnected"] = close_unconnected

    if _NOT_CLOSED_RE.search(text):
        stats["closed"] = False
        _int(_SINGLE_EDGES_RE, "single_edges")
        _int(_MULTI_EDGES_RE, "multi_edges")
    elif _CLOSED_RE.search(text):
        stats["closed"] = True

    _int(_UNCONNECTED_RE, "unconnected_parts")

    match = _NORMAL_ZONES_RE.search(text)
    if match:
        zones = int(match.group(1))
        stats["normal_zones"] = zones
        stats["consistent_normals"] = zones <= 1

    if _SELF_CHECKED_RE.search(text):
        stats["self_intersection_checked"] = True
        match = _SELF_SOME_RE.search(text)
        if match:
            stats["self_intersecting"] = True
            stats["self_intersection_locations"] = int(match.group(1))
        elif _SELF_NONE_RE.search(text):
            stats["self_intersecting"] = False
            stats["self_intersection_locations"] = 0

    return stats


# ============================================================
# LOG DISCOVERY
# ============================================================

def find_surfacecheck_logs(base_dir: str | Path | None = None) -> list[Path]:
    """Find ``log.surfaceCheck*`` files (newest/log order)."""
    base = Path(base_dir) if base_dir else Path(".")
    logs = [p for p in sorted(base.glob("log.surfaceCheck*")) if p.is_file()]
    if logs:
        return logs
    # surfaceCheck runs serially at the case root, but be tolerant of a
    # decomposed case directory in case a caller points at one.
    for proc_dir in sorted(base.glob("processor*")):
        logs.extend(p for p in sorted(proc_dir.glob("log.surfaceCheck*")) if p.is_file())
    return logs


def read_surfacecheck(files: Iterable[Path]) -> dict[str, Any]:
    """Merge surfaceCheck logs, later files overriding earlier values."""
    merged: dict[str, Any] = {}
    for path in files:
        try:
            stats = parse_surfacecheck(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            continue
        merged.update(stats)
    return merged


# ============================================================
# VERIFICATION
# ============================================================

def check_surface(
    stats: dict[str, Any] | None,
    *,
    has_symmetry: bool = False,
    allow_open: bool = False,
    max_illegal_triangles: int = 0,
    max_unconnected_parts: int = 1,
) -> dict[str, Any]:
    """Judge a surface against the defects that break or leak a snappy mesh.

    ``has_symmetry`` marks a half model cut on a symmetry plane: it is open
    along the cut by construction, so a non-closed surface is expected and is
    not an error. ``allow_open`` applies the same exemption for a deliberate
    open surface without a symmetry plane (e.g. a full model rendered as a
    shell).

    Returns a report with ``ok`` (no hard failures), a ``verdict`` of
    good/concern/bad, the collected ``issues`` (hard) and ``warnings`` (soft).
    """
    stats = stats or {}
    if not stats:
        return {
            "available": False,
            "ok": False,
            "verdict": "unknown",
            "verdict_label": "Unknown",
            "stats": {},
            "closed": None,
            "open_allowed": False,
            "has_symmetry": bool(has_symmetry),
            "issues": [],
            "warnings": [],
            "note": "no surfaceCheck log found",
        }

    issues: list[str] = []
    warnings: list[str] = []
    open_allowed = bool(has_symmetry or allow_open)
    closed = stats.get("closed")

    illegal = stats.get("illegal_triangles")
    if isinstance(illegal, int) and not isinstance(illegal, bool) and illegal > max_illegal_triangles:
        issues.append(f"{illegal} illegal (degenerate/duplicate) triangle(s)")

    if stats.get("self_intersecting") is True:
        count = stats.get("self_intersection_locations")
        suffix = f" at {count} location(s)" if isinstance(count, int) else ""
        issues.append(f"surface is self-intersecting{suffix}")

    if closed is False:
        single = stats.get("single_edges")
        multi = stats.get("multi_edges")
        detail = []
        if isinstance(single, int):
            detail.append(f"{single} edge(s) on one face")
        if isinstance(multi, int) and multi:
            detail.append(f"{multi} edge(s) on >2 faces")
        detail_text = f" ({', '.join(detail)})" if detail else ""
        if not open_allowed:
            issues.append(f"surface is not closed — open edges{detail_text}")

    parts = stats.get("unconnected_parts")
    if isinstance(parts, int) and not isinstance(parts, bool) and parts > max_unconnected_parts:
        warnings.append(f"{parts} unconnected surface part(s)")

    if stats.get("consistent_normals") is False:
        zones = stats.get("normal_zones")
        warnings.append(
            f"inconsistent normal orientation ({zones} zone(s))"
            if isinstance(zones, int) else "inconsistent normal orientation"
        )

    nearby = stats.get("nearby_unconnected")
    if isinstance(nearby, int) and nearby > 0:
        warnings.append(f"{nearby} pair(s) of near-coincident unconnected points")

    if issues:
        verdict = "bad"
    elif warnings:
        verdict = "concern"
    else:
        verdict = "good"
    verdict_label = {"good": "Good", "concern": "Concern", "bad": "Bad"}[verdict]
    ok = not issues

    if ok and not warnings:
        note = "surface integrity OK" + (
            " (open along symmetry cut)" if has_symmetry and closed is False else ""
        )
    elif ok:
        note = f"surface usable — {len(warnings)} concern(s): " + "; ".join(warnings)
    else:
        note = f"{len(issues)} surface defect(s): " + "; ".join(issues)
    if verdict != "good":
        note = f"[{verdict_label}] " + note

    return {
        "available": True,
        "ok": ok,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "stats": stats,
        "closed": closed,
        "open_allowed": open_allowed,
        "has_symmetry": bool(has_symmetry),
        "issues": issues,
        "warnings": warnings,
        "note": note,
    }


def surface_check_policy_from_dict(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Extract surface_check thresholds from a case config, if present."""
    if not isinstance(cfg, dict):
        return {}
    section = cfg.get("surface_check", {})
    if not isinstance(section, dict):
        return {}
    policy: dict[str, Any] = {}
    if "allow_open" in section:
        policy["allow_open"] = bool(section["allow_open"])
    for key in ("max_illegal_triangles", "max_unconnected_parts"):
        value = section.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            policy[key] = value
    return policy


def surface_check_report(
    case_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """End-to-end surface-integrity report for a generated/run case directory."""
    base = Path(case_dir) if case_dir else Path(".")
    stats = read_surfacecheck(find_surfacecheck_logs(base))
    config_dict = _load_config_dict(config_path, base)
    return check_surface(
        stats,
        has_symmetry=_config_has_symmetry(config_dict),
        **surface_check_policy_from_dict(config_dict),
    )
