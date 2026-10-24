@echo off
REM HKS add-on installer - installs + configures + starts one shared-repo service
net session >nul 2>&1 || (echo Requesting Administrator rights... & powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
set ADDONS=C:\HKS_PROGRAM\addons
if not exist "%ADDONS%" mkdir "%ADDONS%"
echo === filegator - web file manager (port 8090) ===
cd /d "%ADDONS%"
if not exist php\php.exe (
  echo Downloading PHP for Windows...
  powershell -NoProfile -Command "Invoke-WebRequest 'https://windows.php.net/downloads/releases/latest/php-8.3-nts-Win32-vs16-x64-latest.zip' -OutFile php.zip -UseBasicParsing"
  if not exist php.zip (echo [X] PHP download failed & pause & exit /b 1)
  powershell -NoProfile -Command "Expand-Archive php.zip -DestinationPath php -Force"
  del php.zip
)
if not exist filegator\dist (
  echo Downloading filegator latest release...
  REM round 66 (addon audit): filegator publishes NO release assets - but dist/ is committed in the repo, so the source zip runs directly under php -S.
  powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest 'https://github.com/filegator/filegator/archive/refs/heads/master.zip' -OutFile fg.zip -UseBasicParsing; Expand-Archive fg.zip -DestinationPath fgtmp -Force; if (Test-Path filegator) { Remove-Item filegator -Recurse -Force }; Move-Item fgtmp\filegator-master filegator; Remove-Item fgtmp -Recurse -Force"
  if not exist fg.zip (echo [X] filegator download failed - check github.com/filegator/filegator & pause & exit /b 1)
  del fg.zip
)
netsh advfirewall firewall delete rule name="HKS filegator" >nul 2>nul
netsh advfirewall firewall add rule name="HKS filegator" dir=in action=allow protocol=TCP localport=8090 >nul
echo Starting filegator on http://localhost:8090 ...
start "HKS filegator" /min cmd /c "cd /d %ADDONS%\filegator && ..\php\php.exe -S 0.0.0.0:8090 -t dist"
echo [ok] Default login: admin / admin123 - CHANGE IT after first login.
pause
