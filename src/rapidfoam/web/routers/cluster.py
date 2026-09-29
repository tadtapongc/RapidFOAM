"""Cluster connection endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from rapidfoam.web.schemas import SSHConnectRequest
from rapidfoam.web.state import get_saved_cluster_config, save_cluster_config, ssh_client

router = APIRouter()


@router.get("/api/cluster/saved-config")
async def api_get_saved_config() -> dict[str, Any]:
    """Get cached connection settings without exposing password in plaintext."""
    cfg = get_saved_cluster_config()
    return {
        "host": cfg.get("host", ""),
        "port": cfg.get("port", 22),
        "username": cfg.get("username", ""),
        "remote_repo_path": cfg.get("remote_repo_path", ""),
        "has_saved_password": bool(cfg.get("password")),
        "key_path": cfg.get("key_path", ""),
    }


@router.post("/api/cluster/connect")
async def api_cluster_connect(req: SSHConnectRequest) -> dict[str, Any]:
    """Connect to the remote HPC cluster and test the environment."""
    saved_cfg = get_saved_cluster_config()
    password_to_use = req.password
    # If no password was provided but one is stored locally for this host/user, use it
    if not password_to_use and saved_cfg.get("password"):
        if (not req.host or req.host == saved_cfg.get("host")) and (not req.username or req.username == saved_cfg.get("username")):
            password_to_use = saved_cfg.get("password")

    try:
        res = await asyncio.to_thread(
            ssh_client.connect,
            host=req.host,
            username=req.username,
            password=password_to_use,
            key_path=req.key_path,
            port=req.port,
            remote_repo_path=req.remote_repo_path,
        )
        save_cluster_config({
            "host": req.host,
            "port": req.port,
            "username": req.username,
            "password": password_to_use if req.save_password else None,
            "key_path": req.key_path,
            "remote_repo_path": req.remote_repo_path,
            "save_password": req.save_password,
        })
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/cluster/status")
async def api_cluster_status() -> dict[str, Any]:
    """Get active SSH session and SLURM queue status."""
    connected = ssh_client.is_connected
    jobs = []
    if connected:
        try:
            jobs = await asyncio.to_thread(ssh_client.get_slurm_queue)
        except Exception:
            pass

    return {
        "connected": connected,
        "host": ssh_client.host,
        "username": ssh_client.username,
        "remote_repo_path": ssh_client.remote_repo_path,
        "active_jobs": jobs,
    }


@router.post("/api/cluster/disconnect")
async def api_cluster_disconnect() -> dict[str, Any]:
    """Disconnect SSH session."""
    await asyncio.to_thread(ssh_client.disconnect)
    return {"connected": False}
