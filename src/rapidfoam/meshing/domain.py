"""Domain bounding-box derivation (moved from geometry.py, Phase 2)."""

from __future__ import annotations

from typing import Any

from rapidfoam.core.axes import flow_axis_index_sign, up_axis_index
from rapidfoam.core.faces import face_assignments, face_role
from rapidfoam.stl_utils import BBox

# Small distance the ground patch is embedded below the configured plane so the
# moving-ground wall reliably cuts the background mesh. Shared by the domain
# sizing and the ground-layer clearance guard so they stay consistent.
GROUND_EMBED = 0.01


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


__all__ = ["GROUND_EMBED", "compute_domain_box"]
