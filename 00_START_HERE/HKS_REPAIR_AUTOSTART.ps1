# Re-register only the persistent Windows boot tasks. This does not stop or
# restart any running HKS process. Run elevated after an uninstall or if tasks
# are missing from Task Scheduler.
$ErrorActionPreference = 'Stop'
$root = 'C:\HKS_PROGRAM'
$log = Join-Path $root 'data\autostart_repair.log'

function Write-RepairLog([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Output $line
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Administrator rights are required. Run this repair as administrator.'
    }
    $panel = Join-Path $root 'HKS_AdminPanel\start_panel.bat'
    $agent = Join-Path $root 'run_agent.bat'
    $pythonw = Join-Path $root 'venv\Scripts\pythonw.exe'
    $client = Join-Path $root 'HKS_Client'
    foreach ($required in @($panel, $agent, $pythonw, (Join-Path $client 'agent\watchdog.py'))) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Required HKS file is missing: $required. Repair the installation before registering startup."
        }
    }

    $cmd = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $settings.IdleSettings.StopOnIdleEnd = $false
    $settings.IdleSettings.RestartOnIdle = $false
    $system = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

    $definitions = @(
        @{ Name='HKS-Panel'; Path=$panel; WorkingDirectory=(Split-Path -Parent $panel); Args=$null },
        @{ Name='HKS-Agent'; Path=$agent; WorkingDirectory=$root; Args=$null },
        @{ Name='HKS-Watchdog'; Path=$pythonw; WorkingDirectory=$client; Args='-m agent.watchdog' }
    )
    foreach ($definition in $definitions) {
        if ($definition.Args) {
            $action = New-ScheduledTaskAction -Execute $definition.Path -Argument $definition.Args `
                -WorkingDirectory $definition.WorkingDirectory
        } else {
            $arguments = '/d /c call "{0}"' -f $definition.Path
            $action = New-ScheduledTaskAction -Execute $cmd -Argument $arguments `
                -WorkingDirectory $definition.WorkingDirectory
        }
        $trigger = New-ScheduledTaskTrigger -AtStartup
        Register-ScheduledTask -TaskName $definition.Name -Action $action -Trigger $trigger `
            -Principal $system -Settings $settings -Force | Out-Null

        $saved = Get-ScheduledTask -TaskName $definition.Name -ErrorAction Stop
        $savedAction = @($saved.Actions)[0]
        $hasBootTrigger = [bool]($saved.Triggers | Where-Object {
            $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger'
        })
        $actionMatches = if ($definition.Args) {
            $savedAction.Execute -eq $definition.Path -and $savedAction.Arguments -eq $definition.Args
        } else {
            $savedAction.Execute -eq $cmd -and $savedAction.Arguments -like ('*' + $definition.Path + '*')
        }
        if ($saved.Principal.UserId -notin @('SYSTEM', 'NT AUTHORITY\SYSTEM') -or
            -not $saved.Settings.Enabled -or -not $hasBootTrigger -or -not $actionMatches) {
            throw "Windows did not retain the expected SYSTEM/AtStartup action for $($definition.Name)."
        }
        Write-RepairLog "VERIFIED $($definition.Name): enabled SYSTEM task, AtStartup trigger; existing processes left untouched."
    }
    Write-RepairLog 'Repair complete. Panel/service/watchdog will auto-start on the next Windows boot.'
    exit 0
} catch {
    try { Write-RepairLog ("FAILED: " + $_.Exception.Message) } catch { Write-Error $_ }
    exit 1
}
