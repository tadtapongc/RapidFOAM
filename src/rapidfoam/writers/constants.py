"""Constant directory writers.

Generates: transportProperties, turbulenceProperties
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rapidfoam.core.foam import FOOTER, foam_header


def write_constant(cfg: dict[str, Any], case_dir: Path) -> None:
    """Write transportProperties and turbulenceProperties."""
    const_dir = case_dir / "constant"
    const_dir.mkdir(parents=True, exist_ok=True)

    # transportProperties
    nu = cfg["fluid"]["nu"]
    (const_dir / "transportProperties").write_text(
        foam_header("transportProperties")
        + f"transportModel  Newtonian;\n\nnu              {nu:.6e};\n\n"
        + FOOTER
    )

    # turbulenceProperties
    turb = cfg["turbulence"]
    model = turb["model"]
    content = foam_header("turbulenceProperties")
    content += "simulationType  RAS;\n\n"
    content += "RAS\n{\n"
    content += f"    RASModel        {model};\n"
    content += "    turbulence      on;\n"
    content += "    printCoeffs     on;\n"
    content += "}\n\n"
    content += FOOTER
    (const_dir / "turbulenceProperties").write_text(content)
