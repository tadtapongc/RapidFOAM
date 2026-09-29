"""Boundary condition field writers (0/ directory).

Generates: U, p, k, omega, nut
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rapidfoam.core.axes import vec_str
from rapidfoam.core.faces import face_assignments
from rapidfoam.core.fields import turbulence_values, velocity_vector
from rapidfoam.core.foam import FOOTER, foam_header


def _wing_regex(stl_names: list[str]) -> str:
    """Build regex matching all wing/body patch names."""
    if len(stl_names) == 1:
        return re.escape(stl_names[0])
    return "(" + "|".join(re.escape(n) for n in stl_names) + ")"


def write_fields(cfg: dict[str, Any], case_dir: Path) -> None:
    """Write all 0/ boundary condition files."""
    k_val, omega_val, nut_val = turbulence_values(cfg)
    vel = vec_str(velocity_vector(cfg))
    patches = cfg["patches"]
    stl_names = cfg["stl_names"]
    wing_re = _wing_regex(stl_names)
    moving_ground = cfg["flow"]["ground"]

    wf = cfg["wall_functions"]
    nut_wf = wf["nut"]
    k_wf = wf["k"]
    omega_wf = wf["omega"]

    kv = f"{k_val:.6g}"
    ov = f"{omega_val:.6g}"
    nv = f"{nut_val:.6g}"

    ground_U = (
        f"fixedValue;\n        value           uniform {vel};"
        if moving_ground else "slip;"
    )

    # Ground turbulence BCs: wall functions for moving ground, zeroGradient for slip
    if moving_ground:
        ground_k = f"{k_wf};\n        value           uniform {kv};"
        ground_omega = f"{omega_wf};\n        value           uniform {ov};"
        ground_nut = f"{nut_wf};\n        value           uniform 0;"
    else:
        ground_k = "zeroGradient;"
        ground_omega = "zeroGradient;"
        ground_nut = f"calculated;\n        value           uniform {nv};"

    active_patches = set(face_assignments(cfg).values())
    has_ground = patches["ground"] in active_patches
    has_symmetry = patches["symmetry"] in active_patches

    def _opt_patch(condition: bool, name: str, body: str) -> str:
        if not condition:
            return ""
        return f"    {name}\n    {{\n{body}\n    }}\n"

    zero_dir = case_dir / "0"
    zero_dir.mkdir(parents=True, exist_ok=True)

    # ---- U ----
    u_ground = _opt_patch(has_ground, patches["ground"], f"        type            {ground_U}")
    u_sym = _opt_patch(has_symmetry, patches["symmetry"], "        type            symmetry;")
    (zero_dir / "U").write_text(foam_header("U") + f"""\
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform {vel};

boundaryField
{{
    {patches["inlet"]}
    {{
        type            fixedValue;
        value           uniform {vel};
    }}
    {patches["outlet"]}
    {{
        type            inletOutlet;
        inletValue      uniform (0 0 0);
        value           uniform (0 0 0);
    }}
    "{wing_re}"
    {{
        type            noSlip;
    }}
{u_ground}    {patches["walls"]}
    {{
        type            slip;
    }}
{u_sym}}}

""" + FOOTER)

    # ---- p ----
    p_ground = _opt_patch(has_ground, patches["ground"], "        type            zeroGradient;")
    p_sym = _opt_patch(has_symmetry, patches["symmetry"], "        type            symmetry;")
    (zero_dir / "p").write_text(foam_header("p") + f"""\
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    {patches["inlet"]}
    {{
        type            zeroGradient;
    }}
    {patches["outlet"]}
    {{
        type            fixedValue;
        value           uniform 0;
    }}
    "{wing_re}"
    {{
        type            zeroGradient;
    }}
{p_ground}    {patches["walls"]}
    {{
        type            zeroGradient;
    }}
{p_sym}}}

""" + FOOTER)

    # ---- k ----
    k_ground = _opt_patch(has_ground, patches["ground"], f"        type            {ground_k}")
    k_sym = _opt_patch(has_symmetry, patches["symmetry"], "        type            symmetry;")
    (zero_dir / "k").write_text(foam_header("k") + f"""\
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform {kv};

boundaryField
{{
    {patches["inlet"]}
    {{
        type            fixedValue;
        value           uniform {kv};
    }}
    {patches["outlet"]}
    {{
        type            zeroGradient;
    }}
    "{wing_re}"
    {{
        type            {k_wf};
        value           uniform {kv};
    }}
{k_ground}    {patches["walls"]}
    {{
        type            zeroGradient;
    }}
{k_sym}}}

""" + FOOTER)

    # ---- omega ----
    om_ground = _opt_patch(has_ground, patches["ground"], f"        type            {ground_omega}")
    om_sym = _opt_patch(has_symmetry, patches["symmetry"], "        type            symmetry;")
    (zero_dir / "omega").write_text(foam_header("omega") + f"""\
dimensions      [0 0 -1 0 0 0 0];

internalField   uniform {ov};

boundaryField
{{
    {patches["inlet"]}
    {{
        type            fixedValue;
        value           uniform {ov};
    }}
    {patches["outlet"]}
    {{
        type            zeroGradient;
    }}
    "{wing_re}"
    {{
        type            {omega_wf};
        value           uniform {ov};
    }}
{om_ground}    {patches["walls"]}
    {{
        type            zeroGradient;
    }}
{om_sym}}}

""" + FOOTER)

    # ---- nut ----
    nut_ground = _opt_patch(has_ground, patches["ground"], f"        type            {ground_nut}")
    nut_sym = _opt_patch(has_symmetry, patches["symmetry"], "        type            symmetry;")
    (zero_dir / "nut").write_text(foam_header("nut") + f"""\
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform {nv};

boundaryField
{{
    {patches["inlet"]}
    {{
        type            calculated;
        value           uniform {nv};
    }}
    {patches["outlet"]}
    {{
        type            calculated;
        value           uniform {nv};
    }}
    "{wing_re}"
    {{
        type            {nut_wf};
        value           uniform 0;
    }}
{nut_ground}    {patches["walls"]}
    {{
        type            calculated;
        value           uniform {nv};
    }}
{nut_sym}}}

""" + FOOTER)

