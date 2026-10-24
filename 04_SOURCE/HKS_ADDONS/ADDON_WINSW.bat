@echo off
title HKS Add-on - WinSW
echo === WinSW - wrap ANY exe as a Windows service ===
set D=%ProgramData%\HKS\winsw
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/winsw/winsw/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'WinSW-x64\.exe$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'WinSW-x64.exe'"
if not exist WinSW-x64.exe (
  echo [X] download failed for WinSW - run again / check github releases.
  pause
  exit /b 1
)
echo docs: github.com/winsw/winsw - rename exe + xml, then: myapp.exe install / start
pause
