@echo off
title HKS Add-on - Netbird
echo === Netbird - open-source WireGuard mesh with SSO ===
set D=%ProgramData%\HKS\netbird
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/netbirdio/netbird/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows.*\.msi$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'netbird.msi'"
if not exist netbird.msi (
  echo [X] download failed for Netbird - run again / check github releases.
  pause
  exit /b 1
)
msiexec /i netbird.msi /qn
echo installed - open Netbird from the Start menu to sign in.
pause
