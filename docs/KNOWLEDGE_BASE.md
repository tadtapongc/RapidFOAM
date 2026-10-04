# RapidFOAM — Knowledge Base

> Comprehensive reverse-engineering / onboarding reference. Every claim below is grounded in
> code on disk. `__version__ = "1.6.0"`, but the `dev` branch additionally carries an
> unreleased set of diagnostics (opt-in y+ fit, softened two-pass gate, field outputs,
> per-part telemetry); those are called out as **[dev]**. Line numbers are 1-based and refer
> to the files as they exist now. This document supersedes all earlier `docs/` notes.

---

## 1. What RapidFOAM is

RapidFOAM is a **Python automation suite + browser Web Studio** that turns an **ASCII STL +
small JSON config** into a complete, runnable **OpenFOAM** external-aerodynamics case for
Formula Student / FSAE vehicles, runs it locally or on a SLURM cluster, and post-processes
forces, mesh quality, surface integrity and near-wall (y+) diagnostics. It is the internal
aero pipeline of the **Rapidamente Formula Student** team (Chulalongkorn University).

- **Target solver:** ESI/OpenCFD **OpenFOAM v2606 only** (the version string is hard-coded in
  `core/foam.py`). Other releases are untested (`README.md`).
- **Core runtime dependencies:** *none* — Python stdlib only.
- **Optional extras:** `[web]` = fastapi, uvicorn, paramiko, python-multipart;
  `[plot]` = matplotlib (`pyproject.toml`).
- **Front end:** vanilla JS + Three.js r128 + Chart.js 4.4, served by FastAPI; no bundler.
- **License:** MIT. Repository: `https://github.com/tadtapongc/RapidFOAM`.

### 1.1 Quick facts

| Property | Value |
| --- | --- |
| Version | `1.6.0` (`src/rapidfoam/__init__.py`) |
| Python | `>=3.9`; CI matrix 3.9–3.13; dev machine observed on 3.14 |
| Build | hatchling; package root `src/rapidfoam` (`pyproject.toml`) |
| Lint | Ruff (`select = ["F"]`, line-length 120, target py39) |
| Python tests | ~384 test methods across 19 files (`unittest`) |
| JS tests | 62 (`node --test` + JSDOM) |
| CI | `.github/workflows/ci.yml`: ubuntu+windows × py3.9–3.13 (ruff + unittest + build); separate Node-20 JSDOM job |
| External tools | `surfaceCheck`, `surfaceFeatureExtract`, `blockMesh`, `decomposePar`, `snappyHexMesh`, `checkMesh`, `renumberMesh`, `potentialFoam`, `simpleFoam` |

### 1.2 Entry points

| Console script | Target | Role |
| --- | --- | --- |
| `rapidfoam` / `rapidfoam-setup` / `cfd-setup` | `rapidfoam.cli:setup_main` | Generate a case from config |
| `rapidfoam-forces` / `cfd-forces` | `rapidfoam.cli:forces_main` | Forces + `--mesh`/`--surface`/`--yplus`/`--check` |
| `rapidfoam-monitor` / `cfd-monitor` | `rapidfoam.postproc.convergence_monitor:main` | Standalone auto-stop monitor |
| `rapidfoam-studio` / `rapidfoam-web` / `cfd-web` | `rapidfoam.web.app:main` | Web Studio (FastAPI + static UI) |
| `python -m rapidfoam` | `__main__.py` → `cli.setup_main` | Same as CLI setup |

Repo-root shims `setup_case.py` and `read_forces.py` (11 lines each) insert `src/` on
`sys.path` and call `cli.setup_main()` / `cli.forces_main()`.

---

## 2. Repository layout

```
RapidFOAM/
├─ .github/workflows/ci.yml       # ruff + unittest + build; separate JSDOM job
├─ .gitattributes                 # * text=auto eol=lf; STL/png/jpg/pdf binary
├─ configs/
│  ├─ config.json                 # committed full-example config (only tracked config)
│  └─ *.json                      # scratch experiments (gitignored)
├─ stl/                           # committed: geometry.stl sample only; rest gitignored
├─ cases/                         # generated OpenFOAM case dirs (gitignored except .gitkeep)
├─ src/rapidfoam/                 # the package (bounded contexts, §3)
├─ tests/
│  ├─ golden/                     # committed casegen snapshots
│  └─ js/                         # JSDOM front-end tests (npm test)
├─ docs/KNOWLEDGE_BASE.md         # this document
├─ package.json                   # jsdom devDependency + npm test script
├─ pyproject.toml                 # metadata, optional deps, scripts, ruff config
├─ README.md / CHANGELOG.md / LICENSE
├─ setup_case.py / read_forces.py # 11-line CLI shims
├─ run_app.bat / run_app.sh       # 1-click Studio launchers (create .venv, install [web])
```

Conventions fixed in code (`config.py`): `STL_DIR = "stl"`, `CASE_DIR = "cases"`. Both the
CLI and the Studio resolve geometry/cases against these, and the web layer relies on the
fixed base for path-traversal checks.

### 2.1 Package map with line counts

| Context | Files |
| --- | --- |
| root | `cli.py` 355, `config.py` 727, `__init__.py` 4, `__main__.py` 14 |
| `core/` | `axes.py` 70, `faces.py` 40, `fields.py` 33, `foam.py` 46, `caseconfig.py` 262 |
| `geometry/` | `stl.py` 462 |
| `meshing/` | `presets.py` 201, `domain.py` 127, `sizing.py` 136, `grading.py` 174, `layers.py` 332, `params.py` 288, `plan.py` 157, `context.py` 52, `pipeline.py` 26, `writers/{block_mesh.py 107, feature_extract.py 39, snappy.py 276}` |
| `casegen/` | `builder.py` 353, `constants.py` 38, `fields.py` 231, `solver.py` 325, `scripts.py` 418 |
| `runtime/` | `convergence_monitor.py` 187, `remote_cases.py` 229 |
| `postproc/` | `forces.py` 569, `checkmesh.py` 775, `surfacecheck.py` 350, `yplus.py` 157, `residuals.py` 90, `compare.py` 88, `convergence_monitor.py` 165, `plotting.py` 312, `fielddata.py` 173 |
| `web/` | `app.py` 183, `state.py` 137, `schemas.py` 55, `ssh_client.py` 715, `routers/{case.py 335, cases.py 316, cluster.py 88, config.py 109, stl.py 132, telemetry.py 847}`, `services/{telemetry.py 786, geometry.py 46, downloads.py 48}` |
| `web/static/` | `index.html` 1643, `css/style.css` 2623, `js/app.js` 4355, `js/viewer.js` 1122, `js/charts.js` 636, `js/telemetry2d.js` 330, `js/telemetry3d.js` 159, svg assets |

