<#
.SYNOPSIS
    Install (or re-install) the "Client360 SharePoint Subscription Renewal" scheduled task.

.DESCRIPTION
    Registers a Windows scheduled task that runs
    deploy\windows\Renew-SharePointSubscription.ps1 every 4 hours.

    CADENCE - why 4 hours:
      The Graph subscription lives 4200 minutes (~70h). `--ensure` renews only once at most 720
      minutes (12h) of that remain, so the window in which a renewal actually happens is 12h wide.
      Running every 4 hours puts THREE attempts inside every window, so the subscription still renews
      after two consecutive failed runs (transient Graph error, reboot, network blip). Every 12h would
      leave no margin at all; every 6h leaves one. The run itself is one Graph list call plus, at most,
      one create/renew, so the extra frequency costs essentially nothing.

      -StartWhenAvailable additionally catches up a run missed while the machine was off.

    IDEMPOTENT: re-running unregisters any existing task of the same name and registers it again from
    the current definition, so repeated installs converge rather than duplicate or fail. It touches
    ONLY the task named below - "Client360 TaxDome Sync" and every other task are left alone.

    ACCOUNT: defaults to the built-in SYSTEM account, which is non-interactive, needs no stored
    password, and survives password rotation. SYSTEM is appropriate here because the work is a local
    process reading a local file and calling an outbound HTTPS API - it needs no network identity, no
    mapped drive, and no user profile. (This is unlike the TaxDome Sync task, which must run as a real
    user because it depends on the interactively-mapped Z: drive.) Pass -UserId/-Password only if
    local policy forbids SYSTEM for outbound-calling tasks.

.PARAMETER InstallRoot
    Client360 installation root. Defaults to C:\Client360.

.PARAMETER TaskName
    Scheduled task name. Defaults to 'Client360 SharePoint Subscription Renewal'.

.PARAMETER IntervalHours
    Repetition interval in hours. Defaults to 4. Values above 12 are refused: they cannot guarantee a
    run inside the renewal window.

.PARAMETER UserId
    Optional account to run as. Defaults to the SYSTEM service account.

.PARAMETER Password
    Optional password for -UserId. Never stored in the repository; supply interactively at install
    time. Omit entirely when using the default SYSTEM account.

.PARAMETER WhatIf
    Show what would be registered without changing anything.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File `
      C:\Client360\deploy\windows\Install-SharePointRenewalTask.ps1

.NOTES
    Requires an elevated PowerShell session. No secret is embedded in the task definition, the command
    line, or this repository: the wrapper reads C:\Client360\app\.env at run time.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]       $InstallRoot   = 'C:\Client360',
    [string]       $TaskName      = 'Client360 SharePoint Subscription Renewal',
    [int]          $IntervalHours = 4,
    [string]       $UserId,
    [SecureString] $Password
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The renewal window is 720 minutes (12h) wide. An interval wider than that cannot guarantee a run
# inside it, which would let the subscription silently expire.
$MaxIntervalHours = 12
if ($IntervalHours -lt 1 -or $IntervalHours -gt $MaxIntervalHours) {
    throw ("IntervalHours must be between 1 and {0}: the subscription renews only in its final 12 " +
           "hours, so a wider interval cannot guarantee a run inside that window." -f $MaxIntervalHours)
}

$wrapper = Join-Path $InstallRoot 'deploy\windows\Renew-SharePointSubscription.ps1'
if (-not (Test-Path -LiteralPath $wrapper)) {
    throw "Renewal wrapper not found: $wrapper"
}

# -WorkingDirectory is C:\Client360 so `python -m scripts.…` resolves the package from the repo root.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $wrapper) `
    -WorkingDirectory $InstallRoot

# Start on the next hour boundary, then repeat indefinitely.
#
# Indefinite repetition is [TimeSpan]::Zero, NOT [TimeSpan]::MaxValue. Task Scheduler's schema
# defines a Repetition Duration of PT0S as "repeat indefinitely", and that is what Zero serializes
# to. MaxValue instead serializes to P99999999DT23H59M59S, which is out of range for the Duration
# element, so Register-ScheduledTask rejects the whole task XML:
#     The task XML contains a value which is incorrectly formatted or out of range.
#     (14,42):Duration:P99999999DT23H59M59S
# That is a REGISTRATION-time failure — the task is never created at all, so automatic renewal
# silently does not exist. (Observed in production installing this task on the 0.13.0 release.)
$startAt = (Get-Date).Date.AddHours((Get-Date).Hour + 1)
$trigger = New-ScheduledTaskTrigger -Once -At $startAt `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration ([TimeSpan]::Zero)

# IgnoreNew: never overlap runs. StartWhenAvailable: catch up a run missed while powered off.
# A 10-minute limit is generous for one Graph call and stops a hung run blocking the next.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)

if ($UserId) {
    $principalArgs = @{ User = $UserId; RunLevel = 'Limited' }
}
else {
    # SYSTEM: non-interactive, no stored password, unaffected by password rotation.
    $principalArgs = @{ User = 'NT AUTHORITY\SYSTEM'; LogonType = 'ServiceAccount'; RunLevel = 'Limited' }
}
$principal = New-ScheduledTaskPrincipal @principalArgs

$description = @"
Renews the Client360 SharePoint Microsoft Graph change-notification subscription.
Runs deploy\windows\Renew-SharePointSubscription.ps1 every $IntervalHours hour(s); the wrapper calls
`python -m scripts.manage_sharepoint_subscription --ensure`, which creates the subscription when
missing, renews it inside its final 12 hours, and otherwise leaves it unchanged.
Does not restart Client360 and does not run a document sync. No secrets in this definition.
"@

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task (every $IntervalHours h)")) {
    # Idempotent: drop any existing registration of THIS task only, then register afresh.
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Output "Existing task found - unregistering before re-registering (idempotent install)."
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    $register = @{
        TaskName    = $TaskName
        Action      = $action
        Trigger     = $trigger
        Settings    = $settings
        Description = $description
    }

    if ($UserId -and $Password) {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password))
        Register-ScheduledTask @register -User $UserId -Password $plain -RunLevel Limited | Out-Null
        # Do not leave the plaintext password lying around in the session.
        Remove-Variable plain -ErrorAction SilentlyContinue
    }
    else {
        Register-ScheduledTask @register -Principal $principal | Out-Null
    }

    Write-Output "Registered '$TaskName': every $IntervalHours hour(s), starting $startAt."
    Write-Output "Wrapper: $wrapper"
    Write-Output "Logs:    $(Join-Path $InstallRoot 'logs\sharepoint-subscription')"
    Write-Output ""
    Write-Output "Verify with:"
    Write-Output "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
    Write-Output "Run once now with:"
    Write-Output "  Start-ScheduledTask -TaskName '$TaskName'"
}
