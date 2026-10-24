@echo off
title HKS Add-on - AutoHotkey
echo === AutoHotkey - Windows automation scripting (v2) ===
set D=%ProgramData%\HKS\ahk
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/AutoHotkey/AutoHotkey/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'AutoHotkey_2.*\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'ahk.zip'"
if not exist ahk.zip (
  echo [X] download failed for AutoHotkey - run again / check github releases.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'ahk.zip' -DestinationPath '.' -Force"
for /r . %%f in (AutoHotkey64.exe) do if /I not "%%~ff"=="%CD%\AutoHotkey64.exe" copy /y "%%f" "%CD%\AutoHotkey64.exe" >nul 2>&1
if not exist AutoHotkey64.exe (
  echo [X] download failed for AutoHotkey - run again / check github releases.
  pause
  exit /b 1
)
start "" AutoHotkey64.exe
pause
