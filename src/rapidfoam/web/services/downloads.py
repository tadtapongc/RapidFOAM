"""Case-download background worker."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rapidfoam.web.state import _set_download_progress, ssh_client

log = logging.getLogger("rapidfoam.web")


def _run_download(case_name: str, remote_case: str, local_dir: Path) -> None:
    """Background worker: mirror a remote case into the local cases/ folder."""

    def _progress(snapshot: dict[str, Any]) -> None:
        _set_download_progress(
            case_name,
            active=True,
            done=False,
            error=None,
            files=snapshot.get("files", 0),
            dirs=snapshot.get("dirs", 0),
            bytes=snapshot.get("bytes", 0),
            total_bytes=snapshot.get("total_bytes", 0),
        )

    _progress({"files": 0, "dirs": 0, "bytes": 0, "total_bytes": 0})
    try:
        stats = ssh_client.download_directory(remote_case, local_dir, _progress)
    except Exception as exc:
        _set_download_progress(case_name, active=False, done=True, error=str(exc))
        log.warning("Case download failed for %s: %s", case_name, exc)
        return
    _set_download_progress(
        case_name,
        active=False,
        done=True,
        error=None,
        files=stats.get("files", 0),
        dirs=stats.get("dirs", 0),
        bytes=stats.get("bytes", 0),
        total_bytes=stats.get("total_bytes", 0),
    )


__all__ = ["_run_download"]
