# Changelog

All notable changes to RapidFOAM will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.6.0] - 2026-09-29

### Added
- **Boundary-layer thickness coverage**: the mesh-quality report (`read_forces.py
  --mesh`, the Studio Mesh Quality panel) now shows snappy's realised layer
  *thickness* percentage alongside layer-count coverage, and flags a full-but-thin
  stack (count ≥ 90% but thickness < 70%) as a warning — realised y+ remains the
  authoritative near-wall check. The Studio mesh endpoint also now reads the
  two-pass `log.snappyHexMesh.layering` log (boundary-layer coverage was blank for
  two-pass cases).
- **Two-pass layering (opt-in)**: `layers.two_pass` runs `snappyHexMesh` twice —
  pass 1 castellates + snaps (no layers), pass 2 adds layers only with the quality
  gate relaxed (`system/snappyHexMeshDict_layering`), which avoids snappy's
  undo iterations stripping the prisms. Validated on a crude 144-triangle test
  body: `geometry` patch coverage **65% → 92.6%** (5/5 layers). The disabled gate
  is aggressive — it produced max non-orthogonality 115 and skewness 30 — so the
  layering limits must be tapered back toward the normal values with `checkMesh`
  before use. Described in the README Mesh Quality section.
- **Graded background mesh (opt-in)**: `mesh_params.grading` (default `"off"`)
  can derive a per-axis `blockMesh` `simpleGrading` toward the ground and
  symmetry planes — coarsening *away* from the body while keeping the near-body
  cell at the base cell size; the flow/wake axis stays uniform. `"auto"` uses
  `mesh_params.grading_ratio` (default 3.0) and `[gx, gy, gz]` sets explicit
  ratios, and the CLI dry-run reports the grading and cell reduction. It is
  **off by default**: validated on a real case (OpenFOAM v2606, local WSL), it
  trims ~10–30% of the final cells but raises non-orthogonality/aspect ratio
  (e.g. max non-ortho 64.5→68.3, aspect 26→45 at ratio 3.0), so it is opted into
  only when a cell budget matters more than mesh quality.
- **Surface-integrity reporting**: the generated `Allrun`, `Allrun.parallel` and
  `run.sh` run `surfaceCheck` on every STL *before* meshing and write
  `log.surfaceCheck`. `rapidfoam.postproc.surfacecheck` parses it (closure/open
  edges, self-intersection count, illegal triangles, unconnected parts,
  normal-orientation zones, triangle quality/edge lengths) into a
  Good/Concern/Bad verdict, shown in a new **Surface Integrity** Studio panel and
  via `read_forces.py --surface` (exit 0 ok, 2 concerns) and
  `GET /api/telemetry/surface`. A symmetry half model is open along the cut by
  construction and is exempt from the closure requirement.
- **Optional enforcement**: `surface_check.enforce` (default **false**) makes the
  pipeline *abort* on leaking/self-intersecting/illegal geometry. Off, the check is
  report-only and never stops the run; when enforcing, symmetry and
  `surface_check.allow_open` are exempt from the closure check.
- New `surface_check` config section (`enabled`, `enforce`, `check_self_intersection`,
  `allow_open`, `max_illegal_triangles`, `max_unconnected_parts`), validated and
  documented.

### Changed
- **Mesh auto-heuristics default off**: `mesh_params.auto_size` and
  `mesh_params.auto_feature_angle` now default to **false**, joining background
  `grading` (off). The generated mesh is the plain fidelity preset by default —
  predictable and quick to reason about — and the heuristics are opted into only
  when a geometry needs small-feature refinement or a derived feature angle.
- README gains a **Mesh Quality & Remediation** section mapping `checkMesh`
  findings to the single knob to adjust, plus the "never bad rather than always
  optimal" rationale.
- **Near-wall y+ presets retuned to best practice**: `fast` y+ ~50 / 5 layers,
  `standard` y+ ~30 / 8 layers (was y+ ~10, which sits in the buffer layer), and
  `fine` y+ ~1 / 20 layers at growth 1.1. Each preset now sits in a valid
  k-ω SST regime (log layer or viscous sublayer) rather than straddling the
  buffer layer.
