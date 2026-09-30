# RapidFOAM — Knowledge Base

> Comprehensive reverse-engineering / onboarding reference. Every claim below is grounded in
> code on disk at version **1.6.0**. Line numbers are 1-based and refer to the files as they
> exist now. This document supersedes all earlier `docs/` notes (which were removed and
> rewritten from scratch).

---

## 1. What RapidFOAM is

RapidFOAM is a **Python automation suite + browser Web Studio** that turns an **ASCII STL +
small JSON config** into a complete, runnable **OpenFOAM** external-aerodynamics case for
Formula Student / FSAE vehicles, runs it locally or on a SLURM cluster, and post-processes
forces, mesh quality and surface integrity. It is the internal aero pipeline of the
**Rapidamente Formula Student** team (Chulalongkorn University).

- **Target solver:** ESI/OpenCFD **OpenFOAM v2606 only** (the version string is hard-coded in
  `core/foam.py:11`). Other versions are untested (`README.md:37-40`).
- **Core runtime dependencies:** *none* — Python stdlib only.
- **Optional extras:** `[web]` = fastapi, uvicorn, paramiko, python-multipart;
  `[plot]` = matplotlib (`pyproject.toml:48-55`).
- **Front end:** vanilla JS + Three.js r128 + Chart.js 4.4, served by FastAPI; no bundler.
- **License:** MIT. Repository: `https://github.com/tadtapongc/RapidFOAM`.

### 1.1 Quick facts

| Property | Value |
| --- | --- |
| Version | `1.6.0` (`src/rapidfoam/__init__.py:3`) |
| Python | `>=3.9`; CI matrix 3.9–3.13; dev machine observed on 3.14 |
| Build | hatchling; package root `src/rapidfoam` (`pyproject.toml:70-71`) |
| Lint | Ruff configured (`select = ["F"]`, line-length 120, `target-version py39`) — `pyproject.toml:73-81` |
| Python tests | `unittest`; ~357 test methods across 18 files |
| JS tests | `node --test` + JSDOM; 55 tests (`tests/js/app.test.mjs` 47, `viewer.test.mjs` 8) |
| CI | `.github/workflows/ci.yml`: ubuntu+windows × py3.9–3.13; ruff + unittest + build; separate Node-20 JSDOM job |
| External tools | `surfaceCheck`, `surfaceFeatureExtract`, `blockMesh`, `decomposePar`, `snappyHexMesh`, `checkMesh`, `renumberMesh`, `potentialFoam`, `simpleFoam` |

### 1.2 Entry points (`pyproject.toml:57-68`)

| Console script | Target | Role |
| --- | --- | --- |
| `rapidfoam` / `rapidfoam-setup` / `cfd-setup` | `rapidfoam.cli:setup_main` | Generate a case from config |
| `rapidfoam-forces` / `cfd-forces` | `rapidfoam.cli:forces_main` | Forces + `--mesh`/`--surface`/`--yplus`/`--check` |
| `rapidfoam-monitor` / `cfd-monitor` | `rapidfoam.postproc.convergence_monitor:main` | Standalone auto-stop monitor |
| `rapidfoam-studio` / `rapidfoam-web` / `cfd-web` | `rapidfoam.web.app:main` | Web Studio (FastAPI + static UI) |
| `python -m rapidfoam` | `__main__.py` → `cli.setup_main` | Same as CLI setup |
| `python -m rapidfoam.web.app` | `web/app.py:main` | Studio (used by launchers) |

Repo-root shims `setup_case.py` and `read_forces.py` (11 lines each) insert `src/` on
`sys.path` and call `cli.setup_main()` / `cli.forces_main()`.

---

## 2. Repository layout

```
RapidFOAM/
├─ .github/workflows/ci.yml       # CI: ruff + unittest + build; separate JSDOM job
├─ configs/
│  ├─ config.json                 # committed full-example config (the only tracked config)
│  └─ *.json                      # scratch experiments (gitignored)
├─ stl/                           # committed: geometry.stl sample only; rest gitignored
├─ cases/                         # generated OpenFOAM case dirs (gitignored except .gitkeep)
├─ src/rapidfoam/                 # the package (bounded contexts, §4)
├─ tests/                         # Python unit/regression/golden/architecture tests
│  ├─ golden/                     # committed casegen snapshots
│  └─ js/                         # JSDOM front-end tests (npm test)
├─ docs/                          # this document
├─ package.json                   # jsdom devDependency + npm test script
├─ pyproject.toml                 # metadata, optional deps, scripts, ruff config
├─ README.md / CHANGELOG.md / LICENSE
├─ setup_case.py / read_forces.py # 11-line CLI shims
├─ run_app.bat / run_app.sh       # 1-click Studio launchers (create .venv, install [web])
```

Conventions fixed in code (`config.py:25-26`): `STL_DIR = "stl"`, `CASE_DIR = "cases"`. Both
the CLI and the Studio resolve geometry/cases against these, and the web layer relies on the
fixed base for path-traversal checks. The half-wired `stl_dir`/`case_dir` config fields were
removed in v1.5.0.

### 2.1 Package map with line counts

| Context | Files |
| --- | --- |
| root | `cli.py` 355, `config.py` 686, `__init__.py` 4, `__main__.py` 14 |
| `core/` | `axes.py` 70, `faces.py` 40, `fields.py` 33, `foam.py` 46, `caseconfig.py` 262 |
| `geometry/` | `stl.py` 462 |
| `meshing/` | `presets.py` 201, `domain.py` 127, `sizing.py` 136, `grading.py` 174, `layers.py` 233, `params.py` 288, `plan.py` 157, `context.py` 52, `pipeline.py` 26, `writers/{block_mesh.py 107, feature_extract.py 39, snappy.py 284}` |
| `casegen/` | `builder.py` 347, `constants.py` 38, `fields.py` 231, `solver.py` 267, `scripts.py` 418 |
| `runtime/` | `convergence_monitor.py` 187, `remote_cases.py` 229 |
| `postproc/` | `forces.py` 547, `checkmesh.py` 747, `surfacecheck.py` 350, `yplus.py` 157, `residuals.py` 90, `compare.py` 88, `convergence_monitor.py` 165, `plotting.py` 312 |
| `web/` | `app.py` 183, `state.py` 137, `schemas.py` 55, `ssh_client.py` 715, `routers/{case.py 335, cases.py 316, cluster.py 88, config.py 109, stl.py 132, telemetry.py 799}`, `services/{telemetry.py 733, geometry.py 46, downloads.py 48}` |
| `web/static/` | `index.html` 1602, `css/style.css` 2623, `js/app.js` 4261, `js/viewer.js` 1122, `js/charts.js` 636, `js/telemetry2d.js` 330, `js/telemetry3d.js` 159, svg assets |

---

## 3. Architecture overview

### 3.1 Bounded contexts and dependency direction

The dependency direction is enforced at test time by `tests/test_architecture.py` (an AST
import scan). The intended graph:

```
cli / web  ──> casegen ──> meshing ──> geometry ──> core
   │              │           │
   │              └──> runtime │
   └──────────────────> postproc ────────> core
```

| Context | Owns | May depend on | Must not import |
| --- | --- | --- | --- |
| `core/` | axes, faces, fields, FoamFile primitives, config defaults/loader/validation, override-aware `caseconfig` | stdlib | any other `rapidfoam` subpackage |
| `geometry/` | ASCII STL I/O + edge/angle statistics | core | meshing, casegen, postproc, web, cli |
| `meshing/` | presets, domain, sizing, grading, layers, `MeshPlan`, mesh-dict writers, emission pipeline | core, geometry | casegen, postproc, web, cli |
| `casegen/` | builder + constants/fields/solver/scripts writers | core, geometry, meshing, runtime | postproc, web, cli |
| `runtime/` | stand-alone scripts copied into cases / shipped to clusters | stdlib | casegen, meshing, postproc, web |
| `postproc/` | parse OpenFOAM logs/artifacts and judge them | core | meshing, casegen, web |
| `web/` | FastAPI app, routers, services, SSH client, static UI | all of the above | cli |
| `cli.py` | command-line entry points | all of the above | — |

### 3.2 The architecture test (`tests/test_architecture.py`, 93 lines)

- `FORBIDDEN` (lines 21-29) maps each source context to the contexts it must not import:
  `core → {geometry, meshing, postproc, writers, web, cli}`; `geometry → {meshing, postproc,
  writers, web, cli}`; `meshing → {postproc, writers, web, cli}`; `postproc → {meshing,
  writers, web, cli}`; `writers → {postproc, web, cli}`; `casegen → {postproc, web, cli}`;
  `web → {cli}`.
- `KNOWN_DEBT` (exact `(source_module, imported_module)` pairs) and `CONTEXT_DEBT` (coarse
  context pairs) are **both empty sets** (lines 31-35) — the codebase is currently
  boundary-clean. Each would record the phase that removes it.
- `_imported_modules` (lines 48-56) walks the AST collecting only **absolute** imports
  (`node.level == 0`). Relative imports (`from . import x`) are **not** checked — a known
  loophole.
- The single test `test_no_forbidden_context_imports` (line 67) fails on any new violation.

### 3.3 The two seams that define the architecture

1. **Effective config** — `config.effective_config(raw)` (`config.py:297-314`) is the one merge
   of `DEFAULT_CONFIG → user top-level keys → overrides` (comment keys dropped).
   `core.caseconfig.read_case_config()` (`core/caseconfig.py:36-72`) is the one reader for
   everything that reads a case config (telemetry, mesh quality, surface checks, forces CLI);
   it prefers the effective `<case>/case_config.json` over a raw `configs/<case>.json`, and
   runs the file through `effective_config` so overrides are honoured.
