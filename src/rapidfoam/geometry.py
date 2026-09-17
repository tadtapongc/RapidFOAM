"""Geometry utilities — axis math, domain sizing, mesh parameter derivation.

All mesh/domain parameters are derived from STL bounding box.
No geometry-specific tuning required.
"""

from __future__ import annotations

from typing import Any

from rapidfoam.stl_utils import BBox

# ============================================================
# AXIS UTILITIES
# ============================================================

AXIS_MAP: dict[str, tuple[int, int, int]] = {
    "+x": (1, 0, 0), "x": (1, 0, 0), "-x": (-1, 0, 0),
    "+y": (0, 1, 0), "y": (0, 1, 0), "-y": (0, -1, 0),
    "+z": (0, 0, 1), "z": (0, 0, 1), "-z": (0, 0, -1),
}


def parse_axis(s: str) -> tuple[int, int, int]:
    """Parse axis string to unit vector tuple."""
    if not isinstance(s, str):
        raise ValueError("Axis must be a string: +x, -x, +y, -y, +z, -z")
    s = s.strip().lower()
    if s not in AXIS_MAP:
        raise ValueError(f"Invalid axis '{s}'. Use: +x, -x, +y, -y, +z, -z")
    return AXIS_MAP[s]


def axis_index_sign(axis_str: str) -> tuple[int, int]:
    """Return (column_index, sign_multiplier) for axis string."""
    vec = parse_axis(axis_str)
    for i, v in enumerate(vec):
        if v != 0:
            return i, int(v)
    return 0, 1


def up_axis_index(cfg: dict[str, Any]) -> int:
    """Determine the 'up' axis index from downforce direction.

    Convention: downforce_axis='-y' means downforce points -y, so up is +y, index=1.
    """
    df_vec = parse_axis(cfg["outputs"]["downforce_axis"])
    for i, v in enumerate(df_vec):
        if v != 0:
            return i
    return 1


def flow_axis_index_sign(cfg: dict[str, Any]) -> tuple[int, int]:
    """Return (index, sign) of the flow direction."""
    vec = parse_axis(cfg["flow"]["direction"])
    for i, v in enumerate(vec):
        if v != 0:
            return i, int(v)
    return 2, -1


def vec_str(v: tuple[float, ...]) -> str:
    """Format 3-tuple as OpenFOAM vector: (x y z)."""
    return f"({v[0]:.6g} {v[1]:.6g} {v[2]:.6g})"


def velocity_vector(cfg: dict[str, Any]) -> tuple[float, float, float]:
    """Compute velocity vector from flow direction and speed."""
    d = parse_axis(cfg["flow"]["direction"])
    U = cfg["flow"]["velocity"]
    return (d[0] * U, d[1] * U, d[2] * U)


def turbulence_values(cfg: dict[str, Any]) -> tuple[float, float, float]:
    """Compute k, omega, nut from config.

    Returns:
        (k, omega, nut)
    """
    U = cfg["flow"]["velocity"]
    I = cfg["turbulence"]["intensity"]
    nu = cfg["fluid"]["nu"]
    nut_ratio = cfg["turbulence"]["nut_ratio"]
    k = 1.5 * (U * I) ** 2
    omega = k / (nut_ratio * nu)
    nut = nut_ratio * nu
    return k, omega, nut


def face_role(cfg: dict[str, Any], patch_name: str) -> str:
    """Resolve a patch name to its configured boundary role."""
    for role, name in cfg["patches"].items():
        if patch_name == name:
            return role
    return {"farField": "walls"}.get(patch_name, patch_name)


