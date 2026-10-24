@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === HKS add-on prerequisites (git + Node.js + Docker Desktop) ===
where git >nul 2>nul && (echo [ok] git already installed) || (echo Installing Git... & winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements)
where node >nul 2>nul && (echo [ok] Node.js already installed) || (echo Installing Node.js LTS... & winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements)
where docker >nul 2>nul && (echo [ok] Docker already installed) || (echo Installing Docker Desktop... & winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements)
where docker >nul 2>nul && echo [ok] docker CLI present || echo [!] Docker installed - REBOOT once, start Docker Desktop, then re-run this.
echo.
echo Done. If Node/Docker were newly installed, REBOOT and re-run your add-on installer.
pause