2. **Mesh plan** — `meshing.plan.build_mesh_plan()` (`meshing/plan.py:86-123`) derives on a
   private deep copy and returns an immutable `MeshPlan`. `meshing.presets.apply_fidelity_preset`
   (`meshing/presets.py:148-198`) is the only preset resolver. Writers take `MeshPlan` +
   `MeshContext` and never index `cfg`; `meshing.pipeline.emit_mesh_files` (`pipeline.py:14-18`)
   is the single emission entry point.

---

## 4. `core/` — dependency-free primitives

### 4.1 `core/axes.py` (70 lines)

Pure axis/vector math shared by generation and measurement.

- `AXIS_MAP` (11-15): `"+x"→(1,0,0)`… `"-z"→(0,0,-1)` (case-insensitive lookup via
  `parse_axis`).
- `parse_axis(s)` (18) → 3-tuple unit vector; raises `ValueError` for invalid input.
- `axis_index_sign(axis_str)` (28) → `(column_index, sign)`.
- `up_axis_index(cfg)` (37) → index of `outputs.downforce_axis` (up is the opposite of the
  downforce axis).
- `flow_axis_index_sign(cfg)` (49) → `(index, sign)` of `flow.direction`.
- `vec_str(v)` (58) → `(x y z)` with `%.6g`.

### 4.2 `core/faces.py` (40 lines)

- `patch_role(patches, patch_name)` (10) → the role (`inlet`/`outlet`/`ground`/`walls`/
  `symmetry`) for a patch name; `farField` falls back to role `walls`.
- `face_role(cfg, patch_name)` (18) → same via `cfg["patches"]`.
- `face_assignments(cfg)` (23) → the six `-x,+x,-y,+y,-z,+z` faces mapped to patch names.
  When `domain_faces` is present it is authoritative (each value resolved through its role);
  otherwise the default mapping is derived from flow/up/lateral axes: lateral-min → symmetry,
  up-min → ground, downstream → outlet, upstream → inlet, others → walls.

### 4.3 `core/fields.py` (33 lines)

- `velocity_vector(cfg)` (10) → direction × speed.
- `turbulence_values(cfg)` (17) → `(k, omega, nut)` with `k = 1.5(u·I)²`,
  `omega = k/(nut_ratio·nu)`, `nut = nut_ratio·nu`.

### 4.4 `core/foam.py` (46 lines)

- `HEADER` (6) is the OpenFOAM file header with **`Version: v2606`** hard-coded (line 10).
- `FIELD_CLASS` (26) maps field names to OpenFOAM classes; unknown names → `dictionary`.
- `foam_header(obj)` (36) / `bool_str(val)` (41).

### 4.5 `core/caseconfig.py` (262 lines)

The override-aware single source for case config values. Depends only on `rapidfoam.config`.

- `read_case_config(config_path, case_dir)` (36) — precedence `<case_dir>/case_config.json` →
  explicit `config_path` → `./case_config.json`; each run through `effective_config`
  (falls back to the raw mapping if `overrides` is malformed).
- `has_symmetry(cfg)` (75) — explicit `domain_faces` is authoritative; a face value equal to
  the configured symmetry patch name (or containing `"symmetry"`) ⇒ half model. Only when no
  face list exists does it consult `symmetry_plane`/`centerline`. This is what makes a
  full-car config with a stale `symmetry_plane` correctly report as full.
- `has_symmetry_for_case(...)` (99) — config verdict plus a `constant/polyMesh/boundary`
  regex fallback.
- `yplus_target(cfg)` (118) — reads `layers.y_plus_target` or `layers._resolved.y_plus_target`.
- `solver_end_time(cfg)` (141), `stl_files(cfg)` (160), `vehicle_geometry(cfg)` (170),
  `mesh_targets(cfg)` (188), `verdict_bands(cfg)` (210), `surface_policy(cfg)` (232).
- `*_from_case` wrappers (yplus/end_time).

---

## 5. `config.py` (686 lines) — defaults, merge, validation

### 5.1 `DEFAULT_CONFIG` (lines 28-277)

The single universal default tree. Sections: `case_name`, `stl_files`, `flow`
(`velocity 16.67`, `direction "-z"`, `ground True`), `outputs` (`drag_axis "-z"`,
`downforce_axis "-y"`), `fluid` (`nu 1.516e-5`, `rho 1.225`), `turbulence`
(`kOmegaSST`, `intensity 0.005`, `nut_ratio 10`), `patches`, `force_refs`
(`lRef/Aref 1.0`, `CofR [0,0,0]`), `vehicle` (nulls), `parallel` (`n_procs 10`, `scotch`),
`slurm` (`qos cu_hpc`, `partition cpu`, `nodes 1`, `time "auto"`, `mem_per_cpu "2G"`,
`cpus_per_task 1`, `openfoam_module null`, `openfoam_source …v2606/etc/bashrc`), `solver`
(`end_time 800`, `write_interval 400`, `purge_write 2`), `schemes` (bounded limitedLinear /
cellLimited / corrected 0.5), `linear_solvers` (GAMG p, PBiCGStab U), `simple` (SIMPLEC,
`nNonOrthogonalCorrectors 2`, `consistent True`), `relaxation` (U/k/omega 0.7/0.5/0.5, p 0.7),
`wall_functions` (`nutUSpaldingWallFunction`, `kqRWallFunction`, `omegaWallFunction`), `snap`,
`layers` (n_layers 5, expansion 1.2, `relativeSizes True`, first 0.3, min 0.05, y+ null,
`maxFaceThicknessRatio 0.5`, `nSmoothThickness 15`, `ground_layers False`, `ground_n_layers 2`,
`two_pass False`), `feature_extract` (`extractFromSurface`, `includedAngle 140`),
`mesh_quality` (maxNonOrtho 60, maxInternalSkewness 3.5, `verdict_bands`, `relaxed`),
`surface_check` (`enabled True`, `enforce False`, `check_self_intersection True`,
`allow_open False`, `max_illegal_triangles 0`, `max_unconnected_parts 1`), `potential_flow`.

`layers.n_layers`/`expansion_ratio`/`first_layer_thickness` in the defaults are placeholders;
the fidelity preset overwrites them unless the user set them (see §7.1).

### 5.2 Loading and merging

- `deep_merge(base, override)` (284) — recursive; `_`-prefixed keys are skipped.
- `effective_config(raw_user)` (297) — the single merge (defaults + top-level + `overrides`).
- `load_config(path)` (317) — JSON load + `effective_config`.
- `user_set(raw_user, section, key)` (672) — true when a value was explicitly provided at top
  level or under `overrides`. Note: it counts a key present even with a `null` value.

### 5.3 `validate(cfg, project_dir) -> (errors, warnings)` (338-651)

Imports `core.axes`, `core.faces` and `meshing.presets` lazily to avoid a cycle. Highlights:

- Container checks for every dict section + `mesh_params`/`domain`.
- Required: non-empty `stl_files` (unique non-empty stems, no path separators), a valid
  `case_name` (no `.`/`..`/separators), `fidelity ∈ {fast, standard, fine}`.
- Axis string validation and the flow/up/drag axis-independence rules; `domain_faces` must
  assign all six faces with valid roles; inlet and outlet must both be present; ground and
  symmetry may occupy at most one face on their respective axes.
- Numeric bounds for `flow.velocity`, fluid/turbulence/solver/layers/force_refs, mesh_params
  (base cell, `cells_per_length` 5–100, auto_size, feature percentile/cells, max_surface_level,
  auto_feature_angle, crease percentile, feature_angle_ratio, crease_angle_floor,
  resolveFeatureAngle, grading, grading_ratio), `surface_check`, `mesh_quality.verdict_bands`,
  parallel/slurm ints, `CofR`, `surface_level` (two ordered non-negative ints).
- Warnings: two-pass is experimental (quality gate disabled); both ground_plane and
  ground_clearance defined (ground_plane wins); a layer stack below `min_thickness` (snappy
  adds 0 layers); an absolute first layer > ½ the finest surface cell.

---

## 6. `geometry/stl.py` (462 lines) — streaming ASCII STL I/O

- Types: `Triangle` (19), `BBox` (26). Histogram constants (28-37): edge log-histogram spans
  12 decades at 16 bins/decade; 181 dihedral-angle bins.
- **`FeatureAngleStats`** (40-90): `add`/`merge`/`percentile`. Stores the *normal* angle
  between adjacent triangle normals (0° flat join, 90° box corner, →180° sharp fold).
- `_dihedral_angle_deg(n1,n2)` (93) — angle between two outward normals; it is the complement
  of the *included* angle used by `surfaceFeatureExtract` (included = 180 − normal).
- **`EdgeStats`** (110-169): `add`/`add_triangle`/`merge`/`percentile`. Bounded log histogram
  so "small feature" percentiles need no mesh retention; non-positive lengths counted as
  `n_degenerate`; percentile returns the upper bin bound (over-estimates).
- `is_binary_stl(path)` (172) — 84-byte header heuristic checked against `84 + 50·n`.
- `stl_analyze` (200), `stl_analyze_full` (217), `_analyze` (224) — one streaming pass with
  O(1) memory; builds the solid name, triangle count, bbox, `EdgeStats` and
  `FeatureAngleStats`. Shared edges are quantised to 1e-7 m and removed on the second sighting
  so only interior edges yield a dihedral angle and boundary edges never leak memory. Raises
  for binary/malformed/empty files.
