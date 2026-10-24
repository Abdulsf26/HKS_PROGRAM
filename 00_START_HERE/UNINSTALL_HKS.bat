@echo off
title HKS - Complete Uninstall
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
setlocal enabledelayedexpansion
echo.
echo  ==============================================
echo   HKS - COMPLETE UNINSTALL (only removes HKS)
echo  ==============================================
echo   This will remove:
echo    - HKS tasks: HKS-Panel, HKS-Agent, HKS-SessionAgent, HKS-Addon-*
echo    - HKS firewall rules (all rules named "HKS *")
echo    - C:\HKS_PROGRAM (program files)
echo   Your data (logs/captures/db in C:\HKS_PROGRAM\data*) is INSIDE that
echo   folder - you will be asked if you want a backup first.
echo.
if /i "%HKS_GUI_UNINSTALL%"=="YES" (set "CONF=YES") else set /p CONF=Type YES to continue:
if /i not "%CONF%"=="YES" (echo cancelled & pause & exit /b)
echo [pre] Writing uninstall audit (proof of what was removed)...
powershell -NoProfile -Command "$d=Join-Path $env:ProgramData 'HKS-Audit'; New-Item -ItemType Directory -Force -Path $d | Out-Null; $envf='C:\HKS_PROGRAM\HKS_AdminPanel\.env'; $h=$null; if (Test-Path $envf) { $sha=New-Object System.Security.Cryptography.SHA256Managed; $fs=[IO.File]::OpenRead($envf); $h=([BitConverter]::ToString($sha.ComputeHash($fs)) -replace '-','').Substring(0,16); $fs.Close() }; $o=[ordered]@{when=(Get-Date -Format o); who=$env:USERNAME; program_hash=$h}; $json=$o | ConvertTo-Json; [IO.File]::WriteAllText((Join-Path $d ('uninstall-' + (Get-Date -Format yyyyMMdd_HHmmss) + '.json')), $json); Write-Output '  audit written to ProgramData\HKS-Audit'" >nul 2>&1

echo.
echo [1/8] Backing up (optional)...
if defined HKS_GUI_UNINSTALL (set "BAK=%HKS_GUI_BACKUP%") else set /p BAK=Save a backup zip of settings+database to your Desktop first? (y/N):
if /i "%BAK%"=="y" (
  powershell -NoProfile -Command "Compress-Archive -Path 'C:\HKS_PROGRAM\data*','C:\HKS_PROGRAM\HKS_AdminPanel\.env' -DestinationPath \"$env:USERPROFILE\Desktop\HKS_backup_$(Get-Date -Format yyyyMMdd_HHmm).zip\" -Force -ErrorAction SilentlyContinue; Get-ChildItem \"$env:USERPROFILE\Desktop\HKS_backup_*.zip\" | Select-Object -First 1 | ForEach-Object { Write-Output ('  backup: ' + $_.FullName) }"
)

echo [2/8] Ending scheduled tasks...
for %%T in ("HKS-Panel" "HKS-Agent" "HKS-SessionAgent" "HKSAgent") do (
  schtasks /End /TN %%T >nul 2>&1
  schtasks /Delete /TN %%T /F >nul 2>&1
)
powershell -NoProfile -Command "Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like 'HKS-*' } | ForEach-Object { Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue }" >nul 2>&1

echo [3/8] Stopping HKS processes - VERIFIED kill, not just text...
powershell -NoProfile -Command "$pat='server\.main|agent\.(main|session_main)|cloudflared.*trycloudflare'; foreach ($pass in 1,2,3) { $hits = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match $pat }; if (-not $hits) { Write-Output ('  pass ' + $pass + ': nothing left to kill'); break }; Write-Output ('  pass ' + $pass + ': killing ' + @($hits).Count + ' process(es): ' + ((@($hits) | ForEach-Object { $_.Name + '(PID ' + $_.ProcessId + ')' }) -join ', ')); foreach ($p in $hits) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 3 }; $left = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match $pat }; if ($left) { Write-Output ('  [X] STILL ALIVE AFTER 3 PASSES - a reboot will clear them: ' + ((@($left) | ForEach-Object { $_.Name + '(PID ' + $_.ProcessId + ')' }) -join ', ')) } else { Write-Output '  [OK] VERIFIED: every HKS process is dead' }"
REM Narrow process matcher intentionally excludes generic HKS_PROGRAM paths so it cannot kill the GUI setup launcher itself.
sc stop HKSAgent >nul 2>&1
sc delete HKSAgent >nul 2>&1
timeout /t 3 /nobreak >nul

