@echo off
title HKS Admin Panel - Setup
REM Run once on the machine that will host the panel (your PC or an AWS VM with Python).
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python 3.10+ required: https://python.org & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
echo.
echo Setup done. Now run run_server.bat  (first browser visit opens /setup to create the admin user).
pause
