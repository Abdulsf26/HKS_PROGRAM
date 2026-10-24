@echo off
title HKS DOCTOR - complete check
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set REP=%~dp0hks_doctor_report.txt
for %%i in ("%~dp0.") do set "BASE=%%~fi"
set PORT=
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\HKS_Client\config.json' -Raw | ConvertFrom-Json).server_url; $h=($u -replace '^[a-z]+://',''); Write-Output $h.Split('/')[0].Split(':')[-1] } catch { }"`) do set PORT=%%i
if "%PORT%"=="" set PORT=6767
if "%PORT%"=="" set PORT=6767
set /a PORT1=%PORT%+1
echo ===== HKS DOCTOR - %date% %time% ===== > "%REP%"
echo.
echo ===== [A] PHONE ADDRESSES - TRY IN THIS ORDER =====>> "%REP%"
echo [A1] ANYWHERE tunnel - works on mobile data, any Wi-Fi>> "%REP%"
if not exist "%BASE%\data\tunnel_url.txt" echo     [--] no tunnel address - for from-anywhere access double-click HKS_FIX_TUNNEL.bat - needs internet, takes 1-2 min>> "%REP%"
if exist "%BASE%\data\tunnel_url.txt" powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\data\tunnel_url.txt' -TotalCount 1).Trim(); $r=Invoke-WebRequest -UseBasicParsing ($u+'/api/version') -TimeoutSec 15; Write-Output ('    OPEN THIS ON YOUR PHONE - WORKS ANYWHERE: ' + $u) } catch { Write-Output ('    [X] tunnel address not answering: ' + $_.Exception.Message) }" >> "%REP%" 2>&1
echo [A2] SAME-WI-FI http door - phone and laptop on the SAME Wi-Fi>> "%REP%"
powershell -NoProfile -Command "try { [System.Net.ServicePointManager]::ServerCertificateValidationCallback={ $true } } catch {}; try { [System.Net.ServicePointManager]::SecurityProtocol=[System.Net.SecurityProtocolType]::Tls12 } catch {}; $adm='no'; try { if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { $adm='yes' } } catch {}; Write-Output ('    doctor running as Administrator: ' + $adm); $errs=@(); $j=$null; foreach ($pt in @(%PORT%, (%PORT%+1))) { if ($j) { break }; foreach ($s in @('http','https')) { try { $j=Invoke-WebRequest -UseBasicParsing ($s+'://127.0.0.1:'+ $pt +'/api/version') -TimeoutSec 3; if ($j) { Write-Output ('    [OK] panel answers at ' + $s + '://' + '127.0.0.1:' + $pt + $(if ($s -eq 'http') { '  <- USE THIS (no certificate warning - EASIEST)' } else { '  <- USE THIS (a certificate warning is NORMAL - tap Proceed/Advanced)' })) } } catch { $errs += ($s + ':' + $pt + ' - ' + $_.Exception.Message) } } }; if (-not $j) { Write-Output '    [X] panel not answering locally - errors:'; $errs | ForEach-Object { Write-Output ('      ' + $_) } } else { $j2=$null; try { $j2=$j.Content | ConvertFrom-Json } catch {}; $ports=@(); if ($j2 -and $j2.main) { $ports += [int]$j2.main }; if ($j2 -and $j2.http_companion) { $ports += [int]$j2.http_companion }; if (-not $ports) { $ports += %PORT% }; $ips=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '192.168.56.*' }; foreach ($ip in $ips) { foreach ($pp in $ports) { foreach ($s in @('http','https')) { $b=($s+'://'+$ip.IPAddress+':'+$pp); try { $null=Invoke-WebRequest -UseBasicParsing ($b+'/api/version') -TimeoutSec 3; Write-Output ('    [ANSWERS] '+$b+'  <- type EXACTLY this on the phone' + $(if ($s -eq 'http') { ' (no certificate warning - EASIEST)' } else { ' (tap Proceed/Advanced)' }) ) } catch { Write-Output ('    [no answer] '+$b) } } } } }" >> "%REP%" 2>&1
echo [A3] a certificate warning is NORMAL on https - tap Proceed / Advanced>> "%REP%"
if exist "%BASE%\data\tunnel_url.txt" (echo           the ANYWHERE address CHANGES after every laptop restart ->> "%REP%" & echo           always take the fresh one from the HKS Phone Access shortcut>> "%REP%")
echo.>> "%REP%"
echo [1/8] installed panel - auto-detects your port from config...
set PORT=
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\HKS_Client\config.json' -Raw | ConvertFrom-Json).server_url; $h=($u -replace '^[a-z]+://',''); Write-Output $h.Split('/')[0].Split(':')[-1] } catch { }"`) do set PORT=%%i
if "%PORT%"=="" set PORT=6767
if "%PORT%"=="" (echo   [X] config.json missing - HKS not installed at %BASE%>> "%REP%") else (echo   config port: %PORT%>> "%REP%")
powershell -NoProfile -Command "try { [System.Net.ServicePointManager]::ServerCertificateValidationCallback={ $true } } catch {}; try { [System.Net.ServicePointManager]::SecurityProtocol=[System.Net.SecurityProtocolType]::Tls12 } catch {}; $found=$false; foreach ($p in (@('%PORT%','%PORT1%','6767','6768','8000') | Select-Object -Unique)) { if (-not $p) { continue }; foreach ($s in @('http','https')) { try { $r=Invoke-WebRequest -UseBasicParsing ($s+'://127.0.0.1:'+ $p +'/api/version') -TimeoutSec 3; Write-Output ('  [OK] panel RUNNING: ' + $s + '://127.0.0.1:' + $p + ' -> ' + $r.Content); $found=$true; if ($p -eq '%PORT%') { $mainOk=$true } } catch {} } }; if (-not $found) { Write-Output '  [X] NO PANEL RUNNING on any port - reboot the laptop, run INSTALL_HKS.bat as Administrator' } else { if (-not $mainOk) { Write-Output ('  [W] MAIN TLS door %PORT% did not answer the probe - the NO-CERT companion door WORKS: phones use http://YOUR-LAPTOP-IP:%PORT1% right now. If this surprised you, test https://YOUR-LAPTOP-IP:%PORT% in the browser (tap Proceed/Advanced) before reinstalling. FIX if truly down: reboot, INSTALL_HKS.bat as Administrator, answer N at Use HTTPS? (single plain door).') } }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [2/8] scheduled tasks>> "%REP%"
for %%T in (HKS-Panel HKS-Agent HKS-SessionAgent) do call :TASKCHK %%T
if exist "%BASE%\hks_install_report.txt" (echo   --- last install task results:>> "%REP%" & type "%BASE%\hks_install_report.txt">> "%REP%"
)
echo.>> "%REP%"
echo [3/8] running HKS processes>> "%REP%"
powershell -NoProfile -Command "$hits=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and (($_.Name -match '^(python|pythonw|cloudflared)(\.exe)?$') -and ( ($_.CommandLine -match 'server\.main|agent\.(main|session_main)|cloudflared|run_|tunnel') -or (-not $_.CommandLine) ) ) }; foreach ($p in $hits) { $cl='(command line hidden - run doctor as Administrator)'; if ($p.CommandLine) { $cl=$p.CommandLine.Substring(0, [Math]::Min(90, $p.CommandLine.Length)) }; Write-Output ('  [?] ' + $p.Name + ' PID ' + $p.ProcessId + '  ' + $cl) }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [4/8] ANYWHERE tunnel - the phone door>> "%REP%"
if exist "%BASE%\bin\cloudflared.exe" (echo   [OK] cloudflared present>> "%REP%") else (echo   [--] no tunnel installed - for from-anywhere access run HKS_FIX_TUNNEL.bat with internet ON>> "%REP%")
if exist "%BASE%\data\tunnel_url.txt" (
  set /p TURL=<"%BASE%\data\tunnel_url.txt"
  echo   current tunnel address: !TURL!>> "%REP%"
  powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\data\tunnel_url.txt' -TotalCount 1).Trim(); $r=Invoke-WebRequest -UseBasicParsing ($u+'/api/version') -TimeoutSec 15; Write-Output '  [OK] TUNNEL LIVE TEST from this laptop: HTTP' $r.StatusCode $r.Content } catch { Write-Output '  [X] tunnel address NOT answering:' $_.Exception.Message }" >> "%REP%" 2>&1
) else (echo   [--] tunnel installed but no address yet - wait 1-2 min or run HKS_FIX_TUNNEL.bat again>> "%REP%")
if exist "%BASE%\data\tunnel_console.log" powershell -NoProfile -Command "Write-Output '  last tunnel log lines:'; Get-Content '%BASE%\data\tunnel_console.log' -Tail 5 | ForEach-Object { Write-Output '    ' $_ }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [5/8] firewall>> "%REP%"
powershell -NoProfile -Command "$r=Get-NetFirewallRule -DisplayName 'HKS Panel' -ErrorAction SilentlyContinue; if ($r -and $r.Enabled -eq 'True') { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (echo   [OK] rule HKS Panel enabled>> "%REP%") else (echo   [X] rule HKS Panel missing or disabled>> "%REP%")
powershell -NoProfile -Command "$r=Get-NetFirewallRule -DisplayName 'HKS Panel HTTP' -ErrorAction SilentlyContinue; if ($r -and $r.Enabled -eq 'True') { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (echo   [OK] rule HKS Panel HTTP enabled>> "%REP%") else (echo   [--] rule HKS Panel HTTP missing - only needed for https mode>> "%REP%")
echo     - rule "HKS Panel" opens the MAIN port %PORT% - rule "HKS Panel HTTP" opens the NO-CERT port %PORT1%>> "%REP%"
echo.>> "%REP%"
echo [6/8] this laptop networks>> "%REP%"
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { Write-Output '  ip:' $_.IPAddress 'on' $_.InterfaceAlias }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [7/8] what the result means>> "%REP%"
echo   If [4] above says TUNNEL LIVE TEST OK: on your phone turn OFF>> "%REP%"
echo   Wi-Fi - use mobile data - and open EXACTLY the address in [4].>> "%REP%"
echo   It WILL open. The address CHANGES after every laptop restart ->> "%REP%"
echo   always take the fresh one from the HKS Phone Access shortcut.>> "%REP%"
echo   If [1] shows NO PANEL RUNNING: reboot once, reinstall as admin.>> "%REP%"
echo   If ONLY the no-cert door :%PORT1% answers while main :%PORT% is dead - the main TLS listener failed.>> "%REP%"
echo   Then: check data\panel_console.log, reboot, reinstall as admin, or answer N at Use HTTPS?.>> "%REP%"
echo.>> "%REP%"
echo [7b/8] last log lines - the fastest diagnosis>> "%REP%"
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\panel_console.log') { Write-Output '  --- panel_console.log:'; Get-Content '%BASE%\data\panel_console.log' -Tail 8 } else { Write-Output '  --- panel_console.log: none yet' }" >> "%REP%" 2>&1
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\agent_console.log') { Write-Output '  --- agent_console.log:'; Get-Content '%BASE%\data\agent_console.log' -Tail 6 } else { Write-Output '  --- agent_console.log: none yet' }" >> "%REP%" 2>&1
echo   NOTE: 'Invalid HTTP request received' lines in panel_console.log are HARMLESS -
echo   they are the doctor's own plain-http probes touching the TLS door.

