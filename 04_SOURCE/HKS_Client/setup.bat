@echo off
setlocal EnableDelayedExpansion
title HKS Client - One Click Setup
REM ============================================================
REM  HKS Client - ONE CLICK SETUP  (run on the Windows laptop)
REM  Installs to C:\HKS_PROGRAM\HKS_Client and registers:
REM    - HKSAgent          (SYSTEM service,  auto start + restart on failure)
REM    - HKS-SessionAgent  (logon task: screen/input/camera in user session)
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set INSTALL=C:\HKS_PROGRAM\HKS_Client
set HKSROOT=C:\HKS_PROGRAM
echo.
echo  ==================================================
echo    HKS CLIENT SETUP  -  target: %INSTALL%
echo  ==================================================
echo  This machine will be remotely managed from YOUR
echo  admin panel. Only proceed on a laptop you own.
echo  ==================================================
echo.

REM ---------- 1. find / install Python ----------
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY ( python --version >nul 2>&1 && set PY=python )
if not defined PY (
  echo [1/7] Python not found - installing Python 3.12 silently...
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  set "PATH=%PATH%;C:\Program Files\Python312;C:\Program Files\Python312\Scripts"
  py -3 --version >nul 2>&1 && set PY=py -3
  python --version >nul 2>&1 && set PY=python
)
if not defined PY (
  echo  ERROR: could not install Python. Install from https://python.org and re-run.
  pause & exit /b 1
)
echo [1/7] Python OK: %PY%

REM ---------- 2. copy program files ----------
echo [2/7] Copying agent to %INSTALL% ...
robocopy "%~dp0." "%INSTALL%" /E /XD __pycache__ .venv data /XF setup.bat uninstall.bat /NFL /NDL /NJH /NJS >nul
mkdir C:\HKS_PROGRAM\data >nul 2>&1

REM ---------- 3. venv + dependencies ----------
echo [3/7] Creating virtualenv + installing dependencies (one time, ~2-5 min)...
cd /d "%INSTALL%"
if not exist .venv ( %PY% -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if errorlevel 1 ( echo  ERROR: dependency install failed - check internet. & pause & exit /b 1 )

REM ---------- 4. configure ----------
echo [4/7] Configuration
set SRV=
set TOK=
if exist "%~dp0config.ini" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0config.ini") do (
    if /i "%%a"=="server_url" set SRV=%%b
    if /i "%%a"=="token" set TOK=%%b
  )
)
if not defined SRV set /p SRV=  Panel server URL  [wss://YOUR-SERVER:8000/ws/agent]: 
if not defined TOK set /p TOK=  Agent token (panel Settings page): 
python -c "import json,uuid,os;p=r'%INSTALL%\config.json';c=json.load(open(p)) if os.path.exists(p) else {};c['server_url']=r'%SRV%'.strip();c['token']='%TOK%'.strip();c.setdefault('machine_id','HKS-'+uuid.uuid4().hex[:12].upper());c.setdefault('name',os.environ.get('COMPUTERNAME','laptop'));json.dump(c,open(p,'w'),indent=2)"
echo   machine id:
python -c "import json;print('   '+json.load(open(r'%INSTALL%\config.json'))['machine_id'])"

REM ---------- 5. SYSTEM service ----------
echo [5/7] Installing HKSAgent SYSTEM service...
python "%INSTALL%\agent\win_service.py" install >nul 2>&1
sc failure HKSAgent reset= 86400 actions= restart/5000/restart/10000/restart/30000 >nul 2>&1
sc config HKSAgent start= auto >nul 2>&1
python "%INSTALL%\agent\win_service.py" start >nul 2>&1
sc query HKSAgent | find "RUNNING" >nul
if errorlevel 1 (
  echo   pywin32 service failed - falling back to SYSTEM scheduled task...
  schtasks /Create /TN "HKSAgent" /TR "\"%INSTALL%\.venv\Scripts\pythonw.exe\" \"%INSTALL%\agent\main.py\"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F >nul 2>&1
  schtasks /Run /TN "HKSAgent" >nul 2>&1
)
echo   service registered (auto-start + restart on failure).

REM ---------- 6. session agent (logon task) ----------
echo [6/7] Registering desktop session agent (starts at every logon)...
schtasks /Create /TN "HKS-SessionAgent" /TR "\"%INSTALL%\.venv\Scripts\pythonw.exe\" -m agent.session_main" /SC ONLOGON /RL LIMITED /F >nul 2>&1
REM Keep the interactive agent alive through native driver crashes and stale links.
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%INSTALL%\SESSION_WATCHDOG.ps1" -Register
if errorlevel 1 (
  echo  ERROR: Could not register the hidden desktop-agent recovery task.
  pause & exit /b 1
)
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$s=New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; Set-ScheduledTask -TaskName 'HKS-SessionAgent' -Settings $s | Out-Null" >nul 2>&1
schtasks /Run /TN "HKS-SessionAgent" >nul 2>&1

REM ---------- 7. done ----------
echo [7/7] Done!
echo.
echo  ============================================================
echo   HKS agent installed. Open your admin panel in the
echo   browser - this laptop should appear ONLINE within ~15s.
echo   Config file: %INSTALL%\config.json   (edit server/token,
echo   then: sc stop HKSAgent ^&^& sc start HKSAgent)
echo   Remove everything: run uninstall.bat
echo  ============================================================
pause
