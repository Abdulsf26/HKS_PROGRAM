@echo off
setlocal EnableDelayedExpansion
title HKS ALL-IN-ONE - One Click Install
REM ============================================================
REM  Run ONCE on the laptop (Acer). This machine will:
REM    - RUN the admin panel server (port you choose)
REM    - RUN the agent (controls this same laptop)
REM  Then from any phone/PC on the same Wi-Fi open:
REM    http://THIS-LAPTOP-IP:PORT  -> login -> full control
REM  NOTE: after install nothing shows in the taskbar - that is
REM  NORMAL: it runs as background scheduled tasks (HKS-Agent,
REM  HKS-Panel, HKS-SessionAgent). Check: CHECK_STATUS.bat
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set BASE=C:\HKS_PROGRAM
echo.
echo  ==================================================
echo    HKS ALL-IN-ONE INSTALL  -  target: %BASE%
echo    This laptop = server + agent. Control it from
echo    your phone/PC browser on the same Wi-Fi.
echo  ==================================================
echo.

REM ---------- 1. Python ----------
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY ( python --version >nul 2>&1 && set PY=python )
if not defined PY (
  echo [1/6] Python not found - installing silently...
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  set "PATH=%PATH%;C:\Program Files\Python312;C:\Program Files\Python312\Scripts"
  py -3 --version >nul 2>&1 && set PY=py -3
  python --version >nul 2>&1 && set PY=python
)
if not defined PY (
  echo  ERROR: could not install Python. Install from https://python.org
  echo  ^(tick "Add python.exe to PATH"^) and run this again.
  if not defined HKS_GUI_INSTALL pause & exit /b 1
)
echo [1/6] Python OK.

REM ---------- 1b. Stop old HKS (UPGRADE-SAFE) ----------
echo [1b/6] Stopping any old HKS processes (so files can be replaced)...
if exist "%~dp0first_run.py" %PY% "%~dp0first_run.py" --stop-only
if not exist "%~dp0first_run.py" powershell -NoProfile -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -match 'HKS_PROGRAM' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

REM ---------- 2. Copy program (bounded retries - no endless 'in use' hang) ----------
echo [2/6] Copying program to %BASE% ...
rem round 68 (F06): snapshot the current install before overwriting -
rem a bad upgrade rolls back with REVERT_HKS.bat instead of a reinstall.
if exist "C:\HKS_PROGRAM\HKS_AdminPanel\server\main.py" (
  for /f "tokens=2 delims==" %%i in ('wmic os get localdatetime /value 2^>nul') do set DT=%%i
  set "DT=%DT:~0,14%"
  echo [1b/7] Snapshotting current install to HKS_PROGRAM.bak.%DT%
  robocopy "C:\HKS_PROGRAM" "C:\HKS_PROGRAM.bak.%DT%" /E /XD venv data data_panel __pycache__ /NFL /NDL /NJH /NJS >nul
  for /f "skip=3 delims=" %%d in ('dir /b /ad /o-d "C:\HKS_PROGRAM.bak.*" 2^>nul') do rd /s /q "%%d"
)


robocopy "%~dp0HKS_Client" "%BASE%\HKS_Client" /E /R:5 /W:2 /XD __pycache__ .venv data /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto :copy_failed
robocopy "%~dp0HKS_AdminPanel" "%BASE%\HKS_AdminPanel" /E /R:5 /W:2 /XD __pycache__ .venv data /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto :copy_failed
if not exist "%~dp0RESET_PASSWORD.bat" goto :utility_copy_failed
for %%F in (RESET_PASSWORD.bat HKS_DOCTOR.bat CHECK_STATUS.bat HKS_FIX_TUNNEL.bat FIX_PACKAGES.bat UNINSTALL_HKS.bat REVERT_HKS.bat HKS_AUTOSTART.ps1 HKS_REPAIR_AUTOSTART.ps1 HKS_TRACE_DESKTOP.ps1 README_START_HERE.txt) do (
  if exist "%~dp0%%F" copy /y "%~dp0%%F" "%BASE%\%%F" >nul
  if errorlevel 1 goto :utility_copy_failed
)
if not exist "%~dp0RESET_PASSWORD.bat" goto :utility_copy_failed
for %%F in (RESET_PASSWORD.bat HKS_DOCTOR.bat CHECK_STATUS.bat HKS_FIX_TUNNEL.bat FIX_PACKAGES.bat UNINSTALL_HKS.bat REVERT_HKS.bat HKS_AUTOSTART.ps1 HKS_REPAIR_AUTOSTART.ps1 HKS_TRACE_DESKTOP.ps1 README_START_HERE.txt) do (
  if exist "%~dp0%%F" copy /y "%~dp0%%F" "%BASE%\%%F" >nul
  if errorlevel 1 goto :utility_copy_failed
)
if not exist "%~dp0HKS_ADDONS\ADDON_MENU.bat" (
  echo  ERROR: HKS_ADDONS is missing from the extracted package.
  echo  Re-extract the complete HKS_PROGRAM.zip and run INSTALL_HKS.bat again.
  if not defined HKS_GUI_INSTALL pause & exit /b 1
)
robocopy "%~dp0HKS_ADDONS" "%BASE%\HKS_ADDONS" /E /R:5 /W:2 /XD __pycache__ /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto :copy_failed
mkdir "%BASE%\data" >nul 2>&1
mkdir "%BASE%\data_panel" >nul 2>&1

