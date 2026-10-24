@echo off
title HKS Add-on - frp
echo === frp - self-hosted reverse proxy (expose HKS via YOUR VPS) ===
set D=%ProgramData%\HKS\frp
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/fatedier/frp/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows_amd64\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'frp.zip'"
if not exist frp.zip (
  echo [X] download failed for frp - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'frp.zip' -DestinationPath '.' -Force"
for /r . %%f in (frpc.exe) do if /I not "%%~ff"=="%CD%\frpc.exe" copy /y "%%f" "%CD%\frpc.exe" >nul 2>&1
if not exist frpc.exe (
  echo [X] download failed for frp - run again / check github releases.
  pause
  exit /b 1
)
if not exist frpc.toml (
  echo # edit serverAddr + token for YOUR VPS (docs: github.com/fatedier/frp) > frpc.toml
  echo serverAddr = "YOUR.VPS.IP" >> frpc.toml
  echo serverPort = 7000 >> frpc.toml
  echo auth.token = "change-me" >> frpc.toml
)
echo starting frpc with frpc.toml - EDIT IT FIRST for your server!
frpc.exe -c frpc.toml
pause
