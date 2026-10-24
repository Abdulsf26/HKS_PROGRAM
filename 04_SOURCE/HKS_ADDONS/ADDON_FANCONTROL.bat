@echo off
title HKS Add-on - FanControl
echo === FanControl - custom fan curves (anti-heat) ===
set D=%ProgramData%\HKS\fancontrol
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/Rem0o/FanControl.Releases/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'FanControl.*\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'fc.zip'"
if not exist fc.zip (
  echo [X] download failed for FanControl - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'fc.zip' -DestinationPath '.' -Force"
if not exist FanControl.exe (
  echo [X] download failed for FanControl - run again / check github releases.
  pause
  exit /b 1
)
echo NOTE: needs .NET 8 Desktop runtime (offers the download if missing).
start "" FanControl.exe
pause