REM ---------- 3. One shared environment ----------
echo [3/6] Creating environment + installing packages (one time, 3-10 min)...
cd /d "%BASE%"
if not exist venv %PY% -m venv venv
call venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r "%BASE%\HKS_AdminPanel\requirements.txt" || python -m pip install -r "%BASE%\HKS_AdminPanel\requirements.txt"
python -m pip install -q -r "%BASE%\HKS_Client\requirements.txt" || python -m pip install -r "%BASE%\HKS_Client\requirements.txt"
echo       verifying critical packages...
python -c "import websockets, psutil, fastapi, uvicorn" 2>nul
if errorlevel 1 (
  echo  ERROR: core packages missing - check internet and run this again.
  if not defined HKS_GUI_INSTALL pause & exit /b 1
)
python -c "import PIL, mss, cv2, pynput, sounddevice" 2>nul
if errorlevel 1 echo       note: some optional packages missing - those features degrade gracefully, install continues.

REM ---------- 4. Wizard: username / password / port + task registration + ONLINE check ----------
echo [4/6] Configuration wizard...
REM 7.2.54 fix: silent upgrade when saved answers exist (no keyboard hangs)
if exist "%BASE%\data\answers.json" (
  python "%~dp0first_run.py" --answer-file "%BASE%\data\answers.json"
) else if exist "%BASE%\answers.json" (
  python "%~dp0first_run.py" --answer-file "%BASE%\answers.json"
) else (
  python "%~dp0first_run.py" --gui
)
if errorlevel 1 ( echo  Setup wizard failed. & if not defined HKS_GUI_INSTALL pause & exit /b 1 )

REM ---------- 5/6 merged into the wizard (it verifies with YOUR port) ----------
echo.
echo [5/6] Wizard finished - see the check results above.
copy /y "%BASE%\CHECK_STATUS.bat" "%~dp0CHECK_STATUS.bat" >nul 2>&1
REM SYSTEM ONSTART tasks are preferred. This per-user logon fallback starts
REM only components whose local ports are not already listening.
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v HKSProgramRecovery /t REG_SZ /d "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File %BASE%\HKS_AUTOSTART.ps1" /f >nul 2>&1
echo [6/6] Done.
echo.
echo  PHONE: use the "OPEN (NO CERT - easiest): http://IP:PORT+1" address
echo  shown by CHECK_STATUS.bat (zero certificate problems).
echo  Open C:\HKS_PROGRAM\HOW_TO_CONNECT.txt for your control address.
echo  Desktop shortcuts: "HKS Control Center" + "HKS Status (this laptop)".
echo  If the laptop ever shows OFFLINE in the panel: run CHECK_STATUS.bat
echo.
if not defined HKS_GUI_INSTALL pause
exit /b 0

:copy_failed
echo.
echo  ERROR: some files were LOCKED by another process and could not be
echo  replaced. Fix: run this installer again ^(it stops old HKS first^)
echo  or restart the laptop once and run it again.
if not defined HKS_GUI_INSTALL pause
exit /b 1

:utility_copy_failed
echo.
echo  ERROR: HKS could not copy a required support tool into %BASE%.
echo  Close files in the install folder and run INSTALL_HKS.bat again.
if not defined HKS_GUI_INSTALL pause & exit /b 1
