@echo off
title HKS Add-on - Deskflow
echo === Deskflow - one keyboard+mouse shared over the network ===
set D=%ProgramData%\HKS\deskflow
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/deskflow/deskflow/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'win-x64\.(msi|exe)$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile $a.name; Write-Output ('  downloaded: ' + $a.name)"
REM round 65 (audit7 #35): deskflow ships .msi (verified live: deskflow-1.27.0-win-x64.msi) - the old regex demanded .exe and never matched.
set INST=
for %%F in (deskflow-*-win-x64.msi) do set INST=msi
for %%F in (deskflow-*-win-x64.exe) do set INST=exe
if "%INST%"=="" (
  echo [X] download failed for Deskflow - run again / check github releases.
  pause
  exit /b 1
)
echo NOTE: GUI installer. Deskflow port 24800 opened.
powershell -NoProfile -Command "if (-not (Get-NetFirewallRule -DisplayName 'HKS deskflow' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'HKS deskflow' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 24800 | Out-Null }"
if "%INST%"=="msi" for %%F in (deskflow-*-win-x64.msi) do start "" msiexec /i "%%F"
if "%INST%"=="exe" for %%F in (deskflow-*-win-x64.exe) do start "" "%%F"
pause
