# Design — Automatic Per-Surface Sizing (geometry-derived) + optional manual overrides

**Status:** proposal (no code changes yet)
**Goal:** make each STL surface's refinement depend on **how big its features are**
(like an ANSYS size function), not on a semantic label or a hand-tuned per-part
number. A large smooth surface gets coarse cells; a small intricate part gets fine
cells — automatically. A small optional manual override remains for the rare case
where you want to force a value.

---

## 1. Motivation

RapidFOAM already derives one global `surface_level` / `edge_level` for **all**
STLs. In most meshers (ANSYS, STAR-CCM+, Gmsh size fields) the local cell size is
driven by the **local feature size** — surface area / edge length / curvature —
not by "this is the front wing". The same principle applies here: a big flat floor
and a tiny bracket sharing one refinement level means either the floor is
over-refined (cell blow-up) or the bracket is under-resolved.

The good news: the machinery already exists. `build_case` streams every STL once
with `stl_analyze_full`, producing an `EdgeStats` (edge-length histogram) and a
`FeatureAngleStats` (crease histogram). Today those are **merged into one** and fed
to `compute_mesh_params` to pick a single global level. The change is to keep them
**per STL** and derive a level per surface.

---

## 2. The two mechanisms

### 2.1 Primary: geometry-derived per-surface refinement (the ANSYS-like part)

For each STL part, derive its refinement from its own feature size:

```
small_feature  = min( edge_percentile(stats_part, feature_percentile),
                      thinnest_extent_part )
target_cell    = small_feature / feature_cells
level_part     = ceil(log2(base_cell / target_cell))          # 0 if <= 0
level_part     = clamp(level_part, global_surface_level[0], max_surface_level)
```

- Reuses `meshing/sizing.py::_resolve_feature_sizing` **per part** instead of once
  on merged stats.
- Only ever **refines** relative to the preset (never coarsens), same contract as
  today's `auto_size`.
- Guarded against slivers by the existing `model_length * 1e-4` floor.
- Opt-in via the existing `mesh_params.auto_size` (default off), so nothing changes
  unless enabled. When enabled, the per-surface levels replace the single global
  surface/edge level.
- The **edge level** (`features` `.eMesh` refinement) follows the same per-part
  level, so sharp edges on a small part are refined consistently.

This is the whole point: **no naming, no per-part config** — size follows geometry.

### 2.2 Secondary: optional manual per-surface override

For the cases automation can't express — "this internal part gets zero layers",
"force the diffuser finer than its features imply" — keep a small optional block:

```json
{
  "stl_files": ["floor.stl", "rear_wing.stl", "bracket.stl"],
  "mesh_regions": {
    "floor":   { "surface_level": [4, 6] },
    "bracket": { "n_layers": 0 }
  }
}
```

- Keys: STL **stem** (extension stripped), matching `cfg["stl_names"]`.
- v1 allowed keys (kept deliberately small): `surface_level`, `edge_level`,
  `n_layers`.
- Precedence: `global preset < global user override < auto per-surface < manual
  region override`. Manual wins because it is explicit intent.
- Absent → no effect (behaviour identical to today / to the auto result).

---

## 3. Config

No new required keys. Two optional, additive controls:

- `mesh_params.auto_size` (already exists) → **enables per-surface geometry sizing**
  (previously it also applied, but effectively as a single global number; now it is
  genuinely per surface).
- `mesh_regions` (new, optional) → manual per-stem overrides (§2.2).

Nothing else changes. Existing configs and the golden snapshot are unaffected
(`auto_size` off by default; `mesh_regions` absent by default).

---

## 4. Geometry quality: warn, don't gate

Self-intersections / illegal triangles reduce layer coverage and produce a
Marginal/Bad verdict, but a dirty STL is sometimes the only geometry available and
repair can cost more than the run. So **`surfaceCheck` stays report-only by
default** (`surface_check.enforce` is opt-in, unchanged). When defects are found,
the message should be *actionable*, not blocking:

