@echo off
title HKS Add-on - RustDesk
echo === RustDesk - open-source remote desktop ===
set D=%ProgramData%\HKS\rustdesk
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'x86_64\.exe$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'rustdesk_setup.exe'"
if not exist rustdesk_setup.exe (
  echo [X] download failed for RustDesk - run again / check github releases.
  pause
  exit /b 1
)
echo NOTE: GUI installer opens - install, then set a permanent password in RustDesk settings.
echo Autoboot note: SYSTEM autostart runs it hidden; use RustDesk's own Start-with-Windows.
start "" rustdesk_setup.exe
pause
