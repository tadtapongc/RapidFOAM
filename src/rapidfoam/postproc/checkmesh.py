"""Mesh-quality reading and verification.

Parses the ``checkMesh`` log (``log.checkMesh``) and the per-patch layer table
from the ``snappyHexMesh`` log (``log.snappyHexMesh``) so a generated mesh can
be *measured*, not just produced. Surfaces the metrics that actually limit
solution quality: non-orthogonality, skewness, aspect ratio, concave cells,
illegal faces and boundary-layer dropout.

Nothing here runs OpenFOAM; it reads the logs the run scripts already write.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# --- checkMesh metric patterns (tolerant of spacing and exponent signs) ---
_CELLS_RE = re.compile(r"^\s*cells:\s+(\d+)", re.MULTILINE)
_POINTS_RE = re.compile(r"^\s*points:\s+(\d+)", re.MULTILINE)
_FACES_RE = re.compile(r"^\s*faces:\s+(\d+)", re.MULTILINE)
_ASPECT_RE = re.compile(r"Max aspect ratio = ([-+0-9.eE]+)")
_NONORTHO_RE = re.compile(
    r"Mesh non-orthogonality Max:\s*([-+0-9.eE]+)\s+average:\s*([-+0-9.eE]+)"
)
_SKEW_RE = re.compile(r"Max skewness = ([-+0-9.eE]+)")
_VOLUME_RE = re.compile(
    r"Min volume = ([-+0-9.eE]+)\.\s*Max volume = ([-+0-9.eE]+)\."
    r"\s*Total volume = ([-+0-9.eE]+)"
)
_CONCAVE_FACES_RE = re.compile(
    r"There are (\d+) faces with concave angles.*?Max concave angle = ([-+0-9.eE]+)"
)
_CONCAVE_CELLS_RE = re.compile(
    r"Concave cells \(using face planes\) found, number of cells:\s*(\d+)"
)
_WARPED_RE = re.compile(
    r"There are (\d+) faces with ratio between projected and actual area < 0\.8"
)
_FLATNESS_RE = re.compile(
    r"Face flatness \(1 = flat, 0 = butterfly\) : min = ([-+0-9.eE]+)\s+average = ([-+0-9.eE]+)"
)
_DETERMINANT_RE = re.compile(
    r"Cell determinant \(wellposedness\) : minimum:\s*([-+0-9.eE]+)\s+average:\s*([-+0-9.eE]+)"
)
_WEIGHT_RE = re.compile(
    r"Face interpolation weight : minimum:\s*([-+0-9.eE]+)\s+average:\s*([-+0-9.eE]+)"
)
_VOLRATIO_RE = re.compile(
    r"Face volume ratio : minimum:\s*([-+0-9.eE]+)\s+average:\s*([-+0-9.eE]+)"
)
_FAILED_RE = re.compile(r"Failed (\d+) mesh checks")
_MESH_OK_RE = re.compile(r"\bMesh OK\b")

# Cell-type breakdown block ("Overall number of cells of each type:").
_CELL_TYPE_RE = re.compile(
    r"^\s*(hexahedra|prisms|wedges|pyramids|tet wedges|tetrahedra|polyhedra):\s+(\d+)",
    re.MULTILINE,
)

# Boundary patch table row (after the "Checking patch topology" header):
#   patch  faces  points  ok (closed|non-closed singly connected) (bbox) (bbox)
_PATCH_ROW_RE = re.compile(
    r'^\s*"?([^"\s]+)"?\s+(\d+)\s+(\d+)\s+'
    r"(ok|failed|notOk)\s*\(([^)]*)\)"
)

# CFD-practical quality bands, independent of snappy's give-up limits.
#
# snappyHexMesh's ``meshQualityControls`` are where it stops *trying*, not where
# a mesh becomes good: non-orthogonality 65 and skewness 4 are survivable, not
# desirable. Each entry is ``key -> (good, caution, kind, label)`` where the
# value is judged:
#   kind "max": good <= value <= caution ; fail > caution
#   kind "min": good >= value >= caution ; fail < caution
#   kind "zero": good == 0 ; caution when positive (counts)
# The checkMesh *configured* limits still trigger the hard ``ok`` failure; these
# bands drive the good/usable/marginal verdict shown to the user.
QUALITY_TIERS: dict[str, tuple[float, float, str, str]] = {
    "max_non_ortho": (60.0, 70.0, "max", "Max non-orthogonality"),
    "max_skewness": (2.0, 4.0, "max", "Max skewness"),
    "max_aspect_ratio": (50.0, 100.0, "max", "Max aspect ratio"),
    "min_determinant": (0.05, 0.001, "min", "Min cell determinant"),
    "min_interp_weight": (0.1, 0.01, "min", "Min face interpolation weight"),
    "min_volume_ratio": (0.05, 0.01, "min", "Min face volume ratio"),
    "concave_cells": (0.0, 0.0, "zero", "Concave cells"),
}

# Verdict ordering, worst last: index is severity.
_VERDICT_ORDER = ["good", "usable", "marginal", "bad"]
_VERDICT_LABEL = {
    "good": "Good",
    "usable": "Usable",
    "marginal": "Marginal",
    "bad": "Bad",
}


def resolve_bands(
    bands: dict[str, dict[str, float]] | None = None,
) -> dict[str, tuple[float, float, str, str]]:
    """Merge user-supplied good/caution bands over the built-in defaults.

    ``bands`` may override ``good``/``caution`` per metric key; the comparison
    kind and human label always come from the built-in table so a partial
    override cannot change a metric's meaning.
    """
    resolved = dict(QUALITY_TIERS)
    if not isinstance(bands, dict):
        return resolved
    for key, band in bands.items():
        tier = resolved.get(key)
        if tier is None or not isinstance(band, dict):
            continue
        good = band.get("good", tier[0])
        caution = band.get("caution", tier[1])
        if isinstance(good, (int, float)) and isinstance(caution, (int, float)) \
                and not isinstance(good, bool) and not isinstance(caution, bool) \
                and math.isfinite(good) and math.isfinite(caution):
            resolved[key] = (float(good), float(caution), tier[2], tier[3])
    return resolved


def classify_value(
    key: str,
    value: float,
    tiers: dict[str, tuple[float, float, str, str]] | None = None,
) -> str:
    """Return ``good``/``usable``/``marginal`` for a single metric value.

    Unknown keys are treated as ``good`` so adding a metric never downgrades a
    verdict by accident.
    """
    tier = (tiers or QUALITY_TIERS).get(key)
    if tier is None or not math.isfinite(value):
        return "good"
    good, caution, kind, _ = tier
    if kind == "max":
        if value <= good:
            return "good"
        return "usable" if value <= caution else "marginal"
    if kind == "min":
        if value >= good:
            return "good"
        return "usable" if value >= caution else "marginal"
    # zero: any positive count is a concern
    return "good" if value <= 0 else "marginal"


# snappy layer table rows come in two shapes:
#   mid-run:  patch  faces  layers  near-wall-thickness  overall-thickness
#   final:    patch  faces  target  mesh  overall-thickness  coverage-percent
# Capture the patch token plus the whitespace-separated numeric tail and let
# the token count disambiguate.
_LAYER_ROW_RE = re.compile(
    r'^\s*"?([^"\s]+)"?\s+([-+0-9.eE]+(?:\s+[-+0-9.eE]+)*)\s*$'
)


# ============================================================
# LOG DISCOVERY
# ============================================================

def _glob_logs(base: Path, name: str) -> list[Path]:
    logs = [p for p in sorted(base.glob(f"{name}*")) if p.is_file()]
    if logs:
        return logs
    # Parallel runs still write to the case root, but be tolerant of processor
    # directories in case a caller points at a decomposed case.
    for proc_dir in sorted(base.glob("processor*")):
        logs.extend(p for p in sorted(proc_dir.glob(f"{name}*")) if p.is_file())
    return logs


def find_checkmesh_logs(base_dir: str | Path | None = None) -> list[Path]:
    """Find ``log.checkMesh*`` files (newest/log order)."""
    base = Path(base_dir) if base_dir else Path(".")
    return _glob_logs(base, "log.checkMesh")


def find_snappy_logs(base_dir: str | Path | None = None) -> list[Path]:
    """Find ``log.snappyHexMesh*`` files (newest/log order)."""
    base = Path(base_dir) if base_dir else Path(".")
    return _glob_logs(base, "log.snappyHexMesh")


# ============================================================
# PARSERS
# ============================================================

def _num(token: str) -> float | None:
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_checkmesh(text: str) -> dict[str, Any]:
    """Parse a ``checkMesh`` log into a metrics dictionary.

    Returns an empty dict when the text carries no recognisable checkMesh
    output, so it is safe to call on the snappy log or an arbitrary file.
    """
    stats: dict[str, Any] = {}

    def _int(pattern: re.Pattern[str], key: str) -> None:
        match = pattern.search(text)
        if match:
            stats[key] = int(match.group(1))

    def _float(pattern: re.Pattern[str], key: str, group: int = 1) -> None:
        match = pattern.search(text)
        if match:
            value = _num(match.group(group))
            if value is not None:
                stats[key] = value

    _int(_POINTS_RE, "points")
    _int(_FACES_RE, "faces")
    _int(_CELLS_RE, "cells")
    _float(_ASPECT_RE, "max_aspect_ratio")

    match = _NONORTHO_RE.search(text)
    if match:
        stats["max_non_ortho"] = _num(match.group(1))
        stats["avg_non_ortho"] = _num(match.group(2))

    _float(_SKEW_RE, "max_skewness")

    match = _VOLUME_RE.search(text)
    if match:
        for group, key in (
            (1, "min_volume"), (2, "max_volume"), (3, "total_volume")
        ):
            value = _num(match.group(group))
            if value is not None:
                stats[key] = value

    match = _CONCAVE_FACES_RE.search(text)
    if match:
        stats["concave_faces"] = int(match.group(1))
        angle = _num(match.group(2))
        if angle is not None:
            stats["max_concave_angle"] = angle

    _int(_CONCAVE_CELLS_RE, "concave_cells")
    _int(_WARPED_RE, "warped_faces")

    match = _FLATNESS_RE.search(text)
    if match:
        stats["min_flatness"] = _num(match.group(1))
        stats["avg_flatness"] = _num(match.group(2))

    match = _DETERMINANT_RE.search(text)
    if match:
        stats["min_determinant"] = _num(match.group(1))
        stats["avg_determinant"] = _num(match.group(2))

    match = _WEIGHT_RE.search(text)
    if match:
        stats["min_interp_weight"] = _num(match.group(1))
        stats["avg_interp_weight"] = _num(match.group(2))

    match = _VOLRATIO_RE.search(text)
    if match:
        stats["min_volume_ratio"] = _num(match.group(1))
        stats["avg_volume_ratio"] = _num(match.group(2))

    cell_types = {name: int(count) for name, count in _CELL_TYPE_RE.findall(text)}
    if cell_types:
        stats["cell_types"] = cell_types

    patches = parse_boundary_patches(text)
    if patches:
        stats["patches"] = patches

    failed = _FAILED_RE.search(text)
    if failed:
        stats["failed_checks"] = int(failed.group(1))
        stats["ok"] = False
    elif _MESH_OK_RE.search(text):
        stats["failed_checks"] = 0
        stats["ok"] = True

    return stats


def parse_boundary_patches(text: str) -> dict[str, dict[str, Any]]:
    """Parse the "Checking patch topology" table.

    Returns ``{patch: {faces, points, closure, closed}}``. ``closure`` is the
    raw status phrase (e.g. ``closed singly connected``); ``closed`` is True
    only for a closed surface — an open body patch is a leaking geometry.
    """
    patches: dict[str, dict[str, Any]] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = lines[index].lower()
        if "checking patch topology" in header:
            cursor = index + 1
            while cursor < len(lines):
                stripped = lines[cursor].strip()
                if not stripped:
                    if patches:
                        break
                    cursor += 1
                    continue
                row = _PATCH_ROW_RE.match(lines[cursor])
                if not row:
                    if patches:
                        break
                    cursor += 1
                    continue
                patch = row.group(1)
                closure = row.group(5).strip()
                patches[patch] = {
                    "faces": int(row.group(2)),
                    "points": int(row.group(3)),
                    "closure": closure,
                    "closed": closure.lower().startswith("closed"),
                }
                cursor += 1
            if patches:
                break
        index += 1
    return patches


def _parse_layer_row(line: str) -> tuple[str, dict[str, float | int]] | None:
    """Parse one snappy layer-table data row, or return None for any other line."""
    match = _LAYER_ROW_RE.match(line)
    if not match:
        return None
    patch = match.group(1)
    if set(patch) <= set("- "):
        return None
    tokens = match.group(2).split()
    try:
        values = [float(token) for token in tokens]
    except ValueError:
        return None

    if len(values) == 4:
        faces, layers, near, overall = values
        return patch, {
            "faces": int(faces),
            "layers": int(layers),
            "near_wall_thickness": near,
            "overall_thickness": overall,
        }
    if len(values) == 5:
        faces, target, mesh, overall, percent = values
        entry: dict[str, float | int] = {
            "faces": int(faces),
            "layers": int(mesh),
            "target_layers": int(target),
            "overall_thickness": overall,
            "percent": percent,
        }
        if target > 0:
            entry["coverage"] = mesh / target
        return patch, entry
    return None


def parse_layer_coverage(text: str) -> dict[str, dict[str, float | int]]:
    """Parse snappyHexMesh's per-patch layer tables.

    Handles both the mid-run table (patch, faces, layers, near-wall and overall
    thickness) and the richer final table (patch, faces, target layers, mesh
    layers, overall thickness, coverage %). Later tables override earlier ones,
    so the returned values are the final mesh state.
    """
    result: dict[str, dict[str, float | int]] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = lines[index].strip().lower()
        if "patch" in header and "faces" in header and "layers" in header:
            cursor = index + 1
            # Skip the secondary header (e.g. "near-wall overall"/"target mesh")
            # and the dashes rule up to the first parseable data row.
            while cursor < len(lines) and _parse_layer_row(lines[cursor]) is None:
                cursor += 1
            while cursor < len(lines):
                row = _parse_layer_row(lines[cursor])
                if row is None:
                    break
                patch, entry = row
                result[patch] = entry
                cursor += 1
            index = cursor
        else:
            index += 1
    return result


def read_checkmesh(files: Iterable[Path]) -> dict[str, Any]:
    """Merge checkMesh logs, later files overriding earlier values."""
    merged: dict[str, Any] = {}
    for path in files:
        try:
            stats = parse_checkmesh(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        merged.update(stats)
    return merged


def read_layer_coverage(files: Iterable[Path]) -> dict[str, dict[str, float | int]]:
    """Merge layer tables from snappyHexMesh logs, later values winning."""
    merged: dict[str, dict[str, float | int]] = {}
    for path in files:
        try:
            coverage = parse_layer_coverage(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            continue
        merged.update(coverage)
    return merged


# ============================================================
# VERIFICATION
# ============================================================

def check_mesh_quality(
    stats: dict[str, Any] | None,
    layers: dict[str, dict[str, Any]] | None = None,
    target_layers: int | None = None,
    *,
    max_non_ortho: float = 65.0,
    max_skewness: float = 4.0,
    max_aspect_ratio: float = 100.0,
    bands: dict[str, dict[str, float]] | None = None,
    has_symmetry: bool = False,
) -> dict[str, Any]:
    """Compare parsed mesh metrics against quality limits.

    ``max_non_ortho``/``max_skewness`` default to snappyHexMesh's configured
    ``meshQualityControls``; ``max_aspect_ratio`` is a practical CFD guideline.
    A missing layer target only reports coverage, it does not flag dropout.

    ``bands`` optionally overrides the good/caution verdict thresholds per
    metric (see :func:`resolve_bands`); the built-in defaults are used when a
    metric is not overridden.

    ``has_symmetry`` marks a half model cut on a symmetry plane. There, body and
    boundary patches are legitimately non-closed along the cut, so open-patch
    reports are suppressed (only a full model can leak).
    """
    stats = stats or {}
    layers = layers or {}
    issues: list[str] = []
    tiers = resolve_bands(bands)

    if not stats and not layers:
        return {
            "available": False,
            "verdict": "unknown",
            "verdict_label": "Unknown",
            "stats": {},
            "metrics": [],
            "layers": {},
            "patches": {},
            "open_patches": [],
            "cell_types": {},
            "issues": [],
            "ok": False,
            "note": "no checkMesh or snappyHexMesh log found",
        }

    def _finite(value: Any) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and math.isfinite(value):
            return float(value)
        return None

    # Per-metric evaluation table: value, limit, and pass/fail. ``kind`` selects
    # the comparison direction (``max`` = upper bound, ``min`` = lower bound,
    # ``positive`` = must be > 0).
    metrics: list[dict[str, Any]] = []

    def _metric(label: str, key: str, limit: float, kind: str) -> float | None:
        value = _finite(stats.get(key))
        if value is None:
            return None
        if kind == "max":
            passed = value <= limit
            limit_text = f"<= {limit:g}"
        elif kind == "min":
            passed = value >= limit
            limit_text = f">= {limit:g}"
        else:  # positive
            passed = value > 0.0
            limit_text = "> 0"
        level = classify_value(key, value, tiers)
        entry: dict[str, Any] = {
            "key": key,
            "label": label,
            "value": value,
            "limit": limit,
            "limit_text": limit_text,
            "pass": passed,
            "level": level,
        }
        tier = tiers.get(key)
        if tier is not None:
            good, caution, _, _ = tier
            entry["good"] = good
            entry["caution"] = caution
        metrics.append(entry)
        if not passed:
            issues.append(f"{label} {value:g} violates {limit_text}")
        return value

    _metric("Max non-orthogonality", "max_non_ortho", max_non_ortho, "max")
    _metric("Max skewness", "max_skewness", max_skewness, "max")
    _metric("Max aspect ratio", "max_aspect_ratio", max_aspect_ratio, "max")
    _metric("Min cell volume", "min_volume", 0.0, "positive")
    _metric("Min cell determinant", "min_determinant", 0.001, "min")
    _metric("Min face interpolation weight", "min_interp_weight", 0.05, "min")
    _metric("Min face volume ratio", "min_volume_ratio", 0.01, "min")

    concave_cells = stats.get("concave_cells")
    if isinstance(concave_cells, int) and not isinstance(concave_cells, bool):
        metrics.append({
            "key": "concave_cells",
            "label": "Concave cells",
            "value": float(concave_cells),
            "limit": 0.0,
            "limit_text": "== 0",
            "pass": concave_cells == 0,
            "level": classify_value("concave_cells", float(concave_cells), tiers),
            "integer": True,
        })
        if concave_cells > 0:
            issues.append(f"{concave_cells} concave cells")

    failed = stats.get("failed_checks")
    if isinstance(failed, int) and not isinstance(failed, bool) and failed > 0:
        issues.append(f"{failed} failed mesh check(s)")

    # Open (non-closed) body patches are leaking geometry — the highest
    # severity meshing defect, but only a FULL model can leak: a half model cut
    # on a symmetry plane is legitimately open along the cut, as are the domain
    # boundary patches (symmetry/inlet/outlet/ground/farField, the catch-all
    # ".*"). So on a symmetry case we do not report open patches at all; on a
    # full model we flag any non-domain patch that is not closed.
    _DOMAIN_PATCHES = {
        "symmetry", "inlet", "outlet", "ground", "farfield", "front", "back", ".*",
    }
    open_patches: list[str] = []
    patch_info = stats.get("patches")
    if isinstance(patch_info, dict) and not has_symmetry:
        for name, info in patch_info.items():
            key = name.strip('"').lower()
            if key in _DOMAIN_PATCHES or key.startswith(("inlet", "outlet", "symmetry", "ground", "farfield")):
                continue
            if info.get("closed") is False:
                open_patches.append(name)
                issues.append(f"open (non-closed) surface patch '{name}'")

    layer_entries: dict[str, dict[str, Any]] = {}
    for patch, info in layers.items():
        achieved = int(info.get("layers", 0))
        entry: dict[str, Any] = dict(info)
        # A per-patch target from snappy's final table wins over the case-wide
        # config value; otherwise fall back to the configured layer count.
        patch_target = info.get("target_layers")
        effective_target = (
            int(patch_target)
            if isinstance(patch_target, int) and not isinstance(patch_target, bool) and patch_target > 0
            else target_layers
        )
        if effective_target and effective_target > 0:
            entry["coverage"] = achieved / effective_target
            if achieved < effective_target:
                issues.append(
                    f"boundary-layer dropout on '{patch}': "
                    f"{achieved} of {effective_target} layers"
                )
        layer_entries[patch] = entry

    ok = not issues and bool(stats or layers)

    # Overall verdict: the worst tier across every metric, downgraded to "bad"
    # when a hard checkMesh failure, an open patch or layer dropout occurred.
    verdict = "good"
    for entry in metrics:
        level = entry.get("level", "good")
        if _VERDICT_ORDER.index(level) > _VERDICT_ORDER.index(verdict):
            verdict = level
    hard_fail = (
        bool(open_patches)
        or any(
            e.get("coverage") is not None and e["coverage"] < 1.0
            for e in layer_entries.values()
        )
        or (isinstance(stats.get("failed_checks"), int) and stats["failed_checks"] > 0)
    )
    if hard_fail:
        verdict = "bad"
    verdict_label = _VERDICT_LABEL[verdict]

    if ok and verdict == "good":
        note = "mesh quality OK"
    elif ok:
        note = f"mesh quality {verdict_label.lower()} — no hard failures"
    else:
        note = f"{len(issues)} mesh-quality concern(s): " + "; ".join(issues)
    if verdict != "good":
        note = f"[{verdict_label}] " + note

    cell_types = stats.get("cell_types")
    if isinstance(cell_types, dict) and cell_types:
        total = sum(v for v in cell_types.values() if isinstance(v, int)) or 1
        cell_types = {
            name: {
                "count": int(count),
                "fraction": round(int(count) / total, 4),
            }
            for name, count in cell_types.items()
            if isinstance(count, int)
        }

    return {
        "available": True,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "stats": stats,
        "metrics": metrics,
        "layers": layer_entries,
        "patches": patch_info if isinstance(patch_info, dict) else {},
        "open_patches": open_patches,
        "cell_types": cell_types if isinstance(cell_types, dict) else {},
        "target_layers": target_layers,
        "issues": issues,
        "ok": ok,
        "note": note,
    }


def checkmesh_targets_from_dict(cfg: dict[str, Any] | None) -> dict[str, float]:
    """Extract layer/quality targets from an already-loaded case config."""
    if not isinstance(cfg, dict):
        return {}
    targets: dict[str, float] = {}

    layers = cfg.get("layers", {})
    if isinstance(layers, dict):
        n_layers = layers.get("n_layers")
        if isinstance(n_layers, int) and not isinstance(n_layers, bool) and n_layers > 0:
            targets["target_layers"] = float(n_layers)

    quality = cfg.get("mesh_quality", {})
    if isinstance(quality, dict):
        for key, target in (
            ("maxNonOrtho", "max_non_ortho"),
            ("maxInternalSkewness", "max_skewness"),
        ):
            value = quality.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                targets[target] = float(value)
    return targets


def verdict_bands_from_dict(cfg: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """Extract per-metric good/caution bands from a case config, if present."""
    if not isinstance(cfg, dict):
        return {}
    quality = cfg.get("mesh_quality", {})
    if not isinstance(quality, dict):
        return {}
    bands = quality.get("verdict_bands")
    if not isinstance(bands, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for metric, band in bands.items():
        if not isinstance(band, dict):
            continue
        good = band.get("good")
        caution = band.get("caution")
        if isinstance(good, (int, float)) and not isinstance(good, bool) \
                and isinstance(caution, (int, float)) and not isinstance(caution, bool):
            result[metric] = {"good": float(good), "caution": float(caution)}
    return result


def _load_config_dict(
    config_path: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Load the first available case config (explicit, case, cwd)."""
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path))
    if case_dir:
        candidates.append(Path(case_dir) / "case_config.json")
    candidates.append(Path("case_config.json"))

    for path in candidates:
        try:
            if not path.is_file():
                continue
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(cfg, dict):
            return cfg
    return {}


