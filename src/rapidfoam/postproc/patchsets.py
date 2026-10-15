"""Boundary patches that belong to the wind-tunnel domain, not the body.

Shared by the checkMesh verdict (open-patch detection) and the layer-field
first-layer coverage (wetted-body aggregate) so the two consumers cannot drift
apart. A patch counts as a domain boundary when its name is a known domain
patch or starts with a domain prefix (so a renamed ``inlet_2`` still counts).
"""

from __future__ import annotations

DOMAIN_PATCHES = frozenset({
    "symmetry", "inlet", "outlet", "ground", "farfield", "front", "back", ".*",
})
DOMAIN_PREFIXES = ("inlet", "outlet", "symmetry", "ground", "farfield")


def is_domain_patch(name: str) -> bool:
    """True when a patch name is a domain boundary rather than a body surface."""
    key = name.strip('"').lower()
    return key in DOMAIN_PATCHES or key.startswith(DOMAIN_PREFIXES)


__all__ = ["DOMAIN_PATCHES", "DOMAIN_PREFIXES", "is_domain_patch"]
