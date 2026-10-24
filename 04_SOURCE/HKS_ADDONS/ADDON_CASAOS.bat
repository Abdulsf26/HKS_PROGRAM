@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === CasaOS inside WSL2 Ubuntu (web UI port 8080) ===
echo CasaOS is Linux-only. On Windows it installs inside WSL2 Ubuntu.
wsl --status >nul 2>nul || (echo Installing WSL2 (reboot needed afterwards)... & wsl --install -d Ubuntu & pause & exit /b 0)
wsl -l -q | findstr /i ubuntu >nul || (echo Installing Ubuntu... & wsl --install -d Ubuntu & echo Reboot, finish Ubuntu setup, then re-run this file. & pause & exit /b 0)
echo Installing CasaOS inside Ubuntu (takes a few minutes)...
wsl -d Ubuntu -- bash -c "curl -fsSL https://raw.githubusercontent.com/IceWhaleTech/CasaOS-Installer/main/install.sh | bash"
echo [ok] CasaOS UI: http://localhost:8080 (WSL2 forwards it to Windows).
echo NOTE: port 8080 is shared with the Shinobi add-on - run one at a time.
pause
