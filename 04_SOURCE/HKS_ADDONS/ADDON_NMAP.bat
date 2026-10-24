@echo off
title HKS Add-on - Nmap
echo === Nmap - network + port scanner ===
set D=%ProgramData%\HKS\nmap
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://nmap.org/dist/nmap-7.991-setup.exe' -OutFile 'nmap_setup.exe'"
REM round 65 (audit7 #33): nmap ships NO Windows assets on GitHub releases -
REM the official installer lives at nmap.org/dist. Pinned 7.991 2026-10-04;
REM if it ages, browse https://nmap.org/dist/ and bump the version.
if not exist nmap_setup.exe (
  echo [X] download failed for Nmap - check https://nmap.org/dist/ and update the URL in this bat.
  pause
  exit /b 1
)
echo NOTE: GUI installer - keep 'Install Npcap' ticked for full scan support.
start "" nmap_setup.exe
pause
