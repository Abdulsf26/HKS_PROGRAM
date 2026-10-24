@echo off
setlocal EnableExtensions
REM HKS optional MeshCentral server installer/start helper.
set "HKS_ROOT=%~dp0.."
for %%I in ("%HKS_ROOT%") do set "HKS_ROOT=%%~fI"
set "ADDONS=%HKS_ROOT%\addons"
set "MC_DIR=%ADDONS%\meshcentral"
set "PORT=8490"

REM Never spawn a duplicate or overwrite an unknown listener on the addon port.
netstat -ano -p tcp | findstr /R /C:":8490 .*LISTENING" >nul
if not errorlevel 1 (
  echo [!] TCP port 8490 is already listening. HKS will not start another server.
  echo     If it is MeshCentral, open https://localhost:8490 or use the HKS Open button.
  echo     If it is another program, resolve that port conflict before installing.
  exit /b 2
)

net session >nul 2>&1 || (
  echo Requesting Administrator rights for the private-network firewall rule...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

if not exist "%ADDONS%" mkdir "%ADDONS%"
if errorlevel 1 (echo [X] Could not create %ADDONS% & exit /b 1)
if not exist "%MC_DIR%" mkdir "%MC_DIR%"
if errorlevel 1 (echo [X] Could not create %MC_DIR% & exit /b 1)

echo === MeshCentral (HTTPS remote-management server, port %PORT%) ===
where node >nul 2>nul || (echo [X] Node.js missing - run ADDON_PREREQS.bat first & exit /b 1)
where npm >nul 2>nul || (echo [X] npm missing - repair the Node.js LTS installation & exit /b 1)
for /f "tokens=1 delims=." %%V in ('node -p process.versions.node 2^>nul') do set "NODE_MAJOR=%%V"
if not defined NODE_MAJOR (echo [X] Could not read the Node.js version & exit /b 1)
if %NODE_MAJOR% LSS 20 (echo [X] MeshCentral needs Node.js 20 or newer. Update Node.js LTS, then retry. & exit /b 1)

pushd "%MC_DIR%" || (echo [X] Could not enter %MC_DIR% & exit /b 1)
if not exist node_modules\meshcentral\meshcentral.js (
  echo Installing MeshCentral from npm; output will remain visible here...
  call npm install --no-audit --no-fund --save-exact meshcentral
  if errorlevel 1 (echo [X] npm install failed. See the npm error above; existing data was preserved. & popd & exit /b 1)
)

if not exist meshcentral-data mkdir meshcentral-data
if errorlevel 1 (echo [X] Could not create MeshCentral data directory & popd & exit /b 1)
if not exist meshcentral-data\config.json (
  >meshcentral-data\config.json echo {"settings":{"port":8490}}
  if errorlevel 1 (echo [X] Could not create MeshCentral settings & popd & exit /b 1)
) else (
  echo [ok] Keeping existing MeshCentral settings and administrator data.
)

REM Limit automatic exposure to Windows Private networks; do not open Public profiles.
netsh advfirewall firewall delete rule name="HKS MeshCentral" >nul 2>nul
netsh advfirewall firewall add rule name="HKS MeshCentral" dir=in action=allow protocol=TCP localport=%PORT% profile=private >nul
if errorlevel 1 echo [!] Firewall rule was not installed. LAN access may be blocked; inspect Windows Firewall.

echo Starting MeshCentral at https://localhost:%PORT% ...
start "HKS MeshCentral" /min cmd /d /c "cd /d ""%MC_DIR%"" && node node_modules\meshcentral"
popd
echo [ok] Started without replacing existing configuration.
echo     HKS Add-ons will show Open when HTTPS port %PORT% is reachable.
echo     A first-run self-signed TLS warning is expected; check the certificate before continuing.
echo     Phone access uses the laptop's LAN address and may warn because the certificate is self-signed.
pause
exit /b 0
