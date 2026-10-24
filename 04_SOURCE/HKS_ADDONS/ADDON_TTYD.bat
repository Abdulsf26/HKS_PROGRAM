@echo off
setlocal EnableExtensions
REM HKS ttyd exposes an authenticated shell only to Private-profile LAN peers.
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set "ADDONS=C:\HKS_PROGRAM\addons"
set "SECRETS=%ProgramData%\HKS\addon_credentials"
if not exist "%ADDONS%\ttyd" mkdir "%ADDONS%\ttyd"
if not exist "%SECRETS%" mkdir "%SECRETS%"
cd /d "%ADDONS%\ttyd"
if not exist ttyd.exe (
  echo Downloading ttyd for Windows...
  powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { Invoke-WebRequest -UseBasicParsing 'https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.win32.exe' -OutFile 'ttyd.exe' } catch { Invoke-WebRequest -UseBasicParsing 'https://github.com/tsl0922/ttyd/releases/download/1.7.3/ttyd.win32.exe' -OutFile 'ttyd.exe' }"
)
if not exist ttyd.exe (echo [ERROR] ttyd download failed. & pause & exit /b 1)
set "PWFILE=%SECRETS%\ttyd-password.txt"
if not exist "%PWFILE%" powershell -NoProfile -Command "$b=New-Object byte[] 24; $r=[Security.Cryptography.RandomNumberGenerator]::Create(); $r.GetBytes($b); [IO.File]::WriteAllText('%PWFILE%',[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','A').Replace('/','B'),[Text.Encoding]::ASCII)"
if not exist "%PWFILE%" (echo [ERROR] Could not create the ttyd password file. & pause & exit /b 1)
set /p "TTYDPW="<"%PWFILE%"
if not defined TTYDPW (echo [ERROR] ttyd password file is empty. & pause & exit /b 1)
icacls "%SECRETS%" /inheritance:r /grant:r "%USERDOMAIN%\%USERNAME%:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" >nul 2>&1
netstat -ano | findstr ":7681" | findstr "LISTENING" >nul
if not errorlevel 1 (echo [ERROR] Port 7681 is already in use. Stop the current owner before retrying. & pause & exit /b 1)
netsh advfirewall firewall delete rule name="HKS ttyd" >nul 2>nul
netsh advfirewall firewall add rule name="HKS ttyd" dir=in action=allow protocol=TCP localport=7681 profile=private remoteip=localsubnet >nul
if errorlevel 1 echo [WARNING] Firewall rule was not added; remote LAN access may be blocked.
echo Starting authenticated ttyd on port 7681. Login: hks
echo Password is stored in: "%PWFILE%"
start "HKS ttyd" /min "%ADDONS%\ttyd\ttyd.exe" -W -c hks:%TTYDPW% -i 0.0.0.0 -p 7681 cmd
echo [OK] ttyd launched with Basic authentication. Keep its password private.
pause
exit /b 0

