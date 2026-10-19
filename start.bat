@echo off
setlocal
title Ollama Vision Studio

echo ========================================================
echo   Ollama Vision Studio
echo   http://localhost:8000
echo ========================================================
echo.

cd /d "%~dp0"

set "VENV_PY=%~dp0backend\venv\Scripts\python.exe"

REM --- First run: bootstrap the environment ---
if not exist "%VENV_PY%" (
    echo [*] First run detected - running setup...
    echo.
    call "%~dp0setup.bat"
    if not exist "%VENV_PY%" (
        echo [X] Setup did not complete. Cannot start.
        pause
        exit /b 1
    )
)

REM --- Warn if the UI has not been built ---
if not exist "%~dp0frontend\dist\index.html" (
    echo [*] Frontend not built - building now...
    pushd "%~dp0frontend"
    call npm install
    call npm run build
    popd
)

REM --- Free the port if something else is holding it ---
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [!] Port 8000 in use by PID %%p - stopping it.
    taskkill /PID %%p /F >nul 2>nul
)

echo [*] Starting server on http://localhost:8000 ...
start "Ollama Studio Server" cmd /k ""%VENV_PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"

echo [*] Waiting for the server to come up...
timeout /t 4 /nobreak >nul

start "" http://localhost:8000

echo.
echo ========================================================
echo   Running. Keep the server window open.
echo   Close it (or press Ctrl+C) to stop the app.
echo ========================================================