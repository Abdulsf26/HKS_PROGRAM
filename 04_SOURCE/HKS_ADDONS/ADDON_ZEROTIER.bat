@echo off
title HKS Add-on - ZeroTier
echo === ZeroTier - mesh VPN (virtual LAN) ===
echo source: github.com/zerotier/ZeroTierOne (official download.zerotier.com MSI)
set D=%ProgramData%\HKS\zerotier
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://download.zerotier.com/dist/ZeroTier%%20One.msi' -OutFile 'zerotier.msi'"
if not exist zerotier.msi (
  echo [X] download failed for ZeroTier - run again / check download.zerotier.com.
  pause
  exit /b 1
)
echo installing silently (admin) - join with: zerotier-cli join <network id>
msiexec /i zerotier.msi /qn
if errorlevel 1 (echo [X] MSI install failed - run the msi by hand from %D% & pause & exit /b 1)
echo done - service starts at boot.
pause
