# RapidFOAM Bug Hunt — Verification & Fix Report

Source report: `bug-hunt/BUGS.md`

## Summary

| Outcome | Count |
|---------|-------|
| Fixed | 15 |
| Confirmed but not fixed | 0 |
| False positive | 0 |
| **Total** | **15** |

Verification: every referenced location was read on the checked-out tree
(`d72a14f`). All 15 reports were confirmed as real defects (none were false
positives) and all 15 are now fixed, including the #5 error-surfacing UX
follow-up.

Test status after fixes: **141 Python tests pass, 10 skipped** (`python -m
unittest discover -s tests`), up from 136, plus **25 front-end tests pass**
(`npm test`, JSDOM + `node:test`). Five Python and 25 JS regression tests were
added.

A front-end test harness now exists under `tests/js/` (`harness.mjs`,
`app.test.mjs`, `viewer.test.mjs`); it loads the browser sources into JSDOM and
covers the fixed front-end defects (#1, #2, #3, #4, #5, #6, #7, #9, #10, #11,
#13). Install once with `npm install`, then run `npm test`.

---

### Bug #1 – "Validate" button silently generates a case and overwrites existing config/case
- **Status**: Fixed
- **Reasoning**: Confirmed. `validateCurrentConfig()` omitted `generate_locally`, and
  `GenerateCaseRequest.generate_locally` defaulted to `True` (`server.py:241`). With the
  handler writing `configs/<case>.json` when `generate_locally or upload_to_cluster`, a
  pure validation call mutated the filesystem.
- **Changes made**:
  - `src/rapidfoam/web/static/js/app.js`: `validateCurrentConfig()` now sends
    `generate_locally: false`.
  - `src/rapidfoam/web/server.py`: model default changed to `generate_locally: bool = False`
    so a caller that omits the flag cannot mutate state.
  - `tests/test_web_api.py`: added `test_generate_request_defaults_to_validation_only`.
- **Risk**: None identified; all JS callers already pass the flag explicitly
  (`app.js:1656`, `app.js:1736`).

---

### Bug #2 – Large (millimetre-scale) STL models are clipped/invisible in the 3D viewer
- **Status**: Fixed
- **Reasoning**: Confirmed. Fixed far plane (`2000`) and `controls.maxDistance` (`1500`)
  cannot frame mm-scale models whose `fitView` distance is ~3× `maxDim`.
- **Changes made**:
  - `src/rapidfoam/web/static/js/viewer.js`: `recomputeOverallBoundingBox()` now scales the
    camera far plane (`max(2000, radius * 20)`) and orbit `maxDistance`
    (`max(1500, radius * 10)`) from the loaded bounds.
- **Risk**: Minor — the far plane is not shrunk back when all STLs are cleared, which is
  harmless (only reduces depth precision marginally on empty scenes).

---

### Bug #3 – Telemetry auto-refresh can permanently stall (`telemetryInFlight` never cleared)
- **Status**: Fixed
- **Reasoning**: Confirmed. `beginTelemetryCaseSwitch()` bumped `telemetryRequestId` without
  clearing `telemetryInFlight`. A poll in flight at that moment had its `finally` suppressed
  by the id check, and the 5 s interval guard then blocked all future polls.
- **Changes made**:
  - `src/rapidfoam/web/static/js/app.js`: `beginTelemetryCaseSwitch()` now sets
    `this.telemetryInFlight = false` when invalidating an in-flight poll.
- **Risk**: None; callers that immediately poll re-set the flag.

---

### Bug #4 – Solver-health badge reports "Healthy" when continuity data is missing
- **Status**: Fixed
- **Reasoning**: Confirmed. `Number(null) === 0`, and `isFinite(0)` is true, so a case with
  no continuity output was labelled "Healthy · N lin iters".
- **Changes made**:
  - `src/rapidfoam/web/static/js/app.js`: `renderSolverHealth()` now computes
    `hasContinuity` (not null/undefined and finite) before classifying the badge; the
    display path already handled `null` correctly.
- **Risk**: None.

---

### Bug #5 – `pollTelemetry` ignores HTTP status; backend errors render as a valid "no data" state
- **Status**: Fixed
- **Reasoning**: Confirmed. The three telemetry fetches branched on `data.has_data` without
  checking `res.ok`; an error body `{ "detail": ... }` fell through to the "Generated /
  Ready" presentation.
- **Changes made**:
  - `src/rapidfoam/web/static/js/app.js`: added `if (!res.ok) throw new Error(...)` before
    parsing in the forces, residuals and solver-health fetches.
  - `src/rapidfoam/web/static/index.html` + `style.css`: added a dismissable
    `#telemetry-error-banner` alert and an `.error` state for the convergence pill.
  - `src/rapidfoam/web/static/js/app.js`: `showTelemetryError()` / `clearTelemetryError()`
    display the reason (e.g. "Forces request failed (HTTP 500)") and reset the pill; the
    banner clears on a healthy poll and on case switch.
- **Risk**: None. Error messages are server/JS-generated and inserted via `textContent`.

---

### Bug #6 – Flow-direction arrow leaks GPU resources on every update
- **Status**: Fixed
- **Reasoning**: Confirmed. `updateFlowArrow` removed the previous `ArrowHelper` from the
  scene but never disposed its line/cone geometries and materials.
- **Changes made**:
  - `src/rapidfoam/web/static/js/viewer.js`: before replacing, the old arrow is traversed and
    each child is passed to `this.disposeObject(...)`, then the reference is nulled.
- **Risk**: None.

---

### Bug #7 – Stored XSS through unescaped values injected via `innerHTML`
- **Status**: Fixed
- **Reasoning**: Confirmed. Filenames/case names/config `stl_files` are only sanitised for
  path separators (`server.py:563-566`, `config.py:335`) and SLURM job fields come from a
  shared cluster; all were interpolated raw into `innerHTML`.
- **Changes made**:
  - `src/rapidfoam/web/static/js/app.js`: added an `escapeHtml()` helper and applied it to
    untrusted interpolations in `renderActiveSTLChips`, `renderQueueTable`,
    `renderDownloads`, `renderCasesArchiveTable` and `showToast`. `data-name="..."` attribute
    interpolation for case action buttons was replaced with `dataset` assignment. The
    archive fidelity CSS class is now sanitised as well.
- **Risk**: The help-popover (`content.title`/`content.html`) and the run-command overlay
  (`data.run_command`) still use `innerHTML`; both are app/server-authored constants, not
  user data, and were left as-is to avoid changing rich-text rendering. A full CSP or
  `textContent` migration would be a larger follow-up.

---

### Bug #8 – Duplicate / concurrent case downloads can corrupt the local case directory
- **Status**: Fixed
- **Reasoning**: Confirmed. The active check (`_get_download_progress`) and the set
  (`_set_download_progress`) were in separate critical sections, so two concurrent POSTs
  could both observe inactive and both schedule `_run_download` into the same directory.
- **Changes made**:
  - `src/rapidfoam/web/server.py`: `api_case_download` now performs the active check, the
    overwrite/409 guard and the reservation inside one `_download_progress_lock` critical
    section. A scheduling failure clears the reservation.
  - `tests/test_web_api.py`: added `test_concurrent_downloads_reserve_case_atomically`.
- **Risk**: The remote-existence and connection checks still happen before the lock; that is
  intentional (they are slow and network-bound) and does not affect the reservation logic.

---

### Bug #9 – Half-initialised `STLViewer` object passed around as valid → null dereferences
- **Status**: Fixed
- **Reasoning**: Confirmed. The constructor returned early when the container was missing or
  `THREE` was undefined, yet the object was still truthy and `app.js` called methods on it.
- **Changes made**:
  - `src/rapidfoam/web/static/js/viewer.js`: the constructor now sets
    `this.available = false` up front and only flips it to `true` at the end of a successful
    `init()`; a `disposed` flag and RAF/observer/listener handles are also initialised.
  - `src/rapidfoam/web/static/js/app.js`: added `createSTLViewer(containerId)`, which returns
    `null` for an unavailable viewer. Both `this.viewer` and `this.telemetryViewer` are built
    through it, so every existing `if (this.viewer)` guard is now honest. Unguarded toggle /
    framing / reset / angle handlers now use `this.viewer?.method(...)`.
  - `src/rapidfoam/web/static/js/app.js`: `showViewerUnavailableWarning()` reports the
    failure in the viewer placeholder and via a toast instead of failing silently.
  - `tests/js/viewer.test.mjs`, `tests/js/app.test.mjs`: cover the unavailable constructor
    paths and `createSTLViewer` returning `null`.
- **Risk**: None material. Behaviour when Three.js loads normally is unchanged.

---

### Bug #10 – `updateDomainBox(null, null)` leaves stale domain state
- **Status**: Fixed
- **Reasoning**: Confirmed. The early return removed the visual box but left
  `domainMin`/`domainMax`/`symPlaneCoord` at their previous values, so `fitView`, toggle and
  reset logic kept referencing a domain that no longer existed.
- **Changes made**:
  - `src/rapidfoam/web/static/js/viewer.js`: the early-return path nulls `domainMin`,
    `domainMax` and `symPlaneCoord`.
- **Risk**: None.

---

### Bug #11 – Viewer never tears down its RAF loop, listeners, resize observer or renderer
- **Status**: Fixed
- **Reasoning**: Confirmed. `animate()` always re-scheduled via `requestAnimationFrame`; the
  resize listener was anonymous; there was no `dispose()`/`destroy()`; two viewers exist.
- **Changes made**:
  - `src/rapidfoam/web/static/js/viewer.js`: the RAF handle is stored (`this._rafId`), the
    resize handler is a named method reference, and a new `dispose()` cancels the loop,
    removes the listener, disconnects the `ResizeObserver`, disposes scene objects, calls
    `renderer.dispose()`, removes the canvas and nulls the fields. `animate()` returns
    immediately once `disposed`, so it cannot reschedule.
  - `src/rapidfoam/web/static/js/app.js`: `disposeTelemetryViewer()` releases the telemetry
    viewer when the 3D panel is collapsed (rebuilt lazily on expand); `CFDApp.destroy()`
    clears polling/timers and disposes both viewers, and is bound to `beforeunload`.
  - `tests/js/viewer.test.mjs`, `tests/js/app.test.mjs`: cover RAF cancellation, listener
    removal, renderer disposal and the app-level destroy path.
- **Risk**: Disposing the telemetry viewer on collapse means a small re-init cost when the
  panel is reopened; this is deliberate to bound WebGL context usage.

---

### Bug #12 – `copy_stl` fast path skips multi-solid name merging when the first solid name matches the stem
- **Status**: Fixed
- **Reasoning**: Confirmed. `stl_info` records only the first `solid` name, so a
  multi-body export whose first solid already matched the requested name took the
  `shutil.copy2` fast path and skipped the merge/rewrite entirely.
- **Changes made**:
  - `src/rapidfoam/stl_utils.py`: `copy_stl` now takes the fast path only when `name is None`.
    When a rename is requested it always streams and rewrites every `solid`/`endsolid` line,
    honouring the documented contract. Docstring updated.
  - `tests/test_regressions.py`: added
    `test_multi_solid_stl_rename_when_first_solid_matches_is_still_merged`.
- **Risk**: Case generation now always streams when a target name is supplied (it is), so a
  single-solid STL is rewritten rather than byte-copied. Cost is one sequential read, already
  performed by `stl_info`; correctness is guaranteed.

---

### Bug #13 – `iterations_per_second == 0` is displayed and serialised as missing
- **Status**: Fixed (defensive)
- **Reasoning**: Partially applicable. The JS falsy test was a genuine reporting defect.
  On the backend, `iterations_per_second` is only assigned when it is strictly positive
  (`et1 > et0` / `et1 > 0`), so a literal `0` cannot currently reach the serialiser — the
  truthiness check was latent, not actively wrong.
- **Changes made**:
  - `src/rapidfoam/web/static/js/app.js`: `renderSolverHealth` now uses an explicit
    `!== null && !== undefined && isFinite(...)` check for `solver-iter-rate`.
  - `src/rapidfoam/web/server.py`: serialisation uses `is not None` instead of a truthiness
    test.
- **Risk**: None. The ETA guard still avoids division by zero.

---

### Bug #14 – Tar-stream download races with SSH disconnect/close
- **Status**: Fixed
- **Reasoning**: Confirmed. `download_directory` intentionally releases `self._lock` while
  streaming, so a concurrent `disconnect()`/`connect()` closes the client and channel
  mid-transfer, leaving a partial local case and a generic error.
- **Changes made**:
  - `src/rapidfoam/web/ssh_client.py`: the transfer now extracts into a hidden sibling
    staging directory (`.<case>.download-<rand>`, which `api_list_cases` already skips as a
    dotfile) and publishes it with an atomic `os.replace` only after the archive is fully
    consumed and a clean exit status is confirmed. On any exception (including a disconnect
    mid-stream) the staging directory is removed, so the destination never contains a
    partially written case. The client/channel used for the transfer is captured under the
    lock. `shutil`, `tempfile` imports added.
  - `tests/test_web_api.py`: added `test_download_directory_is_atomic_on_failure`, which
    feeds a truncated tar and asserts the destination and staging area are both absent.
- **Risk**: On success an overwrite-approved existing `cases/<case>/` is replaced wholesale
  (the intended full-mirror semantic). Unlike per-file resumable sync, an interrupted
  download now leaves the previous copy untouched rather than a half-updated one.

---

### Bug #15 – Symmetry projection crashes on any non-`x/y/z` column name
- **Status**: Fixed
- **Reasoning**: Confirmed. `"xyz".index(name[-1])` raises `ValueError` for header-derived
  keys whose last character is not an axis (e.g. a magnitude column), turning a valid
  telemetry request into HTTP 500 for symmetry cases.
- **Changes made**:
  - `src/rapidfoam/web/server.py`: `_project_force_columns_for_symmetry` and
    `_project_moment_columns_for_symmetry` now pass unknown/derived columns through
    unchanged instead of raising.
  - `tests/test_regressions.py`: added
    `test_symmetry_projection_ignores_non_axis_columns`.
- **Risk**: For an unrecognised column the orientation is unknown, so it is left untouched
  rather than doubled or zeroed. This is the conservative choice and is documented inline.

---

## Files changed

- `src/rapidfoam/web/server.py` — Bug #1 (model default), #8 (atomic reservation),
  #13 (explicit None check), #15 (symmetry projection guards).
- `src/rapidfoam/web/ssh_client.py` — Bug #14 (staged, atomic download).
- `src/rapidfoam/web/static/js/app.js` — Bug #1, #3, #4, #5, #7, #9, #11, #13.
- `src/rapidfoam/web/static/js/viewer.js` — Bug #2, #6, #9, #10, #11.
- `src/rapidfoam/stl_utils.py` — Bug #12.
- `tests/test_web_api.py` — Bug #1, #8, #14 regression tests.
- `tests/test_regressions.py` — Bug #12, #15 regression tests.
- `tests/js/` + `package.json` — Bug #1–#7, #9–#11, #13 front-end regression tests.
- `src/rapidfoam/web/static/index.html`, `css/style.css` — Bug #5 error banner and pill state.