- `stl_info` (315) → `(name, n, bbox)`.
- `read_stl` (329) → `(name, [triangles])` (full read, used for the 2D telemetry geometry).
- `write_stl` (390) — ASCII writer with 6-decimal exponent coordinates.
- `stl_bounds` (408).
- `copy_stl(src, dst, name=None, info=None)` (418) — with `name is None` uses `shutil.copy2`;
  otherwise **always streams** and rewrites every `solid`/`endsolid` line, so multi-body CAD
  exports are merged under one solid name (bug #12 fix; never takes the byte-copy fast path
  merely because the requested name equals the first solid).

---

## 7. `meshing/` — derivation and emission

### 7.1 `meshing/presets.py` (201 lines)

`FIDELITY_PRESETS` (11-139) for `fast`/`standard`/`fine`. Key fields per preset:

| Field | fast | standard | fine |
| --- | --- | --- | --- |
| `cells_per_length` | 20 | 30 | 37.5 |
| `surface_level` | [3,4] | [4,5] | [4,5] |
| `edge_level` | 5 | 6 | 7 |
| `n_layers` / `expansion_ratio` | 5 / 1.2 | 8 / 1.2 | 20 / 1.1 |
| `y_plus_target` | 50 | 30 | 1 |
| `ground_layers` | False | False | False |
| `end_time` / `write_interval` | 800/400 | 1500/500 | 2500/500 |
| `maxGlobalCells` | 10M | 20M | 32M |
| `nCellsBetweenLevels` | 2 | 2 | 2 |
| `resolveFeatureAngle` | 35 | 35 | 30 |
| `nSolveIter` / `nFeatureSnapIter` | 100/10 | 200/15 | 300/20 |
| `nLayerIter` / `nRelaxIter_layers` | 50/10 | 50/10 | 50/10 |
| `slurm_time` / `slurm_mem_per_cpu` | 04:00:00/2G | 08:00:00/2G | 14:00:00/4G |
| `feature_cells` / `max_surface_level` | 3/6 | 4/7 | 5/8 |
| `distance_shells` (base-cell multiples) | (0.25,3),(0.80,2) | (0.25,4),(0.80,3) | (0.25,5),(0.75,4),(1.90,3) |
| `near_wake_level` / `far_wake_level` | 2/1 | 3/1 | 4/2 |

`apply_fidelity_preset(cfg, is_set)` (148-198) fills only fields the user did not set
(`is_set(section,key)` predicate, backed by `config.user_set`): `end_time`, `n_layers`,
`expansion_ratio`, `y_plus_target` (unless `first_layer_thickness` set), `nLayerIter`,
`nRelaxIter`, `ground_layers`, `write_interval`, `snap.nSolveIter`,
`snap.nFeatureSnapIter`, `slurm.time`, `slurm.mem_per_cpu`. **Mutates `cfg` in place** and
returns it (the pre-Phase-2 contract; `build_mesh_plan` runs it on a private copy).

### 7.2 `meshing/domain.py` (127 lines)

- `GROUND_EMBED = 0.01` (14) — the ground patch is embedded this far below the configured
  plane so the moving-ground wall reliably cuts the background mesh. Shared with the
  ground-layer clearance guard.
- `compute_domain_box(cfg, bounds)` (17) — wind-tunnel bounds. Padding factors are
  fidelity-aware (fast 3/6/3/3, standard 4/8/4/4, fine 5/10/5/5 for upstream/downstream/
  lateral/top; overridable via `cfg["domain"]["*_factor"]`). Ground sits at
  `ground_plane`/`ground_clearance`/`smin`; symmetry clips the lateral min. Two thin
  special cases handle `+up = ground` and `+lateral = symmetry` (a deliberate half-model
  variant).

### 7.3 `meshing/sizing.py` (136 lines) — opt-in feature heuristics

- `_resolve_feature_sizing(user_mesh, preset, base_cell, surface_level, edge_level, feature_stats, extents)`
  (11) — estimates the small feature as the min of a robust low-percentile edge and the
  thinnest extent (sliver floor = `model_length*1e-4`), computes the required level to place
  `feature_cells` across it, raises `surface_level[1]`/`edge_level` above the preset (never
  coarsens), capped by `max_surface_level` (1–14). Returns provenance.
- `_resolve_feature_angle(user_mesh, preset, angle_stats)` (78) — takes a high percentile
  (`crease_percentile`, default 99) of the normal-angle histogram, multiplies by
  `feature_angle_ratio` (0.75), clamps to [5,80], and **only sharpens** below the preset
  `resolveFeatureAngle`. Also returns `included_angle_recommended = 180 − resolve`.

### 7.4 `meshing/params.py` (288 lines) — `compute_mesh_params`

`compute_mesh_params(cfg, combined_bounds, feature_stats=None, angle_stats=None, *,
explicit_feature_angle=False)` (21):

- Base cell = `explicit base_cell_size` else `max_extent / cells_per_length`.
- `surface_level`/`edge_level` from user or preset, optionally raised by `_resolve_feature_sizing`
  (only when `auto_size` true).
- `distance_levels` from explicit metres, else preset `distance_shells × base_cell`.
- `resolveFeatureAngle` from user/preset, optionally sharpened by `_resolve_feature_angle`
  (only when `auto_feature_angle` true and no explicit resolve angle).
- **Side effect:** when the derived feature angle sharpened detection, writes
  `cfg["feature_extract"]["includedAngle"]` to the recommended value unless an explicit
  `includedAngle` wins (lines 134-139). `build_mesh_plan` absorbs this by copying `cfg`.
- Builds `nearWakeBox` (length `max(2, flow_extent·1.2)`, high level) and `farWakeBox`
  (`max(4, flow_extent·3.5)`); clips to symmetry, snaps to ground/domain upper faces.
- Calls `compute_block_grading` and returns `base_cell_size`, `surface_level`, `edge_level`,
  `distance_levels`, `refinement_regions`, `grading`, `block_cells`, `nCellsBetweenLevels`,
  `maxGlobalCells`/`maxLocalCells`, `minRefinementCells`, `resolveFeatureAngle`,
  `allowFreeStandingZoneFaces`, plus optional `auto_size`/`feature_angle` provenance and any
  user `location_in_mesh`/`locationInMesh`/`maxLoadUnbalance`.

### 7.5 `meshing/grading.py` (174 lines) — opt-in background grading

- `DEFAULT_GRADING_RATIO = 3.0` (16).
- `_graded_axis(extent, base_cell, ratio, fine_at_start)` (19) — solves for the cell count
  that keeps the fine-end cell ≈ `base_cell` while growing by `ratio` to the far end (linear
  search over n, exact and cheap).
- `compute_block_grading(cfg, domain_box, bounds, base_cell)` (65) — modes: `"off"` (default)
  → uniform `(1 1 1)`; `"auto"` → grade the up axis toward ground and/or the lateral axis
  toward symmetry (flow axis stays uniform); `[gx,gy,gz]` → explicit ratios. Returns a
  provenance dict with `mode`, `grading`, `block_cells`, `uniform_cells`, `ratio`, `axes`,
  `cell_reduction`. Recomputing from an already-graded `mesh_params` honours the recorded
  `grading_info.mode`.

### 7.6 `meshing/layers.py` (233 lines) — y+ → absolute thickness

- `estimate_friction_velocity(U, nu, length)` (18) — flat-plate: Prandtl–Schlichting
  `Cf = 0.026·Re^(−1/7)` above Re 5e5, Blasius `1.328/√Re` below; `u_tau = U·√(Cf/2)`.
- `first_layer_height(y_plus, u_tau, nu)` (37) — `δ₁ = 2·y+·ν/u_τ` (first cell *centre* at
  target y+).
- `_apply_ground_layer_policy(cfg, bounds, layers, resolved)` (44) — ground layers are
  opt-in, need clearance ≥ `max(2 mm, 2×δ₁)` (measured against the embedded road surface),
  and are capped by `ground_n_layers` (≤ n_layers); records a `ground_layers_note`.
- `resolve_layers(cfg, bounds, explicit_first_layer=False, explicit_min_thickness=False)`
  (109) — writes `relativeSizes false`, absolute `first_layer_thickness`, `min_thickness`
  and `layers._resolved` provenance (`u_tau`, `y_plus_target/effective`, `clamped`,
  `requested_thickness`, `thickness_max`, `clamp_level`, `clamp_cell_m`, `stack`, `mode`).
  The y+ thickness is clamped to `maxFaceThicknessRatio × (base_cell / 2^level1)` so snappy
  can actually extrude it; the y+ target is then reported unachievable rather than the mesh
  silently degrading. Explicit `first_layer_thickness` wins over `y_plus_target`.

### 7.7 `meshing/plan.py` (157 lines) — the seam

- `LayerSpec` (24, frozen dataclass) and `MeshPlan` (53, frozen) with `base_cell_size` /
  `surface_level` / `edge_level` properties and `as_config()`.
- `build_mesh_plan(cfg, bounds, *, feature_stats, angle_stats, explicit_feature_angle,
  explicit_first_layer, explicit_min_thickness)` (86) — deep-copies `cfg`, calls
  `compute_mesh_params` + `resolve_layers`, returns the plan **without mutating the caller**.
  `cfg` must already have the fidelity preset applied.
- `plan_from_config(cfg)` (126) — build a plan from an already-derived cfg (for the writer
  shims).
- `apply_plan_to_cfg(cfg, plan)` (145) — compatibility shim writing the plan back into `cfg`
  so `case_config.json` and pre-refactor tests keep working.

### 7.8 `meshing/context.py` (52 lines)

