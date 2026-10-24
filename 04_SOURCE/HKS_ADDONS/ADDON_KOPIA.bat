@echo off
setlocal EnableExtensions
title HKS Add-on - Kopia
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set "D=%ProgramData%\HKS\kopia"
set "SECRETS=%ProgramData%\HKS\addon_credentials"
if not exist "%D%" mkdir "%D%"
if not exist "%SECRETS%" mkdir "%SECRETS%"
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/kopia/kopia/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows-x64.*\.zip$'}|Select-Object -First 1; if(-not $a){throw 'no Windows x64 release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'ko.zip'; Expand-Archive -Path 'ko.zip' -DestinationPath '.' -Force; Get-ChildItem -Recurse -Filter kopia.exe | Select-Object -First 1 | ForEach-Object { Copy-Item $_.FullName 'kopia.exe' -Force }"
if not exist kopia.exe (echo [ERROR] Kopia download failed or extraction failed. & pause & exit /b 1)
set "PWFILE=%SECRETS%\kopia-password.txt"
if not exist "%PWFILE%" powershell -NoProfile -Command "$b=New-Object byte[] 24; $r=[Security.Cryptography.RandomNumberGenerator]::Create(); $r.GetBytes($b); [IO.File]::WriteAllText('%PWFILE%',[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','A').Replace('/','B'),[Text.Encoding]::ASCII)"
if not exist "%PWFILE%" (echo [ERROR] Could not create Kopia credentials. & pause & exit /b 1)
set /p "KOPIAPW="<"%PWFILE%"
if not defined KOPIAPW (echo [ERROR] Kopia password file is empty. & pause & exit /b 1)
icacls "%SECRETS%" /inheritance:r /grant:r "%USERDOMAIN%\%USERNAME%:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" >nul 2>&1
set "KOPIA_PASSWORD=%KOPIAPW%"
"%D%\kopia.exe" --config-file "%D%\config.json" repository status >nul 2>&1
if errorlevel 1 (
  echo Creating a persistent encrypted local Kopia repository...
  if not exist "%D%\repository" mkdir "%D%\repository"
  "%D%\kopia.exe" --config-file "%D%\config.json" repository create filesystem --path "%D%\repository" --password "%KOPIAPW%"
  if errorlevel 1 (echo [ERROR] Kopia repository setup failed; see the output above. & pause & exit /b 1)
)
netstat -ano | findstr ":51515" | findstr "LISTENING" >nul
if not errorlevel 1 (echo [ERROR] Port 51515 is already in use. Stop the current owner before retrying. & pause & exit /b 1)
netsh advfirewall firewall delete rule name="HKS kopia" >nul 2>nul
netsh advfirewall firewall add rule name="HKS kopia" dir=in action=allow protocol=TCP localport=51515 profile=private remoteip=localsubnet >nul
if errorlevel 1 echo [WARNING] Private-LAN firewall rule was not added; remote access may be blocked.
"%D%\kopia.exe" --config-file "%D%\config.json" server users add "hks@%COMPUTERNAME%" --user-password "%KOPIAPW%" >nul 2>&1
if errorlevel 1 "%D%\kopia.exe" --config-file "%D%\config.json" server users set "hks@%COMPUTERNAME%" --user-password "%KOPIAPW%" >nul 2>&1
if errorlevel 1 (echo [ERROR] Could not configure Kopia repository user. & pause & exit /b 1)
set "KOPIA_SERVER_CONTROL_PASSWORD=%KOPIAPW%"
echo Starting password-protected Kopia HTTPS web UI on port 51515.
echo Username: hks. Password: stored in "%PWFILE%"
if not exist "%D%\kopia-server.crt" (
  "%D%\kopia.exe" --config-file "%D%\config.json" server start --address 0.0.0.0:51515 --server-username hks --server-password "%KOPIAPW%" --server-control-username hks-control --server-control-password "%KOPIAPW%" --tls-generate-cert --tls-generate-cert-name localhost --tls-cert-file "%D%\kopia-server.crt" --tls-key-file "%D%\kopia-server.key"
) else (
  "%D%\kopia.exe" --config-file "%D%\config.json" server start --address 0.0.0.0:51515 --server-username hks --server-password "%KOPIAPW%" --server-control-username hks-control --server-control-password "%KOPIAPW%" --tls-cert-file "%D%\kopia-server.crt" --tls-key-file "%D%\kopia-server.key"
)
if errorlevel 1 (echo [ERROR] Kopia server exited with an error; inspect the output above. & pause & exit /b 1)

