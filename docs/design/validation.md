# Design — Validation: grid independence + benchmark cases

**Status:** proposal (no code changes yet)
**Goal:** make RapidFOAM's force numbers *trusted* rather than merely *produced*.
Two independent capabilities:

1. **Grid independence** — compare the `fast`/`standard`/`fine` meshes of a case,
   report the discretisation error, and flag when the forces are mesh-converged.
2. **Benchmark validation** — run canonical geometries with published reference
   data (Ahmed body, etc.) and assert the coefficients agree within tolerance.

This is the difference between "an OpenFOAM automation suite" and "a *validated*
OpenFOAM automation suite".

---

## 1. Grid independence

### 1.1 What it is

Three meshes of increasing refinement should give asymptotically converging
forces. If `standard` and `fine` differ by <3 % in Cd and <5 % in Cl, the answer
is mesh-converged. A Richardson-style extrapolation estimates the exact value.

### 1.2 API

New `postproc/gridstudy.py`:

```python
def grid_study(case_name, fidelities=("fast","standard","fine"),
               case_dir=None, *, cd_thresh=0.03, cl_thresh=0.05) -> dict
```

Returns:

```python
{
  "available": True,
  "per_fidelity": {
      "fast":     {"available": True, "cd": 0.312, "cl": 1.44, "cells": 4.1e6, "refinement": 1.0},
      "standard": {"available": True, "cd": 0.301, "cl": 1.40, "cells": 9.2e6, "refinement": 1.5},
      "fine":     {"available": True, "cd": 0.297, "cl": 1.39, "cells": 20e6,  "refinement": 2.2},
  },
  "deltas": {"cd_std_fine_pct": 1.3, "cl_std_fine_pct": 0.7},
  "richardson": {"p": 1.8, "cd_extrapolated": 0.295, "cl_extrapolated": 1.385},
  "converged": True,
  "verdict": "grid-independent",
  "note": "...",
}
```

Rules:
- Read each fidelity's forces from `cases/<name>/` … but the fidelities are
  **different cases**. Two ways to obtain them:
  - **(a) explicit**: the user runs `fast`/`standard`/`fine` variants (e.g.
    `test_x_fast`, `test_x_standard`, `test_x_fine`) and passes their names;
  - **(b) convenience**: `grid_study(base)` looks for `<base>_fast/_standard/_fine`
    (and `<base>` as `standard`).
- Per fidelity compute the trailing-window Cd/Cl (reuse `postproc.forces` +
  `window_stats`; coefficients from `forceCoeffs` if present, else from
  reference values).
- `refinement` = cube-root of the cell-count ratio (h ∝ N^(-1/3)) or the
  surface-level step; store both if available.
- **Richardson**: `p = ln(|Cd_fine−Cd_std| / |Cd_std−Cd_fast|) / ln(r)`, with
  `r = h_fast/h_std` (needs ≥3 levels; guard div-by-zero → p=NaN).
- `converged` when `|Cd_fine−Cd_std|/Cd_std < cd_thresh` **and**
  `|Cl_fine−Cl_std|/Cl_std < cl_thresh`.
- Verdicts: `grid-independent` / `marginal` / `not-converged` / `insufficient-data`.

### 1.3 CLI + Studio

- CLI: `read_forces.py <case> --grid` (or `--grid base`), exit 0 converged, 2
  not-converged, 3 insufficient.
- CLI (generate): `setup_case.py <config> --grid-refine [--run]` writes three
  `<base>_cpl<N>` configs that differ **only** in `mesh_params.cells_per_length`
  (physics, near-wall layers and end time pinned to the base) — the valid
  refinement ladder — and optionally generates them.
- Studio: a **"Refinement Study"** control in **Case Setup → General**
  (levels + Generate) creates the three cases and, when connected, offers to
  submit all three (confirmed first). A **"Grid Independence"** panel on the
  Telemetry tab reads them (or `<base>_fast/_standard/_fine`) and reports the
  deltas, the Richardson estimate and the verdict.
  Endpoint: `GET /api/telemetry/grid?base=` (study) and
  `POST /api/case/refinement-study` (generate/submit the ladder).

**Why a separate workflow, not a preset.** A fidelity preset bundles mesh *and*
near-wall layers (`n_layers` 5/8/20) *and* `end_time`. Switching `fast` →
`fine` therefore changes the near-wall model too, so a Cd difference is **not**
pure discretisation error and Richardson `p` is invalid. The refinement study
holds all of that fixed and varies only mesh density, which is what makes the
study rigorous. (`grid_study` still accepts preset variants for a quick
sensitivity check, reporting `mode: fidelity` vs `mode: refinement`.)

### 1.4 Out of scope (v1)

- Automated *running* of the three fidelities (generation + solve) — v1 reads
  whatever cases exist; a later `--run-all` could generate+submit them.
- GCI (grid convergence index) with Fs safety factor — optional refinement; the
  simple % delta + Richardson p is the honest v1.