---

## 3. Architecture overview

### 3.1 Bounded contexts and dependency direction

Enforced by `tests/test_architecture.py` (an AST import scan). Intended graph:

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

- `FORBIDDEN` maps each source context to the contexts it must not import:
  `core → {geometry, meshing, postproc, writers, web, cli}`; `geometry → {meshing, postproc,
  writers, web, cli}`; `meshing → {postproc, writers, web, cli}`; `postproc → {meshing,
  writers, web, cli}`; `writers → {postproc, web, cli}`; `casegen → {postproc, web, cli}`;
  `web → {cli}`.
- `KNOWN_DEBT` and `CONTEXT_DEBT` are **both empty** — the codebase is boundary-clean.
- `_imported_modules` collects only **absolute** imports (`node.level == 0 and node.module`);
  relative imports (`from . import x`), dynamic imports and string imports are **not**
  checked — a documented loophole.
- The single test fails on any new violation.

### 3.3 The two seams

1. **Effective config** — `config.effective_config(raw)` (`config.py:315`) is the one merge of
   `DEFAULT_CONFIG → user top-level keys → overrides` (comment keys dropped).
   `core.caseconfig.read_case_config()` (`core/caseconfig.py:36`) is the one reader for
   telemetry, mesh quality, surface checks and the forces CLI; it prefers the effective
   `<case>/case_config.json` over a raw `configs/<case>.json` and always applies overrides.
2. **Mesh plan** — `meshing.plan.build_mesh_plan()` (`meshing/plan.py:86`) derives on a private
   copy and returns an immutable `MeshPlan`. `meshing.presets.apply_fidelity_preset`
   (`meshing/presets.py:148`) is the only preset resolver. Writers consume `MeshPlan` +
   `MeshContext`, never `cfg`; `meshing.pipeline.emit_mesh_files` (`pipeline.py:14`) is the
   single emission entry point.

---

## 4. `core/` — dependency-free primitives

### 4.1 `core/axes.py` (70)
- `AXIS_MAP` maps `+x/-x/...` to unit tuples; `parse_axis(s)` (18) raises on invalid input.
- `axis_index_sign(axis_str)` (28) → `(column_index, sign)`.
- `up_axis_index(cfg)` (37) → index of `outputs.downforce_axis`; `flow_axis_index_sign(cfg)`
  (49) → `(index, sign)` of `flow.direction`; `vec_str(v)` (58) → `(x y z)`.

### 4.2 `core/faces.py` (40)
- `patch_role(patches, name)` (10) → role; `farField` falls back to role `walls`.
- `face_role(cfg, name)` (18); `face_assignments(cfg)` (23) → six faces. `domain_faces` is
  authoritative; otherwise derived from flow/up/lateral axes (lateral-min → symmetry, up-min
  → ground, upstream → inlet, downstream → outlet, others → walls).

### 4.3 `core/fields.py` (33)
- `velocity_vector(cfg)` (10) → direction × speed.
- `turbulence_values(cfg)` (17) → `(k, omega, nut)` with `k = 1.5(u·I)²`.

### 4.4 `core/foam.py` (46)
- `HEADER` hard-codes **`Version: v2606`**; `FIELD_CLASS`; `foam_header(obj)`;
  `bool_str(val)`.

### 4.5 `core/caseconfig.py` (262)
The override-aware single source for case config values (depends only on `rapidfoam.config`).
- `read_case_config(config_path, case_dir)` (36) — precedence `<case_dir>/case_config.json` →
  explicit path → `./case_config.json`; run through `effective_config`.
- `has_symmetry(cfg)` (75) — explicit `domain_faces` authoritative; `symmetry_plane`/
  `centerline` only when no face list. `has_symmetry_for_case` (99) adds a polyMesh fallback.
- `yplus_target` (118), `solver_end_time` (141), `stl_files` (160), `vehicle_geometry` (170),
  `mesh_targets` (188), `verdict_bands` (210), `surface_policy` (232) + `*_from_case` wrappers.

---

## 5. `config.py` (727) — defaults, merge, validation

### 5.1 `DEFAULT_CONFIG`
The universal default tree. Sections: `case_name`, `stl_files`, `flow` (`velocity 16.67`,
`direction "-z"`, `ground True`), `outputs`, `fluid` (`nu 1.516e-5`, `rho 1.225`),
`turbulence` (`kOmegaSST`, `intensity 0.005`, `nut_ratio 10`), `patches`, `force_refs`,
`vehicle`, `parallel` (`n_procs 10`, `scotch`), `slurm` (`qos cu_hpc`, `partition cpu`,
`nodes 1`, `time "auto"`, `mem_per_cpu "2G"`, `cpus_per_task 1`, `openfoam_module null`,
`openfoam_source …v2606/etc/bashrc`), `solver`, `schemes`, `linear_solvers`, `simple`,
`relaxation`, `wall_functions`, `snap`, `layers`, `feature_extract`, **`field_outputs`**
(222, [dev]), `mesh_quality`, `surface_check`, `potential_flow`.

Notable `layers` defaults (**[dev]** coverage-first tuning): `expansion_ratio 1.15`,
`min_thickness_ratio 0.35`, `maxFaceThicknessRatio 0.7`, `nSmoothThickness 20`,
`nRelaxIter 15`, `nLayerIter 75`, `ground_layers False`, `ground_n_layers 2`,
`two_pass True`, `y_plus_fit False`. `mesh_quality.layering_relaxed` defaults to
`{"maxNonOrtho": 80, "maxInternalSkewness": 8}` (the two-pass layer gate). The **[dev]**
`field_outputs` block is:

```python
"field_outputs": {
    "wall_shear_stress": True,
    "y_plus": True,
    "field_min_max": True,
    "vorticity": False,
},
```

### 5.2 Loading and merging
- `deep_merge(base, override)` (302) — recursive; `_`-prefixed keys skipped.
- `effective_config(raw_user)` (315) — the single merge (defaults + top-level + `overrides`).
- `load_config(path)` (335); `user_set(raw_user, section, key)` (713) — true when a value was
  explicitly provided (counts a present key even if `null`).

