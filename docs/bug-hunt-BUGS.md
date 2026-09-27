# RapidFOAM Bug Hunt

> **Historical document.** A point-in-time audit from the v1.2–v1.3 era. All
> listed issues were subsequently fixed; the durable record is the
> `CHANGELOG.md` and the regression tests. Kept for the worked methodology and
> the "riskiest areas" analysis, which may still be informative.

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High     | 2 |
| Medium   | 5 |
| Low      | 7 |
| **Total**| **15** |

### Areas examined most deeply
- `src/rapidfoam/web/server.py` — the FastAPI REST surface, telemetry math (symmetry projection, CofR moment shift, coefficients, aero balance), case download/cancel concurrency, credential handling.
- `src/rapidfoam/web/ssh_client.py` — SSH/SFTP lifecycle, remote bundle reads, tar-stream download, graceful cancel.
- `src/rapidfoam/web/static/js/app.js`, `viewer.js`, `charts.js`, `telemetry2d.js`, `telemetry3d.js` — async polling state machine, DOM injection sinks, Three.js resource/state lifecycle.
- `src/rapidfoam/config.py`, `geometry.py`, `writers/*` — validation, domain/mesh derivation, OpenFOAM dictionary emission.
- `src/rapidfoam/postproc/*` — force/residual parsing, convergence logic.

All Python tests (`test_regressions`, `test_layers`, `test_web_api`) pass on the checked-out tree; the defects below are therefore gaps not covered by the suite.

### Riskiest areas that need more investigation
- **Remote execution trust boundary** (`ssh_client.py`): remote file content is parsed with fixed `__RAPIDFOAM_FILE_BEGIN__`/`END` markers; a crafted file could spoof framing. Cluster is semi-trusted, so impact is bounded but worth a hardening pass.
- **`api_list_cases` remote merge** (`server.py:2174-2283`): status/iteration merge heuristics (SLURM state → status, `modified_ts` ordering) are intricate and lightly tested.
- **`viewer.js` untrusted STL parsing**: r128 `STLLoader` trusts the binary header face count; a corrupt/huge header can trigger a very large typed-array allocation inside `loader.parse`. Confirmed as a risk, not proven to crash.
- **`config.validate`** does not validate `mesh_params.distance_levels`, `distance_shells` or `refinement_regions` shapes; malformed user overrides can surface as `TypeError`/`KeyError` deep in the writers.

---

### Bug #1 – "Validate" button silently generates a case and overwrites existing config/case
- **Severity**: Critical
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/app.js:1596-1608` and `src/rapidfoam/web/server.py:236-241`, `server.py:621-640`
- **Description**: `validateCurrentConfig()` POSTs to `/api/case/generate-and-submit` with `upload_to_cluster:false`, `generate_remotely:false`, `submit_slurm:false` but **omits `generate_locally`**. The Pydantic model defaults `generate_locally: bool = True` (`server.py:241`). In the handler, `if req.generate_locally or req.upload_to_cluster:` writes `configs/<case_name>.json` (`server.py:624-627`) and `if req.generate_locally:` runs the full `_do_generate(...)` locally (`server.py:633-640`). The endpoint's own comment states a pure validation request must not create or overwrite a config (`server.py:621-622`).
- **Why it's a problem**: Clicking the normal "Validate" action overwrites `configs/<case>.json` and regenerates/overwrites the entire `cases/<case>/` directory (including `0/`, `system/`, scripts). A user validating an edit can silently destroy a previously generated or partially-solved case. It also contradicts the regression test `test_validation_only_does_not_write_config`.
- **Suggested fix**: In `validateCurrentConfig`, send `generate_locally: false`. Additionally, make the backend model default `generate_locally: bool = False` and require an explicit action, so a missing flag can never mutate the filesystem.
- **Related code**: `src/rapidfoam/web/server.py:602-640`; JS callers that correctly pass the flag: `app.js:1655-1663`, `app.js:1735-1743`.

---

### Bug #2 – Large (millimetre-scale) STL models are clipped/invisible in the 3D viewer
- **Severity**: High
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/viewer.js:72`, `viewer.js:92`, `viewer.js:416`, `viewer.js:434`
- **Description**: The camera is created with a far plane of `2000` (`this.camera = new THREE.PerspectiveCamera(45, width / height, 0.05, 2000);`) and `controls.maxDistance = 1500`. `fitView`/`setViewAngle` compute `dist = |maxDim/2/tan(fov/2)| * 1.8` and place the camera at an offset magnitude ≈ `1.414 * dist ≈ 3.07 * maxDim`. For a model exported in millimetres (`maxDim ≳ 650`, exactly the case the UI warns about via `scale-warning`, `viewer.js:335`), the camera target distance exceeds both the far plane and `maxDistance`; OrbitControls clamps the radius to `1500`, so the geometry (extending to `~1500 + maxDim/2`) is behind the far plane and is clipped away.
- **Why it's a problem**: Common CAD STL exports in mm cannot be framed or viewed at all after "Fit model"; the model appears blank/clipped and the orbit distance cannot be increased. The application explicitly supports and warns about mm models, so this is a functional break, not a cosmetic issue.
- **Suggested fix**: Derive the far plane and `maxDistance` from the loaded bounds (e.g. `far = Math.max(2000, modelRadius * 20)`, `maxDistance = Math.max(1500, modelRadius * 10)`), or normalise mm inputs to metres on load.
- **Related code**: `viewer.js:405-451`, `viewer.js:331-336`.

