@echo off
title HKS Add-on - OBS Studio
REM round 66 (addon audit): asset regex fixed - real asset is OBS-Studio-<ver>-Windows-x64-Installer.exe.
echo === OBS Studio - record/stream the screen ===
set D=%ProgramData%\HKS\obs
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/obsproject/obs-studio/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'Windows-x64-Installer\.exe$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'obs_setup.exe'"
if not exist obs_setup.exe (
  echo [X] download failed for OBS - run again / check github releases.
  pause
  exit /b 1
)
start "" obs_setup.exe
pause
