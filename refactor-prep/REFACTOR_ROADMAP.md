# RapidFOAM — Refactoring Roadmap

Ordering rule: **make the seam first, then move code behind it, then split the transport and
front-end layers.** Every phase is independently shippable and must keep
`python -m unittest discover -s tests` green (baseline: **330 tests, 15 skipped, ~3 s**).

Legend — Effort: **S** ≈ <½ day, **M** ≈ 1–3 days, **L** ≈ 1 week+.
Every step ends with the **verification checklist** at the bottom of this file.

---

## Phase 0 — Safety net & CI guardrails (do this first) — Effort **S**

The refactor cannot be validated without golden outputs. Pin current behaviour *before* moving
anything, and close the CI gaps that let JS/shell regressions through.

1. `tests/test_mesh_plan_equivalence.py`
   - Matrix of configs (fast/standard/fine × ground/symmetry × grading off/auto × auto_size
     on/off × explicit layer overrides) asserting the effective mesh/layer settings produced by
     `web.server.layer_preview` equal those `cli._do_generate` produces (normalised
     `mesh_params`, `layers`, `feature_extract`). This will **fail today** where preview omits
     preset fields (`server.py:203–238` vs `cli.py:226–258`) — record those deltas as the
     known-fix list for Phase 1.
2. `tests/test_casegen_golden.py`
   - Generate a case to a temp dir for 2–3 representative configs and snapshot the text of
     `blockMeshDict`, `snappyHexMeshDict`, `snappyHexMeshDict_layering`,
     `surfaceFeatureExtractDict`, `Allrun`, `Allrun.parallel`, `run.sh`, and `case_config.json`.
     Compare with volatile paths stripped. Reuse the pattern in `tests/test_grading.py:154–181`.
3. `tests/test_runtime_assets.py`
   - Parse a force-log fixture with `postproc.forces` **and** with the generated
     `convergence_monitor.py` helpers; assert equal results (locks down the AST-embedding
     contract before Phase 5 removes it).
4. `tests/test_studio_config_sync.py`
   - Round-trip a **full-car** config (`symmetry_plane: null`, `-x: farField`) through
     `/api/config/load-file` → form-equivalent transform → `/api/geometry/domain-box`; assert
     `has_symmetry` is False and the config is not coerced to `0.0`.
   - Assert `/api/config/schema-defaults` exposes every preset field that `_do_generate`
     applies (`end_time`, `write_interval`, `n_layers`, `expansion_ratio`, `y_plus_target`,
     `ground_layers`, `nLayerIter`, `nRelaxIter`, `nSolveIter`, `nFeatureSnapIter`, slurm
     time/mem), so JS placeholders cannot drift.
5. `tests/test_telemetry_config.py`
   - Generate a case with `overrides: {fluid: {rho: ...}, force_refs: {Aref, lRef, CofR}}` and
     assert `/api/telemetry/forces` reports the **overridden** reference values (today it
     returns defaults — see `PAIN_POINTS.md #9d`).
   - Assert a preset `layers.y_plus_target` is visible to `/api/telemetry/mesh` for a
     Studio-generated case (`configs/<case>.json` present).
   - Export a >400-iteration history and assert every iteration and coefficient column is
     present (today the CSV is truncated to ≤400 rows, `server.py:1559`).
6. CI: add a `ruff check src tests` step and a `npm test` job to `.github/workflows/ci.yml`
   (currently only `unittest` + `python -m build` run).

**Why first:** every later step moves code that must produce byte-identical dicts and identical
verdicts. Without this, "extract" and "behaviour change" become indistinguishable. Baseline is
fast (~3 s), so this is cheap insurance.

---

## Phase 1 — Quick wins (max value, low risk) — Effort **M**

### 1a. Shared case-config / artifact access — fixes #7, #14
- New `core/caseconfig.py`: `read_case_config()`, `has_symmetry()`, `yplus_target()`,
  `solver_end_time()`, `mesh_targets()`, `surface_policy()`, `load_stl_files()`,
  `load_vehicle_geometry()`, `read_tail()`.
