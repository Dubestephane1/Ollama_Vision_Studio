@echo off
setlocal
title Ollama Vision Studio - Setup

echo ========================================================
echo   Ollama Vision Studio - First-time Setup
echo ========================================================
echo.

cd /d "%~dp0"

REM --- Locate a base Python interpreter ---
set "BASE_PY="
where py >nul 2>nul && set "BASE_PY=py -3.11"
if not defined BASE_PY ( where python >nul 2>nul && set "BASE_PY=python" )

if not defined BASE_PY (
    echo [X] Could not find Python 3.11+
    echo     Install from https://www.python.org/downloads/
    echo     Make sure you tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)

set "VENV_PY=%~dp0backend\venv\Scripts\python.exe"

REM --- Create virtualenv if missing ---
if exist "%VENV_PY%" (
    echo [*] Virtual environment already exists, skipping creation.
) else (
    echo [*] Creating virtual environment in backend\venv ...
    %BASE_PY% -m venv "%~dp0backend\venv"
    if errorlevel 1 (
        echo [X] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

REM --- Install / update dependencies ---
echo [*] Installing dependencies from requirements.txt ...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [X] Failed to install dependencies.
    pause
    exit /b 1
)

REM --- Frontend deps ---
if exist "%~dp0frontend\package.json" (
    echo [*] Installing frontend packages ...
    pushd "%~dp0frontend"
    call npm install
    popd
)

echo.
echo ========================================================
echo   Setup complete!
echo   Run start.bat to launch the app.
echo ========================================================
pause