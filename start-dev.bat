@echo off
setlocal
title Ollama Vision Studio (Dev Mode)

echo ========================================================
echo   Ollama Vision Studio - Development Mode
echo   Backend  : http://localhost:8000
echo   Frontend : http://localhost:5173 (hot reload)
echo ========================================================
echo.

cd /d "%~dp0"

set "VENV_PY=%~dp0backend\venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [*] Virtualenv missing - running setup first...
    call "%~dp0setup.bat"
    if not exist "%VENV_PY%" (
        echo [X] Setup did not complete. Cannot start.
        pause
        exit /b 1
    )
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [!] Port 8000 in use by PID %%p - stopping it.
    taskkill /PID %%p /F >nul 2>nul
)

echo [*] Starting backend with auto-reload ...
start "Ollama Studio Backend" cmd /k ""%VENV_PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"

timeout /t 4 /nobreak >nul

echo [*] Starting Vite dev server ...
pushd "%~dp0frontend"
start "Ollama Studio Frontend" cmd /k "npm run dev"
popd

timeout /t 4 /nobreak >nul

start "" http://localhost:5173

echo.
echo Dev server running. Edit files under frontend/src for live reload.