- `read_case_config()` must resolve raw → defaults → **`overrides`** (i.e. use
  `effective_config`) and prefer the effective `<case>/case_config.json`, so telemetry, mesh
  and postproc all read the same values the solver used (`PAIN_POINTS.md #9d`).
- Replace callers (keep thin old-named wrappers for one release):
  `checkmesh.py:741` (`_load_config_dict`), `:765` (`_config_has_symmetry`), `:788`
  (`checkmesh_targets_from_case`); `surfacecheck.py:22` (drop private cross-import);
  `cli.py:447`; `server.py:1008, 1074, 1863, 1872, 2112, 2367, 2112`; `ssh_client.py:688`.
- Tests: extend `tests/test_checkmesh.py::TestConfigHasSymmetry`.

### 1b. Unify fidelity-preset application — fixes #1, #9b (part)
- New `meshing/presets.py` with `apply_fidelity_preset(cfg, raw_user, user_set_fn) -> cfg`
  (pure; returns a new effective config) containing the logic currently at `cli.py:226–258`.
- Call it from `cli._do_generate` and from `server.layer_preview`; make the Phase 0 equivalence
  test pass.
- Add `core.config.loader.effective_config(raw)` as the single resolver used by generation,
  preview and validation, so "the real config" is one thing.
- Keep `geometry.FIDELITY_PRESETS` as a re-export initially.

### 1b-bis. Canonicalize symmetry — fixes #9b (part)
- Make `core.caseconfig.has_symmetry` the only detector (delete `checkmesh._config_has_symmetry`
  semantics and the `symmetry_plane`-first branch, or make it delegate).
- Preserve `symmetry_plane: null` through the round trip; stop the Studio coercing it to `0.0`
  (`app.js:731–732`). Add a Phase 0 test asserting a full-car config survives the API.

### 1b-ter. Unify preset-guard semantics — fixes #9c
- Replace the `slurm.time == "auto"` sentinel with `_is_set("slurm", "time")` so all preset
  fields use one predicate (`cli.py:254–258`).
- Change the shipped `configs/config.json:92–93` and the Studio defaults
  (`app.js:564–565, 779–780`) to omit `slurm.time`/`mem_per_cpu` (or use `"auto"`) so the
  fidelity preset governs them.
- Make `user_set` ignore explicitly-`null` values (or document that `null` un-sets the preset).
- Test: for each preset field, generate with that field unset and assert the emitted
  `run.sh`/`controlDict`/`snappyHexMeshDict` carry the preset value.


### 1c. Extract axis/face/field helpers — fixes #3 (part)
- New `core/axes.py`, `core/faces.py`, `core/fields.py`; `geometry.py` re-exports them.
- Mechanical; the 330 tests are the net.

### 1d. Unify the y+ verdict — fixes #7
- Delete `server._summarise_yplus` (`server.py:2128–2171`) and `server._read_yplus_texts`
  (`server.py:2085–2109`); call `postproc.yplus.check_yplus_target` + `read_yplus` and adapt the
  response shape.
- Test: assert Web and CLI produce the same status for the same fixture.

### 1e. Add the architecture test — locks in boundaries
- `tests/test_architecture.py` (ast import walker) enforcing the rules in
  `TARGET_ARCHITECTURE.md §6`. Add it now so later phases cannot re-introduce coupling.

**Risk:** low. Additive/re-exports; run the full suite after each sub-step.

---

## Phase 2 — Introduce `MeshPlan` and make derivation pure — Effort **L**

This is the core structural change. Do it module-by-module.

1. Add `meshing/plan.py` (`MeshPlan`, `LayerSpec`, `WakeBox`) and `build_mesh_plan()`,
   initially **wrapping** `compute_mesh_params` + `resolve_layers` so output is identical.
2. Move `geometry.py` sections into `meshing/{presets,domain,sizing,grading,layers}.py` one at a
   time, with `geometry.py` re-exporting.