### 5.3 `validate(cfg, project_dir)` (356)
Imports `core.axes`, `core.faces`, `meshing.presets` lazily. Checks containers; required
`stl_files`/`case_name`/`fidelity`; axis independence; `domain_faces` completeness and
ground/symmetry axis placement; numeric bounds across all sections; `surface_level` ordering;
mesh-quality `verdict_bands` shape. **[dev]** additions:
- `layers.y_plus_fit` must be a `bool`.
- two-pass (521) emits the experimental warning.
- `mesh_quality.layering_relaxed` (543-550) must be an object of finite numbers.
- `field_outputs` (560-567) must be an object of booleans.
Warnings: both ground settings defined; layer stack below `min_thickness`; absolute first
layer exceeds `maxFaceThicknessRatio × finest surface cell` (the message now uses the
configured ratio and hints at `layers.y_plus_fit`).

---

## 6. `geometry/stl.py` (462) — streaming ASCII STL I/O

- `FeatureAngleStats` (40) — streaming dihedral (normal-angle) stats, `percentile`.
- `_dihedral_angle_deg(n1,n2)` (93) — normal angle (complement of the *included* angle).
- `EdgeStats` (110) — bounded log-histogram edge-length stats.
- `is_binary_stl` (172); `stl_analyze` (200) / `stl_analyze_full` (217) / `_analyze` (224) —
  one O(1)-memory streaming pass yielding name, triangle count, bbox, `EdgeStats`,
  `FeatureAngleStats`; shared edges quantised to 1e-7 m.
- `stl_info` (315); `read_stl` (329); `write_stl` (390); `stl_bounds` (408).
- `copy_stl(src, dst, name=None, info=None)` (418) — `name is None` uses `shutil.copy2`;
  otherwise always streams and rewrites every `solid`/`endsolid` line (multi-body merge).

---

## 7. `meshing/` — derivation and emission

### 7.1 `meshing/presets.py` (201)
`FIDELITY_PRESETS` (11-139): `fast`/`standard`/`fine` binding `cells_per_length` (20/30/37.5),
`surface_level`, `edge_level`, `n_layers` (5/8/20), `expansion_ratio` (1.2/1.2/1.1),
`y_plus_target` (50/30/1), `ground_layers` (all False), `end_time`/`write_interval`,
`maxGlobalCells`, `nCellsBetweenLevels`, `resolveFeatureAngle` (35/35/30), snap iters,
`slurm_time`/`slurm_mem_per_cpu`, auto-size fields (`feature_cells`, `max_surface_level`),
`distance_shells`, wake levels, `cell_estimate`/`runtime_estimate`.
`apply_fidelity_preset(cfg, is_set)` (148) fills only unset fields; mutates `cfg` and returns
it (the pre-Phase-2 contract; `build_mesh_plan` runs it on a private copy).

### 7.2 `meshing/domain.py` (127)
`GROUND_EMBED = 0.01`; `compute_domain_box(cfg, bounds)` (17) with fidelity-aware
upstream/downstream/lateral/top factors and ground/symmetry clipping.

### 7.3 `meshing/sizing.py` (136)
`_resolve_feature_sizing` (11) raises `surface_level[1]`/`edge_level` so the smallest feature
spans `feature_cells` (capped by `max_surface_level`), never coarsens. `_resolve_feature_angle`
(78) derives `resolveFeatureAngle` from a high percentile of the normal-angle histogram,
only ever sharpening the preset; returns `included_angle_recommended = 180 − resolve`.

### 7.4 `meshing/grading.py` (174)
`DEFAULT_GRADING_RATIO = 3.0`; `_graded_axis` (19); `compute_block_grading` (65) — modes
`"off"` (uniform), `"auto"` (grade toward ground/symmetry), explicit `[gx,gy,gz]`; returns
provenance (`mode`, `grading`, `block_cells`, `uniform_cells`, `cell_ratio`, `axes`,
`cell_reduction`).

### 7.5 `meshing/params.py` (288)
`compute_mesh_params(cfg, bounds, feature_stats, angle_stats, *, explicit_feature_angle)`
(21): base cell from `base_cell_size` or `max_extent/cells_per_length`; surface/edge levels
(optionally auto-sized); distance levels; feature angle (optionally derived, and writes
`cfg["feature_extract"]["includedAngle"]` when sharpened); near/far wake boxes; grading via
`compute_block_grading`. Returns the mesh-params dict incl. provenance (`auto_size`,
`feature_angle`, `grading_info`).

### 7.6 `meshing/layers.py` (332) — y+ → absolute thickness, **[dev] y+ fit**

- `Y_PLUS_FIT_RATIO_CAP = 0.8`.
- `_fit_y_plus_clamp(cfg, layers, requested_thickness, base_cell, levels, ratio_limit, *,
  allow_level=True)` (24) — **[dev]** mutates `layers`/`cfg["mesh_params"]` so the requested
  thickness is buildable: raise `maxFaceThicknessRatio` up to the cap; if the cap is not
  enough and `allow_level`, coarsen the finest `surface_level`; returns `(ratio, level)` or
  `None` when it declines/cannot help.
- `resolve_y_plus_fit_mode(layers)` (72) — normalises `layers.y_plus_fit` to
  `None`/`"ratio"`/`"full"` (`true` → `"full"`).
- `estimate_friction_velocity(U, nu, length)` (86) — Prandtl–Schlichting / Blasius.
- `first_layer_height(y_plus, u_tau, nu)` (105) → `δ₁ = 2·y+·ν/u_τ`.
- `_apply_ground_layer_policy` (112) — opt-in ground inflation; clearance ≥
  `max(2mm, 2δ₁)`, capped by `ground_n_layers`.
- `resolve_layers(cfg, bounds, *, explicit_first_layer, explicit_min_thickness)` (177) —
  writes `relativeSizes false`, absolute `first_layer_thickness`, `min_thickness` and
  `layers._resolved` provenance; clamps δ₁ to `maxFaceThicknessRatio × (base_cell/2^level1)`.
  **[dev]** when clamped and `y_plus_fit` resolves a mode, calls `_fit_y_plus_clamp` and
  records `fit_applied`/`fit_mode`/`fit_ratio`/`fit_level`; on success `clamped=False`.

### 7.7 `meshing/plan.py` (157)
`LayerSpec` (24), `MeshPlan` (53, frozen) with `base_cell_size`/`surface_level`/`edge_level`
properties and `as_config()`; `build_mesh_plan` (86, pure); `plan_from_config` (126);
`apply_plan_to_cfg` (145) compat shim.

### 7.8 `meshing/context.py` (52)
`MeshContext` (17, frozen) and `build_mesh_context(cfg, stl_names=None)` (30) resolving
`stl_names`/`patches`/`faces`/`snap`/`quality` and the flow/up/lateral indices.