---

### Bug #3 – Telemetry auto-refresh can permanently stall (`telemetryInFlight` never cleared)
- **Severity**: Medium
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/app.js:2500-2503`, `app.js:2885-2887`, `app.js:3054`, `app.js:3442-3444`
- **Description**: `beginTelemetryCaseSwitch()` increments `telemetryRequestId` (line 2502) but does not reset `telemetryInFlight`. `pollTelemetry()` sets `telemetryInFlight = true` (line 2887) and only clears it in `finally` when `reqId === this.telemetryRequestId` (line 3054). `loadCasesArchive()` calls `beginTelemetryCaseSwitch(select.value)` when the selected case disappeared (line 3443) **without a following `pollTelemetry()`**. If a poll is in flight at that moment, the in-flight poll becomes stale, its `finally` does not clear the flag, and the interval guard `if (... && !this.telemetryInFlight && ...)` (line 272) then suppresses all future polls.
- **Why it's a problem**: Telemetry silently stops updating for the rest of the session; the user must click Refresh or change the case to recover. The same trap exists on any future code path that calls `beginTelemetryCaseSwitch` without immediately polling.
- **Suggested fix**: Reset `this.telemetryInFlight = false` inside `beginTelemetryCaseSwitch()`, or track in-flight per request id (e.g. `if (this.telemetryInFlightId === reqId) this.telemetryInFlight = false;`). Ensure `loadCasesArchive` calls `pollTelemetry()` after an implicit switch.
- **Related code**: `app.js:267-273`, `app.js:2489`, `app.js:2492-2498`.

---

### Bug #4 – Solver-health badge reports "Healthy" when continuity data is missing
- **Severity**: Medium
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/app.js:3073-3086` (server source: `server.py:1955-1957`, `server.py:1967`)
- **Description**: `data.latest_continuity_global` may be JSON `null` (the backend emits `None` when every continuity value is null, `server.py:1955-1957`). The display correctly shows `--` (line 3076), but the badge logic uses `const globalContinuity = Number(continuity);` (line 3078). `Number(null) === 0`, and `isFinite(0)` is true, so `Math.abs(0) < 1e-4` sets `health = 'Healthy'` (lines 3082-3083).
- **Why it's a problem**: A solver with no continuity diagnostics is presented as "Healthy · N lin iters", a false convergence/health signal that can mislead users into trusting a broken run.
- **Suggested fix**: Guard null/undefined before `Number()`:
  ```js
  const hasContinuity = continuity !== null && continuity !== undefined && isFinite(Number(continuity));
  if (hasContinuity) { ... } // else leave 'No continuity data'
  ```
