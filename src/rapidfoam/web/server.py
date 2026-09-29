"""Compatibility shim for the Web Studio server.

The FastAPI application now lives in :mod:`rapidfoam.web.app`; this module
re-exports the app, the launcher and every endpoint/helper name the tests and
legacy importers use. New code should import from ``rapidfoam.web.app`` / the
``rapidfoam.web.routers`` and ``rapidfoam.web.services`` packages.
"""

from __future__ import annotations

from typing import Any

from rapidfoam.config import effective_config
from rapidfoam.web.app import app, create_app, main  # noqa: F401
from rapidfoam.web.routers.case import (  # noqa: F401
    api_case_cancel,
    api_case_check_exists,
    api_case_download,
    api_case_download_active,
    api_case_download_progress,
    api_case_generate_and_submit,
    api_case_submit,
    api_geometry_domain_box,
)
from rapidfoam.web.routers.cases import api_case_delete, api_list_cases  # noqa: F401
from rapidfoam.web.routers.cluster import (  # noqa: F401
    api_cluster_connect,
    api_cluster_disconnect,
    api_cluster_status,
    api_get_saved_config,
)
from rapidfoam.web.routers.config import (  # noqa: F401
    api_config_defaults,
    api_config_load_file,
    api_config_templates,
)
from rapidfoam.web.routers.stl import (  # noqa: F401
    api_get_stl_file,
    api_stl_check_exists,
    api_stl_list,
    api_stl_upload,
)
from rapidfoam.web.routers.telemetry import (  # noqa: F401
    api_telemetry_export,
    api_telemetry_forces,
    api_telemetry_logs,
    api_telemetry_mesh,
    api_telemetry_residuals,
    api_telemetry_solver,
    api_telemetry_surface,
)
from rapidfoam.web.schemas import (  # noqa: F401
    CaseDownloadRequest,
    DomainBoxRequest,
    GenerateCaseRequest,
    JobCancelRequest,
    JobSubmitRequest,
    SSHConnectRequest,
)
from rapidfoam.web.services import telemetry as _telemetry_service
from rapidfoam.web.services.geometry import layer_preview  # noqa: F401
from rapidfoam.web.services.telemetry import (  # noqa: F401
    _project_force_columns_for_symmetry,
    _project_moment_columns_for_symmetry,
    _read_yplus_texts,
    _summarise_yplus,
    parse_residuals_from_log,
    parse_solver_diagnostics_from_log,
    read_file_tail,
)
from rapidfoam.web.state import (  # noqa: F401
    ALLOWED_LOG_TYPES,
    CASE_NAME_REGEX,
    JOB_ID_REGEX,
    PROJECT_ROOT,
    _download_progress,
    _download_progress_lock,
    _get_download_progress,
    _list_download_progress,
    _set_download_progress,
    get_saved_cluster_config,
    save_cluster_config,
    ssh_client,
)

_remote_telemetry_cache = _telemetry_service._remote_telemetry_cache


def merge_config_with_defaults(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge user configuration and selective overrides on top of DEFAULT_CONFIG."""
    return effective_config(raw_cfg)


if __name__ == "__main__":
    main()