### 7.9 `meshing/pipeline.py` (26)
`emit_mesh_files(plan, ctx, case_dir)` (14); `emit_mesh_files_from_config(cfg, case_dir)` (21).

### 7.10 `meshing/writers/`
- **`block_mesh.py` (107)** — `write_block_mesh_dict` (41); `FACE_MAP`; `_PATCH_TYPES`
  (`ground → wall`); missing `block_cells` warns and falls back to uniform counts.
- **`feature_extract.py` (39)** — one entry per STL with `includedAngle`, `nonManifoldEdges`,
  `openEdges`, `writeObj no`.
- **`snappy.py` (276)** — `_location_in_mesh` (13); `_quality_controls_block(controls,
  relaxed, fallback)` (56) renders one `meshQualityControls` block (**[dev]**); 
  `write_snappy_hex_mesh_dict` (111) emits geometry/features/refinementRegions/addLayersControls
  and the quality block. **Two-pass** writes `snappyHexMeshDict_layering` whose gate is the
  configured `mesh_quality.relaxed` (**[dev]**, previously a total disable), overridable via
  `mesh_quality.layering_relaxed`.

---

## 8. `casegen/` — case assembly

### 8.1 `casegen/builder.py` (353)
`build_case(cfg_path, project_dir, dry_run=False, reporter=None)` (31), called by
`cli._do_generate` and the web endpoint; raises `CaseGenerationError` (19) instead of exiting.
Pipeline: load+validate → resolve STL stems + `domain_faces` → stream STLs
(`stl_analyze_full`) → derive `domain_box` → clearance report → `apply_fidelity_preset` →
`build_mesh_plan` → `apply_plan_to_cfg` → print derived report → create dirs → `copy_stl` →
emit mesh dicts + solver/fields/constants/scripts → back up `0/`→`0.orig/` → write
`case_config.json`. Dry run returns `None` after the report.

### 8.2 Emitted case directory
```
cases/<case_name>/
├─ 0/                 U, p, k, omega, nut
├─ 0.orig/            backup of 0/
├─ constant/          triSurface/*.stl, transportProperties, turbulenceProperties, polyMesh/
├─ system/            blockMeshDict, surfaceFeatureExtractDict, snappyHexMeshDict
│                     (+ snappyHexMeshDict_layering when layers.two_pass),
│                     controlDict, fvSchemes, fvSolution, decomposeParDict
├─ Allrun, Allrun.parallel, Allclean, run.sh
├─ convergence_monitor.py
├─ case_config.json
└─ test.foam          # ParaView marker (0 bytes)
```
At solve time the time dirs also receive the optional diagnostic fields (`wallShearStress`,
`yPlus`, `vorticity`) and `postProcessing/` gains `forces`, per-part `forces_<part>`,
`forceCoeffs`, `residuals`, `yPlus`, `fieldMinMax`, `wallPressure_{min,max,average}_<patch>`.

### 8.3 `casegen/constants.py` (38), `casegen/fields.py` (231)
- `write_constant` (14): `transportProperties` (Newtonian, nu), `turbulenceProperties` (RAS).
- `write_fields` (25): `U/p/k/omega/nut` with per-patch BCs; `_wing_regex` over all STL stems;
  moving-ground vs slip; ground/symmetry patches emitted only when assigned.

### 8.4 `casegen/solver.py` (325) — incl. **[dev]** field outputs
- `write_control_dict` (22): `simpleFoam`, `startFrom latestTime`, `stopAt endTime`, function
  objects `forces`, `forceCoeffs` (liftDir/dragDir/pitchAxis = lift×drag), optional per-part
  `forces_<part>` (multi-STL), `residuals` (solverInfo), `yPlus`. **[dev]** it also emits the
  diagnostic outputs gated by `field_outputs`:
  - `yPlus { writeFields <y_plus>; }`
  - `wallShearStress` (surface field, restricted to the body patches)
  - `fieldMinMax { fields (yPlus); mode component; writeLocation true; }` → max + location
  - `vorticity` (opt-in)
  There is **no `wallPressure` function object** — the type does not exist in v2606; wall
  pressure is the `p` boundary.
- `write_fv_schemes` (203), `write_fv_solution` (242), `write_decompose_par_dict` (316).

### 8.5 `casegen/scripts.py` (418)
`MONITOR_CLEANUP` / `STOPAT_RESTORE`; `_convergence_monitor_script(cfg)` (44) copies
`runtime/convergence_monitor.py` verbatim with a `DRAG_IDX/DRAG_SIGN/DF_IDX/DF_SIGN` header.
`write_scripts` (67) writes the surfaceCheck gate (report-only unless `enforce`) and
`Allrun.parallel`, `Allrun`, `Allclean`, `run.sh`. Two-pass adds a conditional
`snappyHexMesh -s layering` step; SLURM `run.sh` has a `cleanup` trap that reconstructs on
interrupt.

---

## 9. `runtime/` — reproducible assets copied to cases/clusters

- `runtime/convergence_monitor.py` (187) — canonical dependency-free auto-stop monitor
  (`THRESHOLD 0.5%`, `WINDOW 200`, `MIN_ITERS 300`, `INTERVAL 10 s`); writes
  `stopAt writeNow;` on convergence.
- `runtime/remote_cases.py` (229) — cluster inventory/status/force summary, printed between
  `__CASE_JSON_START__`/`__CASE_JSON_END__`; parity-tested against the canonical reader.

---

## 10. `postproc/` — measurement (never runs OpenFOAM)

### 10.1 `postproc/forces.py` (569)
- `load_axis_config` (16); `_load_case_configs` (51); `is_symmetry_case` (77) —
  delegates to `caseconfig.has_symmetry`, defaults a no-faces config to a half model.
- `_dir_time` (119); `force_layout_from_header` (126); `find_force_files` (143).
- **[dev]** `find_per_part_force_files(base_dir)` (170) → `{part: [force.dat, ...]}` from the
  `forces_<part>` objects.
- `read_forces` (196); `parse_tabular_dat` (293); `normalize_component_columns` (354);
  `normalize_coefficient_columns` (379); `find_moment_files` (394);
  `find_coefficient_files` (420); `window_stats` (451); `check_convergence` (473);
  `print_summary` (517). Column tables: `FORCE_COMPONENT_COLUMNS`,
  `CLASSIC_COMPONENT_COLUMNS`, `COEFFICIENT_COLUMNS`.