echo.>> "%REP%"
echo ===== END OF REPORT =====>> "%REP%"
type "%REP%"
clip < "%REP%"
echo.
echo  =============================================
echo   DONE - THE FULL REPORT IS COPIED TO CLIPBOARD.
echo   Go to the chat and PASTE it with Ctrl+V.
echo   Saved also to: %REP%
echo  =============================================
if not defined HKS_GUI_INSTALL pause
exit /b 0

:TASKCHK
powershell -NoProfile -Command "if (Get-ScheduledTask -TaskName '%1' -ErrorAction SilentlyContinue) { Write-Output PS_OK } else { Write-Output PS_NO }" > "%TEMP%\hks_tq.txt" 2>nul
find "PS_OK" "%TEMP%\hks_tq.txt" >nul && echo   [OK] task %1 present>> "%REP%" || goto TQNEXT
goto :eof
:TQNEXT
set "QOUT=" & for /f "delims=" %%A in ('schtasks /Query /TN %1 2^>^&1') do set "QOUT=%%A"
echo !QOUT! | find /i "access level" >nul && echo   [--] task %1 hidden without Administrator - run this doctor AS Administrator to verify>> "%REP%" || goto TQMISS
goto :eof
:TQMISS
echo   [--] task %1 missing - FIX: right-click INSTALL_HKS.bat then Run as Administrator to repair it>> "%REP%"
echo        (if this doctor was NOT run as Administrator, tasks can read as missing when they exist - re-run the doctor as Administrator to be sure)>> "%REP%"
goto :eof
