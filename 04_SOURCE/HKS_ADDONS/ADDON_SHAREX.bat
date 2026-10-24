@echo off
title HKS Add-on - ShareX
REM round 71 (addon audit): portable follows the version in the real asset name.
echo === ShareX - screenshots / screen record ===
set D=%ProgramData%\HKS\sharex
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/ShareX/ShareX/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match '.*portable.*\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'sx.zip'"
if not exist sx.zip (
  echo [X] download failed for ShareX - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'sx.zip' -DestinationPath '.' -Force"
if not exist ShareX.exe (
  echo [X] download failed for ShareX - run again / check github releases.
  pause
  exit /b 1
)
start "" ShareX.exe
pause
