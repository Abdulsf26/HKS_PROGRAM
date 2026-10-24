@echo off
title HKS Add-on - Syncthing
echo === Syncthing - continuous file sync ===
set D=%ProgramData%\HKS\syncthing
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/syncthing/syncthing/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows-amd64.*\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'st.zip'"
if not exist st.zip (
  echo [X] download failed for Syncthing - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'st.zip' -DestinationPath '.' -Force"
for /r . %%f in (syncthing.exe) do if /I not "%%~ff"=="%CD%\syncthing.exe" copy /y "%%f" "%CD%\syncthing.exe" >nul 2>&1
if not exist syncthing.exe (
  echo [X] download failed for Syncthing - run again / check github releases.
  pause
  exit /b 1
)
echo web UI: http://LOCALIP:8384
powershell -NoProfile -Command "if (-not (Get-NetFirewallRule -DisplayName 'HKS syncthing' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'HKS syncthing' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8384 | Out-Null }"
syncthing.exe serve --gui-address=0.0.0.0:8384
