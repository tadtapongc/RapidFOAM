# RapidFOAM — Current Architecture

> Whole-codebase architecture review (supersedes the earlier meshing-only snapshot).
> Snapshot: commit `74e1f50` (`feat(mesh): simplify defaults, retune y+, add two-pass layering and thickness reporting`), branch `dev`, version `1.5.0`.
> Test baseline: `python -m unittest discover -s tests` → **330 tests, OK (15 skipped)**, ~3 s. Front end: `npm test` (node `--test` + jsdom).

---

## 1. What RapidFOAM is

A Python automation suite that turns an **ASCII STL + minimal JSON config** into a complete,
runnable OpenFOAM case, runs it locally or on a SLURM cluster, and post-processes forces,
mesh quality and surface integrity. It ships a CLI and a browser "Studio" (FastAPI + vanilla
JS/Three.js).

| Property | Value |
| --- | --- |
| Language | Python (`>=3.9`; CI matrix 3.9–3.13; dev machine runs 3.14) |
| Runtime deps (core) | **none** — stdlib only |
| Optional deps | `fastapi`, `uvicorn`, `paramiko`, `python-multipart` (`[web]`); `matplotlib` (`[plot]`) |
| Build | Hatchling (`pyproject.toml`), package root `src/rapidfoam` |
| Front end | Vanilla JS + Three.js served by FastAPI (`web/static`, no bundler) |
| Tests | `unittest` (Python, 330), `node --test` + jsdom (JS, `tests/js`) |
| Lint/format | Ruff configured (`pyproject.toml:73`, `line-length=120`, `target-version=py39`) — **not run in CI** |
| CI | `.github/workflows/ci.yml`: ubuntu+windows × py3.9–3.13, `pip install -e ".[web,plot]"`, `python -m unittest discover tests`, `python -m build` |
| External system | ESI/OpenCFD OpenFOAM v2606 (only tested version): `surfaceCheck`, `surfaceFeatureExtract`, `blockMesh`, `decomposePar`, `snappyHexMesh`, `checkMesh`, `renumberMesh`, `potentialFoam`, `simpleFoam` |

### Entry points (`pyproject.toml:57–68`)

| Command | Target | Role |
| --- | --- | --- |
| `rapidfoam` / `rapidfoam-setup` / `setup_case.py` | `cli.setup_main` | Generate a case from config |
| `rapidfoam-forces` / `read_forces.py` | `cli.forces_main` | Forces + `--mesh`/`--surface`/`--yplus` verification |
| `rapidfoam-studio` / `rapidfoam-web` | `web.server:main` | Web Studio (FastAPI + static UI) |
| `rapidfoam-monitor` | `postproc.convergence_monitor:main` | Standalone auto-stop monitor |
| `python -m rapidfoam` | `__main__.py` → `cli.setup_main` | Same as CLI setup |
| `cfd-*` | same targets | Backward-compatible aliases |

---

## 2. Repository / package map (measured sizes)