def face_assignments(cfg: dict[str, Any]) -> dict[str, str]:
    """Resolve the six faces once for domain sizing and every writer."""
    patches = cfg["patches"]
    if "domain_faces" in cfg:
        return {direction: patches.get(face_role(cfg, name), name)
                for direction, name in cfg["domain_faces"].items()}
    flow_idx, flow_sign = flow_axis_index_sign(cfg)
    up_idx = up_axis_index(cfg)
    lateral_idx = next(i for i in range(3) if i not in (flow_idx, up_idx))
    faces = {sign + axis: patches["walls"] for axis in "xyz" for sign in "-+"}
    faces[("-" if flow_sign > 0 else "+") + "xyz"[flow_idx]] = patches["inlet"]
    faces[("+" if flow_sign > 0 else "-") + "xyz"[flow_idx]] = patches["outlet"]
    faces["-" + "xyz"[up_idx]] = patches["ground"]
    faces["-" + "xyz"[lateral_idx]] = patches["symmetry"]
    return faces


# ============================================================
# DOMAIN SIZING — Geometry-derived, generous padding
# ============================================================

def compute_domain_box(cfg: dict[str, Any], combined_bounds: BBox) -> dict[str, list[float]]:
    """Compute domain bounding box from STL bounds.

    Uses generous padding to avoid blockage effects:
      - Upstream: 4× geometry length
      - Downstream: 8× geometry length
      - Lateral/top: 4× geometry height
      - Ground at smin[up_idx] (for ground vehicles)
      - Symmetry at lateral=smin[lateral_idx] if symmetry face, else padded

    Returns:
        {"min": [x,y,z], "max": [x,y,z]}
    """
    smin, smax = combined_bounds
    extents = [smax[i] - smin[i] for i in range(3)]

    flow_idx, flow_sign = flow_axis_index_sign(cfg)
    up_idx = up_axis_index(cfg)
    lateral_idx = next(i for i in range(3) if i != flow_idx and i != up_idx)

    # Padding factors (reduced for fast fidelity)
    fidelity = cfg.get("fidelity", "standard")
    if fidelity == "fast":
        default_up, default_down, default_lat, default_top = 3, 6, 3, 3
    elif fidelity == "fine":
        default_up, default_down, default_lat, default_top = 5, 10, 5, 5
    else:
        default_up, default_down, default_lat, default_top = 4, 8, 4, 4

    upstream = cfg.get("domain", {}).get("upstream_factor", default_up)
    downstream = cfg.get("domain", {}).get("downstream_factor", default_down)
    lateral = cfg.get("domain", {}).get("lateral_factor", default_lat)
    top = cfg.get("domain", {}).get("top_factor", default_top)

    dmin = [0.0] * 3
    dmax = [0.0] * 3

    # Flow axis
    flow_extent = max(extents[flow_idx], 0.1)
    if flow_sign > 0:
        dmin[flow_idx] = smin[flow_idx] - flow_extent * upstream
        dmax[flow_idx] = smax[flow_idx] + flow_extent * downstream
    else:
        dmin[flow_idx] = smin[flow_idx] - flow_extent * downstream
        dmax[flow_idx] = smax[flow_idx] + flow_extent * upstream

    # Up axis: ground plane for vehicles, or open air padding for airplanes/free-flight
    up_extent = max(extents[up_idx], 0.1)
    domain_faces = {d: face_role(cfg, name) for d, name in face_assignments(cfg).items()}
    up_min_key = f"-{'xyz'[up_idx]}"
    is_ground = "ground" in domain_faces.get(up_min_key, "").lower()

    ground_coord = cfg.get("ground_plane")
    if ground_coord is not None:
        dmin[up_idx] = float(ground_coord)
    elif "ground_clearance" in cfg and cfg["ground_clearance"] is not None and is_ground:
        dmin[up_idx] = smin[up_idx] - float(cfg["ground_clearance"])
    elif is_ground:
        dmin[up_idx] = smin[up_idx]  # ground plane touches bottom of car
    else:
        # Airborne / airplane: open atmosphere below aircraft
        bottom_factor = cfg.get("domain", {}).get("bottom_factor", top)
        dmin[up_idx] = smin[up_idx] - up_extent * bottom_factor

    dmax[up_idx] = smax[up_idx] + up_extent * top

    # Lateral axis
    lat_extent = max(extents[lateral_idx], 0.1)
    lateral_min_key = f"-{'xyz'[lateral_idx]}"
    is_symmetry = "symmetry" in domain_faces.get(lateral_min_key, "").lower()

    # Centerline / symmetry plane coordinate (supports planes not at 0)
    sym_coord = cfg.get("symmetry_plane")
    if sym_coord is None:
        sym_coord = cfg.get("centerline")

    if is_symmetry:
        if sym_coord is not None:
            dmin[lateral_idx] = float(sym_coord)
            car_half_width = max(0.1, smax[lateral_idx] - float(sym_coord))
            dmax[lateral_idx] = smax[lateral_idx] + car_half_width * lateral
        elif abs(smin[lateral_idx]) < 0.05:
            dmin[lateral_idx] = 0.0
            dmax[lateral_idx] = smax[lateral_idx] + lat_extent * lateral
        else:
            dmin[lateral_idx] = smin[lateral_idx]
            dmax[lateral_idx] = smax[lateral_idx] + lat_extent * lateral
    else:
        dmin[lateral_idx] = smin[lateral_idx] - lat_extent * lateral
        dmax[lateral_idx] = smax[lateral_idx] + lat_extent * lateral

    # The positive-face variants retain the half-model on the opposite side.
    if domain_faces.get(f"+{'xyz'[up_idx]}") == "ground":
        dmin[up_idx] = smin[up_idx] - up_extent * top
        dmax[up_idx] = (float(ground_coord) if ground_coord is not None else
                       smax[up_idx] + float(cfg.get("ground_clearance") or 0))
    if domain_faces.get(f"+{'xyz'[lateral_idx]}") == "symmetry":
        edge = smax[lateral_idx]
        plane = (float(sym_coord) if sym_coord is not None else
                 0.0 if abs(edge) < 0.05 else edge)
        width = max(0.1, plane - smin[lateral_idx]) if sym_coord is not None else lat_extent
        dmax[lateral_idx] = plane
        dmin[lateral_idx] = smin[lateral_idx] - width * lateral

    return {
        "min": [round(v, 4) for v in dmin],
        "max": [round(v, 4) for v in dmax],
    }


