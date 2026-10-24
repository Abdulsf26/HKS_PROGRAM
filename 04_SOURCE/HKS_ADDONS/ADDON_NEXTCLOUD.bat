@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === Nextcloud All-in-One (mastercontainer, ports 80/8080/8443, Docker) ===
where docker >nul 2>nul || (echo [X] Docker missing - run ADDON_PREREQS.bat first, reboot, start Docker Desktop & pause & exit /b 1)
docker rm -f nextcloud-aio-mastercontainer >nul 2>nul
docker run -d --name nextcloud-aio-mastercontainer --restart always -e SKIP_DOMAIN_VALIDATION=true -p 8080:8080 -p 8443:8443 nextcloud/all-in-one
echo NOTE: open https://localhost:8443 - AIO needs a domain for real use; SKIP_DOMAIN_VALIDATION makes local testing possible.
netsh advfirewall firewall delete rule name="HKS Nextcloud AIO" >nul 2>nul
netsh advfirewall firewall add rule name="HKS Nextcloud AIO" dir=in action=allow protocol=TCP localport=8443 >nul
echo [ok] Open https://localhost:8443 - it shows the AIO setup password:
docker logs nextcloud-aio-mastercontainer 2>&1 | findstr "password"
echo NOTE: uses ports 80/8080/8443 - stop Shinobi first if it is running (port 8080 clash).
pause