- **`layers.min_thickness_ratio` removed**: `minThickness` is always the full
  first layer (the quality-gate behaviour the old default already used), dropping
  a fence-sitting knob.
- **Architecture refactor**: the package is now split into bounded contexts —
  `core/` (axes, faces, fields, FoamFile primitives, config, caseconfig),
  `geometry/` (STL I/O + stats), `meshing/` (presets, domain, sizing, grading,
  layers, `MeshPlan`, writers, pipeline), `casegen/` (builder + case writers),
  `runtime/` (scripts copied to cases/clusters), `postproc/` (measurement) and
  `web/` (app, routers, services). Config reads go through one override-aware
  `core.caseconfig`; mesh derivation goes through the immutable `MeshPlan`; the
  web server is split into routers/services. The old compatibility shims
  (`geometry.py`, `writers/*`, `web/server.py`) are removed and the
  `rapidfoam-studio`/`rapidfoam-web` entry points target `rapidfoam.web.app`.
- **Studio launcher module moved**: use `python -m rapidfoam.web.app`;
  `rapidfoam.web.server` no longer exists. The `rapidfoam-studio` /
  `rapidfoam-web` console scripts are unchanged.
- **SLURM walltime/memory follow the fidelity preset**: `time`/`mem_per_cpu` are
  applied from the preset unless set explicitly — in the CLI, the shipped
  config, and the Studio (which no longer emits defaults that overrode the
  preset). `standard` memory is now `2G`.
- **Full-car symmetry**: a config with an explicit non-symmetry face list is no
  longer treated as a half model just because `symmetry_plane` is present; the
  Studio omits `symmetry_plane` when the field is left blank.
- **New Studio controls**: boundary patch names, surface-integrity check,
  vehicle geometry (persisted to the case config), background grading and
  two-pass layering.

### Fixed
- The `surfaceCheck` enforce gate now recognises an explicit symmetry face (not
  only `symmetry_plane`), so a default half model is exempt from the closure
  check instead of aborting on its legitimate cut.
- Remote telemetry now fetches `log.surfaceCheck`, so the Surface Integrity
  panel populates for cluster cases.
- Telemetry reference values honour the `overrides` block, and the CSV export
  emits the full history instead of the downsampled UI series.

## [1.5.0] - 2026-09-28

