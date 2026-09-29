"""Boundary face-role assignment (extracted from geometry.py, Phase 1c)."""

from __future__ import annotations

from typing import Any, Mapping

from rapidfoam.core.axes import flow_axis_index_sign, up_axis_index


def patch_role(patches: Mapping[str, str], patch_name: str) -> str:
    """Resolve a patch name to its configured boundary role."""
    for role, name in patches.items():
        if patch_name == name:
            return role
    return {"farField": "walls"}.get(patch_name, patch_name)


def face_role(cfg: dict[str, Any], patch_name: str) -> str:
    """Resolve a patch name to its configured boundary role."""
    return patch_role(cfg["patches"], patch_name)


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


__all__ = ["face_role", "face_assignments", "patch_role"]