```
RapidFOAM/
├─ configs/                 # tracked: config.json only; ~28 scratch *.json locally (gitignored)
├─ stl/                     # tracked: geometry.stl sample only (gitignored otherwise)
├─ cases/                   # generated case dirs (gitignored; samples NOT committed)
├─ src/rapidfoam/
│  ├─ __init__.py           # __version__ = "1.5.0"                                     (4)
│  ├─ __main__.py           # python -m rapidfoam → cli.setup_main                      (10)
│  ├─ config.py             # DEFAULT_CONFIG + load/deep_merge/validate                (679)
│  ├─ geometry.py           # axes + domain + presets + sizing + grading + layers    (1153)  GOD MODULE
│  ├─ stl_utils.py          # ASCII STL IO + EdgeStats/FeatureAngleStats             (462)
│  ├─ cli.py                # setup_main / forces_main / _do_generate                 (706)  GOD FUNCTION
│  ├─ writers/
│  │  ├─ base.py            # FoamFile header/footer + bool_str                        (34)
│  │  ├─ constants.py       # transportProperties, turbulenceProperties                (30)
│  │  ├─ fields.py          # 0/ U, p, k, omega, nut                                  (198)
│  │  ├─ mesh.py            # blockMeshDict, snappyHexMeshDict, featureExtract         (466)
│  │  ├─ solver.py          # controlDict, fvSchemes, fvSolution, decomposeParDict     (267)
│  │  └─ scripts.py         # Allrun, Allrun.parallel, Allclean, run.sh, AST embed     (541)  FRAGILE
│  ├─ postproc/
│  │  ├─ forces.py          # force/moment/coeff parsing + convergence                 (546)
│  │  ├─ checkmesh.py       # checkMesh + layer-coverage parser + verdict              (833)  GOD MODULE
│  │  ├─ surfacecheck.py    # surfaceCheck parser + pipeline gate                      (362)
│  │  ├─ yplus.py           # yPlus.dat reader + regime-aware verdict                  (157)
│  │  ├─ residuals.py       # solverInfo parser                                         (90)
│  │  ├─ plotting.py        # matplotlib static + live convergence plots               (267)
│  │  ├─ compare.py         # multi-case comparison table                               (88)
│  │  └─ convergence_monitor.py # importable auto-stop monitor                         (165)
│  └─ web/
│     ├─ server.py          # FastAPI app + 28 routes + helpers                    (2797)  GOD MODULE
│     ├─ ssh_client.py      # Paramiko SSH/SFTP + embedded cluster inventory script    (935)  FRAGILE
│     └─ static/
│        ├─ index.html      # single page, all views                                (1403)
│        ├─ css/style.css                                                           (2278)
│        └─ js/
│           ├─ app.js       # one class CFDApp (lines 120–4182)                     (4183)  GOD CLASS
│           ├─ viewer.js    # STLViewer (Three.js)                                    (969)
│           ├─ charts.js    # TelemetryCharts (Chart.js-free canvas)                  (605)
│           ├─ telemetry2d.js, telemetry3d.js                                         (298/141)
├─ tests/                   # 330 unittest tests + tests/js (node)
│  ├─ test_web_api.py       # 1785 lines — god test file for the whole API
│  ├─ test_checkmesh.py 475, test_regressions.py 432, test_auto_sizing.py 354,
│  │  test_layers.py 312, test_surfacecheck.py 292, test_shell_scripts.py 241,
│  │  test_grading.py 186, test_yplus.py 97
│  └─ js/ app.test.mjs 753, viewer.test.mjs 205, harness.mjs 150
├─ docs/                    # historical bug-hunt reports
├─ package.json             # front-end test tooling only (jsdom)
├─ pyproject.toml           # build + scripts + optional deps + ruff
├─ run_app.bat / run_app.sh # 1-click Studio launchers
├─ setup_case.py / read_forces.py # 7-line shims to cli
└─ README.md, CHANGELOG.md, LICENSE
```

---

## 3. Architectural style

Layered-by-folder, **dictionary-driven**, with a single shared mutable `cfg` dict as the
de-facto inter-module interface. Folder names suggest layers, but the real coupling follows
`cfg` and a handful of cross-cutting helpers. Three defining traits:

1. **`cfg` is the spine.** `data/geometry/writers/postproc` all receive and mutate the same
   nested dict. There is no typed interface between derivation and emission.
2. **Three "generations" of the same pipeline coexist** (see §6).
3. **Generation and measurement are separated** (`writers/` vs `postproc/`) but leak into each
   other (`writers/scripts.py` imports `postproc.forces`).