# ============================================================
# FIDELITY PRESETS — Optimized for Formula Student / FSAE Aerodynamics
# ============================================================

FIDELITY_PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        # Quick turnaround for iterative design (~5-10 min on 16-32 cores, ~2-4M cells)
        "desc": "Quick iterative design turnaround",
        "cell_estimate": "~2-4M cells",
        "n_cells_target": 3000000,
        "runtime_estimate": "~5-10 min",
        "base_cell_size": 0.15,        # m — coarse background
        "surface_level": [3, 4],       # 18.75mm - 9.38mm surface cells
        "edge_level": 5,               # 4.69mm at edges
        "n_layers": 3,
        "expansion_ratio": 1.3,
        "first_layer_thickness": 0.4,
        "end_time": 800,
        "write_interval": 400,
        "maxGlobalCells": 8_000_000,
        "nCellsBetweenLevels": 2,
        "resolveFeatureAngle": 35,
        "nSolveIter": 100,             # snap iterations
        "nFeatureSnapIter": 10,
        "nLayerIter": 30,
        "nRelaxIter_layers": 5,
        "slurm_time": "04:00:00",
        # Distance-based refinement shells
        "distance_levels": [
            (0.040, 3),   # 40mm → level 3
            (0.120, 2),   # 120mm → level 2
        ],
        "near_wake_level": 2,          # 37.5mm near wake
        "far_wake_level": 1,           # 75mm far wake
    },
    "standard": {
        # Balanced — optimal for FSAE aero (~30-60 min on 32 cores, sweet spot: ~6-9M cells)
        "desc": "Balanced accuracy and speed for FSAE aero",
        "cell_estimate": "~6-9M cells",
        "n_cells_target": 7500000,
        "runtime_estimate": "~30-60 min",
        "base_cell_size": 0.10,        # 100mm background
        "surface_level": [4, 5],       # 6.25mm bodywork, 3.125mm fine features
        "edge_level": 6,               # 1.56mm at sharp aero edges (wings/gurneys)
        "n_layers": 5,
        "expansion_ratio": 1.2,
        "first_layer_thickness": 0.3,
        "end_time": 1500,
        "write_interval": 500,
        "maxGlobalCells": 18_000_000,
        "nCellsBetweenLevels": 2,      # 2 buffer cells (avoids massive 3D transition bloat)
        "resolveFeatureAngle": 35,     # Prevents general body curvature from ballooning to max level
        "nSolveIter": 200,
        "nFeatureSnapIter": 15,
        "nLayerIter": 50,
        "nRelaxIter_layers": 10,
        "slurm_time": "08:00:00",
        # Conforming distance shells (lean transition around bodywork)
        "distance_levels": [
            (0.025, 4),   # 25mm → level 4 (6.25mm)
            (0.080, 3),   # 80mm → level 3 (12.5mm)
        ],
        "near_wake_level": 3,          # 12.5mm for rear wing vortex / diffuser
        "far_wake_level": 1,           # 50mm for downstream transport (saves ~8M cells)
    },
    "fine": {
        # High resolution validation (~2-4 hours, ~12-16M cells)
        "desc": "High-resolution validation quality",
        "cell_estimate": "~12-16M cells",
        "n_cells_target": 14000000,
        "runtime_estimate": "~2-4 hrs",
        "base_cell_size": 0.08,        # 80mm background
        "surface_level": [5, 6],       # 2.5mm - 1.25mm surface cells
        "edge_level": 7,               # 0.625mm at edges
        "n_layers": 6,
        "expansion_ratio": 1.15,
        "first_layer_thickness": 0.2,
        "end_time": 3000,
        "write_interval": 500,
        "maxGlobalCells": 30_000_000,
        "nCellsBetweenLevels": 2,
        "resolveFeatureAngle": 30,
        "nSolveIter": 300,
        "nFeatureSnapIter": 20,
        "nLayerIter": 50,
        "nRelaxIter_layers": 10,
        "slurm_time": "12:00:00",
        "distance_levels": [
            (0.020, 5),   # 20mm → level 5
            (0.060, 4),   # 60mm → level 4
            (0.150, 3),   # 150mm → level 3
        ],
        "near_wake_level": 4,          # 5mm near wake
        "far_wake_level": 2,           # 20mm far wake
    },
}


