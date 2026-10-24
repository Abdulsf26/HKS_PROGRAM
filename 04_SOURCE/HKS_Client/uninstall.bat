@echo off
title HKS Client - Uninstall
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
echo Stopping and removing HKS agent...
sc stop HKSAgent >nul 2>&1
sc delete HKSAgent >nul 2>&1
schtasks /End /TN "HKS-Agent" >nul 2>&1
schtasks /Delete /TN "HKS-Agent" /F >nul 2>&1
schtasks /End /TN "HKS-Panel" >nul 2>&1
schtasks /Delete /TN "HKS-Panel" /F >nul 2>&1
schtasks /End /TN "HKS-SessionAgent" >nul 2>&1
schtasks /Delete /TN "HKS-SessionAgent" /F >nul 2>&1
schtasks /Delete /TN "HKSAgent" /F >nul 2>&1
netsh advfirewall firewall delete rule name="HKS Panel" >nul 2>&1
schtasks /Delete /TN "HKS-SessionAgent" /F >nul 2>&1
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq HKS*" >nul 2>&1
echo HKS agent removed. (Program files left in C:\HKS_PROGRAM - delete manually if desired.)
pause