```
                        ┌──────────────────────────────────────────────┐
   INPUT / config.json  │  DEFAULT_CONFIG + user JSON + overrides        │
                        └───────────────┬──────────────────────────────┘
                                        │ config.load_config / deep_merge
            ┌───────────────────────────┴───────────────────────────┐
            ▼                                                       ▼
   cli._do_generate  ── derives ──>  geometry.*              postproc.* (parse logs)
        │            config.validate  (axes/domain/mesh/layers)     ▲
        │                                                          │
        ▼                                                          │
   writers/{mesh,fields,solver,constants,scripts} ── emit case    │
        │                                                          │
        ▼                                                          │
   cases/<name>/ (0/, constant/, system/, Allrun*, run.sh,   ──────┘
                  case_config.json, convergence_monitor.py)
        │
        ▼  local shell  |  web/ssh_client.py (cluster)
   OpenFOAM chain → logs → postproc readers → CLI flags / Web API
```

---

## 4. Real module boundaries vs folder boundaries

| Intended concern | Folder(s) today | Real coupling / overlap |
| --- | --- | --- |
| Config input | `config.py` | Merge logic duplicated in `web.server.merge_config_with_defaults` (server.py:186) |
| Mesh derivation | `geometry.py` | Partly re-implemented in `web.server.layer_preview` (server.py:203) and by-hand preset application in `cli._do_generate` (cli.py:226–258) |
| OpenFOAM emission | `writers/` | `writers/scripts.py` imports `postproc.forces` (scripts.py:50) and embeds its AST |
| Execution | generated shell + `web/ssh_client.py` | `ssh_client` embeds a parallel Python post-processor (ssh_client.py:674–896) |
| Measurement | `postproc/` | `surfacecheck` imports `checkmesh` **private** helpers; `server` re-summarises y+ and re-parses force/y+ text |
| Transport / UI | `cli.py`, `web/` | `web.server` imports `cli._do_generate` as a library (server.py:701) |

### 4.1 Actual import edges (`rapidfoam.*` only)

```
config      ──> geometry ──> stl_utils
cli         ──> config, geometry, stl_utils, writers.{constants,fields,mesh,scripts,solver},
                postproc.{forces,plotting,compare,yplus,surfacecheck,checkmesh}
geometry    ──> stl_utils
writers/mesh,fields,solver,scripts ──> geometry
writers/constants ──> writers/base
writers/scripts ──> postproc.forces            ← LAYERING INVERSION
postproc/forces ──> geometry (AXIS_MAP, axis_index_sign)
postproc/residuals,yplus ──> postproc.forces._dir_time   ← private cross-import
postproc/surfacecheck ──> postproc.checkmesh.{_config_has_symmetry,_load_config_dict} ← private
web/server  ──> config, geometry, stl_utils, postproc.*, web.ssh_client, cli._do_generate
web/ssh_client ──> paramiko only (embedded script re-implements postproc)
```

**Clean direction (intended):** `cli/web → writers/postproc → geometry → stl_utils`.
**Violations / smells:** `writers/scripts → postproc.forces`; `web/server → cli`
(transport → transport); `surfacecheck → checkmesh` private helpers; `residuals/yplus →
forces._dir_time` private helper.

### 4.2 Coupling heat map

```
              config geometry stl_utils writers postproc cli web ssh
config           —       Y        .        .       .      .   .   .
geometry         .       —        Y        .       .      .   .   .
stl_utils        .       .        —        .       .      .   .   .
writers          .       Y        .        —      Y**      .   .   .
postproc         .      Y*        .        .       —      .   .   .
cli             Y        Y        Y        Y       Y       —   .   .
web             Y        Y        Y        Y       Y      Y*   —   Y
ssh_client       .       .        .        .      (Y)     .   .   —

Y*  postproc.forces imports geometry axis math
Y** writers/scripts imports postproc.forces and embeds its source  (INVERSION)
web→cli._do_generate (transport→transport)                          (SMELL)
surfacecheck→checkmesh private helpers                              (SMELL)
(Y) ssh_client embeds a divergent copy of postproc.forces           (DUP)
```

---

## 5. The `cfg` spine and its lifecycle

