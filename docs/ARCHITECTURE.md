# RapidFOAM Architecture

A short map of the package. The dependency direction is enforced by
`tests/test_architecture.py` (an `ast` import scan): a forbidden cross-context
import fails the suite.

## Bounded contexts

```
cli / web  ──> casegen ──> meshing ──> geometry ──> core
   │              │           │
   │              └──> runtime │
   └──────────────────> postproc ────────> core
```

| Context | Owns | May depend on | Must not import |
| --- | --- | --- | --- |
| `core/` | axes, faces, fields, FoamFile format, config defaults/loader/validation, override-aware case-config reading | stdlib | any other `rapidfoam` subpackage |
| `geometry/` | ASCII STL I/O + edge/angle statistics | core | meshing, casegen, postproc, web, cli |
| `meshing/` | fidelity presets, domain/sizing/grading/layers, `MeshPlan`, mesh writers, emission pipeline | core, geometry | casegen, postproc, web, cli |
| `casegen/` | case builder + constants/fields/solver/scripts writers | core, geometry, meshing, runtime | postproc, web, cli |
| `runtime/` | stand-alone scripts copied into cases / uploaded to clusters | stdlib | casegen, meshing, postproc, web |
| `postproc/` | parse OpenFOAM logs/artifacts and judge them | core | meshing, casegen, web |
| `web/` | FastAPI app, routers, services, SSH client, static UI | all of the above | cli |
| `cli.py` | command-line entry points | all of the above | — |

## Key seams

- **Effective config** — `core.caseconfig.read_case_config()` resolves
  `raw → defaults → overrides` and is the single reader for telemetry, mesh
  quality, surface checks and the forces CLI. `config.effective_config()` is the
  one merge.
- **Mesh plan** — `meshing.plan.build_mesh_plan()` derives on a private copy and
  returns an immutable `MeshPlan`; `meshing.presets.apply_fidelity_preset()` is
  the only place preset fields are resolved. Writers consume `MeshPlan` +
  `MeshContext`, never `cfg`; `meshing.pipeline.emit_mesh_files()` is the single
  emission entry point.
- **Case generation** — `casegen.builder.build_case()` builds a case from a
  config; `cli._do_generate` and the web API both call it.
- **Runtime assets** — `runtime/convergence_monitor.py` and
  `runtime/remote_cases.py` are real, testable modules copied to the case/cluster
  instead of being embedded strings.

## Entry points

| Command | Target |
| --- | --- |
| `rapidfoam-setup` / `setup_case.py` | `rapidfoam.cli:setup_main` |
| `rapidfoam-forces` / `read_forces.py` | `rapidfoam.cli:forces_main` |
| `rapidfoam-studio` / `rapidfoam-web` | `rapidfoam.web.app:main` |
| `rapidfoam-monitor` | `rapidfoam.postproc.convergence_monitor:main` |

## Web layer

`web/app.py` builds the FastAPI app from `web/routers/*` (cluster, config, stl,
case, cases, telemetry); shared logic lives in `web/services/*` (telemetry
parsing/summarisation, geometry preview, downloads) and `web/state.py`
(SSH singleton, credentials, download progress). Request models live in
`web/schemas.py`.

## Tests

- `python -m unittest discover -s tests` — Python (unit, regression, golden,
  architecture).
- `npm test` — JSDOM front-end tests.
- `ruff check src tests` — lint (pyflakes baseline).

The historical refactor planning notes are under `docs/refactor/`.
