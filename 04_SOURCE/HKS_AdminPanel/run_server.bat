@echo off
title HKS Admin Panel
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python 3.10+ required: https://python.org & pause & exit /b 1)
if not exist .venv (
  echo First run: creating environment + installing dependencies...
  python -m venv .venv || (echo venv failed & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
if not exist certs\server.pem (
  echo Generating TLS certificate (one time)...
  python generate_ssl.py
)
:run
if exist certs\server.pem (
  echo Starting panel with TLS: https://YOUR-IP:8000
  python -m server.main --host 0.0.0.0 --port 8000 --ssl-cert certs\server.pem --ssl-key certs\key.pem
) else (
  echo WARNING: no TLS - running plain HTTP. Fine for home network testing.
  python -m server.main --host 0.0.0.0 --port 8000
)
echo Panel exited (code %errorlevel%) - restarting in 5 s. Close this window to stop HKS.
timeout /t 5 /nobreak >nul
goto run
