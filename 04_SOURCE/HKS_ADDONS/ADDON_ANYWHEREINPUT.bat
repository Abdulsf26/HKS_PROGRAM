@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === AnywhereInput (browser PC control, port 8008, pip) ===
where python >nul 2>nul || (echo [X] Python missing - HKS install should have provided it & pause & exit /b 1)
where git >nul 2>nul || (echo [X] git missing - run ADDON_PREREQS.bat first (AnywhereInput ships from GitHub, not PyPI) & pause & exit /b 1)
python -m pip install --quiet git+https://github.com/Z-Hussein/AnywhereInput.git
if errorlevel 1 (echo [X] pip install failed & pause & exit /b 1)
netsh advfirewall firewall delete rule name="HKS AnywhereInput" >nul 2>nul
netsh advfirewall firewall add rule name="HKS AnywhereInput" dir=in action=allow protocol=TCP localport=8008 >nul
echo Starting AnywhereInput in LAN mode on http://localhost:8008 ...
start "HKS AnywhereInput" /min cmd /c "anywhereinput --tunnel local"
echo [ok] NOTE: HKS already does screen+input natively with auth - this is the
echo original project, kept for comparison.
pause