### 10.2 `postproc/checkmesh.py` (775) — incl. **[dev]** failure reasons
- Regexes for all checkMesh metrics + cell-type/boundary-patch tables.
- **`_FAILURE_PATTERNS`** — regexes for the `***` lines: number of non-orthogonality errors,
  faces incorrectly oriented, faces with low-quality/negative-volume decomposition tets,
  cells with small determinant, faces with small interpolation weight, faces with small
  volume ratio.
- `QUALITY_TIERS` (good/caution bands), `LAYER_COVERAGE_MIN 0.9`, `LAYER_THICKNESS_MIN 0.7`.
- `resolve_bands` (133), `classify_value` (158), `find_checkmesh_logs`/`find_snappy_logs`
  (209/215), `parse_checkmesh` (233) — also builds `stats["failures"]`, `parse_boundary_patches`
  (333), `parse_layer_coverage` (414), `read_checkmesh`/`read_layer_coverage` (446/458),
  `check_mesh_quality` (476) — returns `verdict`, `metrics`, `layers`, `patches`,
  `cell_types`, **`failures`**, `issues`, `warnings`, `ok`, `note`. `mesh_quality_report`
  (757) is the end-to-end wrapper.

### 10.3 `postproc/surfacecheck.py` (350), `yplus.py` (157), `residuals.py` (90), `compare.py` (88), `convergence_monitor.py` (165), `plotting.py` (312)
- surfacecheck: parses closure/open edges/self-intersection/illegal triangles/normals;
  `check_surface` (222) exempts symmetry/allow_open.
- yplus: `find_yplus_files` (18), `read_yplus` (44, per patch min/max/average/time),
  `check_yplus_target` (77) — regime-aware (±50% tolerance, wall-function band vs
  wall-resolved).
- residuals/compare/convergence_monitor/plotting as before.

### 10.4 `postproc/fielddata.py` (173) — **[dev]** new
Parses the small field reductions so the telemetry can locate extrema without streaming
fields.
- `find_field_min_max_files` (32); `parse_field_min_max` (40) — v2606 columns
  `Time field min location(min) [processor] max location(max) [processor]`; `location` is the
  max location; later rows win. `read_field_min_max` (94).

---

## 11. Web layer

### 11.1 `web/app.py` (183)
`create_app()` (37) — localhost-only CORS, six routers, static mount at `/`. `main()` (61) —
UTF-8 reconfig, port collision/reuse/`--restart`, banner from the saved cluster config,
browser open. Module-level `app`.

### 11.2 `web/state.py` (137)
`ssh_client` singleton; `CREDENTIALS_FILE = ~/.rapidfoam_cluster.json` (legacy migration);
`PROJECT_ROOT = Path.cwd()`; `CASE_NAME_REGEX`, `JOB_ID_REGEX`, `ALLOWED_LOG_TYPES`; the
download-progress registry + lock; `get_saved_cluster_config` / `save_cluster_config` /
`_restrict_file_access`.

### 11.3 `web/schemas.py` (55)
Pydantic models incl. `GenerateCaseRequest.generate_locally` defaulting **False** (validation
is side-effect-free).

### 11.4 Routers
- `cluster.py` (88) — saved-config (password redacted), connect (reuses stored password),
  status, disconnect.
- `config.py` (109) — `schema-defaults` (DEFAULT_CONFIG + full preset table), templates,
  `load-file` (traversal-safe; returns raw + merged).
- `stl.py` (132) — list (deduped, bbox + `is_likely_mm`), serve (traversal-safe), check-exists,
  upload (multipart, `override_name`).
- `case.py` (335) — `geometry/domain-box` (domain + `auto_symmetry_plane` + `layer_preview`),
  `case/check-exists`, `case/generate-and-submit` (validate → verify STLs → save config only
  when needed → `build_case` in a thread → optional cluster upload/setup/submit),
  `case/submit`, `case/cancel`, `case/download` (atomic reservation under the progress lock),
  `download/active`, `download/progress`.
- `cases.py` (316) — `GET /api/cases` merges local + remote inventory (SLURM state →
  status, zombie-Solving cleanup, remote-newer precedence, never regresses numeric progress);
  `DELETE /api/cases/{name}`.
- `telemetry.py` (847) — force/coeff/component/balance telemetry with symmetry projection,
  post-run reference overrides, downsampling (and full-history export), residuals, solver
  health, mesh quality, surface integrity, log tail. See §11.6 for the **[dev]** additions.

### 11.5 Services
- `services/telemetry.py` (786) — no FastAPI imports: residual/solver parsing, y+
  (`_summarise_yplus` 204), the shared remote bundler/cache, reference/vehicle loaders, aero
  balance, symmetry projections, moment shift, coefficient computation.
  **[dev]** `_sorted_segments(bundle, suffix, contain=None)` (275) disambiguates combined
  `forces/` from per-part `forces_<part>/`; `_per_part_forces(...)` (289) summarises
  per-component drag/downforce from parsed tabular segments. `_read_remote_telemetry` (300)
  globs `log.snappyHexMesh*` (so two-pass `log.snappyHexMesh.layering` is fetched),
  `postProcessing/forces_*/*/force.dat`, `postProcessing/fieldMinMax/*/fieldMinMax.dat` and
  
- `services/geometry.py` (46) — `_local_stl_exists`; `layer_preview` (19) builds a plan and
  returns `layer_spec.resolved` (+ `auto_size`/`feature_angle`/`surface_level`/`edge_level`,
  and the **[dev]** fit provenance).
- `services/downloads.py` (48) — background `_run_download` worker.

### 11.6 Telemetry endpoints (`routers/telemetry.py`)
| Route | Line | Notes |
| --- | --- | --- |
| `GET /api/telemetry/forces` | 90 | axis map, symmetry ×2, coefficient recompute, components, balance; **[dev]** returns `per_part` build-up |
| `GET /api/telemetry/export` | 397 | CSV/JSON; full history (`max_points=0`) |
| `GET /api/telemetry/residuals` | 456 | |
| `GET /api/telemetry/solver` | 569 | continuity, linear iters, rate, ETA |
| `GET /api/telemetry/mesh` | 654 | checkMesh + layers + y+; adds `field_min_max`, `failures` |
| `GET /api/telemetry/surface` | 756 | surfaceCheck |
| `GET /api/telemetry/logs` | 814 | whitelisted `log_type`, ≤2000 lines |

