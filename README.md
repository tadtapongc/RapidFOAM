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
- **Convergence Auto-Stop**: Background monitor tracks rolling force variation and signals `stopAt writeNow;` once drag and downforce stabilize within a user-defined threshold (default +/- 0.5%).
- **Post-Processing CLI**: Tabulates aerodynamic forces (Drag, Downforce, L/D), plots live convergence curves, and compares multiple case iterations side-by-side.

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
  python -m rapidfoam.web.server
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

The script executes the standard OpenFOAM external aerodynamics pipeline:
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
```

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

### Mesh Fidelity Presets

| Preset | Base Cell | Surface Levels | Edge Level | Boundary Layers | Max Iterations | Target Cells | Estimated Runtime* |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `fast` | 0.15 m | [3, 4] | 5 | 3 | 800 | ~2–4 M | ~5–10 min |
| `standard` | 0.10 m | [4, 5] | 6 | 5 | 1500 | ~6–9 M | ~30–60 min |
| `fine` | 0.08 m | [5, 6] | 7 | 6 | 3000 | ~12–16 M | ~2–4 hrs |

*\* Rough guidance only — not benchmarked. Actual cell counts and solve times depend on geometry complexity, core count, and convergence rate.*

---

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
│   ├── config.py       # Config loading, defaults, and input validation
│   ├── geometry.py     # Domain sizing, fidelity presets, mesh parameters
│   ├── stl_utils.py    # Streaming ASCII STL inspection and validation
│   ├── cli.py          # Command-line entry points (setup, forces)
│   ├── writers/        # OpenFOAM dictionary and execution script generators
│   │   ├── base.py     # FoamFile headers and formatting helpers
│   │   ├── constants.py# transportProperties, turbulenceProperties
│   │   ├── fields.py   # 0/ initial & boundary fields (U, p, k, omega, nut)
│   │   ├── mesh.py     # blockMeshDict, snappyHexMeshDict, surfaceFeatureExtractDict
│   │   ├── solver.py   # fvSchemes, fvSolution, controlDict, decomposeParDict
│   │   └── scripts.py  # Allrun, Allrun.parallel, Allclean, run.sh, convergence_monitor.py
│   ├── postproc/       # Aerodynamic force analysis & plotting
│   │   ├── forces.py   # force.dat parser, symmetry scaling, convergence checks
│   │   ├── plotting.py # Matplotlib static & live convergence plots
│   │   ├── compare.py  # Multi-case comparison table
│   │   ├── residuals.py# Residual parser
│   │   └── convergence_monitor.py # Standalone convergence auto-stop monitor
│   └── web/            # RapidFOAM Web Studio
│       ├── server.py   # FastAPI backend & static file server
│       ├── ssh_client.py # Paramiko SSH/SFTP client for remote SLURM clusters
│       └── static/     # Web Studio UI (Three.js 3D viewport, telemetry graphs)
├── tests/              # Automated unit & regression tests
├── run_app.bat         # 1-click launcher for Windows
├── run_app.sh          # 1-click launcher for Linux / macOS
├── setup_case.py       # Case generator CLI script
└── read_forces.py      # Force analysis and plotting CLI script
```

---

## Testing

Run the automated test suite with Python's standard `unittest`:

```bash
python -m unittest discover -s tests -v
```

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