`MeshContext` (17, frozen) carries `stl_names`, `patches`, `faces` (resolved),
`snap`, `quality`, `flow_index`, `flow_sign`, `up_index`, `lateral_index`.
`build_mesh_context(cfg, stl_names=None)` (30) resolves them once, keeping writers free of
raw `cfg` indexing.

### 7.9 `meshing/pipeline.py` (26 lines)

`emit_mesh_files(plan, ctx, case_dir)` (14) writes blockMesh, surfaceFeatureExtract and
snappy dicts; `emit_mesh_files_from_config(cfg, case_dir)` (21) is the convenience wrapper.

### 7.10 `meshing/writers/`

- **`block_mesh.py` (107)** — `write_block_mesh_dict` (41): 8 vertices + one hex block with
  `block_cells` and `simpleGrading`; `FACE_MAP` (17) maps directions to hex face strings;
  `_PATCH_TYPES` (23) maps roles to OpenFOAM patch types (`ground → wall`). Missing/invalid
  `block_cells` logs a warning and falls back to uniform counts (55-56).
- **`feature_extract.py` (39)** — one entry per STL with `includedAngle`, `nonManifoldEdges
  yes`, `openEdges yes`, `writeObj no`.
- **`snappy.py` (284)** — `_location_in_mesh` (13) places the probe at the corner maximally
  far from geometry (an explicit `location_in_mesh` wins). `write_snappy_hex_mesh_dict` (56)
  emits geometry (STLs + wake boxes), features (`.eMesh` at edge level), refinementSurfaces
  (`patchInfo wall`), distance `refinementRegions` + wake boxes, the full
  `meshQualityControls` block with a `relaxed` sub-block, snapControls and addLayersControls.
  When `layers.two_pass` is true it **also writes `snappyHexMeshDict_layering`** with a fully
  relaxed quality gate (`maxNonOrtho 180`, `minDeterminant -1e30`, …) and
  `castellatedMesh/snap` false, `addLayers` true.

---

## 8. `casegen/` — case assembly

### 8.1 `casegen/builder.py` (347 lines) — `build_case`

`build_case(cfg_path, project_dir, dry_run=False, reporter=None)` (31), the shared pipeline
called by `cli._do_generate` (`cli.py:96-109`) and the web endpoint
(`web/routers/case.py:184-191`). Raises `CaseGenerationError` (19) instead of exiting.

Steps:
1. `load_config` + raw JSON read; `_is_set` closure over `config.user_set`.
2. `validate` → report warnings, raise on errors.
3. Resolve STL stems via `find_stl`; set `cfg["stl_names"]`, `cfg["domain_faces"] =
   face_assignments(cfg)`.
4. Stream every STL with `stl_analyze_full` → merged `EdgeStats`/`FeatureAngleStats` and
   combined bounds.
5. Auto-derive `domain_box` if `"auto"`/missing; reject non-positive dimensions.
6. STL-vs-domain clearance report (penetration, ground plane, close-to-boundary).
7. `apply_fidelity_preset(cfg, _is_set)` then
   `build_mesh_plan(cfg, bounds, feature_stats, angle_stats, explicit_feature_angle,
   explicit_first_layer, explicit_min_thickness)` then `apply_plan_to_cfg(cfg, plan)`.
8. Print derived geometry/domain/mesh/layers/auto-size/feature-angle/grading sections.
9. Dry run returns `None` here.
10. Create `0/`, `constant/triSurface/`, `system/`; `copy_stl` each STL under its stem name.
11. Emit all files: `emit_mesh_files`, `write_control_dict`, `write_fv_schemes`,
    `write_fv_solution`, `write_decompose_par_dict`, `write_constant`, `write_fields`,
    `write_scripts`.
12. Back up `0/` → `0.orig/`; write `case_config.json` (post-mutation snapshot incl.
    `_resolved`).

### 8.2 Emitted case directory

```
cases/<case_name>/
├─ 0/                 U, p, k, omega, nut
├─ 0.orig/            backup of 0/ (restored by Allclean)
├─ constant/
│  ├─ triSurface/*.stl
│  ├─ transportProperties, turbulenceProperties
│  └─ polyMesh/        (created by blockMesh/snappy at run time)
├─ system/
│  ├─ blockMeshDict, surfaceFeatureExtractDict, snappyHexMeshDict
│  ├─ snappyHexMeshDict_layering   (only when layers.two_pass)
│  ├─ controlDict, fvSchemes, fvSolution, decomposeParDict
├─ Allrun, Allrun.parallel, Allclean, run.sh
├─ convergence_monitor.py
└─ case_config.json
```

### 8.3 `casegen/constants.py` (38) and `casegen/fields.py` (231)

- `write_constant` (14): `transportProperties` (Newtonian, `nu`), `turbulenceProperties`
  (`RAS`, model, `turbulence on`, `printCoeffs on`).
- `write_fields` (25): writes `U, p, k, omega, nut` with per-patch BCs. The wing/body patch is
  a regex over all STL stems (`_wing_regex`, 18). `moving_ground` selects a fixed-value moving
  wall vs slip; ground turbulence uses wall functions or `zeroGradient`/`calculated`. Ground
  and symmetry patches are emitted only when actually assigned (`_opt_patch`, 62).

### 8.4 `casegen/solver.py` (267)

- `write_control_dict` (22): `simpleFoam`, `startFrom latestTime`, `stopAt endTime`, `endTime`,
  `writeInterval`/`purgeWrite`, run-time modifiable; function objects `forces`, `forceCoeffs`
  (with `liftDir` from downforce axis, `dragDir` from drag axis, `pitchAxis = lift × drag`),
  optional per-patch `forces_<part>` for multi-STL geometry, `residuals` (solverInfo), `yPlus`.
- `write_fv_schemes` (145), `write_fv_solution` (184), `write_decompose_par_dict` (258).

### 8.5 `casegen/scripts.py` (418) — execution scripts

Constants `MONITOR_CLEANUP` (14, `stop_monitor` trap on EXIT/INT/TERM) and `STOPAT_RESTORE`
(29). `_convergence_monitor_script(cfg)` (44) copies `runtime/convergence_monitor.py` verbatim
**with a generated `DRAG_IDX/DRAG_SIGN/DF_IDX/DF_SIGN` header** — no AST rewriting.

`write_scripts` (67) builds the surfaceCheck gate (report-only by default; on `enforce` it
greps for not-closed / self-intersecting / illegal triangles and exits 1, exempting symmetry
and `allow_open`) and writes:

- **`Allrun.parallel`** — sources `RunFunctions`; sets `OMPI_MCA_*` to `/dev/shm` or `/tmp`;
  surfaceCheck; `surfaceFeatureExtract → blockMesh → decomposePar → snappyHexMesh -overwrite
  -noFunctionObjects` (+ optional `-s layering`); `checkMesh`; `reconstructParMesh -constant`;
  `renumberMesh`; solver `decomposePar`; non-fatal `potentialFoam`; background monitor;
  `simpleFoam`; `stop_monitor`; `reconstructPar` (preserving processors on failure).
- **`Allrun`** — serial equivalent (no decompose/reconstruct mesh).
- **`Allclean`** — `cleanCase`, remove processors/mesh/eMesh/logs, restore `stopAt`, restore
  `0` from `0.orig`.
- **`run.sh`** — SLURM batch script: `#SBATCH` header (job-name/qos/partition/nodes/ntasks/
  cpus-per-task/mem-per-cpu/time/output), `module purge` + module loads + source bashrc,
  OpenMPI tempdir config, a `cleanup` trap that attempts `reconstructPar -latestTime` if the
  solver phase was reached, then the full mesh/solve pipeline via `mpirun`.

---

## 9. `runtime/` — reproducible assets copied to cases/clusters

### 9.1 `runtime/convergence_monitor.py` (187) — canonical auto-stop

Dependency-free, annotation-free (runs on cluster Python 3.6+). `THRESHOLD 0.5%`,
`WINDOW 200`, `MIN_ITERS 300`, `INTERVAL 10 s`. `find_force_files` (34), `read_forces` (56,
restart-segment aware, ESI vs classic layout), `check_convergence` (103), `trigger_stop` (126,
rewrites `stopAt writeNow;`), `main` (141). The axis constants are injected by
`casegen/scripts.py` ahead of the file.

### 9.2 `runtime/remote_cases.py` (229)

Uploaded base64-encoded and executed on the cluster by `ssh_client.list_remote_cases_detailed`.
Scans `<repo>/cases`, reads each case's config (case_config.json then configs/<name>.json),
detects symmetry (domain_faces, else default half-model, else polyMesh boundary), parses
force.dat with its own `parse_axis` (returns `(idx, sign)`), infers status
(Generated/Meshed/Meshing/Solving/Converged/Completed/Failed), and prints a JSON array between
`__CASE_JSON_START__`/`__CASE_JSON_END__`. Tested for parity against the canonical reader in
`tests/test_remote_cases.py`.

---

## 10. `postproc/` — measurement (never runs OpenFOAM)

### 10.1 `postproc/forces.py` (547)

- `load_axis_config(config_path, case_dir)` (16) → `(drag_idx, drag_sign, df_idx, df_sign,
  drag_axis, df_axis)`. Reads `config_path` (raw) or `case_config.json`. *(Note: this helper
  uses raw `json.load`, not the override-aware `caseconfig` reader — an `overrides.outputs`
  block would not be applied here.)*
- `_load_case_configs` (51), `is_symmetry_case` (77) — delegates to `caseconfig.has_symmetry`;
  if no config has explicit `domain_faces`, a case is a half-model by generator default;
  final fallback is the polyMesh boundary regex.
- `_dir_time` (119), `force_layout_from_header` (126) — True for ESI `total_*`, False for the
  classic pressure+viscous layout, None if unknown.
