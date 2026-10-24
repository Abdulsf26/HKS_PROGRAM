@echo off
title HKS Add-on - LibreHardwareMonitor
echo === LibreHardwareMonitor - CPU/GPU/disk temps + fan speeds ===
set D=%ProgramData%\HKS\lhm
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'LibreHardwareMonitor.*\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'lhm.zip'"
if not exist lhm.zip (
  echo [X] download failed for LibreHardwareMonitor - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'lhm.zip' -DestinationPath '.' -Force"
for /r . %%f in (LibreHardwareMonitor.exe) do if /I not "%%~ff"=="%CD%\LibreHardwareMonitor.exe" copy /y "%%f" "%CD%\LibreHardwareMonitor.exe" >nul 2>&1
if not exist LibreHardwareMonitor.exe (
  echo [X] download failed for LibreHardwareMonitor - run again / check github releases.
  pause
  exit /b 1
)
echo TIP: Options - Remote Web Server gives http://LOCALIP:8088 (temps on phone).
start "" LibreHardwareMonitor.exe
pause
