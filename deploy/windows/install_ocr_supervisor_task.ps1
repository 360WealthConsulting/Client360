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

.PARAMETER AttestUncRoot
    A UNC root (\\server\share) the MACHINE account has been granted access to. An S4U task carries
    no network credentials, so a required UNC root fails closed by default: testing it interactively
    proves only that the OPERATOR can reach it. This attests the credential question and nothing
    else - the share is still checked for existence and readability, so it can never be used to skip
    missing-source validation. Repeatable.

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
    [string] $RunAsUser    = $env:USERNAME,
    [string[]] $AttestUncRoot = @()
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Project venv not found: $python" }
if (-not (Test-Path $ProjectRoot)) { throw "Project root not found: $ProjectRoot" }

# --- 0. S4U viability preflight -------------------------------------------------------------------
# An S4U task runs without the user's logon session. Two things can break that are fine under an
# Interactive task: credentials for NETWORK resources, and per-session MAPPED DRIVES. Both are
# checked here, and a failure STOPS rather than registering a task that silently cannot read.
Write-Host "== S4U viability preflight =="

# (a) PostgreSQL. Password auth over loopback does not depend on the Windows identity, so the check
#     is simply: can this interpreter connect and read?
$dbProbe = @'
import os, sys
from urllib.parse import urlsplit
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
load_dotenv("app/.env")
url = os.getenv("DATABASE_URL")
if not url:
    print("FAIL no DATABASE_URL"); sys.exit(1)
s = urlsplit(url)
if not s.password:
    print("WARN connection has no password: integrated auth may not survive S4U")
try:
    e = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
    with e.connect() as c:
        c.execute(text("SELECT 1"))
    print(f"OK database reachable at {s.hostname}:{s.port or 5432} (password auth={bool(s.password)})")
except Exception as exc:
    print(f"FAIL database unreachable: {type(exc).__name__}: {exc}"); sys.exit(1)
'@
Push-Location $ProjectRoot
$dbResult = $dbProbe | & $python -
$dbExit = $LASTEXITCODE
Pop-Location
Write-Host "  $dbResult"
if ($dbExit -ne 0) { throw "S4U preflight failed: PostgreSQL is not reachable from this context." }

# (b) Storage. The roots that must be readable are the ones CONFIGURATION declares as permanent OCR
#     storage and the ones the DATABASE actually references - NOT a list of drive letters. The list
#     this block used to carry, @('C','D','T'), was an inventory of letters someone once used: nothing
#     else in the tree mentions T:, so the check could fail a host that was perfectly able to run the
#     supervisor, while passing one that was not (a required UNC share, which Win32_LogicalDisk cannot
#     even see). app.deploy.ocr_source_roots is the authority and explains each verdict; this block's
#     only job is to hand it the host volume table, which is the part that genuinely needs CIM.
$logicalDisks = @(Get-CimInstance Win32_LogicalDisk -ErrorAction Stop)
$mappedDisks   = @(Get-CimInstance Win32_MappedLogicalDisk -ErrorAction SilentlyContinue)
$inventory = @{
    volumes = @($logicalDisks | ForEach-Object {
        @{ anchor        = $_.DeviceID
           drive_type    = [int]$_.DriveType
           provider_name = $_.ProviderName
           file_system   = $_.FileSystem }
    })
    # A drive present in Win32_MappedLogicalDisk belongs to THIS interactive logon session. It is
    # enumerated separately because a mapping can be reported with a DriveType that looks benign.
    mapped_anchors = @($mappedDisks | ForEach-Object { $_.DeviceID })
} | ConvertTo-Json -Depth 5 -Compress

$rootArgs = @('-m', 'app.deploy.ocr_source_roots', '--project-root', $ProjectRoot)
foreach ($unc in $AttestUncRoot) { $rootArgs += @('--attest-unc', $unc) }

Push-Location $ProjectRoot
$rootResult = $inventory | & $python @rootArgs
$rootExit = $LASTEXITCODE
Pop-Location
$rootResult | ForEach-Object { Write-Host "  $_" }
if ($rootExit -ne 0) {
    throw "S4U preflight failed: one or more REQUIRED OCR source roots cannot be read by an S4U task. Stopping."
}
Write-Host "== preflight passed =="
Write-Host ""

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
    # Registration must TERMINATE on failure and must never be reported as success it did not have.
    # The old line piped the result to Out-Null and then printed "Registered ..." unconditionally, so
    # an operator lacking the rights to register a task was told the task existed. The ScheduledTasks
    # cmdlets are CIM-backed and do not all surface a failure as a terminating error, so this does not
    # lean on $ErrorActionPreference: it asks for -ErrorAction Stop, catches and rethrows, KEEPS the
    # returned object instead of discarding it, and re-queries the scheduler before claiming anything.
    try {
        $registered = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
            -Principal $principal -Settings $settings -Force -ErrorAction Stop
    } catch {
        throw "Failed to register '$TaskName': $($_.Exception.Message)"
    }
    if (-not $registered) {
        throw "Failed to register '$TaskName': the scheduler returned no task (commonly Access Denied - re-run as Administrator)."
    }
    $verified = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $verified) {
        throw "Registration of '$TaskName' reported no error but the task does not exist. Nothing was installed."
    }
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