- `find_force_files` (143), `read_forces` (174) — parse `force.dat`, de-duplicate/override
  restarted segments, sum pressure+viscous for the classic layout.
- Column tables: `FORCE_COMPONENT_COLUMNS` (247), `CLASSIC_COMPONENT_COLUMNS` (255),
  `COEFFICIENT_COLUMNS` (263).
- `parse_tabular_dat(segments)` (271) — generic tabular parser returning
  `(times, rows, header_names)`; handles multiline `# Time` headers, restart overrides.
- `normalize_component_columns` (332) / `normalize_coefficient_columns` (357) — map to
  canonical total/pressure/viscous keys.
- `find_moment_files` (372), `find_coefficient_files` (398), `window_stats` (429).
- `check_convergence(drags, downforces, window=200, threshold=0.5)` (451) — trailing-window
  relative stdev (%), requires ≥20 samples.
- `print_summary` (495) — CLI table; doubles forces for symmetry cases.

### 10.2 `postproc/checkmesh.py` (747)

- Regexes (23-70) for cells/points/faces, aspect, non-orthogonality, skewness, volumes,
  concave faces/cells, warped faces, flatness, determinant, interpolation weight, volume
  ratio, failed checks, "Mesh OK", cell-type breakdown, boundary-patch rows.
- `QUALITY_TIERS` (83) — good/caution bands independent of snappy's give-up limits:
  non-ortho (60/70), skewness (2/4), aspect (50/100), determinant (0.05/0.001), interp weight
  (0.1/0.01), volume ratio (0.05/0.01), concave (0/0). `LAYER_COVERAGE_MIN 0.9` (106),
  `LAYER_THICKNESS_MIN 0.7` (113).
- `resolve_bands` (116), `classify_value` (141), `parse_checkmesh` (216),
  `parse_boundary_patches` (306), `_parse_layer_row` (348), `parse_layer_coverage` (387).
- `read_checkmesh` (419) / `read_layer_coverage` (431) — merge logs, later wins.
- `check_mesh_quality(stats, layers, target_layers, *, max_non_ortho, max_skewness,
  max_aspect_ratio, bands, has_symmetry)` (449) — per-metric pass/fail + good/usable/marginal
  level; layer coverage + thickness warnings; open-patch detection suppressed for symmetry
  cases; verdict Good/Usable/Marginal/Bad (hard failures force Bad).
- `mesh_quality_report(case_dir, config_path)` (729) — end-to-end.

### 10.3 `postproc/surfacecheck.py` (350)

Parses `log.surfaceCheck`: triangles/vertices/bbox, region table, illegal triangles,
quality/edge min-max, nearby points, closure, unconnected parts, normal zones, self
intersection. `check_surface` (222) → Good/Concern/Bad with `has_symmetry`/`allow_open`
exempting the closure requirement. `surface_check_report` (338) is the end-to-end wrapper.

### 10.4 `postproc/yplus.py` (157)

`find_yplus_files` (18), `read_yplus` (44, latest time wins), `check_yplus_target` (77) —
regime-aware: a wall-function target (y+ ≥ 30) accepts the `wall_function_tiers` band
(30–300); a wall-resolved target (≤ 5) accepts only `wall_resolved_max`; ±50% tolerance for
"on_target".

### 10.5 Others

- `residuals.py` (90): `find_residual_files`, `read_residuals` (restart-aware).
- `compare.py` (88): `compare_cases` — multi-case table with symmetry ×2 naming.
- `convergence_monitor.py` (165): the CLI mirror of the runtime monitor (`rapidfoam-monitor`),
  reusing `postproc.forces`.
- `plotting.py` (312): `plot_forces` (static PNG) and `live_monitor` (matplotlib animation
  with 4 pages: Residuals/Drag/Downforce/Summary).

---

## 11. Web layer

### 11.1 `web/app.py` (183)

`create_app()` (37) builds the FastAPI app: localhost-only CORS regex, six routers
(cluster, config, stl, case, cases, telemetry), static mount at `/` serving
`web/static/`. `main()` (61) is the CLI launcher: UTF-8 reconfig, port collision handling
(reuse an existing RapidFOAM Studio, or `--restart` kills the old PID via `netstat`+`taskkill`
on Windows / `lsof`+SIGKILL on POSIX, else next free port), prints a banner from the saved
cluster config, opens the browser. `app` is module-level.

### 11.2 `web/state.py` (137)

Process-wide shared state: the `ssh_client` singleton (23), `CREDENTIALS_FILE =
~/.rapidfoam_cluster.json` with one-time migration from `~/.cfd_gen_cluster.json` (24-31),
`PROJECT_ROOT = Path.cwd()` (32), `CASE_NAME_REGEX = ^[A-Za-z0-9_-]+$`,
`JOB_ID_REGEX = ^[0-9]+$`, `ALLOWED_LOG_TYPES` (36-45), and the download-progress registry
with `_download_progress_lock` (48-71). `get_saved_cluster_config` (73),
`_restrict_file_access` (89, chmod 600 + Windows `icacls`), `save_cluster_config` (110).

### 11.3 `web/schemas.py` (55)

