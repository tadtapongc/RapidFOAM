# RapidFOAM — Pain Points & Technical Debt

Whole-codebase review (supersedes the meshing-only list). Severity: **H** = blocks/blunts
refactoring, **M** = significant maintainability cost, **L** = papercut. Each item cites
concrete `file:line` evidence and states the refactor risk.

Baseline for all claims: commit `74e1f50`, 330 Python tests OK (15 skipped), `npm test` for JS.

---

## 0. Quick metrics

| File | Lines | Note |
| --- | ---: | --- |
| `src/rapidfoam/web/static/js/app.js` | 4183 | single `CFDApp` class (120–4182), 150+ methods |
| `src/rapidfoam/web/server.py` | 2797 | god module: 28 routes + credentials + cluster + generation + telemetry |
| `src/rapidfoam/web/static/css/style.css` | 2278 | single stylesheet |
| `tests/test_web_api.py` | 1785 | god test file covering the whole HTTP surface |
| `src/rapidfoam/web/static/index.html` | 1403 | single page, all views |
| `src/rapidfoam/geometry.py` | 1153 | god module: axes + domain + presets + sizing + grading + layers |
| `src/rapidfoam/web/ssh_client.py` | 935 | ~223-line embedded Python script (674–896) re-implements postproc |
| `src/rapidfoam/postproc/checkmesh.py` | 833 | parser + verdict + config discovery in one file |
| `src/rapidfoam/cli.py` | 706 | `_do_generate` is a 345-line function (95–440) |
| `src/rapidfoam/config.py` | 679 | 300-line procedural `validate` |
| `src/rapidfoam/writers/scripts.py` | 541 | 3 near-duplicate shell templates + AST-embedded Python |
| `src/rapidfoam/postproc/forces.py` | 546 | canonical force reader (but see #5, #6) |

- `geometry.py` fan-out: imported by `config`, `cli`, `writers/{mesh,fields,solver,scripts}`,
  `postproc/forces`, `web/server`. Any change ripples across the app.
- The same generation pipeline exists **three times** (CLI, Web preview, remote embedded script).
- Config discovery (`explicit → case/case_config.json → cwd`) is written **5–7 times**.

---

## 1. Duplicated preset / effective-settings logic (CLI vs Web vs geometry) — **H**

**Evidence**
- CLI applies fidelity presets by hand — 10 fields: `end_time`, `n_layers`, `expansion_ratio`,
  `y_plus_target`, `nLayerIter`, `nRelaxIter`, `ground_layers`, `write_interval`, `nSolveIter`,
  `nFeatureSnapIter`, `slurm` time/mem (`cli.py:226–258`).
- Web preview re-implements a **subset**: only `n_layers`, `expansion_ratio`, `y_plus_target`
  (`web/server.py:203–238`), then calls `compute_mesh_params`/`resolve_layers`.
- `compute_mesh_params` reads presets a third time internally (`geometry.py:722–723` and the
  `preset.get(...)` lookups throughout `388–936`).
- `web/server.py:653–765` calls `cli._do_generate` as a library (`server.py:701–703`), so the
  transport layer depends on the CLI.

**Impact** The Studio preview can show a different effective mesh than the CLI generates
(preview ignores `nLayerIter`/`nRelaxIter`/`ground_layers`/snap preset values). Any preset
change must be made in 2–3 places; there is no test that preview == generated. This is the
single highest-risk coupling.

**Refactor risk:** High if big-bang. Mitigate with a golden/equivalence test before moving code.

---

## 2. Config is mutated as a side effect of derivation — **H**

**Evidence**
- `compute_mesh_params` writes `cfg["feature_extract"]["includedAngle"]`
  (`geometry.py:785–790`).
- `resolve_layers` writes `cfg["layers"]` (`relativeSizes`, `first_layer_thickness`,
  `min_thickness`, `_resolved`): `geometry.py:1049`, `1122–1125`, `1140–1147`.
- `_apply_ground_layer_policy` writes back `layers["ground_layers"] = False` on guard failure
  (`geometry.py:1017–1029`).
- The Web preview must `copy.deepcopy` its config to avoid clobbering the caller
  (`server.py:211`).
- The generated `case_config.json` is a **post-mutation** snapshot containing `_resolved`
  (`cli.py:436`); `deep_merge` silently drops `_`-prefixed keys on reload (`config.py:288`).

**Impact** Derivation is order-dependent and impure; it cannot be memoized, retried or run in
parallel. Tests must build full `cfg` trees and rely on mutation order
(`tests/test_layers.py` asserts `cfg["layers"]` was written). `case_config.json` shape is an
undeclared output contract.

**Refactor risk:** Medium. Return a value object; keep a one-release compatibility shim that
still writes `cfg`.

---

## 3. `geometry.py` is a god module — **H**

**Evidence** Seven responsibilities in 1153 lines: axis math (`35–132`), field/turbulence
helpers (`87–107`), domain sizing (`139–246`), fidelity presets (`253–381`), feature
sizing/angle (`388–510`), background grading (`517–669`), mesh assembly + y+/layer math
(`672–1150`).

**Impact** Axis math is used by **non-mesh** code: `postproc/forces.py:11` imports
`AXIS_MAP`/`axis_index_sign`; `writers/fields.py`, `writers/solver.py`, `writers/scripts.py`,
`config.py`, `web/server.py` import axis helpers. Presets are read by `config.validate`
(`config.py:338`, `:375`). A meshing change can break validation and post-processing. Low
cohesion, very high fan-out.

**Refactor risk:** Low if extracted mechanically behind re-export shims; 330 tests as the net.

---

## 4. `writers/mesh.py` is a templating god with hidden logic — **M**

**Evidence**
- `write_snappy_hex_mesh_dict` (`mesh.py:143–432`) reads six `cfg` sections directly
  (`mesh_params`, `stl_names`, `patches`, `snap`, `layers`, `mesh_quality`, `domain_box`),
  auto-places `locationInMesh` via an axis heuristic (`mesh.py:212–255`), renders two full
  quality blocks (`quality_text`, `layering_quality_text`), and emits a second two-pass file.
- A nested `_render()` closure builds a ~90-line f-string (`mesh.py:337–422`).
- `write_block_mesh_dict` silently falls back to uniform cell counts when `block_cells` is
  missing (`mesh.py:66–73`) — masks a contract violation instead of raising.
- `locationInMesh` logic duplicates axis computation that lives in `geometry.py`.

**Impact** The writer is not a pure function of `mesh_params`; hard to unit-test without a full
config; the fallback hides bugs (e.g. a caller that forgot `compute_block_grading`).

**Refactor risk:** Low–Medium. Replace with an explicit `MeshPlan`; make the fallback explicit.

---

## 5. `writers/scripts.py` depends on `postproc` and embeds its source via AST — **H**

**Evidence**
- `_clean_force_helpers()` (`scripts.py:48–65`) uses `inspect.getsource` +
  `ast.NodeTransformer` + `ast.unparse` to inline `postproc.forces` functions (`_dir_time`,
  `force_layout_from_header`, `find_force_files`, `read_forces`, `check_convergence`) into the
  generated `convergence_monitor.py`.
- Three shell templates repeat the same blocks:
  - `surface_block` (`scripts.py:212–254`) injected 3× at `286`, `342`, `479`;
  - mesh command block duplicated at `288–298`, `343–350`, `481–506`;
  - `stopAt` restore duplicated at `273–275`, `339–341`, `476–478`.

**Impact** A **layering inversion**: the code generator depends on the post-processor. The
generated script's correctness is coupled to the *exact source shape* of `forces.py` — renaming
a helper, adding a type alias, or moving `read_forces` breaks generated cluster scripts at
runtime with no local test coverage. The duplicated shell blocks drift independently.

**Refactor risk:** High if the AST trick is removed without a versioned replacement. Safer:
ship a maintained standalone module and **copy** it into cases, keeping the AST path as a
one-release fallback.

---

## 6. `ssh_client.py` embeds a parallel post-processing implementation — **H/M**

**Evidence**
- `ClusterSSHClient.list_remote_cases_detailed` builds `remote_script = """ … """`
  (`ssh_client.py:674–896`, ~223 lines) and base64-executes it on the cluster.
- That script defines its own `parse_axis` returning `(idx, sign)` (`ssh_client.py:682–686`) —
  a different shape from `geometry.parse_axis`'s 3-tuple (`geometry.py:42`).
- It re-implements force parsing (columnar vs classic layouts), symmetry detection, rolling-window
  convergence and case status inference — a divergent copy of `postproc.forces`.
- It also defines `read_tail` (`ssh_client.py:688–696`), duplicated by
  `server.read_file_tail` (`server.py:2367`) and `server._read_text_tail_lines`
  (`server.py:1863`).

**Impact** Remote case status can disagree with the canonical readers. Bug fixes to
`postproc.forces` do not propagate to the cluster inventory. The embedded script is invisible
to normal code search, lint and type checks.

**Refactor risk:** Medium. Extract to a shipped, importable module uploaded to the cluster;
add a test that parses a fixture with both the embedded and canonical reader.

---

## 7. Duplicated config discovery / y+ verdict / tail helpers — **M**

**Evidence**
- Config discovery (`explicit → <case>/case_config.json → cwd`), implemented at least five
  times: `checkmesh._load_config_dict` (`checkmesh.py:741–762`),
  `checkmesh.checkmesh_targets_from_case` (`checkmesh.py:788–812`),
  `server._load_solver_end_time` (`server.py:1872`), `server._load_stl_files`
  (`server.py:1008`), `server._load_vehicle_geometry` (`server.py:1074`), plus
  `cli._yplus_target_from_case` (`cli.py:447`) and `server._yplus_target_from_config`
  (`server.py:2112`).
- y+ target readers: `cli._yplus_target_from_case` (`cli.py:447–471`) vs
  `server._yplus_target_from_config` (`server.py:2112–2125`).
- y+ verdict: `postproc.yplus.check_yplus_target` (regime-aware, ±50% band, `yplus.py:77–157`)
  vs `server._summarise_yplus` (fixed ±50%, `server.py:2128–2171`). **Two different verdicts
  for the same data.**
- y+ text parsing: `postproc.yplus.read_yplus` (`yplus.py:44`) vs
  `server._read_yplus_texts` (`server.py:2085–2109`).
- Tail reading: `server.read_file_tail` (`server.py:2367`),
  `server._read_text_tail_lines` (`server.py:1863`), `ssh_client.read_tail`
  (`ssh_client.py:688`).
- `surfacecheck.py:22` imports **private** `_config_has_symmetry` and `_load_config_dict`
  from `checkmesh.py`.
- Merge duplication: `config.load_config` (`config.py:297`) vs
  `web.server.merge_config_with_defaults` (`server.py:186`).

**Impact** Behavioural drift between CLI and Studio (exactly the class of bug
`docs/bug-hunt-BUGS.md` documents). Missing shared "case config access" and "case runtime"
modules.

**Refactor risk:** Low. Pure extraction behind tests.

---

## 8. `web/server.py` is a god module — **H**

**Evidence** 2797 lines, 28 routes, plus helpers importing from `config`, `geometry`,
`stl_utils`, `postproc.{forces,checkmesh,residuals,surfacecheck,yplus}`, `ssh_client`, and
`cli._do_generate`. It owns:
- credential file storage & ACLs (`server.py:136–184`),
- STL list/upload/serve (`505–650`),
- domain-box and layer preview (`203–349`),
- case generation + cluster sync + SLURM submit (`653–790`),
- threaded case download with progress registry (`793–886`),
- remote telemetry bundle reading/segmenting/caching (`888–1005`),
- force parsing, symmetry projection, coefficients, aero balance (`1024–1650`),
- residuals/solver diagnostics (`1722–2083`),
- y+/mesh/surface summarisation (`2085–2330`),
- case listing/deletion (`2379–2675`), static serving and `main()`.
- `PROJECT_ROOT = Path.cwd()` is captured at import (`server.py:95`), so the server is
  cwd-dependent and tests must patch it.

**Impact** Merge conflicts and regressions concentrate here; it is where logic is most likely
to be re-implemented (see #1, #6, #7). The 1785-line `test_web_api.py` is the mirror image.

---

## 9. Front end duplicates server knowledge — **M**

**Evidence**
- `web/static/js/app.js` is a single 4183-line `CFDApp` class encoding preset labels/defaults
  (`app.js:1157–1170` placeholders like `Auto / Preset (10)`), fidelity cards, layer override
  modes (`app.js:879–907`), domain-box parsing, and mesh metric tables.
- `server.py:427–463` already serves `/api/config/schema-defaults` (including
  `fidelity_presets`, `server.py:432–460`), but the UI still hard-codes a parallel copy.
- `index.html` (1403) and `style.css` (2278) are monoliths; `tables/js` tests are thin and
  are **not run in CI**.

**Impact** A preset or metric rename needs edits in Python **and** JS/HTML. API shape changes
are expensive. No linting/type safety in ~5k lines of JS.

---

## 9b. The Studio's stored config diverges from the effective/real config — **H**

This is the user-visible face of #1 and #9: the config the Studio shows, previews and writes is
**not** the config the generator actually uses, and it cannot even represent some valid CLI
configs.

**Evidence**
- **Symmetry is forced.** The default `activeConfig` sets `symmetry_plane: 0.0` and
  `-x: symmetry` (`app.js:171`, `:174`); `buildConfigFromVisualForm` unconditionally writes
  `cfg.symmetry_plane = isNaN(symPlane) ? 0.0 : symPlane` (`app.js:731–732`) and
  `updateVisualFormFromConfig` defaults it to 0.0 (`app.js:520–521`). A blank field ⇒
  `parseFloat('')` ⇒ NaN ⇒ `0.0`, so there is **no UI path to a full-car config**, and syncing a
  `null`-symmetry config through the form rewrites it to `0.0`.
- **The two symmetry detectors disagree on the same `case_config.json`.** `checkmesh.
  _config_has_symmetry` returns `True` whenever `symmetry_plane` is numeric
  (`checkmesh.py:774–777`) — so mesh/surface verdicts suppress open-patch findings
  (`checkmesh.py:578`, `surfacecheck.py:259`) for a full-car Studio case — while
  `forces.is_symmetry_case` ignores `symmetry_plane` and decides from `domain_faces`
  (`forces.py:86–111`). Force doubling therefore depends on which reader runs.
- **The preview resolves a subset of the preset.** `server.layer_preview` applies only
  `n_layers`, `expansion_ratio`, `y_plus_target` (`server.py:213–221`); generation applies 10
  fields including `end_time`, `nLayerIter`, `nRelaxIter`, `ground_layers`, `write_interval`,
  `snap.nSolveIter`, `snap.nFeatureSnapIter`, slurm time/mem (`cli.py:230–258`).
- **The schema endpoint drops fields generation uses.** `/api/config/schema-defaults` returns
  only layers.{y_plus_target,n_layers,expansion_ratio,ground_layers}, mesh.*, solver.*
  (`server.py:432–461`) — no `snap`, `layers.nLayerIter/nRelaxIter`,
  `feature_extract.includedAngle`, or slurm. JS fallback placeholders are additionally stale
  (`app.js:1124–1126`): fast `n_layers 2`/`y+ 30` (real 5/50), standard `3`/`10` (real 8/30),
  fine `12`/`expansion 1.20` (real 20/1.10). `mesh.base_cell_size` is exposed but does not exist
  in `FIDELITY_PRESETS` (they use `cells_per_length`), so it is always null.
- **Misleading control label.** The auto-size dropdown's default option reads `Auto (on)`
  (`index.html:607`) while `auto_size` defaults to **false** (`DEFAULT_CONFIG` has no
  `mesh_params`; `geometry.py:744`; README:331). "Auto" means "follow the preset/default",
  which is off — the `(on)` is stale. The y+ hint (`index.html:652`, "Preset 10–30") also
  disagrees with the presets (fast 50 / standard 30 / fine 1).
- **Stale / phantom labels in the UI and example config** (verified against `DEFAULT_CONFIG`,
  `FIDELITY_PRESETS`, `README.md`):
  - `min_thickness_ratio` is referenced by `index.html:676` (and ~20 scratch configs) but
    **does not exist in the code**; `geometry.py:1067–1071` hard-codes the full first layer.
  - y+ / n_layers placeholders `index.html:651,657` (10, 5) vs real standard (30, 8).
  - Fidelity cards `index.html:197–218` (~2-4M/5-10 min, ~6-9M/30-60 min, ~12-16M/2-4 hrs)
    vs real (~3-5M/10-20 min, ~9-13M/1-2 hrs, ~20-28M/4-6 hrs); replaced at runtime only if the
    schema fetch succeeds (`app.js:1187`).
  - JS fallback table `app.js:1124–1126`: fast `n_layers 2`/`exp 1.30`/`y+ 30`, standard
    `3`/`10`, fine `12`/`exp 1.20` — all wrong; used only when the schema fetch fails.
  - Shipped `configs/config.json:13` comment y+ "~30/~10/~1" vs real "50/30/1".
  - Default `activeConfig` comment example `app.js:215` `_auto_size: true` and `:220`
    `_n_layers: 5` contradict the real defaults.
- **Two merge implementations.** `config.load_config` (`config.py:297`) is used by generation;
  `server.merge_config_with_defaults` (`server.py:186`) by domain-box preview and
  `/api/config/load-file`. "Validate" posts to `generate-and-submit` with all action flags
  false, running `validate()` on the merged raw config **without applying presets** (same
  source-of-truth split as #10).

**Impact** Users see placeholder counts (e.g. `n_layers`) that differ from the generated mesh;
a full-car study from the Studio is silently treated as a half-model in mesh/surface reporting;
and force doubling can be applied on one code path but not another. This is exactly the
CLI↔Studio drift class the project's own `docs/bug-hunt-BUGS.md` records.

**Refactor risk:** Medium–High. Depends on the shared effective-config seam (#1, #2, #11):
fix the single source of truth first, then make the Studio render from it.

---

## 9c. Override/preset interaction is inconsistent (SLURM time & memory silently lose the preset) — **H**

The `overrides` merge itself is correct (`config.load_config:317–324` and
`server.merge_config_with_defaults:186–195` agree; multi-section overrides are tested in
`tests/test_web_api.py:787–839`). The bug is the **preset guards**, which mix two different
notions of "user set" and let top-level fields defeat the preset.

**Evidence**
- `cli._do_generate` guards preset SLURM settings inconsistently
  (`cli.py:254–258`):
  ```python
  # Only apply preset SLURM time if user left it as default 'auto'
  if cfg["slurm"]["time"] == "auto":            # value sentinel, not user_set()
      cfg["slurm"]["time"] = preset.get("slurm_time", "04:00:00")
  if not _is_set("slurm", "mem_per_cpu"):       # user_set()
      cfg["slurm"]["mem_per_cpu"] = preset.get("slurm_mem_per_cpu", ...)
  ```
- The shipped `configs/config.json:92–93` sets `slurm.time = "08:00:00"` and
  `slurm.mem_per_cpu = "2G"` at top level, so **both guards are defeated** even for CLI users.
- The Studio always emits those two top-level fields (`app.js:779–780`, defaulted at
  `app.js:564–565`), so **every Studio-generated case also loses the preset**.
- Verified with the real generator:
  | Config | `fast` preset expects | `run.sh` actually gets |
  | --- | --- | --- |
  | CLI minimal (no `slurm` block) | `--time=04:00:00`, `2G` | `04:00:00`, `2G` ✔ |
  | shipped/Studio config | `04:00:00`, `2G` | **`08:00:00`**, `2G` x |
  | `fine` preset expects | `14:00:00`, `4G` | **`08:00:00`, `2G`** ✗ |

  (Fine is under-provisioned by ~6 h wall time and half the memory → SLURM can kill a
  still-running solve.)

**Impact** Fidelity selection does not change SLURM time/memory in practice, so `fine` (and
`standard`) jobs can time out on the cluster. It is silent: the CLI banner does not print the
resolved SLURM time, and the preset values look authoritative (`FIDELITY_PRESETS`). The same
"guard vs top-level presence" mismatch can bite any future preset field.

**Secondary override issues**
- `user_set` counts a key as set even when its value is `null`, so
  `overrides: {layers: {y_plus_target: null}}` disables the preset y+ target and silently falls
  back to relative layers (`config.py:665–679`).
- The Studio builds `surface_level = [surfMin ?? 4, surfMax ?? 5]` (`app.js:845–846`); entering
  only one bound can produce `[6, 5]`, which `validate` rejects as unordered
  (`config.py:545–549`).
- `validate` runs on the merged config without presets (#10), so layer warnings do not reflect
  the effective (post-override, post-preset) stack.

**Refactor risk:** Low to fix once the effective-config seam exists (#1, #2): make every preset
field decided by one `user_set`-style predicate that ignores preset-injected defaults, and have
the shipped example + Studio omit fields they do not intend to pin (use `"auto"`/absent for
`slurm.time`/`mem_per_cpu`). Add a regression test that each preset field reaches the emitted
dictionaries when unset.

---

## 9d. Telemetry reads the raw config (ignores `overrides`) and truncates exports — **H/M**

**Evidence**
- `_load_reference_quantities` (`server.py:1024–1071`) reads only **top-level** `fluid.rho`,
  `flow.velocity`, `force_refs.{Aref,lRef,CofR}` from the raw `configs/<case>.json` and never
  applies the `overrides` block. The Studio stores `fluid`/`force_refs` edits under
  `overrides.*` (`app.js:825–837`, `911–917`). Verified against a case generated with
  `overrides: {fluid: {rho: 1.18}, force_refs: {Aref: 0.85, lRef: 1.25, CofR: [0.1,0,0.5]}}`:
  | Reader | rho | Aref | lRef | CofR |
  | --- | --- | --- | --- | --- |
  | `_load_reference_quantities` (telemetry) | **1.225** | **1.0** | **1.0** | **[0,0,0]** |
  | `load_config` (generation/solver) | 1.18 | 0.85 | 1.25 | [0.1,0,0.5] |
  Effect: the "Reference conditions" line and the inline reference-editor placeholders are
  wrong; the no-`coefficient.dat` fallback `Cd`/`Cl` (`coeff_source: "computed"`,
  `server.py:1536–1546`) and the post-run recompute use the wrong Aref. The solver's
  `forceCoeffs` file still carries correct values, masking it until the coeff file is absent or
  the editor is opened.
- **Raw config preferred over the effective one.** `api_telemetry_forces` sets
  `cfg_path = configs/<case>.json` when it exists (`server.py:1388–1390`); `api_telemetry_mesh`
  tries `configs/<case>.json` before `case_config.json` (`server.py:2223–2235`). But
  `generate-and-submit` writes the **raw** config there (`server.py:694`) and preset-resolved
  values only into `case_config.json`. So preset-injected `layers.y_plus_target` is invisible →
  `_yplus_target_from_config` returns `None` (`server.py:2112`) → the Mesh Quality y+ panel runs
  with no target and `missed` stays empty (`_summarise_yplus`, `server.py:2159`), while the CLI
  (`_yplus_target_from_case`) finds it in `case_config.json`. `checkmesh_targets_from_dict` also
  misses override-driven layer/quality targets.
- **Export truncates and can drop coefficient columns.** `api_telemetry_export`
  (`server.py:1652–1706`) serialises `api_telemetry_forces()["series"]`, already downsampled by
  `_subsample_indices(..., 400)` (`server.py:1559`), so a 1500-iteration run exports ~400 rows.
  It joins coefficient columns by iteration using the drag subsample indices while coefficients
  are subsampled independently (`server.py:1559–1560`), so cells can be blank. No test covers
  export.

**Impact** Studio reference values / recomputed coefficients can disagree with the solver even
though the solver is correct; y+ verdicts and mesh targets differ from the CLI; exported data is
silently incomplete. All are the same root cause as #7 (duplicated config discovery without
override-aware merging) and #9b.

**Refactor risk:** Low once `core.caseconfig` (Phase 1a) exists: every telemetry reader should
call `read_case_config()` (which resolves raw → defaults → overrides, and prefers the effective
`case_config.json`), and export should use the full history (or expose the downsample factor).
Add tests: reference values honour overrides; y+ target survives the API; a >400-iteration
export has all rows and coefficient columns.

---

## 10. Validation and derivation use different sources of truth — **M**

**Evidence**
- `validate` reads `DEFAULT_CONFIG` and `FIDELITY_PRESETS` for the fidelity-name check
  (`config.py:341`, `:375`) but does **not** apply preset values (n_layers, surface_level)
  before checking layer/thickness warnings (`config.py:558–601`).
- `_do_generate` applies presets **after** `compute_mesh_params` and **after** `validate`
  (`cli.py:221–265`).
- `compute_mesh_params` reads presets internally (`geometry.py:722–723`).

**Impact** Warnings can be emitted for a stack the preset later changes, or omitted for the
actual effective stack. Users, CLI messages and Studio previews can disagree.

---

## 11. Fragile implicit contracts / no plan object — **M**

**Evidence**
- Writers require `cfg["stl_names"]`, `cfg["domain_faces"]`, `cfg["domain_box"]`,
  `cfg["mesh_params"]` to have been injected by `_do_generate` (`cli.py:152–154`,
  `221–224`). The Web path relies on calling that same function in a thread
  (`server.py:701–703`).
- `cfg["mesh_params"]` is a loosely-typed dict; consumers index required keys directly
  (`writers/mesh.py:153–156`).
- `case_config.json` is consumed by `postproc` and the Web with no schema/version.

**Impact** No compile-time or schema boundary between "derivation output" and "writer input".
Refactoring one requires tracing every dict key by hand. A `MeshPlan` value object with
`__post_init__` validation would give a seam.

---

## 12. Execution-script fragility & platform assumptions — **M**

**Evidence**
- The generated scripts are `#!/bin/bash` and use `sed -i`, `chmod 0o755`
  (`scripts.py:73`), `module purge`, `mpirun`, `/dev/shm` — none testable on the Windows CI
  runner; the `test_shell_scripts.py` tests assert text, not behaviour.
- `convergence_monitor.py` is generated by embedding annotations-stripped source for Python
  **3.6** compatibility (`scripts.py:29–45`), while the package targets 3.9+ — a second,
  divergent copy of `postproc/convergence_monitor.py` (which is the real, importable version).
- `Allclean` and the scripts duplicate `stopAt` restoration logic.

**Impact** Drift between the importable monitor and the embedded one; the AST path can silently
break. Shell behaviour is only covered by string assertions.

---

## 13. Tooling / quality gaps — **M**

**Evidence**
- `.github/workflows/ci.yml` runs only `python -m unittest discover tests` and
  `python -m build`. No `ruff`, no type check, **no `npm test`**.
- Ruff is configured (`pyproject.toml:73`) but never invoked.
- No `mypy`/`pyright` config, no pre-commit.
- Type annotations are used inconsistently (`forces.py` references `Optional` only inside
  `from __future__ import annotations` strings without importing it; safe at runtime, but
  breaks `typing.get_type_hints`).
- Scratch configs (~28 `configs/*.json`) and generated cases accumulate locally; only
  `configs/config.json` is tracked (good) but there is no fixture strategy for the golden tests
  the refactor needs.

**Impact** Regressions in the JS layer and the generated shell scripts are not caught in CI —
precisely the two layers most at risk during a refactor.

---

## 14. Public/private boundary confusion — **L**

**Evidence**
- `postproc/residuals.py:9` and `postproc/yplus.py:15` import `forces._dir_time` (private).
- `surfacecheck.py:22` imports two private `checkmesh` helpers.
- `web/server.py:53` imports private `_config_has_symmetry`.
- `config.validate` lazily imports `geometry` inside the function to avoid an import cycle
  (`config.py:338`) — a symptom of the folder layering not matching reality.

**Impact** Mechanical extraction is harder; a rename in one module silently breaks another.

---

## 15. Coupling heat map

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
Y** writers/scripts imports postproc.forces and embeds its source   (INVERSION)
web→cli._do_generate (transport→transport)                           (SMELL)
surfacecheck→checkmesh private helpers; residuals/yplus→forces._dir_time (SMELL)
(Y) ssh_client embeds a divergent copy of postproc.forces            (DUP)
```

---

## 16. What is already good (preserve these)

- **Pure measurement layer**: `postproc/checkmesh.py` and `surfacecheck.py` parse logs and
  never run OpenFOAM; well documented and heavily tested.
- **Streaming STL analysis**: `stl_utils._analyze` (`stl_utils.py:224–312`) is O(1) memory and
  single-pass, with a strong test net.
- **Opt-in heuristics**: `auto_size`, `auto_feature_angle`, `grading`, `two_pass` all default
  off and are documented — a deliberate "predictable over clever" stance that makes refactoring
  safer.
- **Regression tests exist** for exactly the fragile spots (multi-solid rename, symmetry
  projection, force layouts, grading) — `tests/test_regressions.py`.
- **No third-party runtime deps** for core, so extraction/renaming is cheap.
- **Baseline is green and fast**: 330 tests, 15 skipped, ~3 s — usable after every refactor step.
- **Security-conscious web layer**: CORS pinned to localhost, credential ACLs, path-traversal
  guards, atomic downloads, XSS escaping.

---

## 17. Prioritised debt summary

| # | Debt | Severity | Coupling | Test safety net |
| --- | --- | --- | --- | --- |
| 1 | Duplicated preset/effective-settings logic (CLI vs Web vs geometry) | H | High | Weak (no equivalence test) |
| 2 | Config mutated by derivation | H | Medium | Medium (tests assert mutation) |
| 3 | `geometry.py` god module | H | Very high | Strong |
| 8 | `web/server.py` god module | H | Very high | Medium (`test_web_api.py`) |
| 5 | `writers→postproc` AST embedding + duplicated shell | H | Medium | Weak |
| 6 | Embedded remote post-proc copy | H | Low | Weak |
| 7 | Duplicated config/y+/verdict/tail helpers | M | Low | Strong |
| 10 | Validation vs derivation source of truth | M | Medium | Medium |
| 4 | `writers/mesh.py` hidden logic/fallback | M | Medium | Strong |
| 11 | No `MeshPlan` boundary / implicit writer contract | M | High | Medium |
| 12 | Execution-script fragility & platform assumptions | M | Medium | Weak (text only) |
| 13 | CI misses lint/types/JS tests | M | Low | n/a |
| 9 | Front-end duplicated mesh/config knowledge | M | Low | JS tests (thin, not in CI) |
| 9b | Studio stored config diverges from effective config (forced symmetry, stale placeholders, partial preview) | H | High | Weak |
| 9c | Override/preset guard mismatch — preset SLURM time/mem silently lost (shipped config + Studio) | H | Medium | Weak |
| 9d | Telemetry ignores `overrides` and prefers raw config; export truncates to ≤400 rows | H/M | Medium | Weak |
| 14 | Private cross-module imports | L | Low | Strong |

**The keystone debts are #1, #2 and #11**: a single effective-settings/`MeshPlan` seam fixes
three of them at once and de-risks the rest.
