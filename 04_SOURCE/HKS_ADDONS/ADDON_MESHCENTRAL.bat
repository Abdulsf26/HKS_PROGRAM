@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === MeshCentral (remote management server, port 8490) ===
where node >nul 2>nul || (echo [X] Node.js missing - run ADDON_PREREQS.bat first & pause & exit /b 1)
cd /d "%ADDONS%"
if not exist meshcentral mkdir meshcentral
cd meshcentral
if not exist node_modules\meshcentral (
  echo Downloading MeshCentral via npm...
  call npm install meshcentral --no-audit --no-fund
  if errorlevel 1 (echo [X] npm install failed & pause & exit /b 1)
)
if not exist meshcentral-data mkdir meshcentral-data
> meshcentral-data\settings.json echo {"settings":{"port":8490}}
netsh advfirewall firewall delete rule name="HKS MeshCentral" >nul 2>nul
netsh advfirewall firewall add rule name="HKS MeshCentral" dir=in action=allow protocol=TCP localport=8490 >nul
echo Starting MeshCentral on http://localhost:8490 ...
start "HKS MeshCentral" /min cmd /c "cd /d %ADDONS%\meshcentral && node node_modules\meshcentral"
timeout /t 6 >nul
echo [ok] If the first run shows a setup wizard, open http://localhost:8490
echo Admin user is created on first open (remember what you type).
pause
