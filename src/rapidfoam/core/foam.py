"""OpenFOAM file header/footer primitives (dependency-free, used by all writers)."""

from __future__ import annotations


HEADER = """\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2606                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    object      {obj};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

FOOTER = "\n// ************************************************************************* //\n"

FIELD_CLASS: dict[str, str] = {
    "U": "volVectorField",
    "p": "volScalarField",
    "k": "volScalarField",
    "omega": "volScalarField",
    "nut": "volScalarField",
    "Phi": "surfaceScalarField",
}


def foam_header(obj: str) -> str:
    """Generate OpenFOAM file header for a given object name."""
    return HEADER.format(cls=FIELD_CLASS.get(obj, "dictionary"), obj=obj)


def bool_str(val: bool) -> str:
    """Convert Python bool to OpenFOAM bool string."""
    return "true" if val else "false"


__all__ = ["HEADER", "FOOTER", "FIELD_CLASS", "foam_header", "bool_str"]
