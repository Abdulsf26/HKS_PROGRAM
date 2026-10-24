@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === Shinobi CCTV NVR (port 8080, Docker compose) ===
where docker >nul 2>nul || (echo [X] Docker missing - run ADDON_PREREQS.bat first, reboot, start Docker Desktop & pause & exit /b 1)
cd /d "%ADDONS%"
if not exist shinobi mkdir shinobi
cd shinobi
REM round 66 (addon audit): the repo no longer ships docker-compose.yml - the official Docker Hub image replaces it (host port moved off 8080 so CasaOS can coexist).
docker pull shinobicctv/shinobi:latest
docker rm -f hks-shinobi >nul 2>nul
docker run -d --name hks-shinobi --restart unless-stopped -p 8480:8080 -v hks_shinobi_data:/opt/ShinobiDatabase shinobicctv/shinobi:latest
netsh advfirewall firewall delete rule name="HKS Shinobi" >nul 2>nul
netsh advfirewall firewall add rule name="HKS Shinobi" dir=in action=allow protocol=TCP localport=8480 >nul
echo [ok] Shinobi on http://localhost:8480 (host port moved off 8080 so CasaOS can coexist) - first superuser is created on the web page.
pause
