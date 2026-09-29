# RapidFOAM — Target Architecture

Goal: give the whole suite **explicit, testable seams** and **single sources of truth**, without
rewriting the working OpenFOAM generation/measurement logic. Guiding principle:
**extract and re-route, don't rewrite.**

The target keeps the current public surface (CLI commands, `config.json` schema,
`case_config.json` shape, HTTP routes, import paths) working via re-export shims, so no test and
no downstream script has to change until a shim is deliberately retired.

---

## 1. Bounded contexts

Six contexts, each with a clear owner and an enforced dependency direction.

| Context | Owns | May depend on | Must not depend on |
| --- | --- | --- | --- |
| **Core** (`core/`) | axes, face assignment, config defaults/loader/validation, case-config reading, case runtime discovery | stdlib | meshing, casegen, postproc, cli, web |
| **Geometry I/O** (`geometry/`) | ASCII STL read/write/copy, bbox, edge/angle stats | core | meshing, casegen, postproc |
| **Meshing** (`meshing/`) | fidelity presets, domain, sizing, grading, layers, `MeshPlan`, mesh-dict writers | core, geometry | postproc, casegen, cli, web |
| **Case assembly / execution** (`casegen/`) | build case files, shell scripts, runtime assets | core, geometry, meshing, runtime | postproc, cli, web |
| **Runtime assets** (`runtime/`) | stand-alone scripts copied to cases/clusters (convergence monitor, remote inventory/reader) | core (only) | casegen, web |
| **Measurement** (`postproc/`) | parse OpenFOAM logs/artifacts, verdicts | core | meshing, casegen, web |
| **Transport / UI** (`cli/`, `web/`) | argument parsing, HTTP, rendering | all of the above | — |

Target dependency graph (arrows = "depends on"):

```
        cli ─────────────┐
                         ├──> casegen ──> meshing ──> geometry ──> core
        web ─────────────┘        │            │
          │                       └──> runtime │
          └──────────────────> postproc ───────┘
```

The two current inversions are removed:
- `writers/scripts → postproc` becomes `casegen → runtime` (a leaf asset module; no postproc import).
- `web → cli._do_generate` becomes `cli → casegen.builder` and `web → casegen.builder`.

---

## 2. Target package layout

```
src/rapidfoam/
├─ core/
│  ├─ __init__.py
│  ├─ axes.py            # parse_axis, axis_index_sign, up_axis_index, flow_axis_index_sign, vec_str
│  ├─ faces.py           # face_role, face_assignments  (uses axes)
│  ├─ fields.py          # velocity_vector, turbulence_values            (moved out of geometry)
│  ├─ config/
│  │  ├─ defaults.py     # DEFAULT_CONFIG, FIDELITY_NAMES
│  │  ├─ loader.py       # load_config, deep_merge, user_set, find_stl
│  │  └─ validate.py     # validate  (one module, presets-aware)
│  └─ caseconfig.py      # read_case_config(), has_symmetry(), yplus_target(), solver_end_time(),
│                        # mesh_targets(), surface_policy(), load_stl_files(), load_vehicle_geometry()
├─ geometry/
│  ├─ __init__.py        # re-export facade for the old stl_utils API (compat)
│  ├─ stl_io.py          # is_binary_stl, stl_info, read_stl, write_stl, copy_stl, stl_bounds
│  └─ stl_stats.py       # EdgeStats, FeatureAngleStats, stl_analyze(_full), dihedral
├─ meshing/
│  ├─ __init__.py
│  ├─ presets.py         # FIDELITY_PRESETS + apply_fidelity_preset(cfg, raw_user, user_set)
│  ├─ domain.py          # compute_domain_box
│  ├─ sizing.py          # _resolve_feature_sizing, _resolve_feature_angle
│  ├─ grading.py         # _graded_axis, compute_block_grading
│  ├─ layers.py          # estimate_friction_velocity, first_layer_height, resolve_layers (pure)
│  ├─ plan.py            # MeshPlan / LayerSpec / WakeBox + build_mesh_plan()
│  ├─ writers/           # block_mesh.py, snappy.py, feature_extract.py
│  └─ pipeline.py        # emit_mesh_files(plan, case_dir)
├─ casegen/
│  ├─ __init__.py
│  ├─ builder.py         # build_case(cfg_path, project_dir, dry_run, reporter)  ← used by CLI and Web
│  ├─ base.py, constants.py, fields.py, solver.py
│  └─ scripts.py         # one parameterized shell template + runtime-asset selection
├─ runtime/
│  ├─ convergence_monitor.py   # real, importable, testable (replaces AST-embedded source)
│  └─ remote_cases.py          # cluster inventory + canonical force reading (replaces embedded blob)
├─ postproc/             # existing modules; remove private cross-imports; no meshing/casegen imports
│  └─ case_runtime.py    # shared artifact discovery: force/residual/yplus/log/tail readers
├─ cli.py                # thin argument parsing; delegates to casegen.builder (compat entry points)
└─ web/
   ├─ app.py             # FastAPI app assembly + CORS + static mount + main()
   ├─ routers/
   │  ├─ config.py, stl.py, case.py, cluster.py, telemetry.py, static.py
   ├─ services/
   │  ├─ telemetry.py    # force/symmetry/coeff/aero-balance + mesh/surface/y+ summarisation
   │  ├─ downloads.py    # threaded case download + progress registry
   │  └─ cluster_state.py# credentials + saved config + SSH lifecycle
   ├─ ssh_client.py
   └─ static/            # index.html, css/, js/ (split by concern)
```

