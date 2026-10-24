@echo off
title HKS Add-on - TigerVNC
echo === TigerVNC - VNC server + viewer (GPL) ===
set D=%ProgramData%\HKS\tigervnc
mkdir "%D%" 2>nul
cd /d "%D%"
REM round 66 (addon audit): TigerVNC's recent GitHub releases carry NO .exe assets - the official download home is SourceForge. Pinned 1.14.1 2026-10-04.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://downloads.sourceforge.net/project/tigervnc/stable/1.14.1/tigervnc64-1.14.1.exe' -OutFile 'tigervnc_setup.exe'"
if not exist tigervnc_setup.exe (
  echo [X] download failed for TigerVNC - check sourceforge.net/projects/tigervnc/files/stable/ and update the URL in this bat.
  pause
  exit /b 1
)
echo NOTE: GUI installer - choose 'Register VNC as system service' for boot start.
start "" tigervnc_setup.exe
pause