### 11.7 `web/ssh_client.py` (715)
`ClusterSSHClient`, thread-safe via `@_synchronized` except the long `download_directory` and
`cancel_job`. Bundle framing (`FILE_BEGIN`/`FILE_END`, `_parse_marked_bundle`, fixed literal
markers), `read_remote_bundle`, connect (key data → key_path → password → default keys),
`download_directory` (streamed tar → hidden staging dir → atomic `os.replace`), graceful
`cancel_job` (`stopAt writeNow` then `scancel`), `list_remote_cases_detailed` (base64-uploads
`runtime/remote_cases.py`).

---

## 12. Front-end Studio (`web/static/`)

### 12.1 `index.html` (1643)
- CDN: Three.js r128 + OrbitControls + STLLoader; Chart.js 4.4.0; Google fonts. App scripts
  load in order `viewer → charts → telemetry2d → telemetry3d → app`.
- Four tabs: `config-tab`, `cluster-tab`, `telemetry-tab`, `cases-tab`.
- Config tab: viewer panel + config panel with five sub-tabs (General, Flow, Domain, Slurm,
  Expert). The Expert tab has sections 1–7; **section 7 "Diagnostic Field Outputs"** (836-844)
  has checkboxes `cfg-field-wall-shear`, `cfg-field-yplus`, `cfg-field-minmax`,
  `cfg-field-surfacevalue`, `cfg-field-vorticity`. Boundary-layer controls include
  `cfg-override-layer-twopass` and `cfg-override-layer-yplusfit`.
- Telemetry tab: KPIs, reference editor, 2D/3D aero views, four charts, analysis tables
  (coefficient summary, force/moment breakdown, **Build-up by Component**
  `#per-part-table`/`#per-part-tbody`), solver health, surface integrity, mesh quality (with
  the layer table columns `Patch | Layers | Coverage | Thickness | Realised y+ | y+ range
  (min–max)`), log console.
- Archive tab; modals (SSH, confirm/rename, download overlay); toast container.

### 12.2 `js/app.js` (4355) — `CFDApp`
- State + default `activeConfig` (121-236; inert `_`-comment keys).
- `buildConfigFromVisualForm` (730-1047) clones `activeConfig` so unknown/comment keys survive;
  handles `layers.two_pass` (964-967), `layers.y_plus_fit` (968-972), and
  `field_outputs` (1033-1042, only when the controls exist).
- `updateVisualFormFromConfig` (478-717) renders every control, incl. `two_pass` (665-666),
  `y_plus_fit` (667-671) and `field_outputs` (688-694).
- `pollTelemetry` (3141-3361) — forces → residuals → solver → mesh → surface → log tail, with
  request-id staleness guards and per-step error handling.
- `renderMeshQuality` (3490-3673) — metrics, layer coverage, y+ (`avg / target`) and the
  **y+ min–max** cell, **Field diagnostics** (max y+ location + wall-p max) at 3601-3625, and
  **failed checks** appended to the note at 3668-3672.
- `renderTelemetryAnalysis` (3742-3826) — coefficient summary, force/moment breakdown and the
  **per-part build-up** table (3800-3818).
- Viewer lifecycle: `createSTLViewer` (243, returns null when unavailable), `destroy`
  (4331-4349). `escapeHtml` (4249-4259) guards untrusted `innerHTML` interpolations.

### 12.3 `js/viewer.js` (1122), `charts.js` (636), `telemetry2d.js` (330), `telemetry3d.js` (159)
- `STLViewer`: `available`/`disposed` flags, camera far & `maxDistance` scaled from bounds,
  `updateFlowArrow` disposes the previous arrow, `updateDomainBox(null,null)` clears state,
  `dispose()` cancels RAF/listeners/observer/renderer.
- `TelemetryCharts`: forces/residuals/coefficients/components/solver-health charts,
  `convergenceBandPlugin` (±0.5% bands), `computeRollingAverage`.
- `Aero2DView` (cached STL silhouette, CoP, drag/downforce/pitch) and `AeroVectorLayer`
  (force/moment vectors on the viewer).

---

## 13. Diagnostic field outputs & y+ fit **[dev]**

### 13.1 `field_outputs` (config + controlDict)
| Flag | Default | Emitted | Purpose |
| --- | --- | --- | --- |
| `wall_shear_stress` | true | `wallShearStress` surface field | skin friction + separation |
| `y_plus` | true | `yPlus` field (`writeFields true`) | near-wall map |
| `field_min_max` | true | `fieldMinMax` on `yPlus` | max value **+ location** |
| `vorticity` | false | `vorticity` volume field | wake/vortex structure |

There is no `wallPressure` FO (unknown type in v2606 — historically caused a load error) and
no `surfaceFieldValue` wall-pressure stats (removed: it aborted the solve on a missing patch).
`fieldMinMax` is parsed by `postproc/fielddata.py` and surfaced through `/api/telemetry/mesh`
(`field_min_max`) and the Studio mesh panel (max-y+ location).

### 13.2 `layers.y_plus_fit`
`false` (default) → clamp + warn. `true` → raise `maxFaceThicknessRatio` up to 0.8 (surface
resolution untouched) so the target is met; falls back to the plain clamp when the cap still
cannot build the layer. Provenance: `fit_applied`, `fit_ratio`, `fit_level`. CLI dry-run prints
`y+ fit: maxFaceThicknessRatio -> …`.

### 13.3 Two-pass layering gate
`layers.two_pass` is **on by default**. It writes `system/snappyHexMeshDict_layering` whose
`meshQualityControls` use `mesh_quality.layering_relaxed` `{maxNonOrtho 80,
maxInternalSkewness 8}` (a bounded relaxation, not a disable), overridable per case. The
single-pass `snappyHexMeshDict` is unchanged.

### 13.4 Coverage-first layer defaults
The shipped defaults are tuned for boundary-layer coverage on real, complex geometry:
`min_thickness_ratio 0.35` (keep partial stacks), `maxFaceThicknessRatio 0.7` (thicker allowed
layer), `expansion_ratio 1.15` (shorter stack), `nSmoothThickness 20` / `nRelaxIter 15` /
`nLayerIter 75`, `two_pass true`. `y_plus_fit` is deliberately **off**: it is a
*target-matching* tool that raises `maxFaceThicknessRatio` only as needed to hit y+ and can
lift the `min_thickness` floor, so it trades coverage for the target rather than raising it.

### 13.5 Interpreting the diagnostics
- `p` and `wallShearStress` are **kinematic** (×ρ for Pa); `Cp = p / (½U∞²)`.
- `fieldMinMax` gives the max-y+ **coordinate** (the tail location); the per-patch min–max
  comes from `yPlus.dat`; the `y+ range` column shows the spread.