`cfg` is a nested plain dict. Sections: `flow`, `outputs`, `fluid`, `turbulence`, `patches`,
`domain_faces`, `layers`, `snap`, `mesh_quality`, `mesh_params`, `feature_extract`,
`surface_check`, `solver`, `slurm`, `parallel`, `force_refs`, `vehicle`, `schemes`,
`linear_solvers`, `simple`, `relaxation`, `wall_functions`, `potential_flow`. Generators
inject computed sections: `stl_names`, `domain_box`, `mesh_params`, `layers["_resolved"]`.

```
config.json
  │ load_config(): strip keys starting "_", pop "overrides", deep_merge(DEFAULT_CONFIG, ...)
  ▼
cfg (defaults + user + overrides)
  │ validate(cfg, project_dir) -> (errors, warnings)        [presets NOT applied yet]
  ▼
cli._do_generate():                                        (cli.py:95–440)
  │  resolve STL names -> cfg["stl_names"]
  │  cfg["domain_faces"] = face_assignments(cfg)
  │  stream STLs -> EdgeStats/FeatureAngleStats + combined bounds
  │  cfg["domain_box"] = compute_domain_box(...)
  │  cfg["mesh_params"] = compute_mesh_params(...)          [MUTATES cfg["feature_extract"]]
  │  apply fidelity preset fields by hand                  (cli.py:226–258)
  │  resolve_layers(...)                                    [MUTATES cfg["layers"] + _resolved]
  │  write all writer outputs (writers/*)
  │  copy 0/ -> 0.orig
  │  write case_config.json snapshot (post-mutation, includes _resolved)
  ▼
OpenFOAM run -> logs -> postproc parse -> verdict
```

Notable properties:
- **Derivation mutates `cfg` as a side effect** (`compute_mesh_params` writes
  `cfg["feature_extract"]["includedAngle"]`, geometry.py:787–790; `resolve_layers` writes
  `cfg["layers"]`, geometry.py:1049, 1122–1125, 1140–1147; `_apply_ground_layer_policy`
  clears `layers["ground_layers"]`, geometry.py:1017–1029).
- **Validation runs before presets/derivation**, so layer warnings are computed against
  values that are later overwritten (`config.py:558–601` vs `cli.py:221–265`).
- **Presets are applied in `_do_generate` by hand** (10 fields), but also read internally by
  `compute_mesh_params` (geometry.py:722–723) and again, partially, by `web.layer_preview`.
- **The preset guards are inconsistent.** Most fields use `user_set(raw_user, ...)`, but
  `slurm.time` uses a value sentinel (`if cfg["slurm"]["time"] == "auto"`, cli.py:255). The
  shipped `configs/config.json:92–93` and the Studio (`app.js:779–780`) both write concrete
  `slurm.time`/`mem_per_cpu`, so the preset values never apply (detail in
  `PAIN_POINTS.md #9c`).
- Writers index required `cfg` keys directly; the injection contract is implicit.

---

## 6. The three pipeline "generations"

| # | Path | Owner | What it computes | Divergence risk |
| --- | --- | --- | --- | --- |
| 1 | CLI generate | `cli._do_generate` (cli.py:95–440) | Full derivation + all writers | Reference implementation |
| 2 | Web preview / generate | `web.server.layer_preview` (server.py:203–238); `api_case_generate_and_submit` → `cli._do_generate` (server.py:653–765) | Layers/preview only; generation delegates to #1 | Preview applies a **subset** of the preset (n_layers, expansion_ratio, y_plus_target) ⇒ preview can disagree with the generated case |
| 3 | Remote generate + inventory | generated `Allrun*` from `writers/scripts.py`; embedded script in `ssh_client.list_remote_cases_detailed` (ssh_client.py:674–896) | Shell mesh/solve pipeline; remote force parsing | Embedded script re-implements force parsing / symmetry / convergence; can disagree with `postproc.forces` |

The same mesh command sequence and the `surfaceCheck` gate block are repeated in
`Allrun.parallel`, `Allrun`, and `run.sh` (scripts.py:212–254 injected at 286, 342, 479; mesh
block at 288–298, 343–350, 481–506; `stopAt` restore at 273–275, 339–341, 476–478).

---

