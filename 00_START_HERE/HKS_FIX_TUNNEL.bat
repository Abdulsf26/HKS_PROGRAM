@echo off
title HKS - fix / start the ANYWHERE tunnel
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting Administrator rights - click YES on the popup...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
for %%i in ("%~dp0.") do set "BASE=%%~fi"
echo  ==========================================================
echo   This downloads the tunnel tool (one time, ~40 MB, needs
echo   internet) and starts your phone-from-ANYWHERE address.
echo   Total time: about 1-2 minutes. Keep this window open -
echo   it waits for the address and shows it right here.
echo  ==========================================================
set HPY=python
if exist "%BASE%\venv\Scripts\python.exe" set HPY=%BASE%\venv\Scripts\python.exe
"%HPY%" "%~dp0first_run.py" --fix-tunnel
if errorlevel 2 if exist "%~dp0HKS_DOCTOR.bat" call "%~dp0HKS_DOCTOR.bat"

echo   If NO address appeared: many office/school networks (FortiGuard,
echo   FortiGate, Zscaler, Cisco Umbrella...) BLOCK outbound tunnels on
echo   purpose. That is the NETWORK policy, not an HKS failure. Use the
echo   LAN address on your own Wi-Fi, or test the tunnel from a phone
echo   hotspot. From mobile data the tunnel address still works.
echo.
pause
