"""STL upload / listing endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from rapidfoam.config import find_stl
from rapidfoam.stl_utils import stl_info
from rapidfoam.web.state import PROJECT_ROOT, ssh_client

router = APIRouter()


@router.get("/api/stl/list")
async def api_stl_list() -> list[dict[str, Any]]:
    """List local STL files with geometric bounds and metadata."""
    stl_dir = PROJECT_ROOT / "stl"
    stls = []
    seen_paths = set()
    if stl_dir.is_dir():
        # Deduplicate paths (prevent duplicates on case-insensitive filesystems like Windows)
        all_candidates = sorted(stl_dir.glob("*.stl")) + sorted(stl_dir.glob("*.STL"))
        for p in all_candidates:
            resolved = p.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                solid_name, n_facets, bounds = stl_info(p)
                (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds
                dx = xmax - xmin
                dy = ymax - ymin
                dz = zmax - zmin
                is_likely_mm = max(dx, dy, dz) > 20.0
                stls.append({
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                    "format": "ASCII STL",
                    "solid_name": solid_name,
                    "triangles": n_facets,
                    "bounds": {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax]},
                    "dimensions": [dx, dy, dz],
                    "is_likely_mm": is_likely_mm,
                })
            except Exception as exc:
                stls.append({"filename": p.name, "size_bytes": p.stat().st_size, "error": str(exc)})
    return stls


@router.get("/api/stl/file/{filename}")
async def api_get_stl_file(filename: str):
    """Serve a local STL file by name."""
    safe_filename = Path(filename).name
    stl_dir = (PROJECT_ROOT / "stl").resolve()
    p = find_stl(stl_dir, safe_filename)
    if not p or not p.is_file() or not p.resolve().is_relative_to(stl_dir):
        raise HTTPException(status_code=404, detail=f"STL file '{safe_filename}' not found")
    return FileResponse(path=p, media_type="application/octet-stream", filename=p.name)


@router.get("/api/stl/check-exists")
async def api_stl_check_exists(filename: str) -> dict[str, Any]:
    """Check if an STL file already exists locally or on remote cluster."""
    raw_name = Path(filename).name
    safe_name = raw_name if raw_name.lower().endswith(".stl") else f"{raw_name}.stl"

    stl_dir = (PROJECT_ROOT / "stl").resolve()
    local_exists = (stl_dir / safe_name).is_file()

    cluster_exists = False
    if ssh_client.is_connected:
        try:
            remote_path = f"{ssh_client.remote_repo_path}/stl/{safe_name}"
            cluster_exists = ssh_client.remote_file_exists(remote_path)
        except Exception:
            pass

    return {
        "filename": safe_name,
        "exists": local_exists or cluster_exists,
        "local_exists": local_exists,
        "cluster_exists": cluster_exists,
    }


@router.post("/api/stl/upload")
async def api_stl_upload(
    file: UploadFile = File(...),
    override_name: Optional[str] = Form(None),
) -> dict[str, Any]:
    """Upload an STL file to local stl/ directory and inspect its bounds."""
    chosen_name = (override_name.strip() if override_name else "") or file.filename or "uploaded.stl"
    safe_name = Path(chosen_name).name
    if not safe_name.lower().endswith(".stl"):
        safe_name = f"{safe_name}.stl"

    stl_dir = (PROJECT_ROOT / "stl").resolve()
    stl_dir.mkdir(exist_ok=True)

    dest = (stl_dir / safe_name).resolve()
    if not dest.is_relative_to(stl_dir):
        raise HTTPException(status_code=400, detail="Invalid destination path")

    content = await file.read()
    dest.write_bytes(content)

    try:
        solid_name, n_facets, bounds = stl_info(dest)
        (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds
        dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
        return {
            "success": True,
            "filename": safe_name,
            "size_bytes": len(content),
            "format": "ASCII STL",
            "solid_name": solid_name,
            "triangles": n_facets,
            "bounds": {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax]},
            "dimensions": [dx, dy, dz],
            "is_likely_mm": max(dx, dy, dz) > 20.0,
        }
    except Exception as exc:
        return {
            "success": True,
            "filename": safe_name,
            "size_bytes": len(content),
            "warning": f"Uploaded, but geometry inspection failed: {exc}",
        }
