@echo off
title HKS Add-on - Uptime Kuma
echo === Uptime Kuma - uptime/status monitoring ===
set D=%ProgramData%\HKS\uptimekuma
mkdir "%D%" 2>nul
cd /d "%D%"
where node >nul 2>nul
if errorlevel 1 (
  echo [X] Node.js 18+ required. Run ADDON_PREREQS.bat first (installs Node).
  pause
  exit /b 1
)
if not exist uptime-kuma git clone --depth 1 https://github.com/louislam/uptime-kuma.git
cd uptime-kuma
echo installing dependencies (one time)...
call npm install --omit=dev
if errorlevel 1 (
  echo [X] npm install failed.
  pause
  exit /b 1
)
powershell -NoProfile -Command "if (-not (Get-NetFirewallRule -DisplayName 'HKS kuma' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'HKS kuma' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3001 | Out-Null }"
echo web UI: http://LOCALIP:3001
node server/server.js
