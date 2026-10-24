@echo off
setlocal EnableExtensions
title HKS Add-on - Netdata
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set "D=%ProgramData%\HKS\netdata"
if not exist "%D%" mkdir "%D%"
cd /d "%D%"
echo Downloading the current Netdata Windows MSI...
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://github.com/netdata/netdata/releases/latest/download/netdata-latest-x64.msi' -OutFile 'netdata.msi'"
if not exist netdata.msi (echo [ERROR] Netdata MSI download failed. & pause & exit /b 1)
msiexec /i "%CD%\netdata.msi" /qn /norestart /L*v "%D%\install.log"
set "MSI_RC=%ERRORLEVEL%"
if not "%MSI_RC%"=="0" if not "%MSI_RC%"=="3010" (echo [ERROR] Netdata MSI failed with exit code %MSI_RC%. See "%D%\install.log". & pause & exit /b %MSI_RC%)
netsh advfirewall firewall delete rule name="HKS netdata" >nul 2>nul
netsh advfirewall firewall add rule name="HKS netdata" dir=in action=allow protocol=TCP localport=19999 profile=private remoteip=localsubnet >nul
if errorlevel 1 echo [WARNING] Private-LAN firewall rule was not added; remote dashboard access may be blocked.
powershell -NoProfile -Command "$ok=$false; 1..30 | ForEach-Object { if(-not $ok){ try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:19999/api/v1/info'; $ok=($r.StatusCode -eq 200)}catch{}; if(-not $ok){Start-Sleep -Seconds 1}} }; if(-not $ok){exit 1}"
if errorlevel 1 (echo [ERROR] Installation returned success, but Netdata did not answer its local health endpoint. See "%D%\install.log". & pause & exit /b 1)
echo [OK] Netdata is responding at http://localhost:19999
pause
exit /b 0