### Added
- **Mesh-quality verification**: `rapidfoam.postproc.checkmesh` parses the `checkMesh` log and snappyHexMesh's per-patch layer tables, reporting non-orthogonality, skewness, aspect ratio, cell volumes, concave cells, cell types, boundary-patch closure and boundary-layer coverage. Exposed via `read_forces.py --mesh` (exit 0 ok, 2 concerns) and a Studio **Mesh Quality** panel.
- **Tiered mesh-quality verdict**: each metric is judged against CFD-practical good/caution bands (independent of snappy's give-up limits) and rolled up into a Good/Usable/Marginal/Bad verdict, downgraded to Bad on any hard failure. Configurable via `mesh_quality.verdict_bands`.
- **Realised y+ cross-reference**: the Studio mesh panel reads the `yPlus` function object and reports per-patch realised y+ against `layers.y_plus_target`, flagging dropout and missed targets in the same view as layer coverage.
- **Config-form merge**: the Studio now merges form edits over the loaded config instead of rebuilding it, so unknown/future fields and comment keys survive edits and JSON round-trips. Exposes `feature_extract.includedAngle` and `slurm.cpus_per_task`, and sources the fidelity-card cell/time text from the server presets.
- **Geometry-aligned feature extraction**: when the derived `resolveFeatureAngle` sharpens detection, `surfaceFeatureExtract`'s `includedAngle` is raised to match (`180 - resolve`) so explicit `.eMesh` edges and snappy's implicit snapping agree; an explicit `includedAngle` still wins.

### Changed
- **`stl/` and `cases/` are fixed project conventions**: the half-wired `stl_dir`/`case_dir` config fields (honoured by the CLI, ignored by every Studio endpoint) were removed.
- `nSmoothThickness` default raised 10 -> 15 for more stable layer thickness on curved surfaces.

### Fixed
- **Half-model patches no longer flagged as open leaks**: checkMesh reports a symmetry-cut body patch as "non-closed"; the report now suppresses open-patch findings on symmetry cases (only a full model can leak) and treats `ground`/`farField` as domain boundaries.
- **`min_thickness_ratio` default reverted to 1.0**: a measured before/after showed the 0.5 default kept poorly-conditioned partial prisms (layer coverage 2/2 -> 1/2, aspect ratio 20.8 -> 62.4, determinant -585x), so the all-or-nothing floor is restored as a quality gate; the knob remains for deliberate per-case use.

## [1.4.0] - 2026-09-26

### Added
- **Feature-based mesh auto-sizing**: STLs are analyzed in a streaming pass for triangle edge statistics (`EdgeStats`); `compute_mesh_params` widens snappy surface/edge refinement so the smallest geometry feature (robust low-percentile edge, or thinnest extent) is resolved with a configurable number of cells, capped by `max_surface_level` to bound the cell budget. Never coarsens the fidelity preset; toggle with `mesh_params.auto_size`, tune with `feature_percentile`, `feature_cells`, `max_surface_level`. The CLI reports the detected feature size and resulting finest surface cell, and the Studio layer preview reflects it live.
- **Geometry-derived `resolveFeatureAngle`**: a streaming dihedral (normal-angle) histogram (`FeatureAngleStats`) is built from each STL and used to derive the snappy feature threshold, so subtle creases are snapped while smooth tessellation is not. Only ever sharpens detection (never looser than the preset). Tune with `mesh_params.auto_feature_angle`, `crease_percentile`, `feature_angle_ratio`, `crease_angle_floor`.
- **Near-wall y+ verification**: `rapidfoam.postproc.yplus` reads the `yPlus` function object output and compares per-patch averages against `layers.y_plus_target`, regime-aware for wall-function vs. wall-resolved targets. Exposed via `read_forces.py --yplus` (exit 0 met, 2 missed) and summarised in the standard force output.
- **Studio auto-sizing controls**: Auto-Sizing On/Off, Cells Across Smallest Feature and Max Surface Level controls in the mesh override section, with the detected feature and derived feature angle shown next to the layer preview.
- `stl_analyze_full()` unified streaming inspector (solid name, triangle count, bounding box, edge and crease-angle statistics); `stl_info()` and `stl_analyze()` delegate to it.
- `mesh_params.auto_size`/`max_surface_level`/`feature_cells`/`feature_percentile` and `mesh_params.auto_feature_angle`/`crease_percentile`/`feature_angle_ratio`/`crease_angle_floor` documented in the config example and validated.

### Fixed
- **Missing STL before generate/submit**: the cluster upload path silently skipped STLs absent from local `stl/`, shipping a config with missing (or stale) geometry that only failed on the cluster. Every referenced STL is now validated up front with a clear 400, and the upload loop errors instead of skipping.
- **Auto-sizing preview stale on geometry change**: `updateDomainBoxVisualization` fetched the domain-box payload (which carries the layer/auto-size preview) but only updated the 3D viewer, so changing the STL left the preview stale; it is now rendered from the same response.
- **Ground-layer clearance consistency**: the guard measured clearance against the raw `ground_plane` while the mesh places the road at `ground_plane - GROUND_EMBED`; both now share the constant.
- **`potentialFoam` failure visibility**: a failed init is logged as a visible warning in `Allrun`, `Allrun.parallel` and `run.sh` instead of being silently swallowed.
- **Layer/cell size sanity**: validation warns when an absolute first-layer thickness exceeds half the finest surface cell (which makes snappy drop boundary layers silently).

### Changed
- **Wall-treatment labelling**: the `fine` preset targets y+ ~ 1 but uses Spalding-bridging wall functions, so it is now described as "wall-function-bridged at low y+" rather than wall-resolved. Added a README "Near-Wall y+ & Boundary Layers" section covering the sizing chain, wall-treatment tiers, and the flat-plate `u_tau` caveat.

## [1.3.0] - 2026-09-25

### Added
- **Front-end test harness**: JSDOM + `node:test` suite under `tests/js/` covering the Web Studio browser logic (`npm install && npm test`).
- **Telemetry error surfacing**: a visible banner and pill state now explain why telemetry is unavailable (e.g. HTTP 500) instead of silently showing an empty run.

### Fixed
- **Validate no longer mutates state**: the Studio "Validate" action sent no `generate_locally` flag, so the endpoint's `True` default silently overwrote `configs/<case>.json` and regenerated `cases/<case>/`. The request now sets `generate_locally: false` and the API model defaults to `false`, so a validation-only call can never write files.
- **Large (millimetre-scale) STL viewing**: the 3D viewer's fixed far plane (2000) and orbit `maxDistance` (1500) clipped models exported in mm. Both now scale with the loaded geometry bounds.
- **Telemetry auto-refresh stall**: switching the monitored case left `telemetryInFlight` set when a poll was in flight, permanently suppressing the 5 s interval; the flag is now cleared on case switch.
- **Solver-health badge false "Healthy"**: missing continuity was coerced by `Number(null) === 0` and shown as "Healthy"; the badge now requires real, finite continuity data.
- **Telemetry HTTP errors**: `pollTelemetry` ignored `res.ok` and rendered backend errors as a normal "no data" run; each fetch now rejects non-2xx responses.
- **3D flow-arrow GPU leak**: the previous arrow was removed without disposing its geometries/materials, leaking GL buffers on every parameter edit.
- **Stored XSS in the Studio**: filenames, case names and SLURM job fields were interpolated into `innerHTML` unescaped; all untrusted values are now escaped, with attributes set via `dataset`.
- **Concurrent case downloads**: the active-check and reservation were separate critical sections, so two requests could both start and corrupt `cases/<case>/`; reservation is now atomic under the progress lock.
- **Case downloads vs. SSH disconnect**: transfers now extract into a staging directory and publish atomically, so a mid-stream disconnect can no longer leave a partially written case.
- **3D viewer lifecycle**: the viewer now exposes an `available` flag (unavailable instances are treated as absent with a user-visible notice) and a `dispose()` that cancels its RAF loop, listeners, resize observer and WebGL context.
- **Symmetry telemetry on extended force files**: a non-`x/y/z` column (e.g. a magnitude) raised `ValueError` and returned HTTP 500; unknown columns now pass through.
- **`copy_stl` multi-solid merging**: the byte-copy fast path could skip rewriting all `solid`/`endsolid` lines when the first solid happened to match the target name; merging is now always honoured.
- **`iterations_per_second == 0`**: a legitimate zero rate was displayed and serialised as missing; explicit null checks now distinguish `0` from absent.

## [1.2.0] - 2026-09-18

### Added
- **y+ boundary-layer targeting**: fidelity presets carry a near-wall `y_plus_target` that is converted to an absolute first-layer thickness (flat-plate friction-velocity estimate) and written with `relativeSizes false`; `layers.relativeSizes` selects absolute metres versus fractions of the local cell size.
- **FSAE preset refresh**: geometry-relative base cell (`cells_per_length`), wall-function layer counts (fast 2, standard 3), wall-resolved fine tier (12 layers, y+ 1), distance shells as base-cell multiples, refreshed cell/runtime/SLURM estimates; ground layers are opt-in with a clearance guard and a two-layer cap.
- **Studio near-wall controls**: Auto (preset y+) mode with y+ target, ground-layer selector, live layer preview, and server-driven override placeholders.
- **Full aerodynamic telemetry**: force/moment component breakdown (total/pressure/viscous) and `Cd, Cl, Cs, CmPitch, CmRoll, CmYaw` (with Cd/Cl KPIs), coefficient chart, and CSV/JSON export.
- **Post-run reference editor**: recompute coefficients from raw forces with ad-hoc `Aref`, `lRef`, `rho`, `velocity` and `CofR` (parallel-axis shift) without re-running the solver.
- **Solver-health diagnostics**: continuity, linear-solver effort, execution time, iteration rate and ETA from `log.simpleFoam`, plus the ±0.5% convergence band on the force chart and per-panel help popovers.
- **3D aero-load view**: force/moment vectors (resultant and components) anchored at `CofR` on the geometry, with an `i/j/k` readout.
- **2D aero-load side view**: projected STL silhouette with drag/downforce arrows, pitch arc, per-element show/hide toggles and an optional center-of-pressure marker.
- **Aero balance / center of pressure**: front/rear aero load split and CoP location computed from wheelbase and static front weight percent.
- **Rapidamente branding**: team logo as the navbar mark and favicon, with Space Grotesk / Barlow Semi Condensed brand typography.

### Changed
- **Symmetry correctness**: half-model cases now project per-component (in-plane forces and the pitch moment double; side force and roll/yaw cancel) instead of a blanket ×2.
- **Representative values**: 2D/3D vectors, the breakdown table and aero balance use the trailing-window average, matching the KPI cards.
- **Cluster telemetry performance**: remote reads are batched into a single SSH command and shared through a short-lived cache.
- **Telemetry UX**: switching the monitored case clears and guards the view so stale data never appears.

### Fixed
- Boundary layers silently extruded at ~0 thickness when an absolute first-layer thickness was combined with snappy's default relative sizing; layer stack versus `minThickness` is now validated.
- Symmetry detection now treats a config without explicit `domain_faces` as a half-model (matching the generator) and falls back to the mesh boundary.

## [1.1.0] - 2026-09-17

### Added
- **Cluster Case Download**: Download finished cases from a remote SLURM cluster to the local machine, streamed as a remote `tar` archive.
- **Live Download Progress**: Per-case progress reporting with a live progress bar in the Web Studio, plus a guard against re-downloading completed cases.
- **Graceful Job Cancel**: Cancel remote jobs with an automatic write-and-reconstruct step so partial parallel results are preserved.

### Fixed
- Force coefficient parsing, cluster safety checks, and axis handling.
- Prevented progress regression during downloads and repaired Web Studio UI controls.
- 3D viewer now displays the real `geometry.stl` geometry.

## [1.0.0] - 2026-09-10

### Initial Public Release

#### Core CFD Automation Engine
- **Automated Case Generation**: Complete OpenFOAM case scaffolding (`0/`, `constant/`, `system/`) from a single JSON configuration.
- **Dynamic Wind Tunnel Derivation**: Computes virtual wind tunnel bounding boxes automatically from ASCII STL CAD bounds with upstream, downstream, roof, and lateral buffer ratios.
- **snappyHexMesh Refinement Heuristics**: Automated generation of surface refinements, explicit feature edge meshes (`surfaceFeatureExtract`), distance refinement shells, and two-stage wake refinement regions (`nearWakeBox`, `farWakeBox`).
- **Boundary Layer Inflation**: Automated prism layer extrusion settings targeting $y^+$ guidelines.
- **Mesh Fidelity Presets**: Built-in presets (`fast`, `standard`, `fine`) with predefined cell count budgets and refinement levels.
- **Symmetry Plane Support**: Half-car simulation support (e.g. `x = 0`) with automatic 2x force scaling across reports and comparisons.
- **Execution Pipeline Scripts**: Generates hardened POSIX execution scripts (`Allrun`, `Allrun.parallel`, `Allclean`, `run.sh`) with signal handling, background monitor termination, and interrupted parallel run reconstruction.
- **OpenFOAM Compatibility**: Developed and tested against ESI-OpenCFD OpenFOAM (`v2606`) only; other releases (older ESI versions, OpenFOAM Foundation) are untested and may require manual dictionary edits.

#### Real-Time Telemetry & Convergence Monitoring
- **Convergence Auto-Stop**: Live background monitor analyzing rolling window force variation ($\pm 0.5\%$) and gracefully signaling OpenFOAM solvers to stop via `stopAt writeNow;`.
- **Force Analysis CLI**: `rapidfoam-forces` tool for tabulating Drag, Downforce, and L/D ratios, live polling, and multi-case comparisons.

#### Interactive Web Studio
- **3D Viewport**: Real-time CAD and domain wireframe inspection using Three.js and OrbitControls.
- **Live Telemetry Charts**: Streaming force and residual curves powered by Chart.js.
- **Parameter & Preset Editor**: In-browser configuration manager with template presets.
- **Remote Cluster Execution**: SLURM cluster management over SSH/SFTP with job submission (`sbatch`), queue monitoring (`squeue`), and remote log inspection.

#### Packaging & Testing
- Pure zero-dependency core running on Python 3.9+ standard library.
- Comprehensive test suite covering regression checks, script synthesis, API security, and cluster telemetry.
