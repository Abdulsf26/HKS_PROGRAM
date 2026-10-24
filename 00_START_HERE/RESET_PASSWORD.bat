@echo off
title HKS - Reset Admin Password
rem round 67 (Ammar UI doc PART 1): a real way back in. Runs on the laptop,
rem generates a random password, prints it once. Self-elevates like the others.
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo  ==============================================
echo    HKS - RESET ADMIN PASSWORD (on this laptop)
echo  ==============================================
echo.
for %%i in ("%~dp0.") do set "BASE=%%~fi"
if not exist "%BASE%\HKS_AdminPanel\server\db.py" (
  echo  HKS is not installed at %BASE% - nothing to reset.
  echo.
  pause
  exit /b 1
)
set "HPY=python"
if exist "%BASE%\venv\Scripts\python.exe" set "HPY=%BASE%\venv\Scripts\python.exe"
set /p RUSER=Username to reset [admin]: 
if "%RUSER%"=="" set RUSER=admin
set "HKS_RESET_USER=%RUSER%"
set "OUTW=%TEMP%\hks_new_pw.txt"
if exist "%OUTW%" del "%OUTW%" >nul 2>&1
"%HPY%" -c "import sys,os,secrets; sys.path.insert(0, r'%BASE%\HKS_AdminPanel'); os.environ['HKS_DATA_DIR']=r'%BASE%\data_panel'; from server.db import db; from server.security import hash_password; u=db.get_user(os.environ.get('HKS_RESET_USER','')); assert u, 'Username not found'; pw=secrets.token_urlsafe(12); db.set_user_password(u['id'], hash_password(pw)); open(r'%OUTW%','w',encoding='utf-8').write(pw)" >nul 2>&1
if not exist "%OUTW%" (
  echo  RESET FAILED - confirm the username, then run HKS_DOCTOR.bat.
  echo  Fix: run HKS_DOCTOR.bat first, then try again.
  echo.
  pause
  exit /b 1
)
set /p NEWPW=<"%OUTW%"
del "%OUTW%" >nul 2>&1
echo.
echo  ==============================================
echo    NEW PASSWORD for %RUSER%:
echo    %NEWPW%
echo  ==============================================
echo.
echo   Write it down. Log in, then change it in Settings - Users.
echo   Recovery codes from install also still work on the login page.
echo.
pause
exit /b 0
