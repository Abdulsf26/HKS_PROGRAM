@echo off
setlocal EnableExtensions
title HKS Add-on - windows_exporter
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set "D=%ProgramData%\HKS\winexp"
if not exist "%D%" mkdir "%D%"
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/prometheus-community/windows_exporter/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows_exporter-.*-amd64\.msi$'}|Select-Object -First 1; if(-not $a){throw 'no Windows amd64 MSI asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'windows_exporter.msi'"
if not exist windows_exporter.msi (echo [ERROR] windows_exporter MSI download failed. & pause & exit /b 1)
echo Installing windows_exporter as its supported Windows service...
msiexec /i "%CD%\windows_exporter.msi" /qn /norestart /L*v "%D%\install.log"
set "MSI_RC=%ERRORLEVEL%"
if not "%MSI_RC%"=="0" if not "%MSI_RC%"=="3010" (echo [ERROR] MSI install failed with exit code %MSI_RC%. See "%D%\install.log". & pause & exit /b %MSI_RC%)
sc query windows_exporter >nul 2>&1
if errorlevel 1 (echo [ERROR] MSI completed but the windows_exporter service is missing. See "%D%\install.log". & pause & exit /b 1)
sc start windows_exporter >nul 2>&1
netsh advfirewall firewall delete rule name="HKS winexp" >nul 2>nul
netsh advfirewall firewall add rule name="HKS winexp" dir=in action=allow protocol=TCP localport=9182 profile=private remoteip=localsubnet >nul
if errorlevel 1 echo [WARNING] Private-LAN firewall rule was not added; remote metrics may be blocked.
powershell -NoProfile -Command "$ok=$false; 1..20 | ForEach-Object { if(-not $ok){ try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:9182/metrics'; $ok=($r.StatusCode -eq 200 -and $r.Content -match 'windows_exporter')}catch{}; if(-not $ok){Start-Sleep -Seconds 1}} }; if(-not $ok){exit 1}"
if errorlevel 1 (echo [ERROR] Service installed but /metrics did not become healthy on port 9182. & pause & exit /b 1)
echo [OK] windows_exporter service is healthy at http://localhost:9182/metrics
pause
exit /b 0

