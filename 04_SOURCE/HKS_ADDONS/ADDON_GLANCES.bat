@echo off
title HKS Add-on - Glances
echo === Glances - all-in-one system monitor (web) ===
set D=%ProgramData%\HKS\glances
mkdir "%D%" 2>nul
cd /d "%D%"
echo source: github.com/nicolargo/glances (installed with pip)
py -m pip install --upgrade "glances[web]"
if errorlevel 1 (
  echo [X] pip install failed - check internet / python launcher (py).
  pause
  exit /b 1
)
echo web UI: port 61208
powershell -NoProfile -Command "if (-not (Get-NetFirewallRule -DisplayName 'HKS glances' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'HKS glances' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 61208 | Out-Null }"
py -m glances -w