3. Make `resolve_layers` pure: return the resolved spec instead of writing `cfg["layers"]`
   (`geometry.py:1122–1147`). `build_mesh_plan` attaches it to the plan; callers persist via
   `plan.as_config()`.
4. Stop `compute_mesh_params` mutating `cfg["feature_extract"]` (`geometry.py:785–790`):
   return the recommended `includedAngle` in provenance and let the pipeline apply it.
5. Compute `locationInMesh` once in `plan.py` using `core.axes`; remove the heuristic from
   `writers/mesh.py:212–255`.
6. Rewire `cli._do_generate` and `server.layer_preview` to `build_mesh_plan`.
7. Freeze `MeshPlan` (`@dataclass(frozen=True)`) and add a mutation test: call
   `build_mesh_plan` twice on the same input; assert `cfg` is unchanged and the plans are equal.

**Intermediate state (strangler):** `geometry.compute_mesh_params` remains and delegates to
`meshing.plan`; `cfg["mesh_params"]` keeps working. Only remove the shim after Phase 3.

**Risk:** medium-high (touches the spine). Mitigations:
- Golden tests from Phase 0 gate every move.
- Keep `cfg["mesh_params"]` output shape identical via `plan.as_config()`.
- Existing tests assert mutation (`tests/test_layers.py`); provide a compatibility helper that
  applies a plan to `cfg` so they pass unchanged, then migrate the tests deliberately.

---

## Phase 3 — Extract mesh writers behind `MeshPlan` — Effort **M**

1. Split `writers/mesh.py` into `meshing/writers/{block_mesh,snappy,feature_extract}.py`.
2. Change writer signatures to take a `MeshPlan` (+ `stl_names`, `patches`, `quality`, `snap`)
   instead of indexing `cfg`.
3. Make `write_block_mesh_dict`'s missing-`block_cells` fallback (`writers/mesh.py:66–73`)
   explicit (raise or log a warning) — it currently hides a caller contract violation.
4. Add `meshing/pipeline.emit_mesh_files(plan, case_dir)` so both CLI and `casegen` call one
   entry point.
5. Keep `writers/mesh.py` as a re-export shim.

**Risk:** medium. Golden dict tests catch rendering drift.

---

## Phase 4 — Shared case builder; remove `web → cli` — Effort **M**

1. Create `casegen/builder.py::build_case(cfg_path, project_dir, dry_run=False, reporter=None)`.
   Move the body of `cli._do_generate` (`cli.py:95–440`) into it.
   - Parameterize console output through a small `reporter` (default: `print`) so the Web can
     capture output while the CLI keeps its exact UX.
2. `cli._do_generate` becomes a thin wrapper.
3. `server.api_case_generate_and_submit` (`server.py:701–703`) calls `casegen.builder.build_case`
   instead of `cli._do_generate`.
4. Move `writers/{base,constants,fields,solver,scripts}.py` → `casegen/` with re-export shims in
   `writers/`.

**Risk:** medium. CLI output is user-facing; capture it via `tests/test_regressions.py` (which
already runs `_do_generate`) and the Phase 0 golden tests.

---

## Phase 5 — De-embed the scripts and remote runtime — Effort **M**

### 5a. Shell templates — fixes #5 (part)
1. Merge the local-serial, local-parallel and SLURM templates in `casegen/scripts.py` into one
   parameterized template with shared `surface_block`, `mesh_block`, `stopAt`-restore fragments
   (`scripts.py:212–254, 273–275, 286–350, 476–506`).
2. Golden-test the emitted `Allrun`, `Allrun.parallel`, `run.sh`
   (`tests/test_shell_scripts.py` already exists — extend it).

### 5b. Convergence monitor — fixes #5 (part)
1. Make `runtime/convergence_monitor.py` the real module.
2. `casegen/scripts.py` **copies** the module into the case (plus a generated constants header)
   instead of `inspect.getsource` + `ast` rewriting (`scripts.py:48–65`).
