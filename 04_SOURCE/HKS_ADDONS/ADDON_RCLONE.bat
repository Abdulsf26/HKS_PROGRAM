@echo off
setlocal EnableExtensions
title HKS Add-on - rclone
echo === rclone - authenticated remote-control web UI (port 5572) ===
set "D=%ProgramData%\HKS\rclone"
set "SECRETS=%ProgramData%\HKS\addon_credentials"
if not exist "%D%" mkdir "%D%"
if not exist "%SECRETS%" mkdir "%SECRETS%"
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if (-not (Test-Path 'rclone.exe')) { $r=Invoke-RestMethod 'https://api.github.com/repos/rclone/rclone/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows-amd64\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no matching Windows amd64 release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'rc.zip'; Expand-Archive -Path 'rc.zip' -DestinationPath '.' -Force; Get-ChildItem -Recurse -Filter rclone.exe | Select-Object -First 1 | ForEach-Object { Copy-Item $_.FullName 'rclone.exe' -Force } }"
if not exist rclone.exe (echo [ERROR] rclone download failed or extraction failed. & pause & exit /b 1)
set "PWFILE=%SECRETS%\rclone-password.txt"
if not exist "%PWFILE%" powershell -NoProfile -Command "$b=New-Object byte[] 24; $r=[Security.Cryptography.RandomNumberGenerator]::Create(); $r.GetBytes($b); [IO.File]::WriteAllText('%PWFILE%',[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','A').Replace('/','B'),[Text.Encoding]::ASCII)"
if not exist "%PWFILE%" (echo [ERROR] Could not create the rclone password file. & pause & exit /b 1)
set /p "RCLONEPW="<"%PWFILE%"
if not defined RCLONEPW (echo [ERROR] rclone password file is empty. & pause & exit /b 1)
icacls "%SECRETS%" /inheritance:r /grant:r "%USERDOMAIN%\%USERNAME%:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" >nul 2>&1
netstat -ano | findstr ":5572" | findstr "LISTENING" >nul
if not errorlevel 1 (echo [ERROR] Port 5572 is already in use. Stop the current owner before retrying. & pause & exit /b 1)
netsh advfirewall firewall delete rule name="HKS rclone" >nul 2>nul
netsh advfirewall firewall add rule name="HKS rclone" dir=in action=allow protocol=TCP localport=5572 profile=private remoteip=localsubnet >nul
if errorlevel 1 echo [WARNING] Firewall rule was not added; remote LAN access may be blocked.
echo Starting authenticated rclone web UI. Login: hks
echo Password is stored in: "%PWFILE%"
rclone.exe rcd --rc-web-gui --rc-web-gui-no-open-browser --rc-addr 0.0.0.0:5572 --rc-user hks --rc-pass "%RCLONEPW%"