- Coverage drives the y+ tail: a face with **no layers** gets a coarse first cell, so its local
  y+ is far above target. Raising coverage shrinks the max y+.
- **Coverage vs y+ trade:** both scale with the first-layer thickness δ₁. `min_thickness_ratio`
  and `maxFaceThicknessRatio` let you raise coverage *without* moving y+; `y_plus_fit` /
  `surface_level` move the target and thus trade against coverage.

---

## 14. Domain concepts & glossary

| Concept | Definition | Reference |
| --- | --- | --- |
| **Case** | OpenFOAM dir with `0/`, `constant/`, `system/`, scripts, `case_config.json`, `0.orig/`, `test.foam` | `casegen/builder.py` |
| **Fidelity preset** | `fast`/`standard`/`fine` | `meshing/presets.py:11` |
| **Effective config** | raw → defaults → overrides | `config.py:315` |
| **MeshPlan / LayerSpec** | Immutable derivation result consumed by writers/UI/tests | `meshing/plan.py` |
| **Domain box** | Wind-tunnel bounds, `"auto"` or explicit | `meshing/domain.py:17` |
| **Boundary roles** | inlet/outlet/ground/walls(farField)/symmetry → six faces | `core/faces.py:23` |
| **Symmetry / half model** | `domain_faces` symmetry; forces double in-plane, normal cancels | `core/caseconfig.py:75` |
| **GROUND_EMBED** | 0.01 m below the configured ground plane | `meshing/domain.py:14` |
| **Two-pass layering** | pass 1 castellate+snap, pass 2 add layers with the relaxed gate | `writers/snappy.py` |
| **y+ fit** | recalculate a clamped y+ target by raising `maxFaceThicknessRatio` | `meshing/layers.py` |
| **fieldMinMax** | small text reduction (max y+ value + location) read by the telemetry | `postproc/fielddata.py` |
| **Convergence band** | ±0.5% relative stdev over the last 200 iters after ≥300 | `postproc/forces.py:473` |
| **Force layouts** | ESI tabular (9 cols) vs classic pressure+viscous (12 cols) | `postproc/forces.py:126` |

---

## 15. End-to-end flows

### 15.1 Case generation (CLI and Web)
`setup_case.py configs/config.json` → `cli.setup_main` → `cli._do_generate` →
`casegen.builder.build_case`; the Web path `POST /api/case/generate-and-submit` →
`build_case` in a thread. Same pipeline as §8.1.

### 15.2 Execution pipeline (`Allrun.parallel` / `run.sh`)
surfaceCheck (report-only unless enforced) → surfaceFeatureExtract → blockMesh → decomposePar
→ snappyHexMesh `-overwrite` (+ optional layering pass) → checkMesh → reconstructParMesh →
renumberMesh → solver decomposePar → potentialFoam (non-fatal) → background convergence
monitor → simpleFoam → reconstructPar. The monitor writes `stopAt writeNow;` once drag and
downforce vary < ±0.5% over 200 iters after 300.

### 15.3 Telemetry (Studio)
The front end polls `/api/telemetry/{forces,residuals,solver,mesh,surface,logs}` every 5 s.
Each endpoint uses one cached remote bundle (TTL 2.5 s) or local logs; forces are axis-mapped,
symmetry-projected, optionally recomputed with reference overrides and downsampled to ≤400
points (export uses full history). The mesh endpoint additionally returns checkMesh failures,
max-y+ location and wall-pressure stats.

### 15.4 Cluster flow
SSH connect → upload STLs + config → optional remote `setup_case.py` → `sbatch run.sh` →
poll `squeue` → download the case via a streamed tar with atomic publish → optional graceful
cancel.

---

## 16. Configuration reference (key fields)

`configs/config.json` is the committed full example; `_`-prefixed keys are comments. See the
README table for the full list, including the **[dev]** `layers.y_plus_fit`, `layers.two_pass`,
`mesh_quality.layering_relaxed` and `field_outputs.*`.

---

## 17. Test suite, CI and tooling

### 17.1 Python tests (~384 methods, 19 files)

| File | Focus |
| --- | --- |
| `test_architecture.py` | AST import-boundary rules |
| `test_auto_sizing.py` | `EdgeStats`/`FeatureAngleStats`, auto-size/feature-angle |
| `test_casegen_builder.py` | `build_case` + **[dev]** solver field-output FO blocks |
| `test_casegen_golden.py` | Golden snapshot contract |
| `test_checkmesh.py` | checkMesh parsing/verdicts + **[dev]** failure reasons |
| `test_fielddata.py` | `fieldMinMax` parser |
| `test_grading.py` | blockMesh grading |
| `test_layers.py` | y+ → layers, ground policy, two-pass gate, **[dev]** y+ fit |
| `test_mesh_plan.py` | plan purity/determinism |
| `test_mesh_preview_equivalence.py` | preview == generation |
| `test_preset_coverage.py` | preset SLURM time/mem |
| `test_regressions.py` | broad behavioural net (37) |
| `test_remote_cases.py` | runtime inventory parity |
| `test_shell_scripts.py` | real bash execution (posix-only, 16) |
| `test_studio_config_sync.py` | symmetry detectors agree |
| `test_surfacecheck.py` | surfaceCheck parsing/verdicts |
| `test_telemetry_config.py` | overrides honoured, preset y+, export full history |
| `test_web_api.py` | ~86 tests over the HTTP surface + SSH client (incl. **[dev]** per-part, field diagnostics, layering-log bundle) |
| `test_yplus.py` | y+ file discovery/reading/verdict |

SSH is mocked by patching `ClusterSSHClient` methods / the `ssh_client` singleton (no paramiko
sockets); downloads use hand-rolled `FakeClient`/`FakeStream`.

### 17.2 Golden tests, CI, how to run
- Golden: CRLF-normalised text of `blockMeshDict`, `snappyHexMeshDict`,
  `surfaceFeatureExtractDict`, `Allrun.parallel`, `run.sh`, plus parsed `case_config.json`
  (which now includes `field_outputs`). `controlDict` is **not** snapshotted. No regen script.
- CI: ubuntu+windows × py3.9–3.13; `ruff check src tests`, `python -m unittest discover
  tests`, `python -m build`; separate Node-20 `npm ci && npm test`. Gaps: no coverage/types,
  no JS on Windows/macOS, matrix tops at 3.13 while dev runs 3.14.
