"""Shared mutable web-layer state.

The SSH client singleton, credential file handling, project root, name
validators and the in-memory download-progress registry. Kept in one place so
the routers/services can share them without importing the server module.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from rapidfoam.web.ssh_client import ClusterSSHClient

log = logging.getLogger("rapidfoam.web")

ssh_client = ClusterSSHClient()
CREDENTIALS_FILE = Path.home() / ".rapidfoam_cluster.json"
# Backwards compatibility: migrate from old file if exists
_OLD_CREDENTIALS_FILE = Path.home() / ".cfd_gen_cluster.json"
if not CREDENTIALS_FILE.exists() and _OLD_CREDENTIALS_FILE.exists():
    try:
        shutil.copy2(_OLD_CREDENTIALS_FILE, CREDENTIALS_FILE)
    except Exception:
        pass
PROJECT_ROOT = Path.cwd()

CASE_NAME_REGEX = re.compile(r"^[A-Za-z0-9_-]+$")
JOB_ID_REGEX = re.compile(r"^[0-9]+$")
ALLOWED_LOG_TYPES = {
    "simpleFoam",
    "convergenceMonitor",
    "snappyHexMesh",
    "surfaceFeatureExtract",
    "blockMesh",
    "checkMesh",
    "renumberMesh",
    "potentialFoam",
}

# In-memory progress for case downloads (case_name -> snapshot)
_download_progress: dict[str, dict[str, Any]] = {}
_download_progress_lock = threading.Lock()


def _set_download_progress(case_name: str, **fields: Any) -> None:
    with _download_progress_lock:
        _download_progress.setdefault(case_name, {}).update(fields)


def _get_download_progress(case_name: str) -> dict[str, Any]:
    with _download_progress_lock:
        return dict(_download_progress.get(case_name, {"active": False}))


def _list_download_progress(active_only: bool = False) -> list[dict[str, Any]]:
    with _download_progress_lock:
        items: list[dict[str, Any]] = []
        for name, state in _download_progress.items():
            entry = {"case_name": name, **state}
            if active_only and not entry.get("active"):
                continue
            items.append(entry)
        return items


def get_saved_cluster_config() -> dict[str, Any]:
    """Load cached cluster credentials if available."""
    if CREDENTIALS_FILE.exists():
        try:
            return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "host": "",
        "port": 22,
        "username": "",
        "remote_repo_path": "",
        "save_password": True,
    }


def _restrict_file_access(path: Path) -> None:
    """Best-effort restriction of a credential file to the current user."""
    try:
        path.chmod(0o600)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import getpass
            import subprocess

            user = getpass.getuser()
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass


def save_cluster_config(cfg: dict[str, Any]) -> None:
    """Save cluster credentials safely on user machine."""
    try:
        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        to_save = dict(cfg)
        if not to_save.get("save_password"):
            to_save.pop("password", None)
        CREDENTIALS_FILE.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
        _restrict_file_access(CREDENTIALS_FILE)
    except Exception as exc:
        log.warning("Could not persist cluster credentials: %s", exc)


__all__ = [
    "ssh_client",
    "CREDENTIALS_FILE",
    "PROJECT_ROOT",
    "CASE_NAME_REGEX",
    "JOB_ID_REGEX",
    "ALLOWED_LOG_TYPES",
    "_download_progress",
    "_download_progress_lock",
    "_set_download_progress",
    "_get_download_progress",
    "_list_download_progress",
    "get_saved_cluster_config",
    "save_cluster_config",
]
