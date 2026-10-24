@echo off
title HKS - Roll back to the snapshot taken before the last install
rem round 68 (F06): one-click undo of a bad upgrade. Stops tasks, restores
rem the newest HKS_PROGRAM.bak.*, restarts tasks.
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
setlocal enabledelayedexpansion
echo.
echo  ==============================================
echo    HKS - ROLL BACK to pre-upgrade snapshot
echo  ==============================================
echo.
set "NEWEST="
for /f "delims=" %%d in ('dir /b /ad /o-d "C:\HKS_PROGRAM.bak.*" 2^>nul') do (
  if not defined NEWEST set "NEWEST=%%d"
)
if not defined NEWEST (
  echo  No snapshots found - nothing to roll back to.
  echo  (Snapshots are created by INSTALL_HKS.bat before each upgrade.)
  echo.
  pause
  exit /b 1
)
echo  Newest snapshot: %NEWEST%
set /p GO=Stop HKS, restore it, and restart HKS? (y/N): 
if /i not "%GO%"=="y" (echo cancelled & pause & exit /b)
for %%T in ("HKS-Panel" "HKS-Agent" "HKS-SessionAgent") do schtasks /End /TN %%T >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'server\\.main|agent\\.(main|session_main)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 3 /nobreak >nul
robocopy "C:\%NEWEST%" "C:\HKS_PROGRAM" /E /PURGE /XD venv data data_panel __pycache__ /NFL /NDL /NJH /NJS >nul
echo  Restored. Starting tasks...
schtasks /Run /TN "HKS-Panel" >nul 2>&1
schtasks /Run /TN "HKS-Agent" >nul 2>&1
schtasks /Run /TN "HKS-SessionAgent" >nul 2>&1
echo.
echo  Rollback complete. Open the panel and check CHECK_STATUS.bat.
echo  (The snapshot folder is kept - delete C:\%NEWEST% when happy.)
echo.
pause
exit /b 0