> "31 illegal triangles + 2724 self-intersections detected — expect reduced
> layer coverage and a Marginal verdict. Consider lowering
> `layers.min_thickness_ratio`, enabling `layers.two_pass`, or repairing the CAD
> if possible."

Never change meshing behaviour silently because the STL is dirty; just state the
consequence. The two levers that recover the most on an unfixable multi-part car
are already available: a low `min_thickness_ratio` (keep partial stacks) and
two-pass with the relaxed gate; `n_layers: 0` on a part that cannot take layers
stops wasting effort on it.

---

## 5. Where it enters the pipeline

### 4.1 Builder (`casegen/builder.py`)

Today:
```python
edge_stats = EdgeStats()
for stem, path in stl_pairs:
    ... = stl_analyze_full(path)
    edge_stats.merge(stats)          # one merged histogram
```
Change to keep a per-part map:
```python
stats_by_stem: dict[str, EdgeStats] = {}
for stem, path in stl_pairs:
    ... = stl_analyze_full(path)
    stats_by_stem[stem] = stats
    edge_stats.merge(stats)          # still merged for the global box/wake math
```
Both are kept: the merged stats still drive domain-independent global decisions;
the per-part stats drive per-surface levels.

### 4.2 Derivation (`meshing/params.py`) — add a per-surface sizing pass

`compute_mesh_params` (or a thin wrapper) gains an optional
`feature_stats_by_name` map and, when `auto_size`, computes a
`surface_levels: {stem: (l0, l1)}` and `edge_levels: {stem: level}` dict, in
addition to the global `surface_level`/`edge_level` (which stay for the
background/distance/wake math and as the fallback). `MeshPlan.mesh_params` carries
these maps. **The y+/layer derivation is untouched** (§5).

### 4.3 `MeshContext` (new field)

Resolve per-surface overrides into a frozen map:

```python
@dataclass(frozen=True)
class MeshRegion:
    surface_level: tuple[int, int] | None
    edge_level: int | None
    n_layers: int | None

@dataclass(frozen=True)
class MeshContext:
    ...
    regions: Mapping[str, MeshRegion]   # stem -> effective per-surface values
```

`build_mesh_context` composes: auto per-surface level (from the plan) → manual
`mesh_regions` override → global fallback. Unknown stems in `mesh_regions` are
dropped with a warning.

### 4.4 Writers (only the snappy writer changes behaviour)

- `refinementSurfaces { <name> { level (l0 l1); } }` → per-surface level.
- `features { { file "<name>.eMesh"; level L; } }` → per-surface edge level.
- `addLayersControls.layers { "<name>" { nSurfaceLayers N; } }` → per-surface
  layers; `0` = no layers for that part.
- blockMesh, domain, grading, wake boxes, distance shells, quality blocks stay
  **global**.

---

## 6. Boundary layers stay global (v1)

The y+ chain (`resolve_layers`) derives one first-layer thickness and stack from
the global `y_plus_target`, and snappy's `addLayersControls` first layer is a
single value per run. Only the **layer count per patch** is regionalised in v1.
Per-surface *thickness* is out of scope (it would fragment the y+ provenance and
is rarely what you want anyway — you want the same near-wall resolution
everywhere, just fewer/more layers on parts that can't/needn't support them).

---

## 7. Validation (`config.validate`)

Only when `mesh_regions` is present:
- must be an object; keys must be known STL stems; values are objects with only
  the allowed keys;
- `surface_level` two ordered non-negative ints; `edge_level`, `n_layers`
  non-negative ints;
- warnings: unknown stem; `n_layers: 0` on every part; a region level far above
  the preset (cell-budget hint).

`auto_size` already validated. Add a note that auto per-surface sizing is active
only when `auto_size` is true.

---

## 8. Studio

