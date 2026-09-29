"""Field/turbulence derivations (extracted from geometry.py, Phase 1c)."""

from __future__ import annotations

from typing import Any

from rapidfoam.core.axes import parse_axis


def velocity_vector(cfg: dict[str, Any]) -> tuple[float, float, float]:
    """Compute velocity vector from flow direction and speed."""
    d = parse_axis(cfg["flow"]["direction"])
    u = cfg["flow"]["velocity"]
    return (d[0] * u, d[1] * u, d[2] * u)


def turbulence_values(cfg: dict[str, Any]) -> tuple[float, float, float]:
    """Compute k, omega, nut from config.

    Returns:
        (k, omega, nut)
    """
    u = cfg["flow"]["velocity"]
    intensity = cfg["turbulence"]["intensity"]
    nu = cfg["fluid"]["nu"]
    nut_ratio = cfg["turbulence"]["nut_ratio"]
    k = 1.5 * (u * intensity) ** 2
    omega = k / (nut_ratio * nu)
    nut = nut_ratio * nu
    return k, omega, nut


__all__ = ["velocity_vector", "turbulence_values"]
