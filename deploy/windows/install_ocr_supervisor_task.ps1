<#
.SYNOPSIS
    Install or update the continuous parallel-OCR supervisor scheduled task.

.DESCRIPTION
    Creates "Client360 OCR Parallel Supervisor", which runs app.jobs.ocr_supervisor continuously:
    initial OCR -> retry OCR -> classification -> repeat.

    It differs from the existing single-worker task in three ways that matter operationally:

      * LogonType S4U, so it survives logout and reboot. The existing "Client360 OCR Full Corpus"
        task runs Interactive, which means it only runs while a user is signed in - the reason that
        worker has been alive only since the last interactive logon. S4U needs no stored password
        and has no network credentials, which is fine against PostgreSQL on 127.0.0.1.
      * MultipleInstances IgnoreNew plus the supervisor's own advisory lock, so two copies cannot
        sweep at once even if the scheduler and a manual run overlap.
      * RestartCount/RestartInterval, so an unexpected exit is retried rather than leaving the
        corpus stalled until someone notices.

    BEFORE changing anything it exports the CURRENT definition of both tasks to XML, so the previous
    state can be restored exactly with Register-ScheduledTask -Xml.

.PARAMETER Workers
    Parallel OCR workers. Capped at 4 by the supervisor regardless of what is passed here.

.PARAMETER WhatIf
    Show what would happen and export the backup, without registering anything.

.EXAMPLE
    # Preview only - exports the reversible backup, registers nothing
    .\install_ocr_supervisor_task.ps1 -WhatIf

.EXAMPLE
    .\install_ocr_supervisor_task.ps1 -Workers 4
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [int]    $Workers      = 4,
    [string] $ProjectRoot  = 'C:\Client360',
    [string] $OpsDir       = 'C:\Client360Data\ocr-parallel',
    [string] $TaskName     = 'Client360 OCR Parallel Supervisor',
    [string] $LegacyTask   = 'Client360 OCR Full Corpus',
    [string] $BackupDir    = 'C:\Client360Data\ocr-parallel\task-backups',
    [string] $RunAsUser    = $env:USERNAME
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Project venv not found: $python" }
if (-not (Test-Path $ProjectRoot)) { throw "Project root not found: $ProjectRoot" }

# --- 1. reversible backup of the CURRENT task definitions ---------------------------------------
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
foreach ($name in @($LegacyTask, $TaskName)) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        $out = Join-Path $BackupDir ("{0}__{1}.xml" -f ($name -replace '[^\w\-]', '_'), $stamp)
        Export-ScheduledTask -TaskName $name | Set-Content -Path $out -Encoding UTF8
        Write-Host "Backed up '$name' -> $out"
    } else {
        Write-Host "No existing task named '$name' (nothing to back up)"
    }
}

# --- 2. the new task ------------------------------------------------------------------------------
# Working directory MUST be the project root: app/db.py resolves app/.env relative to the cwd.
$arguments = "-m app.jobs.ocr_supervisor --workers $Workers --keep-running --ops-dir `"$OpsDir`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $ProjectRoot

# Start at boot AND at logon, so neither a reboot nor a sign-in leaves the corpus unattended.
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)

# S4U: runs whether or not the user is signed in, with no stored password.
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

if ($PSCmdlet.ShouldProcess($TaskName, 'Register scheduled task')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "Registered '$TaskName' (workers=$Workers, S4U, IgnoreNew, restart every 5m)"
    Write-Host "NOTE: this script does NOT start the task and does NOT disable '$LegacyTask'."
    Write-Host "      Disable the legacy task first, then start this one:"
    Write-Host "        Disable-ScheduledTask -TaskName '$LegacyTask'"
    Write-Host "        Stop-ScheduledTask    -TaskName '$LegacyTask'"
    Write-Host "        Start-ScheduledTask   -TaskName '$TaskName'"
} else {
    Write-Host "WhatIf: would register '$TaskName'"
    Write-Host "  Execute   : $python"
    Write-Host "  Arguments : $arguments"
    Write-Host "  WorkingDir: $ProjectRoot"
    Write-Host "  Principal : $RunAsUser / S4U / Limited"
    Write-Host "  Triggers  : AtStartup, AtLogOn"
    Write-Host "  Settings  : IgnoreNew, no time limit, RestartCount 999 every 5m"
}

# --- 3. rollback instructions ----------------------------------------------------------------------
Write-Host ""
Write-Host "To roll back completely:"
Write-Host "  Stop-ScheduledTask       -TaskName '$TaskName'"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "  Enable-ScheduledTask     -TaskName '$LegacyTask'"
Write-Host "  Start-ScheduledTask      -TaskName '$LegacyTask'"
Write-Host "Or restore an exported definition verbatim:"
Write-Host "  Register-ScheduledTask -TaskName '<name>' -Xml (Get-Content '<backup>.xml' -Raw) -Force"
