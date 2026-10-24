@echo off
title HKS - Fix Packages (repairs CPU/Memory/camera/audio features)
cd /d "%~dp0"
echo.
echo  ======================================================
echo    HKS FIX PACKAGES
echo    Installs the Python tools the laptop-side agent
echo    needs. Missing tools = dashboard cards show "-"
echo    and camera / audio / screen-control stay dead.
echo  ======================================================
echo.
set "PY=python"
if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
echo  Using python: %PY%
echo.
"%PY%" --version
echo.
for %%P in (psutil pillow mss pyautogui pynput opencv-python numpy websockets sounddevice pywin32 pycaw comtypes winotify pyttsx3 pymysql) do (
  echo   installing %%P ...
  "%PY%" -m pip install --quiet --disable-pip-version-check %%P 2>nul && (
    echo     [OK] %%P
  ) || (
    echo     [X] %%P FAILED - the panel keeps working, just this feature stays off
  )
)
echo.
echo  ======================================================
echo   DONE. Now restart HKS so the agents pick up the
echo   new tools: run RESTART_HKS.bat if you have it, or
echo   simply REBOOT the laptop.
echo   Then check the dashboard - the cards should move.
echo  ======================================================
echo.
pause
exit /b 0