---

## 2. Benchmark validation

### 2.1 Structure

```
tests/benchmarks/
├─ README.md                 # provenance of each case + reference data source
├─ ahmed_body/
│   ├─ ahmed.stl             # committed ASCII STL (or a generator script)
│   ├─ config_fast.json
│   └─ reference.json        # {"cd": 0.299, "cl": ..., "Re": 4.29e6, "source": "Ahmed 1984"}
├─ windsor_body/ …
└─ test_benchmark_ahmed.py   # regression: run -> read -> assert within tol
```

### 2.2 Which benchmarks (in order)

1. **Ahmed body 25°** — the classic; published Cd ≈ 0.30 at Re 4.29e6. Simple
   geometry (no wings), so it isolates the pipeline.
2. **Windsor body** — another well-documented automotive body (Cd/Cl axis-dependent;
   more nuance).
3. **DrivAer notchback** — most realistic, but heavier; add last.

Start with **Ahmed only**. One credible benchmark already changes the README
claim from "automation suite" to "validated against published data".

### 2.3 What the test asserts

- Generate the case, run the solver (or reuse a committed result), read forces,
  assert `|Cd_sim − Cd_ref|/Cd_ref < 5%`, `|Cl_sim − Cl_ref|/Cl_ref < 10%`.
- Benchmarks are **slow**, so they are **not** in the default `unittest discover`
  run: gate them behind an env flag (`RUN_BENCHMARKS=1`) or a separate
  `python -m unittest discover -s tests/benchmarks` / a `make bench` target. CI
  runs only a **dry** variant (generate + assert the case is produced and the
  reference JSON is well-formed); the full solve runs locally / nightly.

### 2.4 Committed reference data + provenance

Each `reference.json` records the value, Re, reference area, and the **source**
(paper/URL). No folklore numbers: only published data. If a benchmark can't be
run in CI, the README must say "validated on a local reference run against
<published source>", not "CI-validated".

---

## 3. Where it fits the architecture

- `grid_study` is **postproc** (reads existing case artifacts) → `postproc/`
  (imports `core`, `postproc.forces`). No meshing/web imports. Bounded-context
  clean.
- The Studio panel uses a new `/api/telemetry/grid` route + a service helper;
  same remote-bundle pattern as the other telemetry endpoints (grid across
  *multiple* cases needs each case's force data — fetch per case, reuse the
  bundle cache).
- Benchmarks live under `tests/` and use the public CLI/generator — no coupling.

---

## 4. Tests

- `test_gridstudy.py`: synthetic Cd/Cl series → Richardson p, deltas, verdict
  (converged / marginal / not-converged / insufficient); a missing fidelity flags
  insufficient-data; div-by-zero in Richardson is handled.
- `test_benchmark_reference.py` (always on, fast): parse each reference.json,
  assert schema, assert the STL exists and the config validates/generates.
- `test_benchmark_ahmed.py` (gated by `RUN_BENCHMARKS`): full run + tolerance.

---

## 5. Rollout

1. `postproc/gridstudy.py` + `test_gridstudy.py` (pure, no OpenFOAM) — smallest,
   highest-value first.
2. CLI `--grid` + Studio "Grid Independence" panel + `/api/telemetry/grid`.
3. Ahmed body: STL + config + `reference.json` + dry reference test.
4. Ahmed full-run benchmark (gated).
5. README/KB: "Validated against published data" section with the source.

Each step shippable; steps 1–2 need no external data and ship immediately.

---

## 6. What this buys

- **Grid independence** turns "drag = 242 N" into "drag = 242 N ± 1 % mesh-converged".
- **A benchmark** turns "OpenFOAM automation" into "validated OpenFOAM
  automation" — the single biggest credibility step available, and the thing the
  README currently cannot claim.

---

## 7. Honest constraints

- A 5 % Cd tolerance on an FSAE-scale mesh is achievable but not guaranteed; the
  benchmark must use a mesh fine enough to land inside it, and the tolerance is a
  *regression* bound (it detects drift), not a proof of accuracy.
- Ahmed body is 2D-ish and wingless; passing it validates the *pipeline*, not
  the FSAE vehicle physics. Windsor/DrivAer extend coverage.
- The flat-plate y+ estimate remains approximate; benchmarks don't fix that, they
  bound the total error.

---

## 8. Decision summary

| Question | Answer |
| --- | --- |
| Grid independence | read 3 fidelities' forces → deltas + Richardson p + verdict |
| Where | `postproc/gridstudy.py` (+ CLI `--grid` + Studio panel) |
| Benchmark first | Ahmed body 25°, published Cd ≈ 0.30 |
| Benchmark data | committed `reference.json` with a published source |
| CI | dry reference check always; full solve gated by `RUN_BENCHMARKS` |
| Tolerance | Cd within 5 %, Cl within 10 % (regression bound) |
| Rollout | grid study first (no external data), then Ahmed |
