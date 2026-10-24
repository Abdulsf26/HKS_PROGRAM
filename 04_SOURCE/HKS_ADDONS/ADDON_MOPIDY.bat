@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === Mopidy + Iris music server (port 6680, pip) ===
where python >nul 2>nul || (echo [X] Python missing - HKS install should have provided it & pause & exit /b 1)
python -m pip install --quiet mopidy mopidy-iris
if errorlevel 1 (echo [X] pip install failed & pause & exit /b 1)
netsh advfirewall firewall delete rule name="HKS Mopidy" >nul 2>nul
netsh advfirewall firewall add rule name="HKS Mopidy" dir=in action=allow protocol=TCP localport=6680 >nul
echo Starting Mopidy with Iris on http://localhost:6680 ...
python -c "import gi" 2>nul || (echo [X] GStreamer/PyGObject missing - Mopidy cannot play audio on Windows. See ADDON_PREREQS.bat & pause & exit /b 1)
start "HKS Mopidy" /min cmd /c "python -m mopidy"
echo [ok] Iris web UI on http://localhost:6680
echo NOTE: Mopidy sound output needs GStreamer, which is NOT auto-installed on Windows:
echo this is why the panel marks it experimental. Web UI + setup still work.
pause
