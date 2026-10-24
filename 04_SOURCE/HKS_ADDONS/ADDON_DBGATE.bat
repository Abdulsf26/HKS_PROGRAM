@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === DbGate - database GUI in the browser (port 8485, Docker) ===
where docker >nul 2>nul || (echo [X] Docker missing - run ADDON_PREREQS.bat first, reboot, start Docker Desktop & pause & exit /b 1)
docker pull dbgate/dbgate
docker rm -f hks-dbgate >nul 2>nul
powershell -NoProfile -Command "$g=-join((48..57)+(65..90)+(97..122)|Get-Random -Count 20|ForEach-Object{[char]$_}); Set-Content -Path 'cred.tmp' -Value $g"
set /p HKSPW=<cred.tmp
del cred.tmp >nul 2>&1
docker run -d --name hks-dbgate --restart unless-stopped -p 8485:3000 -e LOGINS=admin:%HKSPW% dbgate/dbgate
echo login: admin / %HKSPW%   (saved in %CD%\credentials.txt)
<nul set /p =dbgate login: admin / %HKSPW% > credentials.txt
echo( >> credentials.txt
netsh advfirewall firewall delete rule name="HKS DbGate" >nul 2>nul
netsh advfirewall firewall add rule name="HKS DbGate" dir=in action=allow protocol=TCP localport=8485 >nul
echo [ok] DbGate on http://localhost:8485
pause
