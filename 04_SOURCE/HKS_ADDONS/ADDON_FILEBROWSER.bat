@echo off
setlocal EnableExtensions
REM File Browser is restricted to this Windows user's home folder and a strong generated password.
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set "FB=%ProgramData%\HKS\filebrowser"
set "SECRETS=%ProgramData%\HKS\addon_credentials"
if not exist "%FB%" mkdir "%FB%"
if not exist "%SECRETS%" mkdir "%SECRETS%"
cd /d "%FB%"
if not exist filebrowser.exe (
  echo Downloading File Browser...
  powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/filebrowser/filebrowser/releases/latest/download/windows-amd64-filebrowser.zip' -OutFile fb.zip; Expand-Archive -Path fb.zip -DestinationPath . -Force"
)
if not exist filebrowser.exe (echo [ERROR] File Browser download or extraction failed. & pause & exit /b 1)
if not exist hks.db filebrowser.exe -d hks.db config init
filebrowser.exe -d hks.db config set port 8487 >nul
if errorlevel 1 (echo [ERROR] File Browser rejected port configuration. & pause & exit /b 1)
set "PWFILE=%SECRETS%\filebrowser-password.txt"
if not exist "%PWFILE%" powershell -NoProfile -Command "$b=New-Object byte[] 24; $r=[Security.Cryptography.RandomNumberGenerator]::Create(); $r.GetBytes($b); [IO.File]::WriteAllText('%PWFILE%',[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','A').Replace('/','B'),[Text.Encoding]::ASCII)"
if not exist "%PWFILE%" (echo [ERROR] Could not create the File Browser password file. & pause & exit /b 1)
set /p "HKSPW="<"%PWFILE%"
if not defined HKSPW (echo [ERROR] File Browser password file is empty. & pause & exit /b 1)
icacls "%SECRETS%" /inheritance:r /grant:r "%USERDOMAIN%\%USERNAME%:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" >nul 2>&1
filebrowser.exe -d hks.db users update admin -p "%HKSPW%" --perm.admin >nul 2>&1
if errorlevel 1 (
  filebrowser.exe -d hks.db users add admin "%HKSPW%" --perm.admin >nul 2>&1
  if errorlevel 1 (echo [ERROR] Could not create or update File Browser admin. Existing database was kept. & pause & exit /b 1)
)
netsh advfirewall firewall delete rule name="HKS filebrowser" >nul 2>nul
netsh advfirewall firewall add rule name="HKS filebrowser" dir=in action=allow protocol=TCP localport=8487 profile=private remoteip=localsubnet >nul
if errorlevel 1 echo [WARNING] Firewall rule was not added; remote LAN access may be blocked.
netstat -ano | findstr ":8487" | findstr "LISTENING" >nul
if not errorlevel 1 (echo [ERROR] Port 8487 is already in use. Stop the current owner before retrying. & pause & exit /b 1)
echo Starting File Browser. Root: "%USERPROFILE%"
echo Login: admin. Password is stored in: "%PWFILE%"
start "HKS File Browser" /min "%FB%\filebrowser.exe" -d "%FB%\hks.db" -a 0.0.0.0 -p 8487 -r "%USERPROFILE%"
echo [OK] File Browser launched with generated credentials and a restricted file root.
pause
exit /b 0

