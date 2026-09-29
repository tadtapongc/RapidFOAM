#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

export PYTHONPATH="$DIR/src:${PYTHONPATH:-}"

echo "======================================================================"
echo "   RapidFOAM Studio - Rapidamente Formula Student"
echo "======================================================================"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 could not be found. Please install Python 3.9+."
    exit 1
fi

VENV_DIR="$DIR/.venv"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "[*] Creating virtual environment in .venv..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if ! python3 -c "import fastapi, uvicorn, paramiko" &> /dev/null; then
    echo "[*] Installing required web dependencies..."
    pip install -e ".[web]"
fi

echo ""
echo "[*] Starting RapidFOAM Studio Web Server..."
echo "[*] Browser will open automatically at http://127.0.0.1:8000"
echo ""

python3 -m rapidfoam.web.app "$@"