- Run: `python -m unittest discover tests`; `ruff check src tests`; `npm ci && npm test`;
  `python -m build`. `test_web_api.py`/`test_telemetry_config.py` write into the repo's real
  `cases/`/`configs/` (cwd must be the repo root).

### 17.3 JS tests (62)
`harness.mjs` builds a JSDOM, loads `charts.js → viewer.js → app.js` via `window.eval`,
stubs THREE/Chart, and can build a bare `CFDApp`/`STLViewer`. `app.test.mjs` covers XSS,
solver health, telemetry switching/errors, viewer lifecycle, form↔JSON merge (incl. two_pass,
y+ fit, **[dev]** field_outputs), fidelity cards, and the mesh/surface/per-part panels.
`viewer.test.mjs` covers arrow disposal, `updateDomainBox(null,null)`, mm-scale far-plane
scaling, unavailable viewer and `dispose`.

---

## 18. Design decisions & invariants

1. **Universal defaults, geometry-relative scaling** — `DEFAULT_CONFIG` fully specifies a case.
2. **One config merge** (`effective_config`) and **one override-aware case reader**
   (`core.caseconfig.read_case_config`).
3. **One mesh seam** — immutable `MeshPlan` on a private copy; writers never index `cfg`.
4. **Streaming STL statistics** — O(1) memory.
5. **Honest y+ chain** — clamp provenance surfaced; **[dev]** optional fit instead of silent
   clamping.
6. **Symmetry as per-axis projection** — in-plane doubles, normal cancels.
7. **Atomic downloads** — staging dir + `os.replace`, atomic reservation.
8. **Opt-in heuristics** — `auto_size`, `auto_feature_angle`, `grading`, `two_pass`,
   `y_plus_fit`, ground layers, `vorticity` all default off.
9. **Runtime assets are real modules** — no AST embedding.
10. **Diagnostics are opt-in-able and telemetry-readable** — `field_outputs` reductions feed
    the Studio; heavier fields go to ParaView.
11. **OpenFOAM header hard-coded to v2606** — the only place to change on retirement.

---

## 19. Gotchas, edge cases and technical debt

### 19.1 Known risk areas
- `GET /api/cases` remote merge (`web/routers/cases.py`) — ~110 lines of nested conditionals
  (SLURM state, zombie states, remote-newer precedence); lightly readable but well-tested.
- SSH bundle markers — fixed literal `__RAPIDFOAM_FILE_BEGIN__`/`END`; a crafted cluster file
  could spoof framing.
- Untrusted STL parsing in the browser (Three.js `STLLoader` trusts the binary face count).
- `load_axis_config` / `_load_case_configs` read raw JSON, not the override-aware reader.
- Architecture test blind spot: relative/dynamic imports are not scanned.
- `fieldMinMax`/`wallPressure` FO types are version-sensitive (wallPressure absent in v2606);
  `surfaceFieldValue` was removed (aborted the solve on a missing patch).

### 19.2 Edge cases baked in
- Restart/re-queue: parsers clear samples when `Time` decreases.
- Symmetry precedence: explicit faces > `symmetry_plane` > default half model > polyMesh.
- Surface enforce exempts symmetry/`allow_open`; checkMesh open-patch findings suppressed for
  symmetry.
- Layer coverage uses snappy's fractional average (2.84/3 kept); full-but-thin stacks warn.
- `iterations_per_second == 0` distinguished from absent; Validate cannot mutate state.
- Two-pass coverage can be *inflated* by a too-loose gate → high skewness / failed checks;
  the gate now reuses `mesh_quality.relaxed`.

### 19.3 Minor items
- CSS rules duplicated/stale; `resetMeshQuality` uses a `colspan="5"` layer-empty row while the
  table has 6 columns; the hidden `btn-load-template` remains bound; front-end fallback
  placeholder tables duplicate server knowledge; `STL_PALETTE`/`STL_CHIP_COLORS` duplicated.
- `docs/` now holds only `KNOWLEDGE_BASE.md`.

---

## 20. Common task recipes

| Task | Where to look |
| --- | --- |
| Add a solver dict | New writer under `casegen/`, call from `casegen/builder.py` |
| Add/emit a field output | `casegen/solver.py` `write_control_dict` + the `field_outputs` flag + `config.py` defaults/validation + README/`configs/config.json` |
| Add a mesh parameter | `DEFAULT_CONFIG` → `meshing/params.compute_mesh_params` → consume via `plan.mesh_params` |
| Tune a fidelity preset | `meshing/presets.py` (served via `/api/config/schema-defaults`) |
| Add a CLI flag | `cli.setup_main`/`forces_main`; thread generation flags through `casegen.builder.build_case` |
| Add a telemetry endpoint | `web/routers/telemetry.py` using `core.caseconfig` + the shared remote bundle glob |
| Add a reduction parser | `postproc/fielddata.py` (find/parse/read) + the bundle glob + `/api/telemetry/mesh` + the Studio panel |
| Add a front-end chart/panel | `charts.js`/`index.html` + a hook in `app.js`; escape dynamic strings |
| Change SSH transport | `web/ssh_client.py`; keep `@_synchronized` and bundle framing |
| Tighten architecture rules | `tests/test_architecture.py` `FORBIDDEN` |
| Init a workspace | `python setup_case.py --init` |

---

## 21. Version history (summary)

- **1.6.0** (2026-09-29): boundary-layer thickness coverage + thin-stack warning; opt-in
  two-pass layering; opt-in graded background mesh; surface-integrity reporting + optional
  enforcement; auto-heuristics default off; y+ presets retuned (fast 50/standard 30/fine 1);
  `min_thickness_ratio` removed; architecture refactor into bounded contexts; SLURM time/mem
  follow the preset; full-car symmetry fix; telemetry honours overrides and exports full
  history.
- **`dev` (unreleased)**: opt-in `layers.y_plus_fit` (boolean); two-pass
  layering gate softened to `mesh_quality.relaxed` (+ `layering_relaxed` override); diagnostic
  `field_outputs` (wall shear, y+ field, `fieldMinMax` max-y+ location, per-patch wall-pressure
  opt-in `vorticity`); Studio "Diagnostic Field Outputs" controls; per-part
  `forces_<part>` build-up table; checkMesh failure-reason detail; per-patch y+ min–max range;
  remote bundle now fetches the layering log and the field reductions; `wallPressure` FO removed
  (unknown in v2606).

---

*This knowledge base is intended to let a new senior engineer (or AI) onboard: every major
flow, design decision, risky seam and test surface is mapped to concrete files and line
numbers, all sourced from the code on disk.*
