<#
.SYNOPSIS
    Runs the Client360 TaxDome Drive -> local document synchronization.

.DESCRIPTION
    Windows-friendly runner for `python -m app.importers.taxdome_drive`. It changes to the Client360
    root, activates the virtualenv, loads app\.env, verifies the source (Z:\) and destination roots,
    creates the destination if missing, runs the sync, and writes a timestamped log to
    C:\Client360\logs\taxdome-sync\. A single-instance lock prevents overlapping executions. Exits
    nonzero on any fatal failure so a Scheduled Task can detect it.

    The TaxDome drive is read-only: this script and the importer never modify Z:\.

.PARAMETER DryRun
    Report what would change without copying files or writing to the database.

.PARAMETER PurgeMissing
    DELETE local copies whose source file has disappeared. Off by default; never use in automation
    unless you have decided retained copies of removed source files should be discarded.

.EXAMPLE
    .\scripts\sync_taxdome_documents.ps1
.EXAMPLE
    .\scripts\sync_taxdome_documents.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$PurgeMissing
)

$ErrorActionPreference = 'Stop'

$Client360Root = 'C:\Client360'
$LogDir        = Join-Path $Client360Root 'logs\taxdome-sync'
$LockFile      = Join-Path $env:TEMP 'client360-taxdome-sync.lock'

# --- logging -----------------------------------------------------------------
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogFile = Join-Path $LogDir "taxdome-sync-$stamp.log"

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'o'), $Level, $Message
    $line | Tee-Object -FilePath $LogFile -Append
}

function Fail {
    param([string]$Message)
    Write-Log $Message 'FATAL'
    if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
    exit 1
}

# --- single-instance lock (belt-and-suspenders with the Scheduled Task setting) ----
if (Test-Path $LockFile) {
    $age = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($age.TotalHours -lt 6) {
        Write-Log "Another sync appears to be running (lock $LockFile, age $([int]$age.TotalMinutes)m). Exiting." 'WARN'
        exit 0
    }
    Write-Log "Stale lock found (age $([int]$age.TotalHours)h); overriding." 'WARN'
}
"$PID $stamp" | Set-Content -Path $LockFile

try {
    Write-Log "TaxDome sync starting (DryRun=$DryRun PurgeMissing=$PurgeMissing)."

    # --- change to the Client360 root ---
    if (-not (Test-Path $Client360Root)) { Fail "Client360 root not found: $Client360Root" }
    Set-Location $Client360Root

    # --- activate the virtualenv ---
    $activate = Join-Path $Client360Root '.venv\Scripts\Activate.ps1'
    if (-not (Test-Path $activate)) { Fail "Virtualenv activate script not found: $activate" }
    . $activate

    # --- load app\.env ---
    $envFile = Join-Path $Client360Root 'app\.env'
    if (-not (Test-Path $envFile)) { Fail "Environment file not found: $envFile" }
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $key, $value = $line.Split('=', 2)
            [System.Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), 'Process')
        }
    }

    # --- resolve + verify source and destination roots ---
    $sourceRoot = if ($env:TAXDOME_DRIVE_ROOT) { $env:TAXDOME_DRIVE_ROOT } else { 'Z:\' }
    $destRoot   = if ($env:CLIENT360_TAXDOME_DOCUMENT_ROOT) { $env:CLIENT360_TAXDOME_DOCUMENT_ROOT } else { 'C:\Client360\Data\Documents\TaxDome' }
    Write-Log "Source root: $sourceRoot"
    Write-Log "Destination root: $destRoot"

    if (-not (Test-Path $sourceRoot)) {
        Fail "TaxDome source drive not available in this security context: $sourceRoot. (Z: is mapped per-user; the Scheduled Task must run as the account that can see it.)"
    }
    # Create the destination if missing.
    if (-not (Test-Path $destRoot)) {
        Write-Log "Destination missing; creating $destRoot."
        New-Item -ItemType Directory -Path $destRoot -Force | Out-Null
    }

    # --- invoke the sync ---
    $syncArgs = @('-m', 'app.importers.taxdome_drive')
    if ($DryRun)       { $syncArgs += '--dry-run' }
    if ($PurgeMissing) { $syncArgs += '--purge-missing' }
    Write-Log ("Running: python " + ($syncArgs -join ' '))

    & python @syncArgs 2>&1 | Tee-Object -FilePath $LogFile -Append
    $code = $LASTEXITCODE
    Write-Log "Sync exited with code $code."
    if ($code -ne 0) { Fail "Sync reported a fatal error (exit $code)." }

    Write-Log "TaxDome sync completed successfully."
    exit 0
}
catch {
    Fail "Unhandled error: $($_.Exception.Message)"
}
finally {
    if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
}