- **Related code**: `server.py:1922-1967`.

---

### Bug #5 – `pollTelemetry` ignores HTTP status; backend errors render as a valid "no data" state
- **Severity**: Medium
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/app.js:2904-2906`, `app.js:3014-3015`, `app.js:3034-3035`
- **Description**: Each fetch does `const data = await res.json();` and immediately branches on `data.has_data`, never checking `res.ok`. On a 400/404/500 the body is `{ "detail": ... }`, which has no `has_data`, so control falls into the "case generated / meshed, no data yet" branches and the empty overlays are shown as if the run legitimately had no output.
- **Why it's a problem**: Real backend or parsing failures are silently presented as a normal empty run ("Status: Ready", "run the solver"), masking failures and leaving stale KPIs on screen.
- **Suggested fix**: Check `if (!res.ok) throw new Error(...)` (or display `data.detail`) before parsing/branching in each of the three telemetry fetches.
- **Related code**: `app.js:2990-3055`.

---

### Bug #6 – Flow-direction arrow leaks GPU resources on every update
- **Severity**: Medium
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/viewer.js:216-223` (called from `viewer.js:114` and `viewer.js:722`)
- **Description**: `updateFlowArrow` removes the previous arrow (`this.scene.remove(this.flowArrow)`) but never calls `dispose()` on it. A `THREE.ArrowHelper` owns a line `BufferGeometry`/`LineBasicMaterial` and a cone `BufferGeometry`/`MeshBasicMaterial`; removing from the scene does not release GL buffers. `updateFlowArrow` is invoked from `updateDomainBox`, which runs on many config/UI changes.
- **Why it's a problem**: Unbounded GPU memory growth during a session of parameter edits, eventually causing WebGL context loss/blank rendering.
- **Suggested fix**: Before removing, traverse the arrow and dispose geometry/material (reuse `this.disposeObject(this.flowArrow)`).
- **Related code**: `viewer.js:161-167`, `viewer.js:852-856`, `viewer.js:133-158` (`disposeObject`).

---

### Bug #7 – Stored XSS through unescaped values injected via `innerHTML`
- **Severity**: Medium
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/app.js:1490-1494`, `app.js:2102-2111`, `app.js:2252-2256`, `app.js:3526-3606`, `app.js:3683`; data origins in `server.py:557-566`, `server.py:414-431`
- **Description**: Multiple render functions build HTML by string interpolation into `innerHTML` without escaping. Examples: STL chip filenames (`renderActiveSTLChips`, line 1490), SLURM job fields from `squeue` (`renderQueueTable`, 2102-2111), download case names (`renderDownloads`, 2252-2256), archive rows including `data-name="${c.name}"` (`renderCasesArchiveTable`, 3588-3606), and `showToast(message)` (3683). Filenames/case names/config `stl_files` are only sanitised for path separators (`server.py:563-566`, `config.py:335`); HTML metacharacters such as `<`, `>`, `"` are accepted, and Linux/macOS filenames may contain them.
- **Why it's a problem**: A geometry uploaded (or a config/case name supplied) with a payload such as `a<img src=x onerror=...>.stl` executes script in any session that renders it — a stored DOM-XSS. The default bind is localhost, which limits remote exploitation, but the server can be started with `--host 0.0.0.0`, and SLURM job names from a shared cluster are also injected.
- **Suggested fix**: Set text via `textContent` / `createElement`, or escape values with a small `escapeHtml()` helper before interpolation; for attributes, prefer `dataset` assignment over inline `data-name="..."` string building.
- **Related code**: `app.js:2445-2447` (popover uses `${content.title}`), `server.py:436`, `server.py:1990-2011`.

---

