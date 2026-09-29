"""Config template / schema endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from rapidfoam.config import DEFAULT_CONFIG, effective_config
from rapidfoam.meshing.presets import FIDELITY_PRESETS
from rapidfoam.web.state import PROJECT_ROOT

router = APIRouter()


@router.get("/api/config/schema-defaults")
async def api_config_defaults() -> dict[str, Any]:
    """Return default config template and presets for the UI.

    Exposes every field the generator applies from the preset (including snap,
    layer-iteration, feature-angle and slurm values) so the UI cannot drift from
    FIDELITY_PRESETS with stale hard-coded placeholders.
    """
    return {
        "default_config": DEFAULT_CONFIG,
        "fidelity_presets": {
            name: {
                "desc": p.get("desc", ""),
                "cell_estimate": p.get("cell_estimate", ""),
                "n_cells_target": p.get("n_cells_target", 0),
                "runtime_estimate": p.get("runtime_estimate", ""),
                "layers": {
                    "y_plus_target": p.get("y_plus_target"),
                    "n_layers": p.get("n_layers"),
                    "expansion_ratio": p.get("expansion_ratio"),
                    "ground_layers": p.get("ground_layers", False),
                    "nLayerIter": p.get("nLayerIter"),
                    "nRelaxIter": p.get("nRelaxIter_layers"),
                },
                "mesh": {
                    "cells_per_length": p.get("cells_per_length"),
                    "base_cell_size": p.get("base_cell_size"),
                    "surface_level": p.get("surface_level"),
                    "edge_level": p.get("edge_level"),
                    "near_wake_level": p.get("near_wake_level"),
                    "far_wake_level": p.get("far_wake_level"),
                    "feature_cells": p.get("feature_cells"),
                    "max_surface_level": p.get("max_surface_level"),
                    "feature_percentile": p.get("feature_percentile"),
                    "resolveFeatureAngle": p.get("resolveFeatureAngle"),
                    "nCellsBetweenLevels": p.get("nCellsBetweenLevels"),
                },
                "snap": {
                    "nSolveIter": p.get("nSolveIter"),
                    "nFeatureSnapIter": p.get("nFeatureSnapIter"),
                },
                "solver": {
                    "end_time": p.get("end_time"),
                    "write_interval": p.get("write_interval"),
                },
                "slurm": {
                    "time": p.get("slurm_time"),
                    "mem_per_cpu": p.get("slurm_mem_per_cpu"),
                },
            }
            for name, p in FIDELITY_PRESETS.items()
        },
    }


@router.get("/api/config/templates")
async def api_config_templates() -> list[dict[str, Any]]:
    """List available config files in configs/ folder."""
    cfg_dir = PROJECT_ROOT / "configs"
    templates = []
    if cfg_dir.is_dir():
        for p in sorted(cfg_dir.glob("*.json")):
            try:
                content = json.loads(p.read_text(encoding="utf-8"))
                templates.append({
                    "filename": p.name,
                    "case_name": content.get("case_name", p.stem),
                    "stl_files": content.get("stl_files", []),
                    "fidelity": content.get("fidelity", "standard"),
                })
            except Exception:
                templates.append({"filename": p.name, "case_name": p.stem, "stl_files": []})
    return templates


@router.get("/api/config/load-file")
async def api_config_load_file(filename: str = "config.json") -> dict[str, Any]:
    """Load and return JSON content of a specific config file."""
    safe_filename = Path(filename).name
    cfg_dir = (PROJECT_ROOT / "configs").resolve()
    path = (cfg_dir / safe_filename).resolve()
    if not path.is_relative_to(cfg_dir) or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Config file {safe_filename} not found")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        return {
            "filename": safe_filename,
            "raw_config": content,
            "merged_config": effective_config(content),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error reading config: {exc}")