3. Test: the generated case contains a runnable `convergence_monitor.py`; a unit test executes
   its `check_convergence` on a fixture (no AST). Keep the AST path behind a flag for one
   release to protect clusters with odd Python versions.

### 5c. Remote case inventory — fixes #6
1. Move the embedded script (`ssh_client.py:674–896`) into `runtime/remote_cases.py`.
2. `list_remote_cases_detailed` uploads/execs that file, using logic consistent with
   `postproc.forces` (or a copied, tested reader).
3. Test: parse a fixture force log with both `postproc.forces` and the runtime script; assert
   equal results.

**Risk:** medium. Keep the AST path as a fallback for one release; the Phase 0 asset test gates
the switch.

---

## Phase 6 — Split `web/server.py` — Effort **L**

Do this **after** the meshing seam is stable, so the split is pure motion.

1. `web/app.py` assembles the app; move CORS, static mount, `main()`.
2. Extract routers in this order (each is cut-and-paste + import fix):
   `routers/config.py` (427–503) → `routers/stl.py` (505–650) → `routers/case.py` (653–886) →
   `routers/telemetry.py` (1366–2330) → `routers/cluster.py` (352–426).
3. Extract services: `services/telemetry.py` (force projection, coefficients, aero balance,
   residuals, mesh/surface/y+ summarisation) and `services/downloads.py` (793–886) and
   `services/cluster_state.py` (136–184).
4. Keep module-level `app` importable from `web.server` (re-export) so `tests/test_web_api.py`
   keeps working; migrate those tests to import routers incrementally.

**Risk:** medium. The Web API tests (`tests/test_web_api.py`, 1785 lines) are the safety net;
run them after each router extraction.

---

## Phase 7 — Front-end consolidation — Effort **M**

1. Drive preset labels/defaults and mesh-metric metadata from `/api/config/schema-defaults`
   (`server.py:427–463`) instead of hard-coded strings in `app.js:1157–1170` and `index.html`.
   Delete the stale fallback table (`app.js:1124–1126`).
2. Add an explicit full-car / no-symmetry control and stop `buildConfigFromVisualForm` coercing
   `symmetry_plane` to `0.0` (`app.js:731–732`); bind the form to the `effective_config`
   returned by the domain-box/preview endpoint so the form, preview and generated case agree.
3. Split the `CFDApp` class (`app.js:120–4182`) by view (config form, viewer panel, telemetry,
   cases archive, cluster, downloads, toast/help) once the API is stable.
4. Add the JS test files to CI (Phase 0) and extend jsdom coverage as modules are extracted.

**Risk:** low-medium. JS tests are thin; add jsdom tests for any extracted module.

---

## Recommended order (what to do, in sequence)

| Order | Step | Effort | Value | Risk |
| ---: | --- | --- | --- | --- |
| 1 | Phase 0 golden/equivalence/asset tests + CI gates | S | Critical | None |
| 2 | 1a shared `core.caseconfig` | S | High | Low |
| 3 | 1b unify preset application | M | High | Low |
| 4 | 1d unify y+ verdict | S | Medium | Low |
| 5 | 1c extract `core.axes`/`faces`/`fields` | S | Medium | Low |
| 6 | 1e architecture test | S | High | None |
| 7 | Phase 2 `MeshPlan` + pure derivation | L | Critical | Med-High |
| 8 | Phase 3 plan-based writers | M | High | Medium |
| 9 | Phase 4 shared `casegen.builder` | M | High | Medium |
| 10 | Phase 5 runtime de-embedding | M | High | Medium |
| 11 | Phase 6 split `web/server.py` | L | High | Medium |
| 12 | Phase 7 front-end | M | Low-Med | Low |

**Quick wins (ship in week 1):** steps 1–6. They remove the worst duplication (case-config +
presets + y+ verdict), lock the boundaries, and produce the equivalence test that de-risks
everything after.