- **Preview**: `layer_preview` (via `/api/geometry/domain-box`) gains a per-surface
  summary `{stem: {surface_level, edge_level, n_layers, source: "auto"|"manual"|"preset"}}`
  so the Boundary Layer panel can render a small table: *this part → level X,
  Y layers (auto/manual)*. This makes the ANSYS-like behaviour visible ("why is the
  bracket finer?" → "because its features are small").
- **Form**: v1 stays **config/JSON-only** (`mesh_regions` in the JSON drawer), like
  `mesh_quality.layering_relaxed`. A graphical per-part editor is deferred.

---

## 9. Tests

- `test_auto_sizing.py` (extend): per-surface levels — a small STL gets a higher
  level than a large one when merged they would share one; auto_size off →
  identical to preset; `max_surface_level` cap per part.
- `test_mesh_regions.py` (new): manual override precedence over auto and preset;
  `n_layers: 0`; unknown stem dropped; malformed rejected.
- `test_casegen_golden.py`: unchanged (auto_size off, no regions → identical).
- Add one golden/regression case with `auto_size: true` + two STLs of different
  feature size asserting the emitted per-surface levels differ.

---

## 10. Compatibility & risk

- **Backward-compatible**: `auto_size` off (default) and no `mesh_regions` →
  output identical to today; golden passes untouched.
- **Bounded blast radius**: builder keeps a map; a sizing pass adds per-surface
  dicts; `MeshContext` + snappy writer consume them. Derivation/y+/layers
  untouched.
- **Risk**: auto per-surface levels can raise the cell count on feature-heavy
  parts — bounded by `max_surface_level`, and the preview + `--mesh` report it.
- **Scope guard**: v1 = geometry-derived `surface_level`/`edge_level` per part +
  manual override of exactly `surface_level`/`edge_level`/`n_layers`. Nothing else.

---

## 11. What we cannot reach with snappyHexMesh (honesty note)

ANSYS's full size function is a **continuous field** driven by curvature and
proximity (small cells in a corner, large elsewhere on the *same* surface).
snappyHexMesh is a **castellated** mesher: refinement is integer **levels per
surface or per region box**. So the achievable ceiling is:

- **per-surface** level from feature size (this design), plus
- global/shell/wake `refinementRegions` boxes.

We cannot express "fine here, coarse there on one surface" without boxes. That is
a mesher limitation, not a RapidFOAM one; documenting it prevents false promises.

---

## 12. Rollout

1. Keep per-STL `EdgeStats` in `build_case` (no behaviour change).
2. Per-surface sizing pass in `meshing/params.py` behind `auto_size`; carry maps on
   the plan.
3. `MeshContext.regions` + `build_mesh_context` composition (+ manual `mesh_regions`).
4. snappy writer emits per-surface level/edge/layers.
5. `config.validate` for `mesh_regions`; `layer_preview` per-surface summary; Studio
   readout; docs.
6. Golden/regression coverage; verify on `test_multiComponent` (auto-size gives the
   small parts finer levels automatically; manual sets `assem5` layers to 0).

Each step independently shippable, suite green after each.

---

## 13. Decision summary

| Question | Answer |
| --- | --- |
| Primary mechanism | **auto per-surface level from each STL's feature size** (`auto_size`) |
| Basis | geometry (edge-length percentile + thinnest extent), **not** part names |
| Manual escape hatch | optional `mesh_regions: { <stem>: {surface_level, edge_level, n_layers} }` |
| Precedence | preset < global override < auto per-surface < manual region |
| Geometry quality | `surfaceCheck` stays a warning (opt-in enforce); never gate |
| Layers | global thickness; **per-surface count** only (v1) |
| Where | builder keeps per-STL stats; sizing pass; `MeshContext`; snappy writer |
| Defaults | `auto_size` off, no `mesh_regions` → identical to today |
| Ceiling | per-surface (not per-face) refinement — snappy limitation, documented |
