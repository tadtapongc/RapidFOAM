# RapidFOAM

OpenFOAM® external aerodynamics automation suite and Web Studio developed for **Rapidamente Formula Student** (Chulalongkorn University).

<img width="1292" height="586" alt="RapidFOAM" src="https://github.com/user-attachments/assets/db91bb4b-5261-4cea-a793-a40dfc756ddc" />

https://github.com/user-attachments/assets/caced105-394c-4c8f-b6f1-86d14df9ae85

> **Trademark Notice**  
> OPENFOAM® is a registered trade mark of OpenCFD Limited, producer and distributor of the OpenFOAM software via [www.openfoam.com](https://www.openfoam.com).  
> This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the OpenFOAM software via [www.openfoam.com](https://www.openfoam.com), and owner of the OPENFOAM® and OpenCFD® trade marks.

RapidFOAM streamlines the OpenFOAM workflow for external vehicle aerodynamics: CAD STL ingestion, domain bounding box calculation, `snappyHexMesh` refinement dictionary generation (surfaces, feature edges, distance shells, two-stage wake boxes, and boundary layers), `simpleFoam` steady-state case setup (SIMPLEC, k-omega SST), execution scripts, and real-time force convergence monitoring (Drag, Downforce, L/D).

---

## Features

- **Case Directory Generation**: Generates complete OpenFOAM case structures (`0/`, `constant/`, `system/`) and execution scripts from a single JSON configuration.
- **Domain & Mesh Parameter Derivation**: Derives wind tunnel dimensions from STL bounding boxes, creates two-stage wake refinement regions (`nearWakeBox`, `farWakeBox`), distance shells, and boundary layer controls.
- **Mesh Fidelity Presets**: Predefined configuration presets (`fast`, `standard`, `fine`) targeting different cell count budgets and turnaround times.
- **Symmetry Plane Support**: Half-car simulations (e.g. `x = 0`) cut mesh cell count roughly in half, with automatic 2x force scaling in summaries and comparison tables.
- **Web Studio Interface**: Browser-based UI with Three.js 3D domain visualization, interactive parameter editor, real-time convergence charts, and remote SLURM cluster job submission over SSH.
- **Full Aerodynamic Telemetry**: Force/moment component breakdown, coefficients (`Cd`, `Cl`, `Cs`, `CmPitch`, `CmRoll`, `CmYaw`), 2D/3D aero-load views, aero balance / center of pressure, and a post-run reference editor for recomputing coefficients without re-running the solver.
- **Surface Integrity Report**: Runs `surfaceCheck` on every STL before meshing and reports closure/open edges, self-intersections, illegal triangles and part count in the Studio and via `read_forces.py --surface`. Report-only by default (`surface_check.enforce` aborts the run on a defect); symmetry half models are exempt from the closure requirement.
- **Mesh-Quality Verification**: Parses the `checkMesh` log and snappyHexMesh's per-patch layer table to report non-orthogonality, skewness, aspect ratio, concave cells, boundary closure and boundary-layer coverage (both layer count and realised thickness), then rolls them into a tiered Good/Usable/Marginal/Bad verdict. A full-but-thin layer stack (all layers present but below 70% of the requested thickness) is flagged as a warning. Cross-references the realised `yPlus` output against the layer sizing target, which remains the authoritative near-wall check.
- **Geometry-Adaptive Meshing (opt-in)**: Streaming STL analysis can drive feature-based surface/edge auto-sizing (`mesh_params.auto_size`) and a geometry-derived `resolveFeatureAngle` (`mesh_params.auto_feature_angle`). Both are **off by default** so the generated mesh is the plain fidelity preset and stays predictable; enable them for geometries with small features or subtle creases.
- **Graded Background Mesh (opt-in)**: `mesh_params.grading` derives a `blockMesh` `simpleGrading` toward the ground/symmetry planes, keeping the near-body cell at the base cell size while coarsening *away* from the body (flow/wake axis stays uniform). Off by default — measured on a real case it trims ~10–30% of the final cells but raises non-orthogonality/aspect ratio, so it is opted into with `"auto"` or an explicit `[gx, gy, gz]`.
- **Remote Case Management**: Submit, monitor and gracefully cancel SLURM jobs; download finished cases from the cluster with live progress (streamed and published atomically, so an interrupted transfer never leaves a partial case).
- **Convergence Auto-Stop**: Background monitor tracks rolling force variation and signals `stopAt writeNow;` once drag and downforce stabilize within a user-defined threshold (default +/- 0.5%).
- **Post-Processing CLI**: Tabulates aerodynamic forces (Drag, Downforce, L/D), plots live convergence curves, compares multiple case iterations side-by-side, verifies near-wall y+ against the sizing target, and reports mesh quality.

---

## OpenFOAM Compatibility & Environment

### Supported OpenFOAM Versions

RapidFOAM has only been tested against **ESI-OpenCFD OpenFOAM v2606**. The generated dictionaries follow OpenCFD syntax conventions (such as `libs (forces);` function objects), and other releases — including older ESI versions and OpenFOAM Foundation builds — are untested and may require manual dictionary edits.

### Environment Configuration
The path to your OpenFOAM installation is configured in `configs/config.json` under `"slurm"`:
- `openfoam_source`: Path to your OpenFOAM environment script (e.g. `"$HOME/OpenFOAM/OpenFOAM-v2606/etc/bashrc"` or `"/opt/openfoam2606/etc/bashrc"`).
- `openfoam_module`: List of environment modules to load on HPC clusters (e.g. `["GCC/11.3.0", "OpenMPI/4.1.4-GCC-11.3.0"]` or `["OpenFOAM/v2206-foss-2022a"]`), or `null` if sourcing directly.

---

## Installation

### Prerequisites

- **Python**: 3.9 or higher
- **OpenFOAM**: Installed locally or on the remote cluster (see supported versions above).

### Setup

Clone the repository and install RapidFOAM in editable mode:

```bash
git clone https://github.com/tadtapongc/RapidFOAM.git
cd RapidFOAM

# Core CLI tools only:
pip install -e .

# With Web Studio and plotting dependencies:
pip install -e ".[web,plot]"
```

Installed console scripts:
- `rapidfoam-setup` (or `rapidfoam`): Case generator CLI
- `rapidfoam-forces`: Force analysis and post-processing CLI
- `rapidfoam-monitor`: Standalone convergence auto-stop monitor
- `rapidfoam-studio` (or `rapidfoam-web`): Interactive Web Studio server

---

## Usage

RapidFOAM can be run through the interactive Web Studio or via the command line.

### Web Studio

The Web Studio provides a 3D viewport to inspect domain sizing, edit flow conditions, generate cases, and manage remote HPC jobs.

- **Windows**: Run `run_app.bat` (or double-click it in Windows Explorer).
- **Linux / macOS**: Run `./run_app.sh` in terminal.
- **Direct Command**:
  ```bash
  rapidfoam-studio
  # or:
  python -m rapidfoam.web.app
  ```

Open `http://127.0.0.1:8000` in your browser.

1. **Upload Geometry**: Drop ASCII STL files into the 3D viewer. Multiple components (e.g. chassis, front wing, rear wing) can be viewed together.
2. **Configure Parameters**: Adjust velocity, fidelity preset, ground clearance, and symmetry planes. The yellow wireframe domain updates dynamically in the 3D viewport.
3. **Run Locally or on HPC**:
   - Click **Generate Case** to build the case directory under `cases/<case_name>/`.
   - Use the **Cluster SSH** dialog to connect to a remote SLURM cluster, transfer the case files, and submit the batch job.
4. **Monitor Telemetry**: Watch force histories (Drag, Downforce) and residuals update as the case solves.

---

### Command-Line Interface (CLI)

For headless servers, batch sweeps, or automated pipelines:

#### 1. Initialize Workspace (Optional)
To create a starter directory structure with a sample configuration:
```bash
python setup_case.py --init
```
This creates `stl/`, `cases/`, and `configs/config.json`.

#### 2. Place CAD Geometry
Export your geometry as an **ASCII STL** in **meters** and place it in the `stl/` folder:
```bash
stl/my_wing.stl
```

> **Geometry Note**: `snappyHexMesh` requires clean, watertight surface geometry without open holes or self-intersecting triangles. Check and repair CAD exports before meshing.

#### 3. Configure Case
Edit `configs/config.json` or create a case-specific JSON config:

```json
{
  "case_name": "front_wing_v1",
  "stl_files": ["my_wing.stl"],
  "fidelity": "standard",
  "flow": {
    "velocity": 20.0,
    "direction": "-z",
    "ground": true
  },
  "outputs": {
    "drag_axis": "-z",
    "downforce_axis": "-y"
  },
  "domain_box": "auto",
  "symmetry_plane": 0.0,
  "parallel": {
    "n_procs": 16
  }
}
```

#### 4. Preview Settings (Dry Run)
Inspect domain extents, estimated base cell sizes, and boundary assignments without writing files:
```bash
python setup_case.py configs/config.json --dry-run
# or:
rapidfoam-setup configs/config.json -n
```

#### 5. Generate Case
Generate the complete OpenFOAM case directory:
```bash
python setup_case.py configs/config.json
```
This generates `cases/<case_name>/` containing `0/`, `constant/`, `system/`, and execution scripts:
- `Allrun.parallel`: MPI parallel execution script
- `Allrun`: Single-core execution script
- `Allclean`: Resets the case directory and restores initial condition fields
- `run.sh`: SLURM batch submission script (`sbatch run.sh`)
- `convergence_monitor.py`: Embedded auto-stop monitor script

#### 6. Run the Simulation
Navigate to the case directory and execute the run script:

```bash
cd cases/front_wing_v1

# Run in parallel using MPI:
./Allrun.parallel

# Or submit to a SLURM cluster:
sbatch run.sh
```

`Allrun` and `Allrun.parallel` (and `run.sh`) first run a `surfaceCheck` integrity check on every STL — report-only by default, aborting on a defect when `surface_check.enforce` is set (see `surface_check` below). They then execute the standard OpenFOAM external aerodynamics pipeline:
1. `surfaceFeatureExtract` (extracts feature edges to `.eMesh`)
2. `blockMesh` (creates background hexahedral mesh)
3. `decomposePar` (splits domain across MPI ranks)
4. `snappyHexMesh -overwrite` (surface snapping, refinement regions, boundary layers in parallel)
5. `checkMesh` (verifies mesh orthogonality and quality metrics)
6. `reconstructParMesh` & `renumberMesh` (assembles and renumbers mesh)
7. `decomposePar` (redistributes mesh for solver ranks)
8. `potentialFoam` (initializes divergence-free flow field)
9. `convergence_monitor.py` (background process tracking force convergence)
10. `simpleFoam` (incompressible SIMPLEC solver with k-omega SST)
11. `reconstructPar` (collates parallel results back to root time directories)

#### 7. Post-Process Aerodynamic Forces
Extract forces, verify convergence, or generate plots:

```bash
# Print force summary table (Drag, Downforce, L/D):
python read_forces.py

# Specify a particular case directory:
python read_forces.py cases/front_wing_v1

# Live convergence plot during solve (requires matplotlib):
python read_forces.py --live

# Save convergence plot to PNG (saves force_convergence.png):
python read_forces.py --save

# Compare all cases in cases/ directory:
python read_forces.py --compare

# Check convergence status (exit code 0 if converged, 1 if not):
python read_forces.py --check

# Verify near-wall y+ against the layer sizing target (exit 0 if met, 2 if missed):
python read_forces.py --yplus

# Verify mesh quality from the checkMesh + snappyHexMesh logs (exit 0 if ok, 2 if concerns):
python read_forces.py --mesh

# Verify surface integrity from the surfaceCheck log (exit 0 if ok, 2 if concerns):
python read_forces.py --surface
```

The force summary also prints a one-line near-wall y+ note (patch averages vs. the
configured `layers.y_plus_target`) whenever the `yPlus` function object has produced
output, so a mesh that misses its near-wall target is visible without re-running.

---

## Configuration Reference

Key settings available in `configs/config.json`:

| Parameter | Type | Description | Default |
| :--- | :--- | :--- | :--- |
| `case_name` | `string` | Target folder name under `cases/` | Required |
| `stl_files` | `list` | List of STL filenames in `stl/` | Required |
| `fidelity` | `string` | Mesh preset: `"fast"`, `"standard"`, or `"fine"` | `"standard"` |
| `flow.velocity` | `float` | Freestream velocity in m/s | `16.67` (~60 km/h) |
| `flow.direction` | `string` | Flow direction vector (`"-z"`, `"+x"`, `"-x"`, etc.) | `"-z"` |
| `flow.ground` | `bool` | Enable moving ground wall at freestream speed | `true` |
| `outputs.drag_axis` | `string` | Axis along which drag force is reported | `"-z"` |
| `outputs.downforce_axis` | `string` | Axis along which downforce (-lift) is reported | `"-y"` |
| `domain_box` | `string` / `dict` | `"auto"` or explicit `{"min": [x,y,z], "max": [x,y,z]}` | `"auto"` |
| `symmetry_plane` | `float` / `null` | Coordinate for symmetry split (e.g. `0.0`), or omit for full 3D | `null` |
| `ground_clearance` | `float` | Relative road gap in meters below lowest STL point | Lowest vertex |
| `ground_plane` | `float` | Fixed CAD elevation coordinate of ground (takes precedence over clearance) | `null` |
| `parallel.n_procs` | `int` | Number of CPU cores for MPI decomposition | `10` |
| `surface_check.enabled` | `bool` | Run `surfaceCheck` before meshing and report it | `true` |
| `surface_check.enforce` | `bool` | Abort the run on a surface defect (report-only when false) | `false` |
| `surface_check.check_self_intersection` | `bool` | Also check self-intersection (slower on large meshes) | `true` |
| `surface_check.allow_open` | `bool` | Permit an open surface without a symmetry plane | `false` |
| `mesh_params.grading` | `string` / `list` | Background grading: `"off"` (default), `"auto"`, or `[gx, gy, gz]` | `"off"` |
| `mesh_params.grading_ratio` | `float` | Far/near cell-size ratio for auto grading (1–20) | `3.0` |

### Mesh Fidelity Presets

| Preset | Base Cell* | Surface Levels | Edge Level | Boundary Layers | y+ Target | Max Iterations | Target Cells | Estimated Runtime** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `fast` | 0.15 m | [3, 4] | 5 | 5 | ~50 | 800 | ~3–5 M | ~10–20 min |
| `standard` | 0.10 m | [4, 5] | 6 | 8 | ~30 | 1500 | ~9–13 M | ~1–2 hrs |
| `fine` | 0.08 m | [4, 5] | 7 | 20 | ~1 | 2500 | ~20–28 M | ~4–6 hrs |

*\* The base cell is derived at generation time as the longest STL extent divided by the preset's `cells_per_length` (20 / 30 / 37.5); the values shown are for a ~3 m model. Surface and edge levels are raised further only when `mesh_params.auto_size` is enabled (off by default).*

*\*\* Rough guidance only — not benchmarked. Actual cell counts and solve times depend on geometry complexity, core count, and convergence rate. All presets use Spalding-bridging wall functions; `fine` targets y+ ~ 1 but keeps those wall functions, so it is a low-y+ mesh rather than a classical low-Re formulation.*

---

## Near-Wall y+ & Boundary Layers

Boundary-layer sizing is driven by a near-wall `y+` target rather than a raw
thickness. From the freestream velocity, fluid properties and model length,
RapidFOAM estimates a flat-plate friction velocity (`u_tau`) and converts the
target into an absolute first-cell height (`delta_1 = 2 * y+ * nu / u_tau`),
written with `relativeSizes false`. The stack is clamped to
`maxFaceThicknessRatio` of the finest surface cell so snappyHexMesh can actually
extrude it.

**Pick a regime and hit it.** For k-ω SST the first-cell y+ should be either
~1 (wall-resolved) or ~30–100 (wall functions); the buffer layer (5–30) is
neither and should be avoided. The presets follow that rule:

| Preset | Regime | y+ target | Layers | Growth |
| :--- | :--- | :--- | :--- | :--- |
| `fast` | wall function | ~50 | 5 | 1.2 |
| `standard` | wall function | ~30 | 8 | 1.2 |
| `fine` | wall-resolved | ~1 | 20 | 1.1 |

A high y+ needs a *thick* first cell, which snappyHexMesh will not build next to
a fine surface cell (`maxFaceThicknessRatio`), so wall-function runs want a
coarser near-wall surface than wall-resolved ones. When the clamp bites, the CLI
reports the realised y+ and warns instead of silently degrading the mesh.

**Wall treatment.** All presets use the Spalding-bridging wall functions
(`nutUSpaldingWallFunction`, `omegaWallFunction`, `kqRWallFunction`), valid
across the whole y+ range. Spalding bridges the entire range, so `fast`/`standard`
sit cleanly in the log layer and `fine` places the first cell in the viscous
sublayer; `fine` keeps those wall functions rather than a separate low-Re
formulation, so treat it as a low-y+ target, not a classical wall-resolved setup.

**Verify, don't assume.** The `u_tau` estimate is a flat-plate correlation and
is typically 30-40% off the local value on a real car. It also uses the *model
length*, so short elements (front wing, gurney flaps, endplate edges) see a
higher local y+ than the estimate predicts. Always check the realised values
after solving:

```bash
python read_forces.py --yplus
```

This reads the `yPlus` function object output and reports per-patch min/max/average
against the target, flagging patches that miss it.

---

## Mesh Quality & Remediation

Meshing is a **fixed preset + measurement** workflow, not a predictive one: the
generator writes a plain `blockMesh` / `snappyHexMesh` case, and the diagnostics
(`read_forces.py --mesh`, `read_forces.py --surface`, and the Studio panels)
report what actually happened. The optional auto-heuristics are **off by default**
so the mesh stays predictable:

| Setting | Default | Effect when enabled |
| :--- | :--- | :--- |
| `mesh_params.auto_size` | `false` | Raise surface/edge refinement so the smallest STL feature is resolved (capped by `max_surface_level`) |
| `mesh_params.auto_feature_angle` | `false` | Derive `resolveFeatureAngle` from the STL crease (normal-angle) distribution |
| `mesh_params.grading` | `"off"` | Grade the background grid toward the ground/symmetry planes (fewer cells, slightly higher non-orthogonality/aspect ratio) |
| `layers.two_pass` | `false` | Two-pass layering: a second `snappyHexMesh` pass adds layers with the quality gate relaxed (`system/snappyHexMeshDict_layering`) — higher boundary-layer coverage at some quality cost; taper the limits back with `checkMesh` |

When `checkMesh` flags a metric, change **one** thing and re-mesh:

| Finding | Likely cause | Adjustment |
| :--- | :--- | :--- |
| High max non-orthogonality | large cell-size jump between background and refinement | raise `mesh_params.nCellsBetweenLevels` (e.g. 3), lower `surface_level`/`edge_level`, disable `grading` |
| High max skewness | layers too thick next to the surface, or aggressive snapping | lower `layers.maxFaceThicknessRatio`, raise `snap.nSolveIter` / `snap.nRelaxIter` |
| High max aspect ratio | thick first layer vs. the finest cell, or coarse cells beside fine ones | reduce `layers.first_layer_thickness`, disable `grading`, reduce the `surface_level` jump |
| Boundary-layer dropout (low coverage) | first layer thicker than snappy can extrude | reduce `layers.first_layer_thickness` or raise `surface_level`, raise `layers.maxFaceThicknessRatio` |
| Concave cells / illegal faces | open or self-intersecting geometry | repair the STL (the `surfaceCheck` gate reports it) |
| Small cell determinant | sharp/degenerate cells surviving smoothing | raise `mesh_quality.errorReduction` / `mesh_quality.nSmoothScale`, repair geometry |

**"Never bad" rather than "always optimal."** Fixed settings cannot be optimal for
every geometry without CFD validation; the goal is a predictable mesh with no
obvious defects (the `surfaceCheck` gate) and no obvious quality failures (the
`checkMesh` verdict), then iterate on a single knob when needed.

## Technical Notes & Conventions

### STL Format & Units
- **Format**: Geometry files must be in **ASCII STL** format. Binary STLs should be converted in CAD before running (e.g. SolidWorks: *Save As → STL → Options → Output: ASCII*).
- **Units**: OpenFOAM assumes geometry coordinates are in **meters**. If CAD is exported in millimeters (mm), scale geometry by `0.001` before running:
  ```bash
  surfaceTransformPoints -scale '(0.001 0.001 0.001)' input.stl output.stl
  ```

### Coordinate System & Axis Flexibility
RapidFOAM supports arbitrary coordinate systems by configuring flow and force directions to match your CAD orientation:

- **Default Formula Student Convention**:
  - Longitudinal flow: along `-Z` (`"flow.direction": "-z"`, `"outputs.drag_axis": "-z"`)
  - Vertical / height: `+Y` (`"outputs.downforce_axis": "-y"`)
  - Lateral / spanwise: `X` (symmetry plane at `x = 0`)
- **Alternative Orientations**: If your CAD model is oriented differently (for example, flow along `+X` and height along `+Z` in aerospace conventions), update `flow.direction`, `drag_axis`, and `downforce_axis` in `configs/config.json`:
  ```json
  "flow": {
    "direction": "+x"
  },
  "outputs": {
    "drag_axis": "+x",
    "downforce_axis": "-z"
  }
  ```
  The generator automatically maps inlet, outlet, ground, and lateral boundaries, and aligns upstream/downstream domain padding with the active flow axis.

### Symmetry Planes
For straight-line running conditions (zero yaw), a half-car model with a symmetry plane at `x = 0` cuts cell count by roughly 50%. When a symmetry boundary is present, RapidFOAM reports both the simulated half-model values and the projected full-car values (multiplied by 2) in summaries and comparison tables.

### Convergence Auto-Stop
The background monitor (`convergence_monitor.py`) inspects force outputs every 10 seconds. Once drag and downforce variation remains within +/- 0.5% over a 200-iteration rolling window (after at least 300 iterations), the monitor writes `stopAt writeNow;` to `system/controlDict` to terminate the solve gracefully and write final results.

---

## Repository Structure

```text
RapidFOAM/
├── configs/            # Case configuration JSON files
├── stl/                # CAD geometry files (ASCII STL in meters)
├── cases/              # Generated OpenFOAM case directories
├── src/rapidfoam/      # Core RapidFOAM package
│   ├── cli.py          # Command-line entry points (setup, forces)
│   ├── config.py       # Config loading, defaults, and validation
│   ├── core/           # Leaf primitives: axes, faces, fields, FoamFile format,
│   │                   #   config loader, override-aware case-config reader
│   ├── geometry/       # Streaming ASCII STL I/O, edges/angle statistics (stl.py)
│   ├── meshing/        # Presets, domain sizing, feature sizing, grading, layers,
│   │                   #   MeshPlan, mesh writers, emission pipeline
│   ├── casegen/        # Case assembly: builder + constants/fields/solver/scripts
│   ├── runtime/        # Standalone scripts copied into cases / uploaded to clusters
│   ├── postproc/       # Force/residual/y+/checkMesh/surfaceCheck parsers & plots
│   └── web/            # Web Studio: app.py, state, schemas, routers/, services/,
│                       #   ssh_client.py, static/ (Three.js viewport, telemetry)
├── tests/              # Python unit & regression tests
│   └── js/             # JSDOM front-end tests (npm test)
├── docs/               # Architecture notes + historical bug-hunt reports
├── package.json        # Front-end test tooling (jsdom)
├── CHANGELOG.md        # Release history
├── run_app.bat         # 1-click launcher for Windows
├── run_app.sh          # 1-click launcher for Linux / macOS
├── setup_case.py       # Case generator CLI script
└── read_forces.py      # Force analysis and plotting CLI script
```

---

## Testing

### Python (core, API, post-processing)

Run the backend suite with Python's standard `unittest`:

```bash
python -m unittest discover -s tests -v
```

### Front-end (Web Studio)

The browser logic is covered by a JSDOM + `node:test` suite. Node.js 18+ is required:

```bash
npm install
npm test
```

Both suites are independent: the Python tests exercise the generator, API and
post-processing, while the JS tests cover the Web Studio telemetry, viewer and
rendering logic.

---

## Authors

- **Tadtapong C.** ([@tadtapongc](https://github.com/tadtapongc)) — Lead Developer & Maintainer
- **Rapidamente Formula Student** (Chulalongkorn University) — Aerodynamics Division

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

### Trademark Notice
OPENFOAM® is a registered trade mark of OpenCFD Limited, producer and distributor of the OpenFOAM software via [www.openfoam.com](https://www.openfoam.com).

This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the OpenFOAM software via [www.openfoam.com](https://www.openfoam.com), and owner of the OPENFOAM® and OpenCFD® trade marks.