### Bug #8 – Duplicate / concurrent case downloads can corrupt the local case directory
- **Severity**: Medium
- **Confidence**: Medium
- **Location**: `src/rapidfoam/web/server.py:771-789`
- **Description**: The endpoint checks `_get_download_progress(case_name).get("active")` and then sets `active=True` in two separate critical sections. Two near-simultaneous POSTs can both observe `active == False`, both bypass the 409 overwrite guard (line 775 checks before either writes), and both schedule `_run_download` writing into the same `cases/<case>/` directory. The second `tar` extraction overwrites/interleaves files from the first.
- **Why it's a problem**: A double-click or two browser tabs during a slow download can produce a corrupted case directory, which is then reported as successfully downloaded.
- **Suggested fix**: Use an atomic compare-and-set under `_download_progress_lock` (reserve the name before the overwrite check and before scheduling), and remove the reservation on failure.
- **Related code**: `server.py:720-751`, `app.js:2171-2220` (overlapping `pollDownloads` interval can also fire duplicate success handling).

---

### Bug #9 – Half-initialised `STLViewer` object passed around as valid → null dereferences
- **Severity**: Low
- **Confidence**: Medium
- **Location**: `src/rapidfoam/web/static/js/viewer.js:18-19`, `viewer.js:56-59`; caller `src/rapidfoam/web/static/js/app.js:239`
- **Description**: The constructor returns early if the container is missing (line 19) or if `THREE` is undefined (line 59), but the object is still truthy. `app.js` assigns it (`this.viewer = new STLViewer(...)`) and later calls methods such as `updateDomainBox` (`this.scene.add`, line 725) and `fitView` (`this.camera.fov`, line 405) that dereference the null fields.
- **Why it's a problem**: If the container id is absent or the Three.js CDN fails to load, the viewer throws `TypeError`s; because `app.js` wraps the update path in a broad try/catch, it fails silently with no user feedback.
- **Suggested fix**: Throw from the constructor when initialisation cannot proceed, and have `app.js` check for a usable viewer (or return `null`) before calling methods.

---

