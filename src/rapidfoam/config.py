"""Configuration loading, defaults, and validation.

The user config is minimal — just geometry + flow conditions.
Everything else is derived from universal defaults that work for any geometry.
"""

from __future__ import annotations

import copy
import json
import logging
import math
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ============================================================
# UNIVERSAL DEFAULTS — Settings that work for any geometry
# ============================================================

# Fixed project-relative layout (not configurable): the Studio and CLI both
# resolve geometry and cases against these, and the web layer relies on the
# fixed base for its path-traversal checks.
STL_DIR = "stl"
CASE_DIR = "cases"

DEFAULT_CONFIG: dict[str, Any] = {
    "case_name": "my_case",
    "stl_files": [],

    # Flow conditions
    "flow": {
        "velocity": 16.67,       # m/s
        "direction": "-z",       # freestream direction
        "ground": True,          # moving ground BC
    },

    # Output axes
    "outputs": {
        "drag_axis": "-z",
        "downforce_axis": "-y",
    },

    # Fluid properties (air at sea level, 20°C)
    "fluid": {
        "nu": 1.516e-5,
        "rho": 1.225,
    },

    # Turbulence (k-omega SST with wall functions — universal choice)
    "turbulence": {
        "model": "kOmegaSST",
        "intensity": 0.005,      # 0.5% — typical for external aero / FSAE
        "nut_ratio": 10,
    },

    # Domain patches
    "patches": {
        "inlet": "inlet",
        "outlet": "outlet",
        "ground": "ground",
        "walls": "farField",
        "symmetry": "symmetry",
    },

    # Force references
    "force_refs": {
        "lRef": 1.0,
        "Aref": 1.0,
        "CofR": [0, 0, 0],
    },

    # Vehicle geometry (for aero-balance / center-of-pressure handoff)
    "vehicle": {
        "wheelbase": None,        # m
        "front_weight_pct": None, # static front weight fraction, %
    },

    # Parallel
    "parallel": {
        "n_procs": 10,
        "method": "scotch",
    },

    # SLURM
    "slurm": {
        "qos": "cu_hpc",
        "partition": "cpu",
        "nodes": 1,
        "time": "auto",
        "mem_per_cpu": "2G",
        "cpus_per_task": 1,
        "openfoam_module": None,
        "openfoam_source": "$HOME/OpenFOAM/OpenFOAM-v2606/etc/bashrc",
    },

    # Solver settings (conservative, never-diverge)
    "solver": {
        "end_time": 800,
        "write_interval": 400,
        "purge_write": 2,
    },

    # Numerical schemes — stable second-order, no overshoot
    "schemes": {
        "div_U": "bounded Gauss limitedLinear 1",
        "div_k": "bounded Gauss upwind",
        "div_omega": "bounded Gauss upwind",
        "grad": "cellLimited Gauss linear 1",
        "laplacian": "Gauss linear limited corrected 0.5",
        "snGrad": "limited corrected 0.5",
        "wallDist": "meshWave",
    },

    # Linear solvers
    "linear_solvers": {
        "p": {
            "solver": "GAMG",
            "smoother": "DICGaussSeidel",
            "tolerance": 1e-7,
            "relTol": 0.01,
            "nPreSweeps": 0,
            "nPostSweeps": 2,
            "cacheAgglomeration": True,
            "agglomerator": "faceAreaPair",
            "nCellsInCoarsestLevel": 500,
            "mergeLevels": 2,
        },
        "U": {
            "solver": "PBiCGStab",
            "preconditioner": "DILU",
            "tolerance": 1e-8,
            "relTol": 0.01,
            "minIter": 1,
        },
    },

    # SIMPLE algorithm (SIMPLEC — more stable than classic SIMPLE)
    "simple": {
        "nNonOrthogonalCorrectors": 2,
        "consistent": True,
    },

    # Relaxation — SIMPLEC-optimized (consistent formulation enables U=0.7, p=0.7)
    "relaxation": {
        "fields": {"p": 0.7},
        "equations": {"U": 0.7, "k": 0.5, "omega": 0.5},
    },

    # Wall functions (Spalding bridges entire y+ range)
    "wall_functions": {
        "nut": "nutUSpaldingWallFunction",
        "k": "kqRWallFunction",
        "omega": "omegaWallFunction",
    },

    # Mesh — snappy settings (geometry-independent)
    "snap": {
        "nSmoothPatch": 5,
        "tolerance": 2.0,
        "nSolveIter": 200,
        "nRelaxIter": 10,
        "nFeatureSnapIter": 15,
        "implicitFeatureSnap": True,
        "explicitFeatureSnap": True,
        "multiRegionFeatureSnap": False,
    },

    # Boundary layers
    "layers": {
        "n_layers": 5,
        "expansion_ratio": 1.2,
        # relativeSizes=true: thicknesses are fractions of the local cell size
        # relativeSizes=false: thicknesses are absolute, in metres
        "relativeSizes": True,
        "first_layer_thickness": 0.3,   # fraction of cell (or metres if relativeSizes=false)
        "min_thickness": 0.05,          # same units as first_layer_thickness
        "y_plus_target": None,          # absolute near-wall target; overrides first_layer_thickness
        "featureAngle": 170,
        "slipFeatureAngle": 30,
        "nGrow": 0,
        "maxFaceThicknessRatio": 0.5,
        "nSmoothSurfaceNormals": 3,
        "nSmoothThickness": 15,
        "nSmoothNormals": 3,
        "nRelaxIter": 10,
        "nBufferCellsNoExtrude": 0,
        "nLayerIter": 50,
        "maxAlignedCells": 200000,
        "minMedialAxisAngle": 90,
        "maxThicknessToMedialRatio": 0.3,
        "nMedialAxisIter": 10,
        "nSmoothDisplacement": 0,
        "detectExtrusionIsland": True,
        "nRelaxedIter": 20,
        "ground_layers": False,
        "ground_n_layers": 2,
        # Two-pass meshing: when true, the first snappyHexMesh run only
        # castellates and snaps, and a second pass adds layers with the quality
        # gate relaxed (system/snappyHexMeshDict_layering). Reaches much higher
        # boundary-layer coverage on complex geometry at some quality cost.
        "two_pass": False,
        # Opt-in: when true and the requested y+ target cannot be built at the
        # current surface resolution (snappy caps a layer at
        # maxFaceThicknessRatio x the local face), raise maxFaceThicknessRatio
        # and, if still needed, coarsen the finest surface level so the target
        # is met instead of being clamped. Off by default (predictable).
        "y_plus_fit": False,
    },

    # Feature extraction (140° captures real aero edges without cosmetic CAD seams)
    "feature_extract": {
        "extractionMethod": "extractFromSurface",
        "includedAngle": 140,
    },

    # Diagnostic surface/field outputs for ParaView inspection of the spatial
    # load map (wall shear stress, the wall pressure as the p boundary) and the
    # y+ field, plus small text reductions (fieldMinMax, surfaceFieldValue) the
    # telemetry can read. Cheap enough to leave on; set a flag false to suppress.
    "field_outputs": {
        "wall_shear_stress": True,
        "y_plus": True,
        "field_min_max": True,
        # Opt-in: surfaceFieldValue aborts the solve if a referenced patch does
        # not exist (e.g. a fully-internal STL part), so this is off by default.
        # Set to true (all STL patches) or a list of patch names that exist.
        "surface_field_value": False,
        "vorticity": False,
    },

    # Mesh quality controls. snappyHexMesh's limits are where it stops *trying*,
    # not where a mesh becomes good, so they are set near the CFD-practical good
    # bands (verdict_bands below) to push it to actually fix bad faces. Tightening
    # costs meshing time; verify with `read_forces.py --mesh`.
    "mesh_quality": {
        "maxNonOrtho": 60,
        "maxBoundarySkewness": 20,
        "maxInternalSkewness": 3.5,
        "maxConcave": 80,
        "minVol": 1e-13,
        "minTetQuality": 1e-15,
        "minArea": -1,
        "minTwist": 0.02,
        "minDeterminant": 0.001,
        "minFaceWeight": 0.05,
        "minVolRatio": 0.01,
        "minTriangleTwist": -1,
        "nSmoothScale": 6,
        "errorReduction": 0.75,
        # Good/caution bands for the mesh-quality verdict (independent of the
        # snappyHexMesh pass/fail limits above). "good" is the value at which a
        # metric is considered healthy; "caution" is the boundary beyond which
        # it is marginal/bad. Override per team standard.
        "verdict_bands": {
            "max_non_ortho": {"good": 60.0, "caution": 70.0},
            "max_skewness": {"good": 2.0, "caution": 4.0},
            "max_aspect_ratio": {"good": 50.0, "caution": 100.0},
            "min_determinant": {"good": 0.05, "caution": 0.001},
            "min_interp_weight": {"good": 0.1, "caution": 0.01},
            "min_volume_ratio": {"good": 0.05, "caution": 0.01},
            "concave_cells": {"good": 0.0, "caution": 0.0},
        },
        "relaxed": {
            "maxNonOrtho": 70,
            "maxBoundarySkewness": 25,
            "maxInternalSkewness": 5,
            "maxConcave": 85,
            "minVol": 1e-13,
            "minTetQuality": 1e-30,
            "minArea": -1,
            "minTwist": 0.001,
            "minDeterminant": 0.0005,
            "minFaceWeight": 0.02,
            "minVolRatio": 0.005,
            "minTriangleTwist": -1,
        },
    },

    # Pre-mesh surface integrity gate (surfaceCheck). Runs before meshing and
    # aborts on leaking/self-intersecting/illegal geometry so a dirty CAD export
    # fails fast instead of after a long snappyHexMesh run. A symmetry half model
    # is open along the cut and is exempt from the closure requirement.
    "surface_check": {
        "enabled": True,
        "enforce": False,                  # abort the run on defects; false = run + report only
        "check_self_intersection": True,   # -checkSelfIntersection (slower on big meshes)
        "allow_open": False,               # permit an open surface without a symmetry plane
        "max_illegal_triangles": 0,
        "max_unconnected_parts": 1,
    },

    # potentialFoam
    "potential_flow": {
        "nNonOrthogonalCorrectors": 10,
    },
}


