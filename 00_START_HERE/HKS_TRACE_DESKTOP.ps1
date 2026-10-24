# Read-only HKS desktop-agent diagnostics. Writes one local report under DATA.
$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\HKS_PROGRAM'
$data = Join-Path $root 'data'
if (-not (Test-Path -LiteralPath $data)) { $data = Join-Path $PSScriptRoot 'DATA' }
New-Item -ItemType Directory -Path $data -Force | Out-Null
$report = Join-Path $data ('DESKTOP_TRACE_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.txt')
$lines = [System.Collections.Generic.List[string]]::new()
function Add-Line([string]$Text='') { $lines.Add($Text) }
function Scrub([string]$Text) {
  $s = [string]$Text
  $s = $s -replace '(?i)(authorization\s*[:=]\s*bearer\s+)\S+', '$1<redacted>'
  $s = $s -replace '(?i)((?:token|password|secret)\s*[:=]\s*)[^\s,;]+', '$1<redacted>'
  return $s
}
Add-Line 'HKS DESKTOP AGENT TRACE (read-only)'
Add-Line ('Created: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'))
Add-Line ('Computer: ' + $env:COMPUTERNAME + ' | User: ' + $env:USERNAME)
Add-Line ('Windows: ' + [Environment]::OSVersion.VersionString)
Add-Line ('Install root: ' + $root)
Add-Line ''
Add-Line '=== PANEL ==='
try {
  $v = Invoke-RestMethod -Uri 'http://127.0.0.1:6768/api/version' -TimeoutSec 3
  Add-Line ("API version: {0}; HTTPS port: {1}; HTTP port: {2}" -f $v.version,$v.main,$v.http_companion)
} catch { Add-Line ('Panel version endpoint: ' + (Scrub $_.Exception.Message)) }
Add-Line ''
Add-Line '=== HKS SCHEDULED TASKS ==='
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -like '*HKS*' })
if (-not $tasks.Count) { Add-Line 'No HKS scheduled tasks found.' }
foreach ($task in $tasks) {
  $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath
  $action = ($task.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join ' | '
  Add-Line ("{0}{1} | {2} | User={3} | Last run {4} | result=0x{5:X8}" -f $task.TaskPath,$task.TaskName,$task.State,$task.Principal.UserId,$info.LastRunTime,[uint32]$info.LastTaskResult)
  Add-Line ('  Action: ' + (Scrub $action))
}
Add-Line ''
Add-Line '=== LOCAL AGENT STATUS PORTS ==='
$ownerIds = @{}
foreach ($port in 8590..8594) {
  $owner = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $port | Select-Object -First 1
  if (-not $owner) { Add-Line ("Port {0}: not listening" -f $port); continue }
  $ownerIds[[int]$owner.OwningProcess] = $true
  try {
    $status = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/status.json" -f $port) -TimeoutSec 2
    Add-Line ("Port {0}: role={1}; connected={2}; pid={3}; latency={4}ms; commands={5}; errors={6}" -f $port,$status.role,$status.connected,$status.pid,$status.latency_ms,$status.cmds,$status.errors)
  } catch { Add-Line ("Port {0}: listener PID {1}; status failed: {2}" -f $port,$owner.OwningProcess,(Scrub $_.Exception.Message)) }
}
Add-Line ''
Add-Line '=== PANEL AND AGENT PROCESS OWNERS ==='
$ports = @(6767,6768,8590,8591,8592,8593,8594)
foreach ($port in $ports) {
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port)
  foreach ($listener in $listeners) { $ownerIds[[int]$listener.OwningProcess] = $true }
}
foreach ($procId in @($ownerIds.Keys | Sort-Object)) {
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId"
  if ($p) {
    Add-Line ("PID {0}; parent {1}; session {2}; {3}; {4}" -f $p.ProcessId,$p.ParentProcessId,$p.SessionId,$p.Name,$p.ExecutablePath)
  }
}
Add-Line ''
Add-Line '=== SESSION AGENT / WATCHDOG PROCESSES ==='
$sessionProcesses = @(Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'agent\.session_main') -or
  ($_.Name -match '^powershell' -and $_.CommandLine -match 'SESSION_WATCHDOG\.ps1')
})
if (-not $sessionProcesses.Count) { Add-Line 'No session agent or watchdog process found.' }
foreach ($p in $sessionProcesses) {
  Add-Line ("PID {0}; parent {1}; session {2}; {3}; {4}" -f $p.ProcessId,$p.ParentProcessId,$p.SessionId,$p.Name,(Scrub $p.CommandLine))
}
Add-Line ''
Add-Line '=== PANEL MACHINE FLAGS (if Python/SQLite is available) ==='
$py = Join-Path $root 'venv\Scripts\python.exe'
if (Test-Path -LiteralPath $py) {
  $probe = @'
import sqlite3, time
p=r'C:\HKS_PROGRAM\data_panel\hks.db'
try:
 c=sqlite3.connect(p,timeout=2)
 for r in c.execute('select machine_id,name,online_svc,online_session,last_seen from machines'):
  print('machine={0}; name={1}; service={2}; desktop={3}; last_seen_age_s={4}'.format(r[0],r[1],r[2],r[3],max(0,int(time.time()-r[4]))))
except Exception as e: print('DB probe failed: '+str(e))
'@
  $result = $probe | & $py - 2>&1
  foreach ($line in $result) { Add-Line (Scrub $line) }
} else { Add-Line 'Bundled Python not found; skipped SQLite probe.' }
Add-Line ''
Add-Line '=== RECENT DESKTOP LOG SIGNALS ==='
$sessionLog = Join-Path $data 'hks_session.log'
if (Test-Path -LiteralPath $sessionLog) {
  $matches = Get-Content -LiteralPath $sessionLog -Tail 1200 | Select-String -Pattern 'ERROR|Traceback|connection lost|agent starting|connected to server|local status page'
  if (-not $matches) { Add-Line 'No matching recent session log lines.' }
  foreach ($m in ($matches | Select-Object -Last 30)) { Add-Line ('  ' + (Scrub $m.Line)) }
} else { Add-Line 'Session log not found.' }
foreach ($logName in @('session_watchdog.log','session_console.log')) {
  $logPath = Join-Path $data $logName
  Add-Line ("--- {0} ---" -f $logName)
  if (Test-Path -LiteralPath $logPath) {
    foreach ($line in (Get-Content -LiteralPath $logPath -Tail 30)) { Add-Line ('  ' + (Scrub $line)) }
  } else { Add-Line '  Log not found.' }
}
Add-Line ''
Add-Line '=== MICROSOFT DEFENDER DETECTIONS (last 72 hours) ==='
try {
  $detections = @(Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -gt (Get-Date).AddHours(-72) })
  if (-not $detections.Count) { Add-Line 'No Defender threat detections in the last 72 hours.' }
  foreach ($d in $detections) {
    Add-Line ("{0} | {1} | actionSucceeded={2} | resources={3}" -f $d.InitialDetectionTime,$d.ThreatID,$d.ActionSuccess,(Scrub ($d.Resources -join ', ')))
  }
} catch { Add-Line ('Defender detection query unavailable: ' + (Scrub $_.Exception.Message)) }
Add-Line ''
Add-Line '=== DEFENDER SERVICE ==='
try { $def = Get-Service WinDefend; Add-Line ("WinDefend: {0} ({1})" -f $def.Status,$def.StartType) }
catch { Add-Line ('WinDefend status unavailable: ' + (Scrub $_.Exception.Message)) }
Add-Line ''
Add-Line 'This report is local. Review it before sharing; it may contain machine names and local file paths.'
$lines | Set-Content -LiteralPath $report -Encoding UTF8
Write-Output $report
