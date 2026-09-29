"""Single, override-aware access to a case's effective configuration.

Everything that reads a case configuration (telemetry, mesh-quality, surface
checks, the forces CLI) used to roll its own "explicit path -> case_config.json
-> cwd" search and, worse, some of them read the raw user config without
applying its ``overrides`` block. This module is the one place that:

  * resolves the candidate config files, preferring the *effective*
    ``<case>/case_config.json`` over a raw ``configs/<case>.json``;
  * applies defaults + ``overrides`` via :func:`rapidfoam.config.effective_config`;
  * exposes small extractors for the values those callers need.

It only depends on ``rapidfoam.config`` (the defaults/merge layer).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from rapidfoam.config import effective_config

_SYMMETRY_PATCH_FALLBACK = "symmetry"


def _read_json_object(path: Path) -> Optional[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def read_case_config(
    config_path: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return the effective configuration for a case, or ``{}`` if none found.

    Precedence: ``<case_dir>/case_config.json`` (the effective snapshot written
    by the generator) > an explicit ``config_path`` > ``./case_config.json``.
    The chosen file is always run through :func:`effective_config`, so a raw
    user config with an ``overrides`` block yields the same values the solver
    ran with.
    """
    candidates: list[Path] = []
    if case_dir is not None:
        candidates.append(Path(case_dir) / "case_config.json")
    if config_path is not None:
        candidates.append(Path(config_path))
    candidates.append(Path("case_config.json"))

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        raw = _read_json_object(path)
        if raw is None:
            continue
        try:
            return effective_config(raw)
        except ValueError:
            # Already-effective config with a malformed/absent overrides key:
            # fall back to the raw mapping rather than failing the reader.
            return raw
    return {}


def has_symmetry(cfg: dict[str, Any] | None) -> bool:
    """Whether the case is a half model (a symmetry boundary is assigned).

    The explicit ``domain_faces`` assignment is authoritative; ``symmetry_plane``
    / ``centerline`` is only consulted when no face list is present. This is the
    order that makes a full-car config (``symmetry_plane`` still written by the
    Studio, but no symmetry face) correctly report as a full model.
    """
    if not isinstance(cfg, dict) or not cfg:
        return False
    faces = cfg.get("domain_faces")
    if isinstance(faces, dict) and faces:
        patches = cfg.get("patches")
        sym_name = _SYMMETRY_PATCH_FALLBACK
        if isinstance(patches, dict) and isinstance(patches.get("symmetry"), str):
            sym_name = patches["symmetry"]
        return any(str(v) == sym_name or "symmetry" in str(v).lower() for v in faces.values())
    for key in ("symmetry_plane", "centerline"):
        value = cfg.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    return False


def has_symmetry_for_case(
    config_path: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> bool:
    """Config-based symmetry, with a ``constant/polyMesh/boundary`` fallback."""
    cfg = read_case_config(config_path=config_path, case_dir=case_dir)
    if has_symmetry(cfg):
        return True
    base = Path(case_dir) if case_dir else Path(".")
    boundary = base / "constant" / "polyMesh" / "boundary"
    if boundary.is_file():
        try:
            content = boundary.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return bool(re.search(r"\btype\s+symmetry(?:Plane)?\s*;", content))
    return False


def yplus_target(cfg: dict[str, Any] | None) -> Optional[float]:
    """Read ``layers.y_plus_target`` (or its resolved fallback) from a config."""
    if not isinstance(cfg, dict):
        return None
    layers = cfg.get("layers", {})
    if not isinstance(layers, dict):
        return None
    for source in (layers, layers.get("_resolved", {})):
        if not isinstance(source, dict):
            continue
        target = source.get("y_plus_target")
        if isinstance(target, (int, float)) and not isinstance(target, bool) and target > 0:
            return float(target)
    return None


def yplus_target_from_case(
    config_path: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> Optional[float]:
    return yplus_target(read_case_config(config_path=config_path, case_dir=case_dir))


def solver_end_time(cfg: dict[str, Any] | None) -> Optional[float]:
    if not isinstance(cfg, dict):
        return None
    solver = cfg.get("solver", {})
    if not isinstance(solver, dict):
        return None
    value = solver.get("end_time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def solver_end_time_from_case(
    config_path: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> Optional[float]:
    return solver_end_time(read_case_config(config_path=config_path, case_dir=case_dir))


def stl_files(cfg: dict[str, Any] | None) -> list[str]:
    """STL basenames referenced by a case config (for 3D telemetry)."""
    if not isinstance(cfg, dict):
        return []
    files = cfg.get("stl_files")
    if isinstance(files, (list, tuple)) and files:
        return [Path(str(name)).name for name in files]
    return []


def vehicle_geometry(cfg: dict[str, Any] | None) -> dict[str, Optional[float]]:
    """Vehicle geometry used for aero-balance (wheelbase + static front weight %)."""
    wheelbase: Optional[float] = None
    front_pct: Optional[float] = None
    if isinstance(cfg, dict):
        vehicle = cfg.get("vehicle") or {}
        if isinstance(vehicle, dict):
            try:
                if vehicle.get("wheelbase") is not None:
                    wheelbase = float(vehicle["wheelbase"])
                fp = vehicle.get("front_weight_pct", vehicle.get("front_pct"))
                if fp is not None:
                    front_pct = float(fp)
            except (TypeError, ValueError):
                pass
    return {"wheelbase": wheelbase, "front_weight_pct": front_pct}


def mesh_targets(cfg: dict[str, Any] | None) -> dict[str, float]:
    """Layer/quality targets a case was generated with (see checkMesh verdict)."""
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


def verdict_bands(cfg: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """Per-metric good/caution bands from a case config, if present."""
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


def surface_policy(cfg: dict[str, Any] | None) -> dict[str, Any]:
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


__all__ = [
    "read_case_config",
    "has_symmetry",
    "has_symmetry_for_case",
    "yplus_target",
    "yplus_target_from_case",
    "solver_end_time",
    "solver_end_time_from_case",
    "stl_files",
    "vehicle_geometry",
    "mesh_targets",
    "verdict_bands",
    "surface_policy",
]
