"""Pydantic request models for the Web Studio API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class SSHConnectRequest(BaseModel):
    host: str = ""
    port: int = 22
    username: str = ""
    password: Optional[str] = None
    key_path: Optional[str] = None
    remote_repo_path: str = ""
    save_password: bool = True


class JobSubmitRequest(BaseModel):
    case_name: str


class JobCancelRequest(BaseModel):
    job_id: str


class CaseDownloadRequest(BaseModel):
    case_name: str
    overwrite: bool = False


class DomainBoxRequest(BaseModel):
    config: dict[str, Any]
    bounds: Optional[dict[str, list[float]]] = None


class GenerateCaseRequest(BaseModel):
    config: dict[str, Any]
    upload_to_cluster: bool = False
    generate_remotely: bool = False
    submit_slurm: bool = False
    # Default to a pure validation request so a caller that forgets to set an
    # explicit action can never be surprised by filesystem mutation.
    generate_locally: bool = False


__all__ = [
    "SSHConnectRequest",
    "JobSubmitRequest",
    "JobCancelRequest",
    "CaseDownloadRequest",
    "DomainBoxRequest",
    "GenerateCaseRequest",
]