## 7. Subsystem deep dives

### 7.1 Input / config — `config.py`
- `DEFAULT_CONFIG` (config.py:28–277) is the universal default tree.
- `load_config` (config.py:297) = strip comment keys + `overrides` + `deep_merge`.
- `validate` (config.py:333–644) is a 300-line procedural validator that imports `geometry`
  lazily inside the function (config.py:338) to dodge a cycle. It knows about mesh_params,
  layers, grading, verdict bands, surface_check, domain_faces, axis independence.
- `find_stl` (config.py:660) is an extension/name-fuzzy resolver.
- `_find_stl` + `validate` duplicate the STL-naming/stem logic also used in
  `cli._do_generate` (cli.py:142–154) and `web.server._local_stl_exists` (server.py:198).

### 7.2 Geometry / mesh derivation — `geometry.py` (GOD MODULE)
Seven responsibilities in one file:
1. Axis math (`AXIS_MAP`, `parse_axis`, `axis_index_sign`, `up_axis_index`,
   `flow_axis_index_sign`, `vec_str`, geometry.py:35–91). **Used far outside meshing.**
2. Field/turbulence helpers (`velocity_vector`, `turbulence_values`, geometry.py:87–107).
3. Face assignment (`face_role`, `face_assignments`, geometry.py:110–132).
4. Domain sizing (`compute_domain_box`, geometry.py:139–246).
5. Fidelity presets (`FIDELITY_PRESETS`, geometry.py:253–381 — `fast`/`standard`/`fine`).
6. Opt-in feature sizing/angle + grading (`_resolve_feature_sizing` 388, `_resolve_feature_angle`
   455, `_graded_axis` 517, `compute_block_grading` 556).
7. Mesh assembly + y+/layer math (`compute_mesh_params` 672–936, `estimate_friction_velocity`
   943, `first_layer_height` 962, `_apply_ground_layer_policy` 969, `resolve_layers` 1034–1150).

Non-mesh code imports axis helpers: `config.validate` (config.py:338),
`postproc.forces` (forces.py:11), `writers/{mesh,fields,solver,scripts}`, `web.server`
(server.py:32). So a meshing change can break validation and post-processing.

### 7.3 STL I/O — `stl_utils.py`
Clean, single responsibility, O(1)-memory streaming:
- `is_binary_stl` (172), `stl_analyze`/`stl_analyze_full`/`stl_info` (200–326) + `_analyze`
  (224) build `EdgeStats`/`FeatureAngleStats`.
- `read_stl`/`write_stl`/`stl_bounds`/`copy_stl` (329–462). `copy_stl` rewrites every
  `solid`/`endsolid` line for multi-body merges (regression-tested).
- Dataclasses `FeatureAngleStats` (40) and `EdgeStats` (110) with streaming histograms.

### 7.4 Emission — `writers/`
- `base.py` (FoamFile header/footer, `bool_str`), `constants.py`, `fields.py`, `solver.py`
  are small, pure dict writers.
- `mesh.py` — `write_snappy_hex_mesh_dict` (143–432) reads six `cfg` sections directly,
  auto-places `locationInMesh` with an axis heuristic (212–255 — duplicates geometry axis
  logic), renders two `meshQualityControls` blocks via a nested `_render()` closure, and emits
  a second two-pass file. `write_block_mesh_dict` silently falls back to uniform cell counts
  when `block_cells` is missing (66–73).
- `scripts.py` — 541 lines generating `convergence_monitor.py`, `Allrun`, `Allrun.parallel`,
  `Allclean`, `run.sh`. Contains `_clean_force_helpers()` (48–65) which uses
  `inspect.getsource` + `ast.NodeTransformer` + `ast.unparse` to inline five
  `postproc.forces` functions into the generated monitor.

### 7.5 Execution
- **Local:** generated `/bin/bash` scripts (scripts.py) invoke the OpenFOAM chain, using
  `RunFunctions` (`runApplication`/`runParallel`). `Allclean` restores `0.orig`.
