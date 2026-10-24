# Per-user recovery for missing HKS scheduled tasks. Runs at interactive logon;
# SYSTEM boot tasks remain the preferred production path.
$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\HKS_PROGRAM'
if (-not (Test-Path -LiteralPath (Join-Path $root 'HKS_AdminPanel\start_panel.bat'))) { exit 0 }
function Test-HksListener([int]$Port) {
  $client = [System.Net.Sockets.TcpClient]::new()
  try { $ar=$client.BeginConnect('127.0.0.1',$Port,$null,$null); if(-not $ar.AsyncWaitHandle.WaitOne(600)){return $false}; $client.EndConnect($ar); return $true }
  catch { return $false } finally { $client.Dispose() }
}
function Start-HksScript([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    Start-Process -FilePath $env:ComSpec -ArgumentList @('/d','/c',('call "{0}"' -f $Path)) -WorkingDirectory $root -WindowStyle Hidden
  }
}
function Get-HksAgentStatus([string]$Role) {
  foreach ($port in 8590..8594) {
    try {
      $status = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/status.json" -f $port) -TimeoutSec 2
      if ($status.role -eq $Role -and $status.pid) {
        try { Get-Process -Id ([int]$status.pid) -ErrorAction Stop | Out-Null; return $status }
        catch { }
      }
    } catch { }
  }
  return $null
}
# Current panel ports are 6767 (HTTPS) and 6768 (HTTP companion). The old
# 8765/8766 probe caused every logon to start a second panel instance.
if (-not (Test-HksListener 6767) -and -not (Test-HksListener 6768)) {
  Start-HksScript (Join-Path $root 'HKS_AdminPanel\start_panel.bat')
  Start-Sleep -Seconds 3
}
if (-not (Get-HksAgentStatus 'svc')) { Start-HksScript (Join-Path $root 'run_agent.bat') }
if (-not (Get-HksAgentStatus 'session')) { Start-HksScript (Join-Path $root 'run_session_agent.bat') }

# Keep a hidden recovery supervisor alive for the interactive agent. The
# scheduled task is not always present (or may still have a direct-agent
# action); this per-user fallback restarts the agent if it exits mid-session.
$watchdog = Join-Path $root 'HKS_Client\SESSION_WATCHDOG.ps1'
if (Test-Path -LiteralPath $watchdog) {
  $needle = [regex]::Escape($watchdog)
  $running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match $needle } |
    Select-Object -First 1
  if (-not $running) {
    $args = @('-NoLogo','-NoProfile','-NonInteractive','-WindowStyle','Hidden',
      '-ExecutionPolicy','Bypass','-File',('"{0}"' -f $watchdog))
    Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden
  }
}
