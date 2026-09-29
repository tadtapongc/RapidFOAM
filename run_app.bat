@echo off
setlocal enabledelayedexpansion

title RapidFOAM Studio - Rapidamente Formula Student

echo ======================================================================
echo    RapidFOAM Studio - Rapidamente Formula Student
echo ======================================================================
echo.

:: Ensure working directory is the script's directory
cd /d "%~dp0"

:: Set PYTHONPATH so src/ is always discovered cleanly
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

:: Check for Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.9+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Virtual environment directory
set "VENV_DIR=%~dp0.venv"

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [*] Creating virtual environment in .venv...
    python -m venv "%VENV_DIR%"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate virtualenv
call "%VENV_DIR%\Scripts\activate.bat"

:: Check and install dependencies if missing
python -c "import fastapi, uvicorn, paramiko" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [*] Checking and installing web dependencies...
    pip install -e ".[web]"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo.
echo [*] Starting RapidFOAM Studio Web Server...
echo [*] Browser will open automatically at http://127.0.0.1:8000
echo.

python -m rapidfoam.web.app %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Server exited with error code %ERRORLEVEL%.
    pause
)