Pydantic request models: `SSHConnectRequest`, `JobSubmitRequest`, `JobCancelRequest`,
`CaseDownloadRequest`, `DomainBoxRequest`, `GenerateCaseRequest` (where
`generate_locally` defaults **False** — the bug #1 hardening).

### 11.4 Routers

- **`routers/cluster.py` (88)** — `GET /api/cluster/saved-config` (password redacted),
  `POST /api/cluster/connect` (reuses stored password for matching host/user; saves creds),
  `GET /api/cluster/status`, `POST /api/cluster/disconnect`.
- **`routers/config.py` (109)** — `GET /api/config/schema-defaults` serves `DEFAULT_CONFIG`
  plus a derived preset table covering every preset field (mesh, snap, layers, solver, slurm);
  `GET /api/config/templates`; `GET /api/config/load-file` (traversal-safe, returns
  `raw_config` + `merged_config`).
- **`routers/stl.py` (132)** — `GET /api/stl/list` (deduped, bbox + `is_likely_mm`),
  `GET /api/stl/file/{filename}` (traversal-safe octet-stream), `GET /api/stl/check-exists`,
  `POST /api/stl/upload` (multipart, `override_name`, re-inspects bounds).
- **`routers/case.py` (335)** — `POST /api/geometry/domain-box` (streams STL stats, computes
  domain + `auto_symmetry_plane` + `layer_preview`); `GET /api/case/check-exists`;
  `POST /api/case/generate-and-submit` (validate → verify all STLs exist → save config only
  when an action needs it → optional `asyncio.to_thread(build_case)` → optional cluster
  upload of STLs/config, remote `setup_case.py`, `sbatch`); `POST /api/case/submit`;
  `POST /api/case/cancel`; `POST /api/case/download` (atomic reservation under
  `_download_progress_lock`, background executor); `GET /api/case/download/active`;
  `GET /api/case/download/progress`.
- **`routers/cases.py` (316)** — `GET /api/cases` merges local + remote case inventory
  (local status inference from force data + log tails; SLURM state cross-reference; zombie
  Solving/Meshing cleanup; remote-newer precedence; never regresses numeric progress).
  `DELETE /api/cases/{case_name}` deletes locally (traversal-checked) and remotely.
  **The remote-merge block (lines 177-286) is the remaining "intricate, lightly-tested"
  hotspot** — see §17.
- **`routers/telemetry.py` (799)** — `GET /api/telemetry/forces` (axis mapping, symmetry
  projection, post-run reference overrides via query params, coefficient recomputation,
  component breakdown, aero balance, downsampling with `max_points`); `GET /api/telemetry/export`
  (CSV/JSON, full history via `max_points=0`); `GET /api/telemetry/residuals`;
  `GET /api/telemetry/solver` (continuity, linear iters, iteration rate, ETA);
  `GET /api/telemetry/mesh` (checkMesh + layer coverage + y+ cross-reference);
  `GET /api/telemetry/surface`; `GET /api/telemetry/logs` (whitelisted `log_type`, ≤2000 lines).

### 11.5 Services

- **`services/telemetry.py` (733)** — no FastAPI imports. Residual parsing
  (`parse_residuals_from_log` 35, `parse_solver_info_text` 76,
  `parse_solver_diagnostics_from_log` 124), y+ (`_read_yplus_texts` 177, `_summarise_yplus`
  204 delegating to `postproc.yplus`), the shared remote bundler/cache
  (`_read_remote_telemetry` 300, TTL 2.5 s, keyed by host+repo+case), downsampling helpers,
  reference/vehicle loaders, aero balance (`_compute_aero_balance` 434), symmetry projections
  (`_project_*_for_symmetry` 534/552/574, `_SYMMETRY_ZERO_COEFFICIENTS` 571), moment shift
  (`_shift_moment_columns` 588, parallel-axis), coefficient computation
  (`_compute_coefficients` 632), `_read_text_tail_lines` (701), `read_file_tail` (723).
- **`services/geometry.py` (46)** — `_local_stl_exists` (14), `layer_preview` (19) which
  deep-copies, applies the preset, builds a plan and returns `layer_spec.resolved` augmented
  with `auto_size`/`feature_angle`/`surface_level`/`edge_level`.
- **`services/downloads.py` (48)** — `_run_download` background worker around
  `ssh_client.download_directory` with progress updates.

### 11.6 `web/ssh_client.py` (715)

`ClusterSSHClient`, thread-safe via `@_synchronized` (31, RLock) — except
`download_directory` and `cancel_job`, which deliberately release the lock for long
operations.

- Bundle framing: `FILE_BEGIN`/`FILE_END` markers (47-48), `_parse_marked_bundle` (51),
  `read_remote_bundle(specs, timeout)` (423) — batches many glob+tail reads into one SSH
  command. **Still uses fixed literal markers** (roadmap hardening item).
- `connect` (90) tries in-memory key data → `key_path` (RSA/Ed25519/ECDSA) → password →
  default keys; default remote repo `/work/home/<user>/Rapidamente/cfd/RapidFOAM` (158).
- `test_connection` (200), `get_sftp` (179), `run_command` (188),
  `ensure_remote_dir` (232), `upload_file` (251), `upload_text` (264),
  `_remote_dir_size` (274).
- `download_directory` (292) — streams `tar -C <dir> -cf - .`, extracts into a hidden
  sibling staging dir, guards path traversal, and publishes with `os.replace` only on a clean
  exit so a mid-stream disconnect never leaves a partial case.
- `read_remote_text` (402) with optional tail.
- `get_slurm_queue` (463), `remote_file_exists` (495), `is_case_running` (507, exact/`cfd_`
  prefix/scheduler-truncated ≥8-char match only), `submit_job` (528),
  `_job_state_and_name` (555), `_request_graceful_stop` (567, `stopAt writeNow` + poll),
  `_wait_for_job_exit` (589), `cancel_job` (603, graceful then `scancel`),
  `list_remote_cases` (640), `list_remote_cases_detailed` (668, base64-uploads
  `runtime/remote_cases.py` and parses the JSON markers, with a basic fallback).

---

## 12. Front-end Studio (`web/static/`)

### 12.1 `index.html` (1602)

- Head loads Google Fonts (Inter, JetBrains Mono, Space Grotesk, Barlow Semi Condensed) and
  **CDN scripts** (no SRI): Three.js r128, OrbitControls, STLLoader, Chart.js 4.4.0.
- Four tab panes: `#config-tab` (Case Setup), `#cluster-tab`, `#telemetry-tab`,
  `#cases-tab` (Archive), driven by `.nav-tab` buttons.
- Config tab: left `#viewer-panel` (STL drop/upload, viewer-canvas container, view toggles,
  framing/angle buttons, domain badge, STL chips, scale warning); right `.config-panel` with
  a config-file picker and five sub-tabs (General, Flow, Domain, Slurm, Expert overrides),
  a `.config-actions-bar` (Validate / Save / Generate Locally / Submit) and a JSON drawer
  (`#raw-json-editor`).
- Telemetry tab: case picker, refresh/polling toggle, export, convergence pill, error banner,
  six KPI cards, reference editor, 2D aero view (`#telemetry-2d-canvas`), collapsible 3D aero
  view, four charts (forces, coefficients, components, residuals), analysis tables, solver
  health, surface-integrity panel, mesh-quality panel, log console.
- Archive tab: stat cards, search/filters, case table.
- Modals: SSH connect, confirmation/rename, download progress overlay, toast container.
- Scripts load at the bottom in order: `viewer.js`, `charts.js`, `telemetry2d.js`,
  `telemetry3d.js`, `app.js`.

### 12.2 `css/style.css` (2623)

Single dark theme driven by `:root` design tokens (surfaces, borders, accents, semantics,
text, fonts, radii). Layouts: navbar, two-column config layout (`60% 40%`), grid layouts,
KPI grids, charts grid, archive dashboard. Responsive at 1500/1100/900/700 px. Notable
components: glass viewer overlays with `backdrop-filter`, STL chips, form system, override
sections, range sliders, switches, fidelity cards, JSON drawer, convergence pill, KPI cards,
solver/mesh metric classes, 2D/3D telemetry panels, console box, modals, toasts, archive
status/spec badges, download progress rows, help popover.

### 12.3 `js/app.js` (4261) — the `CFDApp` god class

`TELEMETRY_HELP` (5-118) is authored help HTML. The constructor (121-236) initialises state
(`telemetryRequestId`, `telemetryInFlight`, `telemetryPollingActive`, 2D/3D handles, archive
state, `downloadStates`, `fidelityPresets`) and the `activeConfig` default (155-233),
including inert `_`-prefixed example/default keys.

Method groups (line ranges): construction/lifecycle 121-306 (`init` awaits backend loads,
sets a 5 s polling interval, binds `beforeunload → destroy`); navigation/sub-tabs 308-355;
config-form binding 357-476; form rendering `updateVisualFormFromConfig` 478-704
(`cloneConfig` 706); config building `buildConfigFromVisualForm` 717-1018; JSON drawer
1020-1101; STL upload/domain/overrides 1106-1724 (includes `updateOverridePlaceholders`,
`loadFidelityPresets`, layer/auto-size previews, `autoSymmetryPlaneCenter`,
`updateDomainBoxVisualization`, `handleSTLFiles`, `renderActiveSTLChips`); template loading
1726-1770; validate/generate/save/confirm 1772-2085; cluster SSH UI 2087-2251; SLURM queue
2253-2328; download tracking 2330-2472; telemetry UI binding 2474-2564; help popover
2566-2648; telemetry case-switch/reset 2650-2793 (`beginTelemetryCaseSwitch`,
`clearTelemetryView`, `showTelemetryError`); 2D aero 2795-2919; 3D aero 2921-3099;
`pollTelemetry` 3101-3321; renderers 3323-3755 (solver health, surface, mesh, KPIs, analysis,
reference placeholders); ref/balance overrides + export 3757-3883; cases archive 3885-4150;
helpers 4152-4234 (`escapeHtml` 4155, `showToast` 4220); lifecycle `destroy` 4237-4255 and
the `DOMContentLoaded` bootstrap 4259-4261.

Key behaviours:

- **Form → config** (`buildConfigFromVisualForm`) starts from a clone of `activeConfig`, so
  unknown/future keys and `_`-comment keys survive; it only assigns/deletes owned keys. A
  blank symmetry field **deletes** `symmetry_plane` (no coercion to 0.0). Ground
  clearance/plane are mutually exclusive. Overrides are assigned only when populated and
  `cfg.overrides` is deleted if empty. Guarded by `isSyncingFromJson`.
- **Config → form** (`updateVisualFormFromConfig`) renders every control, infers layer mode
  from the config, reconciles STL files against the server list, then schedules a preview.
- **Telemetry polling** (`pollTelemetry`): increments `telemetryRequestId`, sets
  `telemetryInFlight`, fetches forces → residuals → solver → mesh → surface → log tail, each
  guarded against staleness, and clears the in-flight flag in `finally` only when the request
  id still matches. `beginTelemetryCaseSwitch` clears the flag and invalidates the id.
- **XSS**: all untrusted values (filenames, case names, SLURM fields, patch/region names)
  pass through `escapeHtml` before `innerHTML`; attributes use `dataset`. Help popover HTML
  and `data.run_command` are app-authored and intentionally not escaped.
- `createSTLViewer` returns `null` for an unavailable viewer so `if (this.viewer)` guards are
  honest.

### 12.4 `js/viewer.js` (1122) — `STLViewer`

Three.js r128 scene with OrbitControls, per-STL meshes (`stlMeshes` Map, palette colors),
ground grid, flow arrow, origin axes, gizmo, domain wireframe (inlet/outlet planes + sprites,
ground plane, optional symmetry plane, badge). Constructor sets `available=false` and only
flips it true after a successful `init()`. `recomputeOverallBoundingBox` scales camera far
(`max(2000, radius·20)`) and orbit `maxDistance` (`max(1500, radius·10)`) from the loaded
bounds and flags mm-scale geometry (`> 20 m` heuristic). `updateDomainBox(null, null)` clears
domain state. `disposeObject`/`disposeGroup` release geometries/materials/textures; `dispose()`
cancels RAF, removes listeners, disconnects the ResizeObserver, disposes the renderer.

### 12.5 `js/charts.js` (636) — `TelemetryCharts`

Chart.js wrapper with `CONVERGENCE_THRESHOLD_PCT = 0.5` and a custom `convergenceBandPlugin`
that paints translucent ±0.5% bands on the force chart. Charts: forces (raw+smoothed drag and
downforce, dual axes), residuals (p/Ux/Uy/Uz/k/omega, log y), coefficients
(Cd/Cl/Cs/CmPitch/CmRoll/CmYaw), components (stacked pressure+viscous bars), solver health
(continuity log + linear iters). `computeRollingAverage` (48) is an O(n) slider.

### 12.6 `js/telemetry2d.js` (330) — `Aero2DView`

Canvas side-view free body. Projects the STL silhouette onto the flow–vertical plane and
caches it offscreen (rebuilt only when geometry/viewport/axes change); draws ground reference,
CoP marker from aero balance, flow arrow, drag/downforce arrows from CofR, and a pitch arc.

### 12.7 `js/telemetry3d.js` (159) — `AeroVectorLayer`

Adds force/moment arrows (resultant + drag/downforce/side components) and a CofR marker to a
Three.js scene; `clear()` disposes geometries/materials; labels are canvas sprites.

---

## 13. Domain concepts & glossary

| Concept | Definition | Reference |
| --- | --- | --- |
| **Case** | Self-contained OpenFOAM dir `cases/<name>/` with `0/`, `constant/`, `system/`, scripts, monitor, `case_config.json`, `0.orig/` | `casegen/builder.py:278-339` |
| **Fidelity preset** | `fast`/`standard`/`fine` binding refinement, layers, y+, solver iters, SLURM time/mem | `meshing/presets.py:11-139` |
| **Effective config** | raw → defaults → overrides | `config.py:297` |
| **MeshPlan / LayerSpec** | Immutable derivation result consumed by writers/UI/tests; `layer_spec.resolved` carries provenance | `meshing/plan.py:24-123` |
| **Domain box** | Wind-tunnel bounds `{min,max}` or `"auto"` (fidelity-aware upstream/downstream/lateral/top) | `meshing/domain.py:17` |
| **Boundary roles** | inlet/outlet/ground/walls(farField)/symmetry mapped to six faces | `core/faces.py:23` |
| **Symmetry / half model** | A `domain_faces` symmetry assignment; forces double in-plane, normal cancels | `core/caseconfig.py:75`; `web/services/telemetry.py:534` |
| **GROUND_EMBED** | 0.01 m below the configured ground plane | `meshing/domain.py:14` |
| **Distance shells** | Refinement levels by distance from the surface (base-cell multiples in presets) | `meshing/params.py:101-109` |
| **Wake boxes** | nearWakeBox (high-res) + farWakeBox (low-cost downstream transport) | `meshing/params.py:186-256` |
| **Two-pass layering** | snappy pass 1 castellate+snap, pass 2 add layers with a relaxed gate | `meshing/writers/snappy.py:279-284` |
| **Auto-size** | Feature-based surface/edge refinement (opt-in) | `meshing/sizing.py:11` |
| **Auto-feature-angle** | Geometry-derived `resolveFeatureAngle` (opt-in) | `meshing/sizing.py:78` |
| **Grading** | Opt-in background `simpleGrading` toward ground/symmetry | `meshing/grading.py:65` |
| **y+ chain** | `y_plus_target → u_tau → δ₁ = 2·y+·ν/u_τ`, clamped by `maxFaceThicknessRatio` | `meshing/layers.py:109` |
| **Convergence band** | ±0.5% relative stdev over the last 200 iters, after ≥300 | `postproc/forces.py:451` |
| **Force layouts** | ESI tabular `total/pressure/viscous` (9 cols) vs classic pressure+viscous force+moment (12 cols) | `postproc/forces.py:126-266` |

---

## 14. End-to-end flows

### 14.1 Case generation (CLI and Web)

`setup_case.py configs/config.json` → `cli.setup_main` → `cli._do_generate`
(`cli.py:96`) → `casegen.builder.build_case` (`builder.py:31`). The Web path
`POST /api/case/generate-and-submit` → `routers/case.py:137` → `build_case` in a thread.
Both share the exact same pipeline (§8.1).

### 14.2 Execution pipeline (`Allrun.parallel` / `run.sh`)

surfaceCheck (report-only unless enforced) → surfaceFeatureExtract → blockMesh → decomposePar
→ snappyHexMesh `-overwrite` (+ optional layering pass) → checkMesh → reconstructParMesh →
renumberMesh → solver decomposePar → potentialFoam (non-fatal) → background convergence
monitor → simpleFoam → reconstructPar. The monitor writes `stopAt writeNow;` once drag and
downforce vary < ±0.5% over 200 iters after 300.

### 14.3 Telemetry (Studio)

The front end polls `/api/telemetry/{forces,residuals,solver,mesh,surface,logs}` every 5 s.
On the server, each telemetry endpoint uses one cached remote bundle (`_read_remote_telemetry`,
TTL 2.5 s) or local logs; forces are axis-mapped, symmetry-projected, optionally recomputed
with post-run reference overrides, and downsampled to ≤400 points (export uses full history).

### 14.4 Cluster flow

SSH connect → upload STLs + config → optional remote `setup_case.py` → `sbatch run.sh` →
poll `squeue` (`GET /api/cluster/status`) → download the case via a streamed tar with atomic
publish → optional graceful cancel (`stopAt writeNow` then `scancel`).

---

## 15. Test suite, CI and tooling

### 15.1 Python tests (~357 test methods, 18 files)

| File | Focus |
| --- | --- |
| `test_architecture.py` | AST import-boundary rules (the only enforcement) |
| `test_auto_sizing.py` | `EdgeStats`, `FeatureAngleStats`, auto-size/feature-angle derivation + validation |
| `test_casegen_builder.py` | `build_case` end-to-end, dry-run, typed errors, two-pass, surface-enforce |
| `test_casegen_golden.py` | Golden snapshot contract (see §15.2) |
| `test_checkmesh.py` | checkMesh parsing/verdicts, layer coverage, bands, symmetry suppression |
| `test_grading.py` | `compute_block_grading`, dict emission, validation |
| `test_layers.py` | friction velocity, first-layer height, resolve/clamp, ground-layer guard, two-pass |
| `test_mesh_plan.py` | plan purity/determinism/legacy equivalence/round-trip |
| `test_mesh_preview_equivalence.py` | `layer_preview` == generated `case_config.json` across fidelities/overrides |
| `test_preset_coverage.py` | Preset SLURM time/mem reach `run.sh` for minimal + shipped config (fine) |
| `test_regressions.py` | Broad behavioural net (37 tests; scripts, restart, symmetry, copy_stl, plotting…) |
| `test_remote_cases.py` | `runtime/remote_cases.py` parity with the canonical force reader |
| `test_shell_scripts.py` | Real bash execution of generated scripts (Linux/POSIX only) |
| `test_studio_config_sync.py` | Symmetry detectors agree; full-car not coerced |
| `test_surfacecheck.py` | surfaceCheck parsing/verdicts/policy |
| `test_telemetry_config.py` | Overrides honoured by telemetry; preset y+ visible; export full history |
| `test_web_api.py` | ~2007 lines, 83 tests over the whole HTTP surface + SSH client |
| `test_yplus.py` | y+ file discovery/reading/regime-aware verdict |

`test_web_api.py` mocks the SSH layer by patching `ClusterSSHClient` methods and the
`ssh_client` singleton (not Paramiko itself); async routes are driven with `asyncio.run`.
`test_shell_scripts.py` is guarded by `@unittest.skipUnless(os.name == "posix" and
shutil.which("bash"))` (line 20) and synthesises a fake OpenFOAM toolchain.

### 15.2 Golden tests (`tests/test_casegen_golden.py`, 72 lines; `tests/golden/`)

`setUp` writes a single-triangle `stl/body.stl` and a `standard`-fidelity config
(`golden_test`, velocity 20, `-z`, ground), runs `_do_generate`, and compares CRLF-normalised
text of `blockMeshDict`, `snappyHexMeshDict`, `surfaceFeatureExtractDict`, `Allrun.parallel`,
`run.sh`, plus parsed `case_config.json`, against committed `*.golden` files. There is **no
path-scrubbing and no update script** — the artifacts are inherently path-free (temp dir;
relative names), and a diff is a manual behaviour-change decision.

### 15.3 JS tests (`tests/js/`)

`harness.mjs` (160): builds a JSDOM, loads sources via `window.eval` with an exporter that
republishes classes, installs THREE/Chart stubs, and can build a bare `CFDApp`/`STLViewer`
prototype instance with seeded state (bypassing the I/O-heavy constructor).
`app.test.mjs` (903) covers XSS escaping, solver-health badges, telemetry-switch/polling,
viewer lifecycle, auto-size/layer previews, form→JSON merge (preserving comment/unknown keys),
fidelity cards, mesh/surface panels. `viewer.test.mjs` (232) covers flow-arrow disposal,
`updateDomainBox(null,null)`, mm-scale far-plane scaling, unavailable-viewer flags and
`dispose`.

### 15.4 CI (`.github/workflows/ci.yml`, 62 lines)

- `test` job: ubuntu + windows × py3.9–3.13; `pip install -e ".[web,plot]"` + build;
  `ruff check src tests`; `python -m unittest discover tests`; `python -m build`.
- `frontend` job: ubuntu, Node 20, `npm ci && npm test`.

Gaps: ruff only selects `F`; no coverage/type check; no `npm`/pip caching; Windows skips the
16 shell tests; CI max Python is 3.13 while the dev machine is 3.14; no macOS runner.

### 15.5 How to run

```bash
pip install -e ".[web,plot]"
python -m unittest discover tests        # from repo root (tests use relative cases/, configs/, stl/)
python -m unittest discover -s tests -p "test_checkmesh.py"   # single module
ruff check src tests
npm install && npm test                  # JSDOM front-end
python -m build
```

---

## 16. Design decisions & invariants

1. **Universal defaults, geometry-relative scaling** — `DEFAULT_CONFIG` fully specifies a
   working case; the user supplies geometry + flow + axes (`config.py:28`).
2. **One config merge** — `effective_config` is used by loader, web and caseconfig; overrides
   are first-class.
3. **One mesh seam** — `MeshPlan` is immutable and built on a private copy; writers consume
   plan + context, never `cfg`. `apply_plan_to_cfg` preserves `case_config.json` and legacy
   tests.
4. **One override-aware case reader** — `core.caseconfig.read_case_config` is the path for
   telemetry, mesh, surface and the forces CLI.
5. **Streaming STL statistics** — O(1) memory log/angle histograms, quantised shared-edge
   tracking.
6. **Honest y+ chain** — clamp provenance is surfaced (CLI + Studio) rather than silently
   degrading.
7. **Symmetry as per-axis projection** — in-plane doubles, normal cancels; coefficients
   follow (`Cs`/`CmRoll`/`CmYaw` → 0).
8. **Atomic downloads** — staging dir + `os.replace`, atomic reservation under a lock.
9. **Opt-in heuristics** — `auto_size`, `auto_feature_angle`, `grading`, `two_pass`,
   ground layers are all off by default ("predictable over clever").
10. **Runtime assets are real modules** — the generated monitor is a verbatim copy of
    `runtime/convergence_monitor.py` with an axis header; the remote inventory is
    `runtime/remote_cases.py` (no AST embedding).
11. **Non-fatal potentialFoam** — a failed init warns but never aborts.
12. **OpenFOAM header hard-coded to v2606** — the only place to change if it retires.

---

## 17. Gotchas, edge cases and current technical debt

### 17.1 Known remaining risk areas

- **`GET /api/cases` remote merge** (`web/routers/cases.py:177-286`) — ~110 lines of nested
  conditionals encoding local-status inference, SLURM-state → status, zombie Solving/Meshing
  cleanup, and remote-newer precedence. Extensively exercised by `test_web_api.py` but the
  least readable block; a `RemoteCaseMerger` state machine was proposed but not implemented.
- **SSH bundle marker trust** — `read_remote_bundle` (`ssh_client.py:423-461`) uses fixed
  literal `__RAPIDFOAM_FILE_BEGIN__`/`END` markers; a crafted cluster file could spoof
  framing. The cluster is semi-trusted, so impact is bounded.
- **Untrusted STL parsing in the viewer** — Three.js r128 `STLLoader` trusts the binary
  header face count (the app only handles ASCII STLs server-side, but the browser loads bytes
  directly). Uploading a crafted binary could allocate a large typed array.
- **`load_axis_config` / `_load_case_configs` read raw JSON** (`postproc/forces.py:16,51`) —
  they do not apply the `overrides` block, unlike `core.caseconfig`. Axis values are rarely
  overridden, but this is an inconsistency worth remembering.
- **Architecture test blind spot** — relative imports are not scanned (`test_architecture.py:54`),
  so an intra-package relative import can cross a boundary undetected.

### 17.2 Edge cases baked into behaviour

- Restart/re-queue handling: every parser clears samples when `Time` decreases, so a re-queued
  run supersedes the old trajectory (`forces.py:218`, `services/telemetry.py:52,143`).
- Symmetry detection precedence (`caseconfig.py:75`): explicit faces > `symmetry_plane` >
  default half-model > polyMesh boundary.
- The surfaceCheck enforce gate exempts symmetry and `allow_open` from closure checks
  (`casegen/scripts.py:101-123`); checkMesh open-patch findings are suppressed for symmetry
  (`postproc/checkmesh.py:579`).
- Layer coverage uses snappy's fractional average layer count (e.g. 2.84/3) rather than
  truncating (`checkmesh.py:370-383`); full-but-thin stacks are a warning, not a failure.
- `iterations_per_second == 0` is distinguished from absent (`routers/telemetry.py:576-584`,
  explicit `is not None` serialisation).
- `Validate` cannot mutate state: `GenerateCaseRequest.generate_locally` defaults False and
  configs are written only when an action needs them (`schemas.py:45`, `case.py:175`).
- `is_case_running` only matches exact / `cfd_`-prefixed / scheduler-truncated-≥8-char job
  names, avoiding substring false positives (`ssh_client.py:508-526`).

### 17.3 Minor inconsistencies / stale UI

- A few CSS rules are duplicated/stale (e.g. `.btn-danger` base class never defined; alerts
  declared twice) — see `style.css`.
- `app.js` still carries fallback placeholder tables that duplicate server knowledge, though
  the server now serves the full preset schema (`routers/config.py:18-70`).
- `postproc/forces.load_axis_config` and `_load_case_configs` duplicate (a subset of)
  `caseconfig.read_case_config`.

---

## 18. Common task recipes

| Task | Where to look |
| --- | --- |
| **Add a new OpenFOAM solver dict** | Add a writer under `casegen/` and call it from `casegen/builder.py:312-330`; mirror `constants.py`/`solver.py` (`foam_header` + `FOOTER` + `bool_str`). The architecture test forbids importing web/cli/postproc/meshing/geometry from casegen. |
| **Add a mesh parameter** | Default in `config.DEFAULT_CONFIG` (`config.py`); derive in `meshing/params.compute_mesh_params`; consume via `plan.mesh_params` in a writer. `apply_plan_to_cfg` keeps `case_config.json` honest. |
| **Tune a fidelity preset** | `meshing/presets.py`; the Studio pulls it via `/api/config/schema-defaults`, so the form updates without JS changes. Keep cells/snap/solver/SLURM couples coherent. |
| **Add a CLI flag** | `cli.setup_main` / `cli.forces_main` (`cli.py:14,123`); thread generation flags through `casegen.builder.build_case`. |
| **Add a telemetry endpoint** | `web/routers/telemetry.py`, using `core.caseconfig.read_case_config` and the shared remote bundle (`_read_remote_telemetry`); do not issue a new SSH command per request. |
| **Add a front-end chart** | `TelemetryCharts` (`charts.js`) + a canvas in `index.html` + a hook in `app.js:bindTelemetryEvents`. Escape any dynamic string with `escapeHtml`. |
| **Change SSH transport** | `ClusterSSHClient` (`ssh_client.py`); keep `@_synchronized` on fast methods and bundle framing compatible with `_parse_marked_bundle`. |
| **Run on a new cluster** | Edit `configs/config.json` `slurm` (`openfoam_module`, `openfoam_source`, qos, partition, nodes, …) or set it in the Studio. Default remote repo is `/work/home/<user>/Rapidamente/cfd/RapidFOAM`. |
| **Add a log parser** | Mirror `postproc/*`: `parse_*`, `find_*log_files`, `check_*` returning `{available, ok, verdict, verdict_label, stats, issues, warnings, note}`. |
| **Tighten architecture rules** | Edit `FORBIDDEN`/`KNOWN_DEBT` in `tests/test_architecture.py`; CI then blocks new violations. |
| **Change the default port / skip browser** | `rapidfoam-studio --no-browser --port 8765 --restart` (`web/app.py:74-153`). |
| **Init a workspace** | `python setup_case.py --init` creates `configs/config.json`, `stl/`, `cases/`. |

---

## 19. Configuration reference (key fields)

`configs/config.json` is the committed full example; comment keys start with `_` and are
ignored. Solver-coupled sections (`schemes`, `relaxation`, `linear_solvers`, `simple`,
`wall_functions`, `snap`, snappy `mesh_quality` knobs) are intentionally omitted from the
example because the preset sets them.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `case_name` | str | `my_case` | Folder under `cases/` |
| `stl_files` | list | — | ASCII STLs in `stl/` |
| `fidelity` | str | `standard` | `fast`/`standard`/`fine` |
| `flow.velocity` | float | 16.67 | m/s |
| `flow.direction` | str | `-z` | `±x/±y/±z` |
| `flow.ground` | bool | true | moving ground vs slip |
| `outputs.drag_axis` / `downforce_axis` | str | `-z` / `-y` | reporting axes (must be independent) |
| `domain_box` | `"auto"`/dict | `auto` | explicit min/max |
| `symmetry_plane` | float/null | null | centerline coord |
| `ground_clearance` / `ground_plane` | float/null | null | relative vs absolute (ground_plane wins) |
| `domain_faces` | dict | derived | six faces → roles/patch names |
| `patches` | dict | defaults | patch names for roles |
| `vehicle.wheelbase` / `front_weight_pct` | float/null | null | aero balance |
| `surface_check.*` | mixed | enabled, report-only | `enforce` aborts on defects |
| `mesh_params.auto_size` / `auto_feature_angle` | bool | false | opt-in heuristics |
| `mesh_params.grading` | `"off"`/`"auto"`/list | off | opt-in background grading |
| `mesh_params.cells_per_length` | float | preset | base cell = longest extent / this |
| `layers.y_plus_target` | float/null | preset | drives absolute first layer |
| `layers.two_pass` | bool | false | experimental two-pass layering |
| `parallel.n_procs` | int | 10 | MPI ranks |
| `slurm.time` / `mem_per_cpu` | str | `"auto"` | omitted ⇒ preset governs |
| `overrides` | dict | — | expert overrides merged last |

---

## 20. Version history (summary)

- **1.6.0** (2026-09-29): boundary-layer thickness coverage + full-but-thin warning; opt-in
  two-pass layering; opt-in graded background mesh; surface-integrity reporting + optional
  enforcement; auto-heuristics default off; y+ presets retuned (fast 50/standard 30/fine 1);
  `min_thickness_ratio` removed; architecture refactor into bounded contexts; SLURM
  time/mem follow the fidelity preset; full-car symmetry fix; telemetry honours overrides and
  exports the full history.
- **1.5.0**: mesh-quality verification + tiered verdict; realised y+ cross-reference;
  config-form merge; geometry-aligned feature extraction; `stl/`/`cases/` fixed conventions.
- **1.4.0**: feature-based auto-sizing; geometry-derived `resolveFeatureAngle`; y+
  verification; missing-STL guard; ground-layer clearance consistency.
- **1.3.0**: JSDOM front-end tests; telemetry error surfacing; a batch of security/robustness
  fixes (Validate mutation, mm-scale viewer, telemetry stall, XSS, download races, viewer
  lifecycle, symmetry projection, `copy_stl` merge, `iters/s == 0`).
- **1.2.0**: y+ boundary-layer targeting; FSAE preset refresh; Studio near-wall controls; full
  aero telemetry; post-run reference editor; solver-health diagnostics; 2D/3D aero views;
  aero balance/CoP; Rapidamente branding.
- **1.1.0**: cluster case download + progress; graceful cancel.
- **1.0.0**: initial public release.

---

*This knowledge base is intended to let a new senior engineer (or AI) onboard: every major
flow, design decision, risky seam and test surface is mapped to concrete files and line
numbers, all sourced from the code on disk at v1.6.0.*
