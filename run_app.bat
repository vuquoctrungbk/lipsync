@echo off
REM Launch the Lip-Sync green-screen app (local, 127.0.0.1:7860).
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
    echo Virtual environment missing. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
    exit /b 1
)
.\.venv\Scripts\python.exe app.py %*