### Bug #10 – `updateDomainBox(null, null)` leaves stale domain state
- **Severity**: Low
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/viewer.js:523-533`
- **Description**: When called with no domain, the visual box is removed but the function returns before clearing `this.domainMin`/`this.domainMax` (they retain the previous values). `fitView('domain')`, `setViewAngle`, `resetCamera` and toggle logic then keep framing/showing a domain that no longer exists.
- **Why it's a problem**: UI state and visuals disagree; the camera can frame empty space after the domain is cleared.
- **Suggested fix**: Set `this.domainMin = this.domainMax = this.symPlaneCoord = null` on the early-return path.

---

### Bug #11 – Viewer never tears down its RAF loop, listeners, resize observer or renderer
- **Severity**: Low
- **Confidence**: Medium
- **Location**: `src/rapidfoam/web/static/js/viewer.js:122-130`, `viewer.js:981-985`
- **Description**: `animate()` unconditionally re-schedules itself with `requestAnimationFrame`, even when the container is hidden (it only skips rendering). `window.addEventListener('resize', ...)` is anonymous (cannot be removed), and there is no `dispose()`/`destroy()` to `cancelAnimationFrame`, `removeEventListener`, `resizeObserver.disconnect()` or `renderer.dispose()`. Two viewers exist in the app (`app.js:239`, `app.js:2716`).
- **Why it's a problem**: Permanent RAF chains and WebGL contexts accumulate if the UI is re-initialised; browsers cap live WebGL contexts, after which new contexts fail silently.
- **Suggested fix**: Store the RAF handle, add a `dispose()` that cancels it, disconnects the observer, removes listeners and disposes the renderer; stop scheduling when the container is not visible.

---

### Bug #12 – `copy_stl` fast path skips multi-solid name merging when the first solid name matches the stem
- **Severity**: Low
- **Confidence**: Medium
- **Location**: `src/rapidfoam/stl_utils.py:222-241`
- **Description**: `copy_stl` takes the `shutil.copy2` fast path when `name is None or name == original_name`, where `original_name` is only the **first** solid name (`stl_info` records the first `solid` line, `stl_utils.py:85-86`). The streaming rewrite path — which rewrites every `solid`/`endsolid` line to merge multi-body CAD exports — is skipped. The output therefore depends on an incidental detail (whether the first solid happens to be named like the stem), contradicting the function's documented contract ("Every solid/endsolid line is rewritten so that multi-body CAD exports are merged under the single solid name").
- **Why it's a problem**: The behaviour is non-deterministic with respect to the requested name, and the guaranteed merge is silently skipped. Functional impact is conditional: `snappyHexMesh` usually reads an STL as one surface regardless of embedded solid names, but multi-region workflows (e.g. `multiRegionFeatureSnap`, or tools that split by solid) can observe unexpected sub-patch names.
- **Suggested fix**: Only use the fast path when the file is known to contain a single solid; otherwise stream and rewrite (or always stream to honour the contract).

---

### Bug #13 – `iterations_per_second == 0` is displayed and serialised as missing
- **Severity**: Low
- **Confidence**: High
- **Location**: `src/rapidfoam/web/static/js/app.js:3070`, `src/rapidfoam/web/server.py:1963`
- **Description**: JS uses `data.iterations_per_second ? ... : '--'`; a legitimate `0 it/s` is falsy and shown as `--`. The backend similarly maps `0` to `null`: `round(iterations_per_second, 4) if iterations_per_second else None` (`server.py:1963`).
- **Why it's a problem**: Real diagnostic values are reported as absent.
- **Suggested fix**: Use explicit `!== null && isFinite(...)` checks on both sides.

---

### Bug #14 – Tar-stream download races with SSH disconnect/close
- **Severity**: Low
- **Confidence**: Medium
- **Location**: `src/rapidfoam/web/ssh_client.py:325-374`
- **Description**: `download_directory` deliberately releases `self._lock` while streaming (`exec_command` is taken under the lock, the body read is not). Any concurrent call to `disconnect()` (e.g. `/api/cluster/disconnect`, or a re-`connect()`) closes `self._client`/channels mid-stream. Reads from `stdout` then raise; the `finally` swallows the `recv_exit_status` exception and reports a generic failure, potentially after a partial extraction.
- **Why it's a problem**: Disconnecting or reconnecting during a long transfer leaves a partially written local case and a confusing error, and can interleave with a new session.
- **Suggested fix**: Capture the client/channel reference used for the transfer and abort cleanly on disconnect (e.g. a per-transfer cancel event), or extract to a temp directory and atomically rename on success.

---

### Bug #15 – Symmetry projection crashes on any non-`x/y/z` column name
- **Severity**: Low
- **Confidence**: Medium
- **Location**: `src/rapidfoam/web/server.py:1113-1136`
- **Description**: `_project_force_columns_for_symmetry` / `_project_moment_columns_for_symmetry` compute `"xyz".index(name[-1])` for every key in `force_cols`/`moment_cols`. When `normalize_component_columns` returns header-derived keys (any header containing `total_x`), a force/moment file that also carries an extra non-axis column (e.g. a derived/magnitude column, or a future OpenFOAM output) makes `name[-1]` something other than `x/y/z`, raising `ValueError` and returning HTTP 500 for symmetry cases.
- **Why it's a problem**: A valid-but-extended solver output column turns a half-model telemetry request into a server error.
- **Suggested fix**: Skip or pass through names whose last character is not in `"xyz"`:
  ```python
  if name[-1] not in "xyz": return list(values)
  ```
  or restrict projection to the known `total_/pressure_/viscous_` axis keys.

---

## Notes on false positives avoided
- The nested `@_synchronized` calls in `ssh_client.py` (`connect` → `disconnect`/`test_connection`; `is_case_running` → `get_slurm_queue`) are **not** deadlocks because `_lock` is a `threading.RLock`.
- `config.validate`'s early return on non-object sections correctly prevents the `flow`/`outputs`/`patches` attribute errors that would otherwise occur.
- The `runApplication -s solver decomposePar` / `runParallel -s potential potentialFoam` syntax is valid for the ESI/Foundation `RunFunctions` (`-suffix|-s <suffix>`), so it is not a defect; only the resulting log filename differs from the committed case artifact.
- `_shift_moment_columns` pressure/viscous branches correctly use the matching pressure/viscous force columns; no cross-contamination was found.
