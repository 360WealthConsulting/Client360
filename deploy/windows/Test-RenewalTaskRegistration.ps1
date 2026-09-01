#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Windows-side acceptance test for the SharePoint renewal scheduled task.

.DESCRIPTION
    The renewal task has now been rejected at REGISTRATION twice, each time by a value that only
    Task Scheduler validates:

        [TimeSpan]::MaxValue -> Duration P99999999DT23H59M59S   (above the accepted range)
        [TimeSpan]::Zero     -> Duration PT0S                   (below the schema's PT1M floor)

    Both produced 0x80041318 ("The task XML contains a value which is incorrectly formatted or out
    of range"), and both passed every static check, because the value is only rejected by the
    service when the XML is parsed. Nothing that runs on CI or a developer Mac can catch that class
    of defect - only Windows can.

    So this script runs the REAL installer (no reconstructed copy of its trigger/settings, which
    would drift) against a DISPOSABLE task name, exports the XML the service actually stored,
    asserts the properties that matter, and unregisters it again. It proves registration succeeds
    AND that the stored definition says what the deployment needs it to say.

    It never touches the production task: it only ever addresses $TaskName, which defaults to a
    self-test name distinct from the real one. The disposable task is disabled the moment it is
    registered, so it cannot fire while the test runs, and it is removed in a finally block.

.PARAMETER InstallRoot
    Client360 installation root. Defaults to C:\Client360.

.PARAMETER TaskName
    Disposable task name to register under. MUST NOT be the production task name; the script
    refuses to run if it is.

.OUTPUTS
    Exit code 0 when every assertion passes, 1 otherwise.

.EXAMPLE
    .\Test-RenewalTaskRegistration.ps1
#>
[CmdletBinding()]
param(
    [string] $InstallRoot = 'C:\Client360',
    [string] $TaskName    = 'Client360 SharePoint Subscription Renewal SELFTEST'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PRODUCTION_TASK = 'Client360 SharePoint Subscription Renewal'
if ($TaskName -eq $PRODUCTION_TASK) {
    throw "Refusing to run against the production task name '$PRODUCTION_TASK'. This test registers and DELETES its task."
}

$installer     = Join-Path $InstallRoot 'deploy\windows\Install-SharePointRenewalTask.ps1'
$expectedWrap  = Join-Path $InstallRoot 'deploy\windows\Renew-SharePointSubscription.ps1'
$IntervalHours = 4

$failures = New-Object System.Collections.Generic.List[string]
function Check {
    param([string] $What, [bool] $Ok, [string] $Detail = '')
    if ($Ok) { Write-Host "  PASS  $What" -ForegroundColor Green }
    else     { Write-Host "  FAIL  $What $Detail" -ForegroundColor Red; $failures.Add($What) | Out-Null }
}

$registered = $false
try {
    if (-not (Test-Path -LiteralPath $installer)) { throw "Installer not found: $installer" }

    Write-Host "Registering disposable task '$TaskName' via the real installer..." -ForegroundColor Cyan
    # THE test: on both previous releases this line is where the deployment died.
    & $installer -InstallRoot $InstallRoot -TaskName $TaskName -IntervalHours $IntervalHours
    if ($LASTEXITCODE -ne 0) { throw "Installer exited $LASTEXITCODE" }
    $registered = $true

    # Disable immediately: it must never actually fire during the test.
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null

    Write-Host "`nInspecting the XML Task Scheduler actually stored:" -ForegroundColor Cyan
    [xml] $xml = Export-ScheduledTask -TaskName $TaskName
    $ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')

    $rep = $xml.SelectSingleNode('//t:Triggers/t:TimeTrigger/t:Repetition', $ns)
    Check 'repetition pattern is present' ($null -ne $rep)

    if ($rep) {
        $interval = $rep.SelectSingleNode('t:Interval', $ns)
        Check "repetition interval is PT${IntervalHours}H" `
              ($null -ne $interval -and $interval.InnerText -eq "PT${IntervalHours}H") `
              "(got '$(if ($interval) { $interval.InnerText } else { '<missing>' })')"

        # The regression itself. An ABSENT Duration is the documented "repeat indefinitely";
        # any present value must be >= PT1M, and the two we shipped were out of range on both ends.
        $duration = $rep.SelectSingleNode('t:Duration', $ns)
        Check 'repetition duration is ABSENT (= repeat indefinitely)' ($null -eq $duration) `
              "(got '$(if ($duration) { $duration.InnerText } else { '' })' - a present Duration eventually STOPS renewal)"
    }

    $raw = Export-ScheduledTask -TaskName $TaskName
    Check 'XML does not contain the out-of-range duration P99999999DT23H59M59S' `
          ($raw -notmatch 'P99999999')
    Check 'XML does not contain a PT0S duration' ($raw -notmatch '<Duration>PT0S</Duration>')

    $settings = $xml.SelectSingleNode('//t:Settings', $ns)
    Check 'StartWhenAvailable is true' `
          ($settings.SelectSingleNode('t:StartWhenAvailable', $ns).InnerText -eq 'true')
    Check 'MultipleInstancesPolicy is IgnoreNew' `
          ($settings.SelectSingleNode('t:MultipleInstancesPolicy', $ns).InnerText -eq 'IgnoreNew')
    Check 'ExecutionTimeLimit is PT10M' `
          ($settings.SelectSingleNode('t:ExecutionTimeLimit', $ns).InnerText -eq 'PT10M')

    $principal = $xml.SelectSingleNode('//t:Principals/t:Principal', $ns)
    $userId = $principal.SelectSingleNode('t:UserId', $ns).InnerText
    # S-1-5-18 is the SID Task Scheduler stores for NT AUTHORITY\SYSTEM.
    Check 'runs as SYSTEM' ($userId -eq 'S-1-5-18' -or $userId -match 'SYSTEM') "(got '$userId')"

    $args_ = $xml.SelectSingleNode('//t:Actions/t:Exec/t:Arguments', $ns).InnerText
    Check 'invokes the renewal wrapper' ($args_ -like "*$expectedWrap*") "(got '$args_')"

    # The task definition is world-readable to any local admin; a secret in it would be a leak.
    Check 'no secret-shaped value in the task definition' `
          ($raw -notmatch '(?i)(password|clientstate|secret|api[_-]?key)\s*[:=]\s*\S')
}
catch {
    Write-Host "  FAIL  registration/inspection threw: $($_.Exception.Message)" -ForegroundColor Red
    $failures.Add('registration') | Out-Null
}
finally {
    if ($registered) {
        Write-Host "`nRemoving disposable task '$TaskName'..." -ForegroundColor Cyan
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host "FAILED ($($failures.Count)): $($failures -join '; ')" -ForegroundColor Red
    exit 1
}
Write-Host 'SUCCESS: the renewal task registers and repeats indefinitely.' -ForegroundColor Green
exit 0
