"""Solver-related OpenFOAM dict writers.

Generates: controlDict, fvSchemes, fvSolution, decomposeParDict

Single-stage pipeline: robust bounded schemes with tight p relTol
and conservative relaxation. No scheme switching needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rapidfoam.geometry import parse_axis
from rapidfoam.writers.base import FOOTER, bool_str, foam_header


# ============================================================
# CONTROLDICT
# ============================================================

def write_control_dict(cfg: dict[str, Any], case_dir: Path) -> None:
    """Generate controlDict with force function objects."""
    stl_names = cfg["stl_names"]
    force_patches = " ".join(stl_names)
    solver = cfg["solver"]

    drag_vec = parse_axis(cfg["outputs"]["drag_axis"])
    df_vec = parse_axis(cfg["outputs"]["downforce_axis"])
    drag_dir = f"({drag_vec[0]} {drag_vec[1]} {drag_vec[2]})"
    lift_dir = f"({df_vec[0]} {df_vec[1]} {df_vec[2]})"

    # Pitch axis = liftDir x dragDir. For the default FSAE convention
    # (drag=-Z, downforce=-Y) this gives +X, matching the standard
    # right-handed pitch axis (nose-up positive).
    pitch = (
        df_vec[1] * drag_vec[2] - df_vec[2] * drag_vec[1],
        df_vec[2] * drag_vec[0] - df_vec[0] * drag_vec[2],
        df_vec[0] * drag_vec[1] - df_vec[1] * drag_vec[0],
    )
    pitch_dir = f"({pitch[0]} {pitch[1]} {pitch[2]})"

    refs = cfg["force_refs"]
    cofr = refs["CofR"]
    cofr_str = f"({cofr[0]} {cofr[1]} {cofr[2]})"
    rho = cfg["fluid"]["rho"]
    velocity = cfg["flow"]["velocity"]

    # Per-patch forces for multi-part geometry
    per_patch = ""
    if len(stl_names) > 1:
        for p in stl_names:
            per_patch += f"""
    forces_{p}
    {{
        type            forces;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        patches         ({p});
        rho             rhoInf;
        rhoInf          {rho};
        CofR            {cofr_str};
    }}
"""

    content = f"""\
application     simpleFoam;

startFrom       latestTime;
stopAt          endTime;
endTime         {solver["end_time"]};
deltaT          1;

writeControl    timeStep;
writeInterval   {solver["write_interval"]};
purgeWrite      {solver["purge_write"]};
writeFormat     binary;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
writeAtEnd      true;

functions
{{
    forces
    {{
        type            forces;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        patches         ({force_patches});
        rho             rhoInf;
        rhoInf          {rho};
        CofR            {cofr_str};
    }}

    forceCoeffs
    {{
        type            forceCoeffs;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        patches         ({force_patches});
        rho             rhoInf;
        rhoInf          {rho};
        CofR            {cofr_str};
        liftDir         {lift_dir};
        dragDir         {drag_dir};
        pitchAxis       {pitch_dir};
        magUInf         {velocity};
        lRef            {refs["lRef"]};
        Aref            {refs["Aref"]};
    }}
{per_patch}
    residuals
    {{
        type            solverInfo;
        libs            (utilityFunctionObjects);
        writeControl    timeStep;
        writeInterval   1;
        fields          (U p k omega);
    }}

    yPlus
    {{
        type            yPlus;
        libs            (fieldFunctionObjects);
        writeControl    writeTime;
    }}
}}

"""
    (case_dir / "system" / "controlDict").write_text(
        foam_header("controlDict") + content + FOOTER
    )


# ============================================================
# FVSCHEMES
# ============================================================

def write_fv_schemes(cfg: dict[str, Any], case_dir: Path) -> None:
    """Generate fvSchemes — bounded TVD/upwind schemes, cell-limited."""
    s = cfg["schemes"]

    content = f"""\
ddtSchemes      {{ default steadyState; }}

gradSchemes
{{
    default         Gauss linear;
    grad(U)         {s["grad"]};
    grad(k)         {s["grad"]};
    grad(omega)     {s["grad"]};
}}

divSchemes
{{
    default         none;
    div(phi,U)      {s["div_U"]};
    div(phi,k)      {s["div_k"]};
    div(phi,omega)  {s["div_omega"]};
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}

laplacianSchemes {{ default {s["laplacian"]}; }}
interpolationSchemes {{ default linear; }}
snGradSchemes    {{ default {s["snGrad"]}; }}
wallDist         {{ method {s["wallDist"]}; }}

"""
    (case_dir / "system" / "fvSchemes").write_text(
        foam_header("fvSchemes") + content + FOOTER
    )


# ============================================================
# FVSOLUTION
# ============================================================

def write_fv_solution(cfg: dict[str, Any], case_dir: Path) -> None:
    """Generate fvSolution — tight p relTol, conservative relaxation."""
    solvers = cfg["linear_solvers"]
    simple = cfg["simple"]
    relax = cfg["relaxation"]
    pot = cfg["potential_flow"]

    p = solvers["p"]
    u = solvers["U"]
    eq_relax = relax["equations"]
    field_relax = relax["fields"]

    eq_str = " ".join(f"{k} {v};" for k, v in eq_relax.items())
    field_str = " ".join(f"{k} {v};" for k, v in field_relax.items())

    content = f"""\
solvers
{{
    p
    {{
        solver          {p["solver"]};
        smoother        {p["smoother"]};
        tolerance       {p["tolerance"]};
        relTol          {p["relTol"]};
        nPreSweeps      {p["nPreSweeps"]};
        nPostSweeps     {p["nPostSweeps"]};
        cacheAgglomeration {bool_str(p["cacheAgglomeration"])};
        agglomerator    {p["agglomerator"]};
        nCellsInCoarsestLevel {p["nCellsInCoarsestLevel"]};
        mergeLevels     {p["mergeLevels"]};
    }}

    Phi
    {{
        $p;
    }}

    "(U|k|omega)"
    {{
        solver          {u["solver"]};
        preconditioner  {u["preconditioner"]};
        tolerance       {u["tolerance"]};
        relTol          {u["relTol"]};
        minIter         {u["minIter"]};
    }}
}}

SIMPLE
{{
    nNonOrthogonalCorrectors {simple["nNonOrthogonalCorrectors"]};
    consistent      {bool_str(simple["consistent"])};
}}

potentialFlow
{{
    nNonOrthogonalCorrectors {pot["nNonOrthogonalCorrectors"]};
}}

relaxationFactors
{{
    equations {{ {eq_str} }}
    fields    {{ {field_str} }}
}}

"""
    (case_dir / "system" / "fvSolution").write_text(
        foam_header("fvSolution") + content + FOOTER
    )


# ============================================================
# DECOMPOSEPARDICT
# ============================================================

def write_decompose_par_dict(cfg: dict[str, Any], case_dir: Path) -> None:
    """Generate decomposeParDict."""
    parallel = cfg["parallel"]
    content = (
        f"numberOfSubdomains  {parallel['n_procs']};\n\n"
        f"method          {parallel['method']};\n\n"
    )
    (case_dir / "system" / "decomposeParDict").write_text(
        foam_header("decomposeParDict") + content + FOOTER
    )
