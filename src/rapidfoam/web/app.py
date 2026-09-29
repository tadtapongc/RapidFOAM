"""FastAPI application assembly for the Web Studio."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from rapidfoam import __version__
from rapidfoam.web.routers import case as _case_router
from rapidfoam.web.routers import cases as _cases_router
from rapidfoam.web.routers import cluster as _cluster_router
from rapidfoam.web.routers import config as _config_router
from rapidfoam.web.routers import stl as _stl_router
from rapidfoam.web.routers import telemetry as _telemetry_router
from rapidfoam.web.state import get_saved_cluster_config

_ROUTERS = (
    _cluster_router,
    _config_router,
    _stl_router,
    _case_router,
    _cases_router,
    _telemetry_router,
)


def create_app() -> FastAPI:
    """Assemble the FastAPI app: CORS, routers and the static front end."""
    application = FastAPI(title="RapidFOAM Studio", version=__version__)

    # Restrict CORS to local origins only to protect credentials and SSH operations
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in _ROUTERS:
        application.include_router(module.router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        application.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return application


app = create_app()


def main() -> None:
    """CLI launcher for the web server."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Launch RapidFOAM Studio (Rapidamente Formula Student).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--restart", action="store_true", help="Restart server if an instance is already running")
    args = parser.parse_args()

    import socket
    import urllib.request

    def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0

    def get_pid_on_port(port: int) -> Optional[int]:
        try:
            if sys.platform == "win32":
                import subprocess
                out = subprocess.check_output("netstat -ano -p tcp", shell=True, text=True, errors="replace")
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                        return int(parts[4])
            else:
                import subprocess
                out = subprocess.check_output(["lsof", "-t", f"-i:{port}"], text=True, errors="replace")
                pids = [int(p) for p in out.strip().splitlines() if p.strip().isdigit()]
                return pids[0] if pids else None
        except Exception:
            pass
        return None

    def kill_process_tree(pid: int) -> bool:
        try:
            if sys.platform == "win32":
                import subprocess
                subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True)
                return True
            else:
                os.kill(pid, 9)
                return True
        except Exception:
            return False

    target_port = args.port
    if is_port_in_use(target_port, args.host):
        is_cfd_studio = False
        try:
            req = urllib.request.urlopen(f"http://{args.host}:{target_port}/api/config/schema-defaults", timeout=1)
            if req.status == 200:
                is_cfd_studio = True
        except Exception:
            pass

        if is_cfd_studio and not args.restart:
            # An existing studio is already serving this port: reuse it.
            url = f"http://{args.host}:{target_port}"
            print(f"[*] RapidFOAM Studio is already running on {url}. Use --restart to force a restart.")
            if not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return

        if is_cfd_studio and args.restart:
            old_pid = get_pid_on_port(target_port)
            print(f"[*] Found existing RapidFOAM Studio running on port {target_port} (PID {old_pid or 'unknown'}).")
            print("[*] Restarting server to ensure latest code is active...")
            if old_pid and old_pid != os.getpid():
                kill_process_tree(old_pid)
                time.sleep(1.0)

        # If port is still busy (e.g. by another application), find next available port
        while is_port_in_use(target_port, args.host):
            target_port += 1
        if target_port != args.port:
            print(f"[*] Port {args.port} was busy. Switched to next available port: {target_port}")
        args.port = target_port

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print("\n" + "=" * 60)
    cfg = get_saved_cluster_config()
    target = cfg.get("host") or "Not configured (set in Web UI)"
    print("  RapidFOAM Studio Web Server | Rapidamente Formula Student")
    print(f"  Cluster target: {target}")
    print(f"  Listening on:   {url}")
    print("=" * 60 + "\n")

    if not args.no_browser:
        def _open_browser() -> None:
            time.sleep(0.8)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port)


__all__ = ["app", "create_app", "main"]


if __name__ == "__main__":
    main()