**Deep structural work:** Phase 2 (`MeshPlan`) is the keystone. Do it before splitting the Web
server, otherwise the Web split just moves the duplication around.

---

## Risk register

| Risk | Where | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| Dict rendering drifts during writer extraction | `writers/mesh.py` | Med | High | Phase 0 golden dict snapshots per config |
| CLI↔Web behaviour split is "fixed" incorrectly | preset/layer resolution | Med | High | Equivalence test written *before* refactor |
| `MeshPlan` migration breaks tests that assert cfg mutation | `tests/test_layers.py` | High | Med | Compat `apply_plan_to_cfg()`; migrate tests deliberately |
| Public import paths break downstream scripts | `geometry`, `stl_utils`, `writers` | Med | Med | Keep re-export shims for one release |
| Generated cluster scripts break after AST removal | `scripts.py`, clusters | Med | High | `runtime/` asset + fallback flag + double-reader test |
| Web API regression during router split | `web/server.py` | Med | Med | Extract one router at a time; run `test_web_api.py` |
| `case_config.json` schema leaks to consumers | `plan.as_config()` | Low | High | Freeze output shape; assert against golden `case_config.json` |
| Two-pass / layering dict drift | `snappyHexMeshDict_layering` | Low | Med | Existing `tests/test_layers.py::TestTwoPassLayering` |
| Front-end regression not caught in CI | `web/static/js/*` | High | Med | Add `npm test` to CI in Phase 0 |
| Removed private helper breaks a hidden caller | `forces._dir_time`, checkmesh helpers | Low | Low | Architecture test forbids cross-context private imports |

---

## Intermediate states (strangler-fig plan)

The migration is designed so old and new paths coexist:

```
Release A  (Phases 0–1):  old code paths + new shared helpers + architecture/equivalence tests
                          geometry.py / stl_utils.py / writers/ unchanged (re-export shims only)

Release B  (Phases 2–3):  meshing/* is real; geometry.py is a facade;
                          writers take MeshPlan; cfg["mesh_params"] view preserved
                          cli + web both call build_mesh_plan

Release C  (Phases 4–5):  casegen.builder is real; web no longer imports cli;
                          scripts use runtime/ assets; server.py split into routers

Release D  (Phase 6–7):   facades removed; front end server-driven and modularized
```

Anti-corruption layer = the re-export shims in `geometry.py`, `stl_utils.py`,
`writers/__init__.py`, and `web/server.py`. They are deleted only after a release with no
internal callers and all tests migrated.

---

## Verification checklist per step

1. `python -m unittest discover -s tests` → 330+ tests, OK.
2. Golden meshing dicts and `case_config.json` byte-identical for the representative matrix.
3. `tests/test_architecture.py` import-boundary test passes.
4. Preview/generate equivalence test passes.
5. `ruff check src tests` passes.
6. `npm test` passes for any JS change.
7. No new direct `cfg["..."]` indexing inside `meshing/writers/*` (grep check).
8. No new cross-context `import _private` (grep check / architecture test).

Run 1–4 after **every** merge; treat a golden diff as a behaviour-change decision, not an
automatic accept.

---

## Suggested sequencing of commits

Keep each commit a single mechanical move plus its test update, message prefixed with the
phase, e.g.:

```
test(p0): golden casegen snapshots + mesh plan equivalence + runtime asset parity
test(p0): run ruff and npm test in CI
refactor(core): extract axes/faces/fields; geometry re-exports
refactor(core): add caseconfig; reroute duplicated readers
refactor(meshing): add presets.apply_fidelity_preset
refactor(postproc): unify y+ verdict on check_yplus_target
test(arch): enforce bounded-context import rules
refactor(meshing): introduce MeshPlan; make derivation pure
refactor(meshing): plan-based writers + pipeline.emit_mesh_files
refactor(casegen): extract builder.build_case; web drops cli import
refactor(runtime): copy convergence monitor; remote_cases module
refactor(web): split server into routers + services
refactor(web-ui): server-driven presets; split CFDApp by view
```