# ============================================================
# CONFIG LOADING
# ============================================================

def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override dict into base dict."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key.startswith("_"):
            continue
        if isinstance(val, dict):
            result[key] = deep_merge(result.get(key, {}) if isinstance(result.get(key), dict) else {}, val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def effective_config(raw_user: dict[str, Any]) -> dict[str, Any]:
    """Resolve a raw user config (defaults + top-level keys + ``overrides``).

    This is the single merge used by both the file loader and the web layer, so
    the effective settings are identical whether a case is generated by the CLI
    or the Studio. Comment keys (``_``-prefixed) are dropped, matching
    ``deep_merge``.
    """
    if not isinstance(raw_user, dict):
        raise ValueError("Config must be a JSON object")
    clean_cfg = {k: v for k, v in raw_user.items() if not k.startswith("_")}
    overrides = clean_cfg.pop("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("'overrides' must be an object")
    cfg = deep_merge(DEFAULT_CONFIG, clean_cfg)
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def load_config(config_path: str | Path) -> dict[str, Any]:
    """Load user config JSON and merge with universal defaults.

    User only needs to specify:
      - case_name
      - stl_files
      - flow (velocity, direction, ground)
      - outputs (drag_axis, downforce_axis)

    Everything else uses universal defaults.
    """
    config_path = Path(config_path)
    with open(config_path, encoding="utf-8") as f:
        user_cfg = json.load(f)
    return effective_config(user_cfg)


# ============================================================
# VALIDATION
# ============================================================

def validate(cfg: dict[str, Any], project_dir: Path) -> tuple[list[str], list[str]]:
    """Validate config. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    from rapidfoam.core.axes import parse_axis
    from rapidfoam.core.faces import face_assignments, face_role
    from rapidfoam.meshing.presets import FIDELITY_PRESETS

    # Check containers before dereferencing nested values.
    sections = [key for key, value in DEFAULT_CONFIG.items() if isinstance(value, dict)]
    for section in sections + ["mesh_params", "domain"]:
        if not isinstance(cfg.get(section, {}), dict):
            errors.append(f"'{section}' must be an object")
    if errors:
        return errors, warnings

    def finite(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def vector(value: Any, label: str) -> bool:
        valid = isinstance(value, (list, tuple)) and len(value) == 3 and all(finite(v) for v in value)
        if not valid:
            errors.append(f"{label} must contain three finite numbers")
        return valid

    def positive(section: str, key: str, integer: bool = False, allow_zero: bool = False) -> None:
        value = cfg.get(section, {}).get(key)
        if key not in cfg.get(section, {}):
            return
        if (not finite(value) or (value < 0 if allow_zero else value <= 0)
                or (integer and not isinstance(value, int))):
            errors.append(f"{section}.{key} must be a finite {'integer' if integer else 'number'} "
                          f"{'≥ 0' if allow_zero else '> 0'}")

    # Required fields
    stl_files = cfg.get("stl_files")
    if not isinstance(stl_files, list) or not stl_files or not all(isinstance(n, str) and n for n in stl_files):
        errors.append("'stl_files' is required (list of STL filenames)")
        stl_files = []

    name = cfg.get("case_name")
    if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in '/\\\r\n'):
        errors.append("'case_name' must be a nonempty folder name without path separators")
    if not isinstance(cfg.get("fidelity", "standard"), str) or cfg.get("fidelity", "standard") not in FIDELITY_PRESETS:
        errors.append("fidelity must be fast, standard, or fine")

    # Flow
    flow = cfg.get("flow", {})
    positive("flow", "velocity")
    if not isinstance(flow.get("ground"), bool):
        errors.append("flow.ground must be true or false")

    try:
        parse_axis(flow.get("direction", ""))
    except ValueError as e:
        errors.append(f"flow.direction: {e}")

    # Output axes
    outputs = cfg.get("outputs", {})
    for key in ("drag_axis", "downforce_axis"):
        try:
            parse_axis(outputs.get(key, ""))
        except ValueError as e:
            errors.append(f"outputs.{key}: {e}")

    # STL files
    stl_dir = project_dir / STL_DIR
    if not stl_dir.is_dir():
        errors.append(f"STL directory not found: {stl_dir}")
    else:
        stems = set()
        for name in stl_files:
            stem = name.rsplit(".", 1)[0] if "." in name else name
            if not stem or stem in stems or any(c in name for c in '/\\\r\n'):
                errors.append(f"STL names must be filenames with unique nonempty stems: {name!r}")
                continue
            stems.add(stem)
            found = _find_stl(stl_dir, name)
            if not found:
                errors.append(f"STL not found: '{name}' in {stl_dir}")

    # Patches
    required_patches = {"inlet", "outlet", "ground", "walls", "symmetry"}
    missing = required_patches - set(cfg.get("patches", {}).keys())
    if missing:
        errors.append(f"Missing patch keys: {missing}")
    patch_names = list(cfg.get("patches", {}).values())
    if not all(isinstance(n, str) and n and not any(c.isspace() for c in n) for n in patch_names):
        errors.append("patch names must be nonempty strings without whitespace")
    elif len(set(patch_names)) != len(patch_names):
        errors.append("patch names must be unique")

    for section, keys in {
        "fluid": ("nu", "rho"), "turbulence": ("intensity", "nut_ratio"),
        "solver": ("end_time", "write_interval"),
        "layers": ("expansion_ratio", "first_layer_thickness", "min_thickness"),
        "force_refs": ("lRef", "Aref"),
    }.items():
        for key in keys:
            positive(section, key)
    base_cell = cfg.get("mesh_params", {}).get("base_cell_size")
    if base_cell is not None and base_cell != "auto":
        positive("mesh_params", "base_cell_size")
    cells_per_length = cfg.get("mesh_params", {}).get("cells_per_length")
    if cells_per_length is not None and (
            not finite(cells_per_length) or not (5.0 <= cells_per_length <= 100.0)):
        errors.append("mesh_params.cells_per_length must be a number between 5 and 100")
    auto_size = cfg.get("mesh_params", {}).get("auto_size")
    if auto_size is not None and not isinstance(auto_size, bool):
        errors.append("mesh_params.auto_size must be true or false")
    feature_percentile = cfg.get("mesh_params", {}).get("feature_percentile")
    if feature_percentile is not None and (
            not finite(feature_percentile) or not (0.0 < feature_percentile <= 100.0)):
        errors.append("mesh_params.feature_percentile must be a number in (0, 100]")
    feature_cells = cfg.get("mesh_params", {}).get("feature_cells")
    if feature_cells is not None and (not finite(feature_cells) or feature_cells <= 0):
        errors.append("mesh_params.feature_cells must be a positive number")
    max_surface_level = cfg.get("mesh_params", {}).get("max_surface_level")
    if max_surface_level is not None and (
            not isinstance(max_surface_level, int) or isinstance(max_surface_level, bool)
            or max_surface_level < 1):
        errors.append("mesh_params.max_surface_level must be an integer >= 1")
    auto_feature_angle = cfg.get("mesh_params", {}).get("auto_feature_angle")
    if auto_feature_angle is not None and not isinstance(auto_feature_angle, bool):
        errors.append("mesh_params.auto_feature_angle must be true or false")
    crease_percentile = cfg.get("mesh_params", {}).get("crease_percentile")
    if crease_percentile is not None and (
            not finite(crease_percentile) or not (0.0 <= crease_percentile <= 50.0)):
        errors.append("mesh_params.crease_percentile must be a number in [0, 50]")
    feature_angle_ratio = cfg.get("mesh_params", {}).get("feature_angle_ratio")
    if feature_angle_ratio is not None and (
            not finite(feature_angle_ratio) or not (0.05 <= feature_angle_ratio <= 1.0)):
        errors.append("mesh_params.feature_angle_ratio must be a number in (0, 1]")
    crease_angle_floor = cfg.get("mesh_params", {}).get("crease_angle_floor")
    if crease_angle_floor is not None and (
            not finite(crease_angle_floor) or not (0.0 <= crease_angle_floor < 90.0)):
        errors.append("mesh_params.crease_angle_floor must be a number in [0, 90)")
    resolve_feature_angle = cfg.get("mesh_params", {}).get("resolveFeatureAngle")
    if resolve_feature_angle is not None and (
            not finite(resolve_feature_angle) or not (0.0 < resolve_feature_angle <= 180.0)):
        errors.append("mesh_params.resolveFeatureAngle must be a number in (0, 180]")
    grading = cfg.get("mesh_params", {}).get("grading")
    if grading is not None and not isinstance(grading, bool):
        if isinstance(grading, str):
            if grading.strip().lower() not in ("auto", "off", "none", "uniform"):
                errors.append("mesh_params.grading must be 'auto', 'off', or three positive numbers")
        elif isinstance(grading, (list, tuple)):
            if len(grading) != 3 or not all(finite(v) and v > 0 for v in grading):
                errors.append("mesh_params.grading must be three positive numbers")
        else:
            errors.append("mesh_params.grading must be 'auto', 'off', or three positive numbers")
    grading_ratio = cfg.get("mesh_params", {}).get("grading_ratio")
    if grading_ratio is not None and (
            not finite(grading_ratio) or not (1.0 <= grading_ratio <= 20.0)):
        errors.append("mesh_params.grading_ratio must be a number between 1 and 20")
    if "ground_layers" in cfg.get("layers", {}) and not isinstance(cfg["layers"]["ground_layers"], bool):
        errors.append("layers.ground_layers must be true or false")
    if "two_pass" in cfg.get("layers", {}) and not isinstance(cfg["layers"]["two_pass"], bool):
        errors.append("layers.two_pass must be true or false")
    y_plus_fit = cfg.get("layers", {}).get("y_plus_fit")
    if y_plus_fit is not None and not (
            isinstance(y_plus_fit, bool)
            or (isinstance(y_plus_fit, str) and y_plus_fit.strip().lower() in ("ratio", "full"))):
        errors.append('layers.y_plus_fit must be false, "ratio", or "full"')
    if cfg.get("layers", {}).get("two_pass") is True:
        warnings.append(
            "layers.two_pass is experimental: the layering pass disables the "
            "mesh-quality gate, which can produce highly skewed cells. Taper the "
            "limits in system/snappyHexMeshDict_layering against checkMesh before "
            "production use."
        )
    verdict_bands = cfg.get("mesh_quality", {}).get("verdict_bands")
    if verdict_bands is not None:
        if not isinstance(verdict_bands, dict):
            errors.append("mesh_quality.verdict_bands must be an object")
        else:
            for metric, band in verdict_bands.items():
                if not isinstance(band, dict) or set(band) != {"good", "caution"}:
                    errors.append(
                        f"mesh_quality.verdict_bands.{metric} must have exactly "
                        f"'good' and 'caution' numbers"
                    )
                elif not finite(band.get("good")) or not finite(band.get("caution")):
                    errors.append(
                        f"mesh_quality.verdict_bands.{metric}.good/caution must be finite numbers"
                    )
    layering_relaxed = cfg.get("mesh_quality", {}).get("layering_relaxed")
    if layering_relaxed is not None:
        if not isinstance(layering_relaxed, dict):
            errors.append("mesh_quality.layering_relaxed must be an object")
        else:
            for key, value in layering_relaxed.items():
                if not finite(value):
                    errors.append(f"mesh_quality.layering_relaxed.{key} must be a finite number")
    surface_check = cfg.get("surface_check", {})
    for key in ("enabled", "enforce", "check_self_intersection", "allow_open"):
        if key in surface_check and not isinstance(surface_check[key], bool):
            errors.append(f"surface_check.{key} must be true or false")
    for key in ("max_illegal_triangles", "max_unconnected_parts"):
        value = surface_check.get(key)
        if value is not None and (not isinstance(value, int)
                                  or isinstance(value, bool) or value < 0):
            errors.append(f"surface_check.{key} must be an integer ≥ 0")
    field_outputs = cfg.get("field_outputs", {})
    if not isinstance(field_outputs, dict):
        errors.append("'field_outputs' must be an object")
    else:
        for key in ("wall_shear_stress", "y_plus", "field_min_max", "vorticity"):
            if key in field_outputs and not isinstance(field_outputs[key], bool):
                errors.append(f"field_outputs.{key} must be true or false")
        sfv = field_outputs.get("surface_field_value")
        if sfv is not None and not isinstance(sfv, bool) and not (
                isinstance(sfv, (list, tuple)) and all(isinstance(p, str) for p in sfv)):
            errors.append("field_outputs.surface_field_value must be true, false, or a list of patch names")
    for section, keys in {
        "parallel": ("n_procs",), "slurm": ("nodes", "cpus_per_task"),
        "mesh_params": ("maxGlobalCells", "maxLocalCells", "nCellsBetweenLevels"),
    }.items():
        for key in keys:
            positive(section, key, integer=True)
    for key in ("edge_level", "near_wake_level", "far_wake_level", "wake_level", "minRefinementCells"):
        positive("mesh_params", key, integer=True, allow_zero=True)
    positive("layers", "n_layers", integer=True, allow_zero=True)
    positive("layers", "ground_n_layers", integer=True, allow_zero=True)
    positive("solver", "purge_write", integer=True, allow_zero=True)
    for key in cfg.get("domain", {}):
        if key.endswith("_factor"):
            positive("domain", key)
    for key in ("ground_plane", "ground_clearance", "symmetry_plane", "centerline"):
        value = cfg.get(key)
        if value is not None and (not finite(value) or (key == "ground_clearance" and value < 0)):
            errors.append(f"{key} must be finite" + (" and ≥ 0" if key == "ground_clearance" else ""))
    vector(cfg["force_refs"].get("CofR"), "force_refs.CofR")
    mesh = cfg.get("mesh_params", {})
    for key in ("locationInMesh", "location_in_mesh"):
        if key in mesh:
            vector(mesh[key], f"mesh_params.{key}")
    level = mesh.get("surface_level")
    if level is not None and (not isinstance(level, (list, tuple)) or len(level) != 2
                             or not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in level)
                             or level[0] > level[1]):
        errors.append("mesh_params.surface_level must be two ordered nonnegative integers")

    # Ground settings precedence warning
    if cfg.get("ground_plane") is not None and cfg.get("ground_clearance") is not None:
        warnings.append(
            f"Both 'ground_plane' ({cfg['ground_plane']}) and 'ground_clearance' "
            f"({cfg['ground_clearance']}) are defined; 'ground_plane' takes precedence."
        )

    # A layer stack that cannot reach minThickness makes snappyHexMesh unmark
    # every extrusion, silently adding 0 boundary layers.
    lay = cfg.get("layers", {})
    y_target = lay.get("y_plus_target")
    if y_target is not None:
        if not finite(y_target) or not (0.1 <= y_target <= 200):
            errors.append("layers.y_plus_target must be a finite number between 0.1 and 200 (or null)")
    n_layers = lay.get("n_layers")
    ratio = lay.get("expansion_ratio")
    first = lay.get("first_layer_thickness")
    min_th = lay.get("min_thickness")
    if (isinstance(n_layers, int) and not isinstance(n_layers, bool) and n_layers >= 1
            and finite(first) and finite(ratio) and finite(min_th)):
        if math.isclose(ratio, 1.0):
            total = first * n_layers
        else:
            total = first * (ratio ** n_layers - 1) / (ratio - 1)
        if total < min_th:
            mode = "fraction of cell size" if lay.get("relativeSizes", True) else "metres"
            warnings.append(
                f"layers.stack: first_layer_thickness {first:g} x {n_layers} layers "
                f"(expansion {ratio:g}) = {total:.3g} total, below min_thickness {min_th:g} "
                f"(units: {mode}) — snappyHexMesh will add 0 layers. Increase "
                f"first_layer_thickness/n_layers or lower min_thickness."
            )

    # Absolute layers thicker than the finest surface cell cannot be inserted
    # next to it (snappy respects maxFaceThicknessRatio), so layers silently drop.
    # Only meaningful for user-set absolute thicknesses; y+ resolution is clamped.
    mesh = cfg.get("mesh_params", {})
    base_cell = mesh.get("base_cell_size")
    surf_level = mesh.get("surface_level")
    if (lay.get("relativeSizes") is False and finite(first) and first > 0
            and finite(base_cell) and base_cell not in (None, "auto")
            and isinstance(surf_level, (list, tuple)) and len(surf_level) == 2
            and isinstance(surf_level[1], int) and not isinstance(surf_level[1], bool) and surf_level[1] >= 0):
        cell_fine = float(base_cell) / (2 ** surf_level[1])
        thickness_ratio = float(lay.get("maxFaceThicknessRatio", 0.5) or 0.5)
        if first > thickness_ratio * cell_fine:
            warnings.append(
                f"layers.first_layer_thickness {first:g} m exceeds "
                f"maxFaceThicknessRatio ({thickness_ratio:g}) of the finest "
                f"surface cell ({cell_fine:.3g} m, level {surf_level[1]}) — snappyHexMesh "
                f"may drop boundary layers. Reduce the thickness, raise surface_level, "
                f"lower base_cell_size, or enable layers.y_plus_fit."
            )

    # Domain box (can be "auto" or {"min": [x,y,z], "max": [x,y,z]})
    box = cfg.get("domain_box")
    if box not in ("auto", None) and not isinstance(box, dict):
        errors.append("'domain_box' must be 'auto' or a dict: {\"min\": [x,y,z], \"max\": [x,y,z]}")
    elif isinstance(box, dict):
        min_valid = vector(box.get("min"), "domain_box.min")
        max_valid = vector(box.get("max"), "domain_box.max")
        if min_valid and max_valid:
            for i in range(3):
                if box["min"][i] >= box["max"][i]:
                    errors.append(f"domain_box min[{i}] >= max[{i}]")

    # Geometry and field writers require independent flow and vertical axes.
    try:
        flow_vec = parse_axis(flow.get("direction", ""))
        up_vec = parse_axis(outputs.get("downforce_axis", ""))
        drag_vec = parse_axis(outputs.get("drag_axis", ""))
        if any(a and b for a, b in zip(flow_vec, up_vec)):
            errors.append("flow.direction and outputs.downforce_axis must use different axes")
        if any(a and b for a, b in zip(drag_vec, up_vec)):
            errors.append("drag_axis and downforce_axis must use different axes")
    except ValueError:
        pass
    faces = cfg.get("domain_faces")
    if "domain_faces" in cfg:
        if not isinstance(faces, dict) or set(faces) != {s+a for a in "xyz" for s in "-+"}:
            errors.append("domain_faces must assign all six faces: -x, +x, -y, +y, -z, +z")
        elif any(not isinstance(v, str) or face_role(cfg, v) not in required_patches for v in faces.values()):
            errors.append("domain_faces values must be boundary roles or configured patch names")
    if not errors:
        resolved = face_assignments(cfg)
        roles = {d: face_role(cfg, p) for d, p in resolved.items()}
        if "inlet" not in roles.values() or "outlet" not in roles.values():
            errors.append("domain_faces must include inlet and outlet")
        up_idx = next(i for i, v in enumerate(up_vec) if v)
        flow_idx = next(i for i, v in enumerate(flow_vec) if v)
        lateral_idx = next(i for i in range(3) if i not in (up_idx, flow_idx))
        for role, axis in (("ground", up_idx), ("symmetry", lateral_idx)):
            assigned = [d for d, r in roles.items() if r == role]
            if len(assigned) > 1 or any(d[-1] != "xyz"[axis] for d in assigned):
                errors.append(f"{role} must occupy at most one face on the {'xyz'[axis]} axis")
    return errors, warnings


def _find_stl(stl_dir: Path, name: str) -> Path | None:
    """Search for STL file with flexible naming."""
    # Strip extension if user included it
    stem = name.rsplit(".", 1)[0] if "." in name else name

    for pattern in (name, f"{stem}.stl", f"{stem}.STL",
                    f"{stem.lower()}.stl", f"{stem.lower()}.STL"):
        p = stl_dir / pattern
        if p.is_file():
            return p
    return None


def find_stl(stl_dir: Path, name: str) -> Path | None:
    """Public interface for STL file search."""
    return _find_stl(stl_dir, name)


def user_set(raw_user: dict[str, Any], section: str, key: str) -> bool:
    """True when a config value was explicitly provided by the user.

    Checks the top level of the raw config and its 'overrides' block, the
    two places the CLI and the Studio accept explicit values.
    """
    if not isinstance(raw_user, dict):
        return False
    overrides = raw_user.get("overrides")
    if isinstance(overrides, dict):
        block = overrides.get(section)
        if isinstance(block, dict) and key in block:
            return True
    block = raw_user.get(section)
    return isinstance(block, dict) and key in block
