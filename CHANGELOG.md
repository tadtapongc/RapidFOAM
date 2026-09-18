# Changelog

All notable changes to RapidFOAM will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