> **Compatibility rule.** During migration keep `rapidfoam/geometry.py`,
> `rapidfoam/stl_utils.py`, and `rapidfoam/writers/` as **shims** that re-export from the new
> modules, and keep module-level `app` importable from `web/server.py`. No test or external
> import needs to change until a shim is deleted.

---

## 3. The central new seam: `MeshPlan`

Today the interface between derivation and emission is a mutable `cfg` dict. The target
interface is an **immutable plan value object** produced once and consumed by every writer, the
CLI display, the Studio preview, and tests.

```python
# meshing/plan.py
@dataclass(frozen=True)
class LayerSpec:
    n_layers: int | None
    expansion_ratio: float
    relative_sizes: bool
    first_layer_thickness: float | None
    min_thickness: float | None
    ground_n_layers: int
    two_pass: bool
    provenance: Mapping[str, Any]   # u_tau, y_plus_target/effective, clamped, notes

@dataclass(frozen=True)
class WakeBox:
    name: str
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    level: int

@dataclass(frozen=True)
class MeshPlan:
    domain_box: Mapping[str, tuple[float, float, float]]
    base_cell_size: float
    surface_level: tuple[int, int]
    edge_level: int
    distance_levels: tuple[tuple[float, int], ...]
    refinement_regions: tuple[WakeBox, ...]
    grading: tuple[float, float, float]
    block_cells: tuple[int, int, int]
    resolve_feature_angle: float
    location_in_mesh: tuple[float, float, float]
    layers: LayerSpec
    quality: Mapping[str, float]
    feature_extract: Mapping[str, Any]
    provenance: Mapping[str, Any]          # auto_size, feature_angle, grading_info

    def as_config(self) -> dict[str, Any]: ...   # back-compat cfg["mesh_params"] view
```

- `build_mesh_plan(cfg, bounds, feature_stats, angle_stats, *, raw_user, user_set)` is the
  **only** place presets, sizing, grading and layers are resolved.
- Writers take `(plan, stl_names, patches, quality, snap, case_dir)` and never index `cfg`.
- `plan.as_config()` keeps `case_config.json` and the UI's `mesh_params`/`layers._resolved`
  shape working unchanged.
- Derivation no longer writes `cfg["layers"]` / `cfg["feature_extract"]`. The preset
  application returns a new effective config; the pipeline decides whether to persist it.
- `locationInMesh` is computed once in `plan.py` (using `core.axes`) and stored on the plan.

This single object fixes pain points **1, 2, 4, 10, 11** at once.

---

## 4. Single sources of truth

| Concept | Today | Target |
| --- | --- | --- |
| Effective mesh settings | `compute_mesh_params` + `cli._do_generate` + `server.layer_preview` | `meshing.plan.build_mesh_plan` |
| Effective whole config | `cli.py:226–258` (10 fields) vs `server.layer_preview` (3 fields) vs JS defaults | `core.config.loader.effective_config(raw_user)` — one preset/override resolution used by CLI, Web, preview and validation |
| Preset-guard predicate | mixed: `user_set()` for most fields, `slurm.time == "auto"` sentinel for SLURM (`cli.py:255`) | one `user_set`-style predicate for every preset field; shipped/Studio configs omit fields they do not intend to pin |
| Symmetry semantics | `symmetry_plane` numeric (checkmesh) vs `domain_faces` (forces) vs forced `0.0` in Studio | one `core.caseconfig.has_symmetry`; canonicalization keeps `symmetry_plane: null` representable; Studio sends `null` for full car |
| Config schema for the UI | partial `/api/config/schema-defaults` + stale JS fallbacks | `core.config.schema` (presets, defaults, field metadata) served whole; Studio renders from it |
| Fidelity presets | `geometry.FIDELITY_PRESETS` (+ JS copies) | `meshing.presets.FIDELITY_PRESETS`, served via `/api/config/schema-defaults` |
| Config merge | `config.load_config` + `server.merge_config_with_defaults` | `core.config.loader` |
| Case config discovery | 5–7 helpers in `checkmesh`, `server`, `cli` | `core.caseconfig.read_case_config` |
| Symmetry detection | `forces.is_symmetry_case` + `checkmesh._config_has_symmetry` + embedded copy | `core.caseconfig.has_symmetry`; `postproc`/`runtime` call it |
| y+ verdict | `postproc.yplus.check_yplus_target` vs `server._summarise_yplus` | `postproc.yplus.check_yplus_target` only; Web adapts the shape |
| Force parsing | `postproc.forces` + `ssh_client` embedded + `convergence_monitor` | `postproc.forces`; `runtime/remote_cases.py` imports/copies it |
| File tail reading | `server.read_file_tail`, `server._read_text_tail_lines`, `ssh_client.read_tail` | `core.caseconfig.read_tail` (or `postproc.case_runtime`) |
| Shell mesh pipeline | 3 duplicated template blocks | one `casegen/scripts.py` template parameterized by target |
| Axis math | `geometry.parse_axis` + `ssh_client` embedded copy | `core.axes.parse_axis` |

