<#
.SYNOPSIS
    Renew the SharePoint Microsoft Graph change-notification subscription (Task Scheduler entry point).

.DESCRIPTION
    Runs `python -m scripts.manage_sharepoint_subscription --ensure` from C:\Client360 with the
    production environment loaded from C:\Client360\app\.env.

    `--ensure` is idempotent and decides for itself what is needed:
      * missing subscription      -> creates it
      * <= 12h of lifetime left   -> renews it (back to a ~70h expiry)
      * more than 12h left        -> leaves it untouched

    The subscription lives 4200 minutes (~70h) and renews inside the final 720 minutes (12h), so the
    window in which a renewal can happen is 12 hours wide. The scheduled task therefore runs every
    4 hours: three attempts land inside every window, so two consecutive failures (a transient Graph
    error, a reboot, a network blip) still leave a successful renewal before expiry.

    SCOPE - this script deliberately does NOT:
      * restart Client360 or any service
      * run a SharePoint document sync
      * write to the database
      * modify the .env file
    It performs exactly one Graph list call plus, at most, one create/renew.

.PARAMETER InstallRoot
    Client360 installation root. Defaults to C:\Client360.

.PARAMETER LogRoot
    Directory for transcript logs. Defaults to <InstallRoot>\logs\sharepoint-subscription.

.PARAMETER RetentionDays
    Days of logs to keep. Defaults to 30.

.OUTPUTS
    Exit code 0 on success, non-zero on failure, so Task Scheduler records a failed run.

.NOTES
    SECRETS: this script never echoes .env values. Variables parsed from .env are pushed straight into
    the process environment and are never written to the console, the transcript, or the log file. The
    Python command redacts clientState and other secret-bearing fields from its own JSON output.
#>
[CmdletBinding()]
param(
    [string] $InstallRoot   = 'C:\Client360',
    [string] $LogRoot,
    [int]    $RetentionDays = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $LogRoot) { $LogRoot = Join-Path $InstallRoot 'logs\sharepoint-subscription' }

$envPath    = Join-Path $InstallRoot 'app\.env'
$pythonExe  = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$startedUtc = (Get-Date).ToUniversalTime()
$stamp      = $startedUtc.ToString('yyyyMMdd-HHmmss')
$logFile    = Join-Path $LogRoot "renew-$stamp.log"

function Write-Log {
    param([string] $Message, [string] $Level = 'INFO')
    $line = '{0}Z [{1}] {2}' -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss'), $Level, $Message
    Write-Output $line
    if (Test-Path -LiteralPath $LogRoot) { Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 }
}

# --- .env -> process environment -------------------------------------------------------------------
# Parsed in-process and injected directly. Values are NEVER echoed: only the NAME of each key loaded is
# reported, so a log shows that configuration arrived without disclosing any of it.
function Import-DotEnv {
    param([Parameter(Mandatory)] [string] $Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Environment file not found: $Path"
    }

    $loaded = New-Object System.Collections.Generic.List[string]
    foreach ($raw in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).Trim() }

        $split = $line.IndexOf('=')
        if ($split -lt 1) { continue }

        $name  = $line.Substring(0, $split).Trim()
        $value = $line.Substring($split + 1).Trim()

        # Strip one matched pair of surrounding quotes, the way python-dotenv does.
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }

        Set-Item -Path ("Env:{0}" -f $name) -Value $value
        $loaded.Add($name) | Out-Null
    }
    return $loaded
}

$exitCode = 1
try {
    if (-not (Test-Path -LiteralPath $LogRoot)) {
        New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
    }

    Write-Log "SharePoint subscription renewal starting (host=$env:COMPUTERNAME user=$env:USERNAME)"

    if (-not (Test-Path -LiteralPath $InstallRoot)) { throw "Install root not found: $InstallRoot" }
    if (-not (Test-Path -LiteralPath $pythonExe))   { throw "Python interpreter not found: $pythonExe" }

    $names = Import-DotEnv -Path $envPath
    # Names only - never values.
    Write-Log ("Loaded {0} settings from app\.env" -f $names.Count)

    Set-Location -LiteralPath $InstallRoot
    Write-Log "Running: python -m scripts.manage_sharepoint_subscription --ensure"

    # Capture stdout+stderr so the JSON result (already secret-free) reaches the log.
    $output = & $pythonExe -m scripts.manage_sharepoint_subscription --ensure 2>&1
    $exitCode = $LASTEXITCODE

    foreach ($line in $output) { Write-Log $line }

    if ($exitCode -eq 0) {
        # The action verb is the operationally interesting bit for a scheduler log.
        $action = 'unknown'
        if ($output -match '"action"\s*:\s*"([a-z]+)"') { $action = $Matches[1] }
        Write-Log "SUCCESS: subscription ensure completed (action=$action)"
    }
    else {
        Write-Log "FAILURE: ensure exited with code $exitCode" 'ERROR'
    }
}
catch {
    # Message only - no stack payloads that might carry a response body.
    Write-Log ("FAILURE: {0}" -f $_.Exception.Message) 'ERROR'
    $exitCode = 1
}
finally {
    if (Test-Path -LiteralPath $LogRoot) {
        $cutoff = (Get-Date).AddDays(-$RetentionDays)
        Get-ChildItem -LiteralPath $LogRoot -Filter 'renew-*.log' -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt $cutoff } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    $elapsed = ((Get-Date).ToUniversalTime() - $startedUtc).TotalSeconds
    Write-Log ("Finished in {0:N1}s with exit code {1}" -f $elapsed, $exitCode)
}

exit $exitCode