- **Cluster:** `web/ssh_client.py` (`ClusterSSHClient`, 70–831) does SSH/SFTP, SLURM
  submit/cancel with graceful stop, atomic directory download (tar stream → hidden staging
  dir → `os.replace`), and remote file bundles. `list_remote_cases_detailed` base64/runs a
  ~223-line embedded Python script (674–896) that re-implements force parsing, symmetry
  detection and convergence with its own `parse_axis` returning `(idx, sign)` (different
  shape from `geometry.parse_axis`'s 3-tuple) and its own `read_tail`.

### 7.6 Measurement — `postproc/`
Pure parsers (never run OpenFOAM):
- `forces.py` — force/moment/coefficient discovery + parsing, layout detection, symmetry,
  convergence (`check_convergence`), `parse_tabular_dat`, column normalisers, `window_stats`.
- `checkmesh.py` (GOD MODULE) — regex parsers, `parse_boundary_patches`, `parse_layer_coverage`,
  tiered `check_mesh_quality` verdict, plus config discovery (`_load_config_dict`,
  `_config_has_symmetry`, `checkmesh_targets_from_case`).
- `surfacecheck.py` — parser + `check_surface` gate; imports checkmesh private helpers.
- `yplus.py` — parser + regime-aware `check_yplus_target`.
- `residuals.py`, `plotting.py`, `compare.py`, `convergence_monitor.py`.

### 7.7 Transport / UI — `web/server.py` (GOD MODULE) + static
2797 lines, **28 routes**, and it owns: credential file storage/ACLs (136–183), STL
list/upload/serve (505–650), domain/layer preview (203–349), case generation + cluster sync +
SLURM submit (653–790), threaded case download + progress registry (793–886), remote telemetry
bundle + cache (888–1005), force parsing/projection/coefficients/aero balance (1024–1650),
residual/solver diagnostics (1722–2083), y+/mesh/surface summarisation (2085–2330),
case listing/deletion and static serving (2332–2680), plus `main()`.

Front end: `app.js` is a single `CFDApp` class (app.js:120–4182) holding all state (viewers,
charts, telemetry polling, cluster UI, config editing) with 150+ methods; `index.html` (1403)
holds all views; `style.css` (2278). Preset labels/defaults are hard-coded in `app.js`
(e.g. app.js:1157–1170) even though the server already exposes
`/api/config/schema-defaults` (server.py:427–463).

### 7.8 Studio config sync: preview ⇄ form ⇄ generated case

The Studio holds its own config model (`CFDApp.activeConfig`, default at app.js:155–235),
edits it from the form (`buildConfigFromVisualForm`, app.js:690–941), renders the effective
mesh from `layer_preview`, and posts the config to `/api/case/generate-and-submit`. These four
representations do **not** share one source of truth:

```
  app.js default activeConfig ──┐
  user form edits ───────────────┤ buildConfigFromVisualForm (app.js:690)
  JSON drawer edits ─────────────┘        │  always sets symmetry_plane + domain_faces
                                          ▼
  POST /api/case/generate-and-submit ──► merge_config_with_defaults (server.py:186)
        │                                        │  writes configs/<case>.json (raw)
        │  preview: /api/geometry/domain-box      ▼
        │    -> layer_preview (server.py:203)   cli._do_generate (server.py:701)
        │       applies 3/10 preset fields         applies 10/10 preset fields
        └────────────────────────────────────┐  ├─ writes case_config.json (post-mutation)
                                             ▼  ▼
             Studio panels <── postproc readers ─┘
```

Concrete divergences (detail in `PAIN_POINTS.md #9b`):

| Representation | Source | Applies presets? |
| --- | --- | --- |
| Form defaults / placeholders | hard-coded `app.js:1124–1126` + partial `/api/config/schema-defaults` | No (stale vs `FIDELITY_PRESETS`) |
| Preview (`layer_preview`) | `server.py:203–238` | 3 of 10 fields |
| Validation | `validate()` on merged raw config | No |
| Generated case | `cli._do_generate` (`cli.py:226–258`) | 10 of 10 fields |

Two consequences visible to a user: (a) a full-car configuration cannot be expressed — the form
always writes `symmetry_plane` (app.js:171, 520–521, 731–732), and that makes
`checkmesh._config_has_symmetry` (checkmesh.py:774–777) treat it as a half model while
`forces.is_symmetry_case` (forces.py:86–111) does not; (b) the previewed/preset layer counts and
y+ differ from the generated case because the preview and schema endpoints drop the fields the
generator uses.

---

## 8. Key data structures

| Structure | Where | Shape |
| --- | --- | --- |
| `cfg` dict | everywhere | namespaced mutable config tree (see §5) |
| `MeshParams` | `compute_mesh_params` (geometry.py:913–936) | plain dict: `base_cell_size`, `surface_level`, `edge_level`, `distance_levels`, `refinement_regions`, `grading`, `block_cells`, `resolveFeatureAngle`, provenance `grading_info`/`auto_size`/`feature_angle` |
| resolved layers | `cfg["layers"]["_resolved"]` (geometry.py:1073–1087) | `u_tau`, `first_layer_thickness`, `min_thickness`, `stack`, `y_plus_effective`, clamp provenance |
| `EdgeStats` / `FeatureAngleStats` | stl_utils.py:40–169 | streaming log/linear histograms with `merge`/`percentile` |
| `BBox` | stl_utils.py:26 | `((xmin,ymin,zmin),(xmax,ymax,zmax))` |

---

## 9. Testing & CI

- **Python:** 330 `unittest` tests in 8 files, ~3 s. Strong net around the fragile spots:
  `test_checkmesh`, `test_surfacecheck`, `test_grading`, `test_layers`, `test_auto_sizing`,
  `test_shell_scripts`, `test_regressions` (multi-solid rename, symmetry projection, force
  layouts, grading). `test_web_api.py` (1785 lines) covers the whole HTTP surface.
- **JS:** `tests/js/{app,viewer}.test.mjs` (958 lines) + `harness.mjs`, via `npm test`
  (node `--test`, jsdom). Package.json only lists the two test files explicitly.
- **CI** runs Python tests on ubuntu+windows × py3.9–3.13 and `python -m build`. **Gaps:**
  no `ruff`/lint step, no type check, **no `npm test`**, and the Python matrix includes 3.9
  though the generated cluster monitor targets Python 3.6 via AST annotation stripping.

### Historical context
`docs/bug-hunt-BUGS.md` / `bug-hunt-BUGS_FIXED.md` record 15 fixed defects (XSS via
`innerHTML`, telemetry poll stall, download races, `copy_stl` multi-solid merge, symmetry
projection crash on non-axis columns, etc.). Several were caused by exactly the duplication
this review flags (CLI vs Web/JS logic drift), which is why the codebase already has a strong
regression suite — the refactor should treat it as the contract.

---

## 10. Summary of architectural pain (detail in `PAIN_POINTS.md`)

1. `geometry.py`, `web/server.py`, `app.js` are god modules/classes with very high fan-out.
2. The effective mesh/preset logic is implemented 2–3× (CLI vs Web preview vs geometry internals).
3. Derivation mutates `cfg`; validation/derivation use different sources of truth.
4. `writers/scripts.py` inverts the dependency direction and embeds `postproc.forces` source via AST.
5. The remote inventory script is an unsearchable, divergent copy of `postproc.forces`.
6. Config discovery, y+ verdicts, tail-reading and symmetry detection are each duplicated.
7. No typed seam between derivation and emission (no `MeshPlan`); writers index `cfg` keys.
8. Front end duplicates server-side preset/metric knowledge.
9. **Studio config is not the effective config**: it always forces a symmetry plane (so a
   full-car case is mis-reported), previews only 3/10 preset fields, and its placeholders/schema
   drop fields the generator applies (§7.8).
10. CI misses lint, types and JS tests; ruff is configured but unused.
