"""OpenFOAM file header/footer utilities.

The canonical primitives live in :mod:`rapidfoam.core.foam`; this module
re-exports them for the existing writer imports.
"""

from __future__ import annotations

from rapidfoam.core.foam import (  # noqa: F401
    FIELD_CLASS,
    FOOTER,
    HEADER,
    bool_str,
    foam_header,
)

__all__ = ["HEADER", "FOOTER", "FIELD_CLASS", "foam_header", "bool_str"]