echo [4/8] Removing startup-folder fallbacks...
powershell -NoProfile -Command "$startup=[Environment]::GetFolderPath('Startup'); Get-ChildItem $startup -Filter 'HKS*' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue" >nul 2>&1

echo [5/8] Removing firewall rules...
powershell -NoProfile -Command "Get-NetFirewallRule -DisplayName 'HKS*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue" >nul 2>&1
netsh advfirewall firewall delete rule name="HKS Panel" >nul 2>&1
netsh advfirewall firewall delete rule name="HKS Panel HTTP" >nul 2>&1

echo [6/8] Removing desktop shortcuts...
del /q "%USERPROFILE%\Desktop\HKS Control Center.url" >nul 2>&1
del /q "%USERPROFILE%\Desktop\HKS Status (this laptop).url" >nul 2>&1

echo [7/8] Removing C:\HKS_PROGRAM ...
cd /d C:\
rmdir /s /q "C:\HKS_PROGRAM" >nul 2>&1
if exist "C:\HKS_PROGRAM" (
  echo   folder locked - reboot and run this file once more to finish.
) else (
  echo   removed.
)

echo [8/8] Verifying (every HKS trace must be gone)...
powershell -NoProfile -Command "$l=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 3332,3333,3334,8000,8001 -and $_.OwningProcess -ne 0 }; if ($l) { Write-Output '  [X] something still listens on HKS ports - see PID:' $l.OwningProcess } else { Write-Output '  [OK] no HKS panel listening' }"
schtasks /Query /TN "HKS-Panel" >nul 2>&1 && (echo   [X] HKS-Panel task still present) || (echo   [OK] no HKS scheduled tasks)
sc query HKSAgent >nul 2>&1 && (echo   [X] HKSAgent service still present) || (echo   [OK] no HKS service)
powershell -NoProfile -Command "$r=netsh advfirewall firewall show rule name=all | Select-String 'HKS' -SimpleMatch; if ($r) { Write-Output '  [X] HKS firewall rules remain:' } else { Write-Output '  [OK] no HKS firewall rules' }"
if exist "C:\HKS_PROGRAM" (
  echo   [X] C:\HKS_PROGRAM still locked - scheduling delete at next reboot.
  set "HPY2=python"
  if exist "C:\HKS_PROGRAM\venv\Scripts\python.exe" set "HPY2=C:\HKS_PROGRAM\venv\Scripts\python.exe"
  %HPY2% -c "import ctypes; ctypes.windll.kernel32.MoveFileExW(r'C:\HKS_PROGRAM', None, 0x4)" >nul 2>&1 && (echo   folder will vanish at next reboot) || (echo   could not schedule - delete by hand after reboot)
) else (
  echo   [OK] C:\HKS_PROGRAM removed
)
echo.
if defined HKS_GUI_UNINSTALL (set "CLEAN=N") else set /p CLEAN=Also remove HKS zip/parts files from your Downloads? (y/N):
if /i "%CLEAN%"=="y" (
  del /q "%USERPROFILE%\Downloads\HKS_part_*.txt" >nul 2>&1
  del /q "%USERPROFILE%\Downloads\HKS_PROGRAM.zip" >nul 2>&1
  del /q "%USERPROFILE%\Downloads\HKS_PROGRAM.zip" >nul 2>&1
  del /q "%USERPROFILE%\Downloads\HKS_PROGRAM_*.txt" >nul 2>&1
  del /q "%USERPROFILE%\Downloads\hks_export_*.zip" >nul 2>&1
  del /q "%USERPROFILE%\Downloads\HKS_sel_*.zip" >nul 2>&1
  del /q "%USERPROFILE%\Downloads\HKS_backup_*.zip" >nul 2>&1
  echo   Downloads cleaned (only HKS-named files touched).
)
echo.
echo  HKS uninstall finished. Removal proof: %ProgramData%\HKS-Audit
if not defined HKS_GUI_INSTALL pause
exit /b 0