def _config_has_symmetry(config_dict: dict[str, Any] | None) -> bool:
    """True when a case is a half model (symmetry plane / symmetry boundary).

    Prefers an explicit ``symmetry_plane``/``centerline``; falls back to any
    ``domain_faces`` entry mapped to the configured symmetry patch name, then
    to a ``none``-free default where the generator assigns ``-<lateral>``.
    """
    if not isinstance(config_dict, dict):
        return False
    for key in ("symmetry_plane", "centerline"):
        value = config_dict.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    domain_faces = config_dict.get("domain_faces")
    if isinstance(domain_faces, dict):
        patches = config_dict.get("patches", {})
        sym_name = patches.get("symmetry") if isinstance(patches, dict) else None
        if isinstance(sym_name, str):
            return any(str(v) == sym_name for v in domain_faces.values())
        return any(str(v).lower().startswith("symmetry") for v in domain_faces.values())
    return False


def checkmesh_targets_from_case(
    config_path: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> dict[str, float]:
    """Read the layer/quality targets a case was generated with.

    Looks at an explicit config, then ``<case>/case_config.json``, then the
    current directory. Missing values are simply omitted.
    """
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path))
    if case_dir:
        candidates.append(Path(case_dir) / "case_config.json")
    candidates.append(Path("case_config.json"))

    for path in candidates:
        try:
            if not path.is_file():
                continue
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        return checkmesh_targets_from_dict(cfg)
    return {}


def mesh_quality_report(
    case_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """End-to-end mesh-quality report for a generated/run case directory."""
    base = Path(case_dir) if case_dir else Path(".")
    stats = read_checkmesh(find_checkmesh_logs(base))
    layers = read_layer_coverage(find_snappy_logs(base))
    config_dict = _load_config_dict(config_path, base)
    targets = checkmesh_targets_from_dict(config_dict)
    return check_mesh_quality(
        stats,
        layers,
        int(targets["target_layers"]) if "target_layers" in targets else None,
        max_non_ortho=targets.get("max_non_ortho", 65.0),
        max_skewness=targets.get("max_skewness", 4.0),
        bands=verdict_bands_from_dict(config_dict),
        has_symmetry=_config_has_symmetry(config_dict),
    )
