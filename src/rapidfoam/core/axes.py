"""Axis and vector math shared by generation and measurement.

Extracted from ``geometry.py`` (Phase 1c) so that ``postproc`` and the writers
can depend on a small, dependency-free module instead of the meshing god module.
"""

from __future__ import annotations

from typing import Any

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


__all__ = [
    "AXIS_MAP",
    "parse_axis",
    "axis_index_sign",
    "up_axis_index",
    "flow_axis_index_sign",
    "vec_str",
]