# ============================================================
# MESH PARAMETER DERIVATION — Universal, geometry-adaptive
# ============================================================

def compute_mesh_params(cfg: dict[str, Any], combined_bounds: BBox) -> dict[str, Any]:
    """Derive all mesh parameters from geometry bounds.

    Domain and wake dimensions follow geometry bounds. Cell sizes and distance
    shells use metre-valued fidelity presets unless explicitly overridden.

    Refinement strategy:
        - Distance-based shells around the STL surface.
          Cells are refined based on proximity to the geometry, giving smooth
          transitions that conform to the actual shape.
        - Two-stage wake region:
          1. nearWakeBox: High resolution immediately behind vehicle (diffuser,
             rear wing vortices, tire separation). Length ~ 1.2x vehicle length.
          2. farWakeBox: Low-cost downstream transport to outlet without numerical
             diffusion, avoiding millions of wasted cells far downstream.

    Fidelity levels:
        "fast"     — iterative design, quick turnaround (~2-4M cells)
        "standard" — balanced accuracy/speed for FSAE (~6-9M cells)
        "fine"     — final report quality (~12-16M cells)

    Returns dict with:
        base_cell_size, surface_level, edge_level, distance_levels,
        refinement_regions (nearWakeBox + farWakeBox), n_layers, etc.
    """
    smin, smax = combined_bounds
    extents = [smax[i] - smin[i] for i in range(3)]
    max_extent = max(extents)

    if max_extent <= 0:
        raise ValueError("STL has zero extent — check your geometry")

    # Get fidelity preset
    fidelity = cfg.get("fidelity", "standard")
    preset = FIDELITY_PRESETS.get(fidelity, FIDELITY_PRESETS["standard"])

    user_mesh = cfg.get("mesh_params", {})

    # Base cell: respect user override or use fidelity preset
    base_cell = user_mesh.get("base_cell_size", preset["base_cell_size"])

    # Surface and edge levels: respect user override or use fidelity preset
    surface_level = user_mesh.get("surface_level", preset["surface_level"])
    edge_level = user_mesh.get("edge_level", preset["edge_level"])

    # Distance-based refinement shells
    distance_levels = user_mesh.get("distance_levels", preset["distance_levels"])
    if distance_levels and isinstance(distance_levels[0], list):
        distance_levels = [tuple(x) for x in distance_levels]

    resolve_feature_angle = user_mesh.get("resolveFeatureAngle", preset.get("resolveFeatureAngle", 35))

    # Wake levels (uncoupled from surface level to prevent wake bloat)
    near_wake_level = user_mesh.get("near_wake_level", preset.get("near_wake_level", 3))
    far_wake_level = user_mesh.get("far_wake_level", preset.get("far_wake_level", 1))

    # Support legacy single wake_level override if user explicitly set it
    if "wake_level" in user_mesh:
        near_wake_level = user_mesh["wake_level"]

    flow_idx, flow_sign = flow_axis_index_sign(cfg)
    up_idx = up_axis_index(cfg)
    lateral_idx = next(i for i in range(3) if i != flow_idx and i != up_idx)

    # Vertical alignment (ground for vehicles, or symmetric padding for airplanes)
    domain_faces = {d: face_role(cfg, name) for d, name in face_assignments(cfg).items()}
    up_min_key = f"-{'xyz'[up_idx]}"
    is_ground = "ground" in domain_faces.get(up_min_key, "").lower()

    # Centerline / symmetry coordinate
    lateral_min_key = f"-{'xyz'[lateral_idx]}"
    is_symmetry = "symmetry" in domain_faces.get(lateral_min_key, "").lower()

    sym_coord = cfg.get("symmetry_plane")
    if sym_coord is None:
        sym_coord = cfg.get("centerline")
    if sym_coord is not None:
        sym_x = float(sym_coord)
    elif "domain_box" in cfg and isinstance(cfg["domain_box"], dict) and "min" in cfg["domain_box"]:
        sym_x = cfg["domain_box"]["min"][lateral_idx]
    elif abs(smin[lateral_idx]) < 0.05:
        sym_x = 0.0
    else:
        sym_x = smin[lateral_idx]

    # Precompute ground coordinate once if ground plane is present
    ground_z: float | None = None
    if is_ground:
        if "ground_plane" in cfg and cfg["ground_plane"] is not None:
            ground_z = float(cfg["ground_plane"]) - 0.01
        elif "ground_clearance" in cfg and cfg["ground_clearance"] is not None:
            ground_z = smin[up_idx] - float(cfg["ground_clearance"]) - 0.01
        elif "domain_box" in cfg and isinstance(cfg["domain_box"], dict) and "min" in cfg["domain_box"]:
            ground_z = cfg["domain_box"]["min"][up_idx] - 0.01
        else:
            ground_z = smin[up_idx] - 0.01

    # --- 1. Near Wake Box (High-resolution: rear wing, diffuser, tire separation) ---
    near_pad_lat = max(0.10, extents[lateral_idx] * 0.15)
    near_pad_top = max(0.15, extents[up_idx] * 0.25)
    near_length = max(2.0, extents[flow_idx] * 1.2)

    near_min = list(smin)
    near_max = list(smax)
    if is_ground:
        assert ground_z is not None
        near_min[up_idx] = ground_z
    else:
        near_min[up_idx] = smin[up_idx] - near_pad_top

    near_max[up_idx] = smax[up_idx] + near_pad_top
    near_min[lateral_idx] = smin[lateral_idx] - near_pad_lat
    near_max[lateral_idx] = smax[lateral_idx] + near_pad_lat

    # If lateral min is symmetry plane, clip cleanly to symmetry plane
    if is_symmetry:
        near_min[lateral_idx] = sym_x

    if flow_sign > 0:
        near_min[flow_idx] = smin[flow_idx] + extents[flow_idx] * 0.6
        near_max[flow_idx] = smax[flow_idx] + near_length
    else:
        near_min[flow_idx] = smin[flow_idx] - near_length
        near_max[flow_idx] = smin[flow_idx] + extents[flow_idx] * 0.4

    # --- 2. Far Wake Box (Lower resolution: downstream transport to outlet) ---
    far_pad_lat = max(0.20, extents[lateral_idx] * 0.30)
    far_pad_top = max(0.25, extents[up_idx] * 0.40)
    far_length = max(4.0, extents[flow_idx] * 3.5)

    far_min = list(smin)
    far_max = list(smax)
    if is_ground:
        assert ground_z is not None
        far_min[up_idx] = ground_z
    else:
        far_min[up_idx] = smin[up_idx] - far_pad_top

    far_max[up_idx] = smax[up_idx] + far_pad_top
    far_min[lateral_idx] = smin[lateral_idx] - far_pad_lat
    far_max[lateral_idx] = smax[lateral_idx] + far_pad_lat

    if is_symmetry:
        far_min[lateral_idx] = sym_x

    if flow_sign > 0:
        far_min[flow_idx] = smax[flow_idx]
        far_max[flow_idx] = smax[flow_idx] + far_length
    else:
        far_min[flow_idx] = smin[flow_idx] - far_length
        far_max[flow_idx] = smin[flow_idx]

    domain_box = cfg.get("domain_box")
    if not isinstance(domain_box, dict):
        domain_box = compute_domain_box(cfg, combined_bounds)
    if domain_faces.get(f"+{'xyz'[up_idx]}") == "ground":
        for upper in (near_max, far_max):
            upper[up_idx] = domain_box["max"][up_idx] + 0.01
    if domain_faces.get(f"+{'xyz'[lateral_idx]}") == "symmetry":
        for upper in (near_max, far_max):
            upper[lateral_idx] = domain_box["max"][lateral_idx]

    default_regions = [
        {"name": "nearWakeBox", "min": [round(v, 4) for v in near_min],
         "max": [round(v, 4) for v in near_max], "level": near_wake_level},
        {"name": "farWakeBox", "min": [round(v, 4) for v in far_min],
         "max": [round(v, 4) for v in far_max], "level": far_wake_level},
    ]

    refinement_regions = user_mesh.get("refinement_regions", default_regions)

    result = {
        "base_cell_size": round(base_cell, 4),
        "surface_level": surface_level,
        "edge_level": edge_level,
        "distance_levels": distance_levels,
        "refinement_regions": refinement_regions,
        "nCellsBetweenLevels": user_mesh.get("nCellsBetweenLevels", preset.get("nCellsBetweenLevels", 2)),
        "maxGlobalCells": user_mesh.get("maxGlobalCells", preset.get("maxGlobalCells", 18_000_000)),
        "maxLocalCells": user_mesh.get("maxLocalCells", 2_000_000),
        "minRefinementCells": user_mesh.get("minRefinementCells", 10),
        "resolveFeatureAngle": resolve_feature_angle,
        "allowFreeStandingZoneFaces": user_mesh.get("allowFreeStandingZoneFaces", True),
    }
    for key in ("location_in_mesh", "locationInMesh", "maxLoadUnbalance"):
        if key in user_mesh:
            result[key] = user_mesh[key]
    return result