---

## 5. Extraction / split / merge decisions

**Extract (move as-is, mechanical):**
- `core/axes.py`, `core/faces.py`, `core/fields.py` ← `geometry.py:35–132`, `87–107`.
- `meshing/{presets,domain,sizing,grading,layers}.py` ← `geometry.py` sections.
- `meshing/writers/*` ← `writers/mesh.py`; `meshing/pipeline.py` ← orchestration in `cli._do_generate`.
- `casegen/*` ← `writers/{base,constants,fields,solver,scripts}.py` + `cli._do_generate`.
- `core/caseconfig.py` ← duplicated readers (#7); `postproc/case_runtime.py` ← artifact discovery.
- `web/services/*`, `web/routers/*` ← `web/server.py`.

**Split:**
- `stl_utils.py` → `geometry/stl_io.py` + `geometry/stl_stats.py` (input/output vs sizing stats).
- `postproc/checkmesh.py` → keep parser + `check_mesh_quality`; move `_load_config_dict`,
  `_config_has_symmetry`, target/band extraction to `core/caseconfig.py`.
- `web/server.py` → routers (HTTP) + services (logic) + `app.py` (assembly).
- `web/static/js/app.js` (`CFDApp`) → modules by view: `configForm`, `viewerPanel`,
  `telemetry`, `casesArchive`, `clusterPanel`, `downloads`, `toast/help`.

**Merge:**
- Three shell templates → one parameterized template sharing `surface_block`, `mesh_block`,
  `stopAt` restore.
- The `_load_*`/`_yplus_*`/tail helpers → `core.caseconfig` / `postproc.case_runtime`.
- The two y+ summaries → `postproc.yplus`.
- The embedded remote script → `runtime/remote_cases.py`, reusing canonical force-reader logic.
- The generated `convergence_monitor.py` → **copy** `runtime/convergence_monitor.py` verbatim
  (with a tiny generated constants header) instead of AST extraction.

**Do not merge:** `postproc` (measurement) and `meshing` (generation) stay separate — they
read/write the same format but have opposite lifecycles.

---

## 6. Dependency rules to enforce (CI)

Add an import-linter contract (or a ~20-line `tests/test_architecture.py` that walks `ast`
imports) so the boundaries cannot erode:

1. `rapidfoam.core` must not import any other `rapidfoam` subpackage.
2. `rapidfoam.geometry` must not import `meshing`, `casegen`, `postproc`, `web`, `cli`.
3. `rapidfoam.meshing` must not import `casegen`, `postproc`, `web`, `cli`.
4. `rapidfoam.runtime` must not import `casegen`, `meshing`, `postproc`, `web`.
5. `rapidfoam.postproc` must not import `casegen`, `meshing`, `web`.
6. `rapidfoam.casegen` must not import `web` (and not `postproc`).
7. `rapidfoam.web` must not import `rapidfoam.cli`.
8. No `from rapidfoam.<other> import _private` across contexts (public helpers only).

The test fails the moment someone re-introduces the old coupling. Add it in Phase 1 so later
phases cannot regress the boundaries.

---

## 7. Web layer target shape

`web/server.py` becomes a thin assembly module; each endpoint group moves to a router and
shared logic to a service:

```
web/app.py               create_app() -> FastAPI (CORS, static, include_router); main()
web/routers/config.py    /api/config/{schema-defaults,templates,load-file}
web/routers/stl.py       /api/stl/{list,file,check-exists,upload}
web/routers/case.py      /api/geometry/domain-box, /api/case/{check-exists,generate-and-submit,
                         submit,cancel,download,download/active,download/progress,cases,cases/{name}}
web/routers/cluster.py   /api/cluster/{saved-config,connect,status,disconnect}
web/routers/telemetry.py /api/telemetry/{forces,export,residuals,solver,mesh,surface,logs}
web/services/telemetry.py   parse/project/summarise (no FastAPI imports)
web/services/downloads.py   threaded download + progress registry
web/services/cluster_state.py credentials file + saved config + SSH client singleton
```

`/api/geometry/domain-box` calls `meshing.plan.build_mesh_plan` (or a dedicated
`meshing.preview.preview_plan`) instead of the hand-rolled `layer_preview`, so the preview is
guaranteed to match generation. `web/routers/case.py` calls `casegen.builder.build_case`.

---

## 8. Front-end target shape

- Serve preset labels/defaults, metric tables and schema from `/api/config/schema-defaults`
  (`server.py:427–463`) and a metric-metadata endpoint instead of hard-coding them in
  `app.js:1157–1170`. Serve the **whole** preset (including `snap`, `nLayerIter`,
  `nRelaxIter`, `feature_extract.includedAngle`, slurm) so placeholders cannot drift.
- Make the effective config the single object the Studio renders: the domain-box/preview
  endpoint returns `effective_config` (via `core.config.loader.effective_config`) alongside the
  domain and `layer_preview`, and the form binds to that.
- Represent "no symmetry" faithfully: the Studio must be able to send `symmetry_plane: null`
  (add an explicit full-car / no-symmetry control) instead of always coercing to `0.0`
  (`app.js:731–732`), and `core.caseconfig.has_symmetry` becomes the only detector.
- Split the `CFDApp` god class into feature modules that import shared helpers (`fetchJson`,
  `escapeHtml`, DOM utils) — one per view listed in §5.
- Split `index.html`/`style.css` by view if practical, but this is **not** on the critical path.
- Add the JS test files to CI and extend jsdom coverage as modules are extracted.

---

## 9. Runtime assets (`runtime/`) — the de-embedding plan

Today the generated monitor and the remote inventory are embedded strings/AST. Target:

- `runtime/convergence_monitor.py` is a real module (already largely exists as
  `postproc/convergence_monitor.py`). `casegen/scripts.py` **copies the file** into the case and
  prepends a generated constants header (`DRAG_IDX`, `DRAG_SIGN`, `DF_IDX`, `DF_SIGN`).
- `runtime/remote_cases.py` is a standalone script uploaded/executed on the cluster; it
  contains (or imports a copied) canonical force reader rather than a re-implementation.
- Both are pure-Python, dependency-free, and covered by unit tests that call their functions on
  fixtures — no `inspect.getsource`, no `ast` rewriting.

---

## 10. What NOT to do

- **Don't rewrite the OpenFOAM dicts.** They are battle-tested and covered by tests. Move them;
  don't regenerate them in a new framework.
- **Don't change the `config.json` schema or the `case_config.json` shape.** Preserve keys,
  including the computed `_resolved` block; only move code (`plan.as_config()` is the seam).
- **Don't introduce a DI framework or plugin system.** This is a small single-tenant
  CLI/library; plain modules + dataclasses suffice.
- **Don't delete `geometry.py` / `stl_utils.py` / `writers/` early.** Keep shims until the new
  modules are proven and all internal callers/tests are migrated.
- **Don't unify `meshing` and `postproc`.** Different bounded contexts, opposite lifecycles.
- **Don't move `_do_generate` wholesale into the Web.** Extract `casegen.builder.build_case` and
  make `_do_generate` a thin CLI wrapper so console UX stays stable.
- **Don't chase a full UI rewrite.** Server-driven presets + JS module split are enough.

---

## 11. Success criteria

1. One function resolves effective mesh settings for both CLI and Studio (`build_mesh_plan`),
   proven by an equivalence test.
2. `MeshPlan` is the only input to mesh writers; writers no longer index `cfg`.
3. Derivation functions are pure (no `cfg` writes) — verified by calling them twice and
   asserting no mutation.
4. `meshing` has zero imports from `cli`/`web`/`postproc`/`casegen`; `core` imports nothing
   internal; enforced by `tests/test_architecture.py`.
5. `web/server.py` < ~400 lines, all endpoints in routers, all shared logic in services.
6. `casegen/scripts.py` has one mesh-pipeline template and no `postproc` import; the generated
   monitor is a copied, tested module.
7. Config discovery, y+ verdict, symmetry detection and force parsing each have exactly one
   implementation.
8. All 330 existing tests still pass, plus new equivalence/architecture/golden tests; CI also
   runs `ruff` and `npm test`.
