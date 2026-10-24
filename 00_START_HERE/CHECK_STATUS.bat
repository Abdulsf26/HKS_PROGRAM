@echo off
title HKS Status Check
REM HKS diagnostic - works on any ALL-IN-ONE install.
REM Reads your port from C:\HKS_PROGRAM\HKS_Client\config.json
for %%i in ("%~dp0.") do set "BASE=%%~fi"
if not exist "%BASE%\HKS_Client\config.json" (
  echo HKS is not installed at %BASE% yet. Run INSTALL_HKS.bat first.
  if not defined HKS_GUI_INSTALL pause & exit /b 1
)
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try { ([uri]((Get-Content '%BASE%\HKS_Client\config.json' -Raw | ConvertFrom-Json).server_url)).Port } catch { 8000 }"`) do set PORT=%%P
if not defined PORT set PORT=8000
echo HKS port: %PORT%
echo.
echo ============ HKS STATUS ============
echo.
echo [scheduled tasks]
schtasks /Query /TN HKS-Agent 2>nul | find /i "HKS-Agent"
schtasks /Query /TN HKS-Panel 2>nul | find /i "HKS-Panel"
schtasks /Query /TN HKS-SessionAgent 2>nul | find /i "HKS-SessionAgent"
echo.
echo [agent heartbeat - local] (each HKS process answers on 8590-8594)
powershell -NoProfile -Command "8590..8594 | ForEach-Object { try { (Invoke-WebRequest -UseBasicParsing \"http://127.0.0.1:$_/status.json\" -TimeoutSec 2).Content } catch {} }"
powershell -NoProfile -Command "if (-not (8590..8594 | Where-Object { try { (Invoke-WebRequest -UseBasicParsing \"http://127.0.0.1:$($_)/status.json\" -TimeoutSec 2).StatusCode -eq 200 } catch { $false } })) { 'agent status page: NOT RESPONDING on any port' }"
echo.
echo [panel - local] (tries HTTPS then HTTP)
powershell -NoProfile -Command "$ok=$false; foreach ($s in @('https','http')) { try { $r=Invoke-WebRequest -UseBasicParsing ($s + '://127.0.0.1:%PORT%/') -TimeoutSec 3; Write-Output ('panel ' + $s + ': OK ' + $r.StatusCode); $ok=$true; break } catch {} }; if (-not $ok) { Write-Output 'panel: NOT RESPONDING' }"
echo.
echo [last agent (service) log lines]
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\agent_console.log') { Get-Content '%BASE%\data\agent_console.log' -Tail 12 } else { 'no agent log yet' }"
echo.
echo [last desktop (session) agent lines]
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\session_console.log') { Get-Content '%BASE%\data\session_console.log' -Tail 12 } else { 'no session log yet' }"
echo.
echo [last panel log lines]
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\panel_console.log') { Get-Content '%BASE%\data\panel_console.log' -Tail 12 } else { 'no panel log yet' }"
echo.
echo Desktop agent offline (camera/screen/keyboard dead)?
set /p D=Press D then ENTER to start it NOW (or just ENTER to skip): 
if /i "%D%"=="D" (
  schtasks /Run /TN HKS-SessionAgent >nul 2>&1
  if errorlevel 1 (
    start "" "%BASE%\run_session_agent.bat"
  )
  echo started - give it 10 seconds, then refresh the panel.
)
echo.
echo Panel offline (page will not open)?
set /p P=Press P then ENTER to start it NOW (or just ENTER to skip): 
if /i "%P%"=="P" (
  schtasks /Run /TN HKS-Panel >nul 2>&1
  if errorlevel 1 (
    start "" "%BASE%\HKS_AdminPanel\start_panel.bat"
  )
  echo panel starting - give it 10 seconds.
)
echo.
if not defined HKS_GUI_INSTALL pause
