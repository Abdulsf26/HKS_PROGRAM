@echo off
title HKS Add-on - Jellyfin
echo === Jellyfin - free media server ===
set D=%ProgramData%\HKS\jellyfin
mkdir "%D%" 2>nul
cd /d "%D%"
REM round 66 (addon audit): Jellyfin Windows installers are NOT on GitHub - they live on repo.jellyfin.org. Pinned 10.11.11 2026-10-04; newer: repo.jellyfin.org/?path=/server/windows/latest-stable
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://repo.jellyfin.org/files/server/windows/stable/v10.11.11/amd64/jellyfin_10.11.11_windows-x64.exe' -OutFile 'jellyfin_setup.exe'"
if not exist jellyfin_setup.exe (
  echo [X] download failed for Jellyfin - check repo.jellyfin.org/?path=/server/windows/latest-stable and update the URL in this bat.
  pause
  exit /b 1
)
echo NOTE: installer - choose 'Install as service' (runs at boot). Port 8096.
powershell -NoProfile -Command "if (-not (Get-NetFirewallRule -DisplayName 'HKS jellyfin' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'HKS jellyfin' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8096 | Out-Null }"
start "" jellyfin_setup.exe
pause
