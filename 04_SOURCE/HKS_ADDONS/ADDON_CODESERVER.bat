@echo off
setlocal EnableExtensions
REM HKS add-on installer - fail fast and report the exact Docker step.
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set "ADDONS=C:\HKS_PROGRAM\addons"
if not exist "%ADDONS%" mkdir "%ADDONS%"
set "DOCKER="
where docker >nul 2>nul && set "DOCKER=docker"
if not defined DOCKER if exist "C:\Program Files\Docker\Docker\resources\bin\docker.exe" set "DOCKER=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
if not defined DOCKER (
  echo [ERROR] Docker CLI is missing. Install Docker Desktop from Add-ons ^> Prerequisites, start it, then retry.
  goto :fail
)
echo === Checking Docker Desktop engine ===
"%DOCKER%" info --format "{{.ServerVersion}}"
if errorlevel 1 (
  echo [ERROR] Docker Desktop engine is not ready. Open Docker Desktop, wait for Engine running, then retry.
  goto :fail
)
echo === Checking port 8482 ===
netstat -ano | findstr ":8482" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo [ERROR] Port 8482 is already in use. Stop the service using that port and retry.
  goto :fail
)
echo === Downloading code-server image ===
"%DOCKER%" pull codercom/code-server
if errorlevel 1 (
  echo [ERROR] Image download failed. Check Docker Desktop networking, disk space, and proxy settings.
  goto :fail
)
echo === Replacing the HKS-managed code-server container ===
"%DOCKER%" rm -f hks-code-server >nul 2>nul
"%DOCKER%" run -d --name hks-code-server --restart unless-stopped --cpus=1.0 --memory=512m --memory-swap=768m -p 8482:8080 -v hks_code_data:/home/coder codercom/code-server
if errorlevel 1 (
  echo [ERROR] Container creation failed. Check Docker Desktop resources and the error above.
  goto :fail
)
"%DOCKER%" ps --filter "name=^/hks-code-server$" --filter "status=running" --format "{{.Names}}" | findstr /x "hks-code-server" >nul
if errorlevel 1 (
  echo [ERROR] Docker created the container but it is not running. Recent logs:
  "%DOCKER%" logs --tail 30 hks-code-server
  goto :fail
)
echo === Waiting for code-server HTTP health ===
set "READY="
for /l %%I in (1,1,20) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8482/; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } } catch {}; exit 1" >nul 2>nul
  if not errorlevel 1 (
    set "READY=1"
    goto :ready
  )
  timeout /t 2 /nobreak >nul
)
:ready
if not defined READY (
  echo [ERROR] Container is running but code-server did not answer on port 8482. Recent logs:
  "%DOCKER%" logs --tail 30 hks-code-server
  goto :fail
)
netsh advfirewall firewall delete rule name="HKS code-server" >nul 2>nul
netsh advfirewall firewall add rule name="HKS code-server" dir=in action=allow protocol=TCP localport=8482 >nul
if errorlevel 1 echo [WARNING] Could not add the Windows Firewall rule; local access can still work.
echo [OK] code-server is responding at http://localhost:8482
echo [INFO] First-run password is stored in the HKS code-server config volume. Use: docker exec hks-code-server sh -c "cat ~/.config/code-server/config.yaml"
 echo [INFO] To see its startup details: "%DOCKER%" logs hks-code-server
pause
exit /b 0
:fail
echo.
echo [FAILED] code-server was not reported as installed. Fix the message above and retry.
pause
exit /b 1
