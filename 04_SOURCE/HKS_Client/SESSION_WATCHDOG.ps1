param([switch]$Register, [switch]$Apply, [switch]$SelfTest)

$ErrorActionPreference = 'Stop'
$client = $PSScriptRoot
$root = (Resolve-Path (Join-Path $client '..')).Path
$log = Join-Path $root 'data\session_watchdog.log'
$statusPorts = 8590..8594

function Get-SessionStatus([scriptblock]$Probe = {
    param($Uri)
    Invoke-RestMethod -Uri $Uri -TimeoutSec 2
}) {
    foreach ($port in $statusPorts) {
        try {
            $status = & $Probe "http://127.0.0.1:$port/status.json"
            if ($status.role -eq 'session') {
                $script:sessionEndpointPort = $port
                if ($script:lastReportedPid -ne [int]$status.pid -or $script:lastReportedPort -ne $port) {
                    Write-WatchLog "Found session endpoint on port $port (PID $($status.pid), connected=$($status.connected))."
                    $script:lastReportedPid = [int]$status.pid
                    $script:lastReportedPort = $port
                }
                return $status
            }
        } catch { }
    }
    if (-not $script:lastNoEndpointLog -or ((Get-Date) - $script:lastNoEndpointLog).TotalSeconds -ge 60) {
        Write-WatchLog 'No role=session endpoint found on ports 8590-8594.'
        $script:lastNoEndpointLog = Get-Date
    }
    return $null
}

function Test-SessionOwner([object]$Status) {
    if ($null -eq $Status -or -not $Status.pid) { return $false }
    try {
        $process = Get-Process -Id ([int]$Status.pid) -ErrorAction Stop
        return -not $process.HasExited
    } catch { return $false }
}

function Write-WatchLog([string]$Message) {
    try {
        $dir = Split-Path -Parent $log
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 2MB) {
            Move-Item -LiteralPath $log -Destination ($log + '.old') -Force
        }
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    } catch { }
}

if ($SelfTest) {
    $cases = @(
        @{ name = 'session on 8590'; rows = @{ 8590 = @{ role='session'; pid=1 } }; expected = 8590 },
        @{ name = 'session on 8591'; rows = @{ 8590 = @{ role='svc'; pid=2 }; 8591 = @{ role='session'; pid=3 } }; expected = 8591 },
        @{ name = 'session on 8592'; rows = @{ 8590 = @{ role='svc'; pid=2 }; 8591 = @{ role='other'; pid=4 }; 8592 = @{ role='session'; pid=5 } }; expected = 8592 }
    )
    foreach ($case in $cases) {
        $rows = $case.rows
        $probe = { param($uri) $port = [int](([uri]$uri).Port); if ($rows.ContainsKey($port)) { return $rows[$port] }; throw 'not listening' }.GetNewClosure()
        $script:sessionEndpointPort = $null
        $found = Get-SessionStatus -Probe $probe
        if ($null -eq $found -or $script:sessionEndpointPort -ne $case.expected) { throw "Self-test failed: $($case.name)" }
        Write-Output "PASS $($case.name)"
    }
    exit 0
}

if ($Register) {
    $taskName = 'HKS-SessionAgent'
    if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
        throw "Scheduled task '$taskName' is not registered. Run the HKS setup repair first."
    }
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $args = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $client
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
    Write-WatchLog 'Registered hidden watchdog as HKS-SessionAgent task action.'
    if ($Apply) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $taskName
        Write-WatchLog 'Restarted HKS-SessionAgent with the watchdog action.'
    }
    exit 0
}

$env:HKS_DATA_DIR = Join-Path $root 'data'
$env:PYTHONUNBUFFERED = '1'
$python = $null
$pythonCandidates = @((Join-Path $client '.venv\Scripts\pythonw.exe'),
                      (Join-Path $root 'venv\Scripts\pythonw.exe'))
foreach ($candidate in $pythonCandidates) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $probePython = $candidate -replace 'pythonw\.exe$', 'python.exe'
    if (-not (Test-Path -LiteralPath $probePython)) { continue }
    & $probePython -c 'import websockets, psutil' *> $null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    Write-WatchLog "Interpreter at $candidate is missing a required package (websockets/psutil)."
}
if (-not $python) {
    Write-WatchLog 'No usable client/root virtualenv found; install client requirements before starting the desktop agent.'
}
$retrySeconds = 5
while ($true) {
    if (-not $python -or -not (Test-Path -LiteralPath $python)) {
        Write-WatchLog 'Agent Python is unavailable or incomplete; retrying in 60 seconds.'
        Start-Sleep -Seconds 60
        continue
    }

    Write-WatchLog 'Starting interactive desktop agent.'
    try {
        $proc = Start-Process -FilePath $python -ArgumentList '-m agent.session_main' `
            -WorkingDirectory $client -WindowStyle Hidden -PassThru
        $startedAt = Get-Date
        $missingSince = $null
        $lastProbe = [datetime]::MinValue
        $reason = 'agent process exited'

        while (-not $proc.HasExited) {
            Start-Sleep -Seconds 5
            $proc.Refresh()
            if ($proc.HasExited) { break }
            if (((Get-Date) - $lastProbe).TotalSeconds -lt 15) { continue }
            $lastProbe = Get-Date
            try {
                $status = Get-SessionStatus
                if (Test-SessionOwner $status) {
                    $missingSince = $null
                    if ($status.connected -eq $true) {
                        continue
                    }
                    # Remote server loss is handled by Connection.start_forever.
                    # Keep the local agent and its streams alive while it retries.
                    continue
                }
                if ($null -eq $missingSince) { $missingSince = Get-Date }
                if (((Get-Date) - $missingSince).TotalSeconds -ge 60 -and ((Get-Date) - $startedAt).TotalSeconds -ge 60) {
                    $reason = 'session agent local health endpoint/process was unavailable for 60 seconds'
                    break
                }
            } catch {
                if ($null -eq $missingSince) { $missingSince = Get-Date }
                if (((Get-Date) - $missingSince).TotalSeconds -ge 60 -and ((Get-Date) - $startedAt).TotalSeconds -ge 60) {
                    $reason = 'session agent local health endpoint was unavailable for 60 seconds'
                    break
                }
            }
        }

        if (-not $proc.HasExited) {
            Write-WatchLog "$reason; restarting the process tree."
            & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $proc.Id /T /F *> $null
            Start-Sleep -Seconds 2
            $retrySeconds = 5
        } else {
            $code = $proc.ExitCode
            if ($code -eq 42) {
                # Another instance owns the session lock. A server disconnect
                # does not mean that local owner is dead; only retry if its local
                # role endpoint/process disappears.
                $ownerMissingSince = $null
                while ($true) {
                    Start-Sleep -Seconds 5
                    $existing = Get-SessionStatus
                    if (Test-SessionOwner $existing) { $ownerMissingSince = $null; continue }
                    if ($null -eq $ownerMissingSince) { $ownerMissingSince = Get-Date }
                    if (((Get-Date) - $ownerMissingSince).TotalSeconds -ge 30) { break }
                }
                $retrySeconds = 1
                continue
            } elseif ($code -eq 43) { $retrySeconds = 300 }
            else { $retrySeconds = 5 }
            Write-WatchLog "Agent exited with code $code; retrying in $retrySeconds seconds."
        }
    } catch {
        Write-WatchLog ("Could not start/monitor agent: " + $_.Exception.Message)
        $retrySeconds = 15
    }
    Start-Sleep -Seconds $retrySeconds
}
