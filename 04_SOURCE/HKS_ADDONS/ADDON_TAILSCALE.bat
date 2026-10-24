@echo off
title HKS Add-on - Tailscale
echo === Tailscale - WireGuard mesh VPN ===
echo source: github.com/tailscale/tailscale (official pkgs.tailscale.com stable MSI)
set D=%ProgramData%\HKS\tailscale
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi' -OutFile 'tailscale.msi'"
if not exist tailscale.msi (
  echo [X] download failed for Tailscale - run again / check pkgs.tailscale.com.
  pause
  exit /b 1
)
echo installing silently - log in from the tray icon afterwards.
msiexec /i tailscale.msi /qn /norestart
if errorlevel 1 (echo [X] MSI install failed - run the msi by hand from %D% & pause & exit /b 1)
echo done - service starts at boot.
pause
