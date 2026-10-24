@echo off
title HKS Add-on - osquery
echo === osquery - query your OS with SQL ===
set D=%ProgramData%\HKS\osquery
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/osquery/osquery/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows_x86_64\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'osq.zip'"
if not exist osq.zip (
  echo [X] download failed for osquery - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'osq.zip' -DestinationPath '.' -Force"
for /r . %%f in (osqueryi.exe) do if /I not "%%~ff"=="%CD%\osqueryi.exe" copy /y "%%f" "%CD%\osqueryi.exe" >nul 2>&1
if not exist osqueryi.exe (
  echo [X] download failed for osquery - run again / check github releases.
  pause
  exit /b 1
)
echo try: SELECT * FROM system_info;   (.exit quits)
osqueryi.exe
