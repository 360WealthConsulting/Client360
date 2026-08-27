<#
.SYNOPSIS
  Verify that the Client360 Windows service loads the CANONICAL production environment file.

.DESCRIPTION
  PRODUCTION ENVIRONMENT FILE: C:\Client360\app\.env

  This script is READ-ONLY. It never writes, renames, deletes, or restarts anything, and it never
  prints a configuration VALUE — only variable NAMES, file paths, and pass/fail.

  It checks, in order:
    1. the NSSM service's AppParameters actually contain "--env-file C:\Client360\app\.env";
    2. that file exists and is readable;
    3. which configuration variable NAMES it defines (names only — no values, ever);
    4. whether either ambiguous sibling exists:
         C:\Client360\.env       <- NOT loaded by the service
         C:\Client360\app.env    <- NOT loaded by the service

  Editing a sibling changes nothing about the running service. That is the failure this script
  exists to make loud and unmissable. See deploy/windows/README.md.

.PARAMETER ServiceName
  Windows service name to inspect. Default: Client360.

.PARAMETER EnvFile
  The path the service is REQUIRED to load. Default: the canonical production file. Override only
  for a non-production host, never to make a failing production check pass.

.PARAMETER RequireService
  Treat "service is not installed" as a failure. Off by default so the script is safe to run before
  the first install.

.PARAMETER NoNames
  Suppress the variable-name listing (paths and pass/fail only).

.OUTPUTS
  Exit codes:
    0  OK — the service loads the canonical file, and it exists.
    2  MISMATCH — the service is configured with a different --env-file, or none at all.
    3  MISSING — the required environment file does not exist or cannot be read.
    4  UNKNOWN — the service configuration could not be read (and -RequireService was given).

.EXAMPLE
  .\validate_production_env.ps1
.EXAMPLE
  .\validate_production_env.ps1 -RequireService
#>
[CmdletBinding()]
param(
  [string]$ServiceName = 'Client360',
  [string]$EnvFile     = 'C:\Client360\app\.env',
  [switch]$RequireService,
  [switch]$NoNames
)

$ErrorActionPreference = 'Stop'

# Siblings that are NOT the production runtime env file. Mirrors AMBIGUOUS_ENV_FILES in
# app/deploy/service.py — keep the two lists in step.
$AmbiguousEnvFiles = @(
  'C:\Client360\.env',
  'C:\Client360\app.env'
)

function ConvertTo-ComparablePath {
  param([string]$Path)
  if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
  return $Path.Trim().Trim('"').Replace('/', '\').TrimEnd('\').ToLowerInvariant()
}

function Get-ServiceAppParameters {
  <# Read the service's configured arguments. NSSM first (how Client360 is installed); the CIM
     service PathName as a fallback so an sc.exe-installed service still gets checked. #>
  param([string]$Name)

  $nssm = Get-Command nssm -ErrorAction SilentlyContinue
  if ($nssm) {
    try {
      $raw = & $nssm.Source get $Name AppParameters 2>$null
      if ($LASTEXITCODE -eq 0 -and $raw) {
        # NSSM emits UTF-16; strip embedded NULs and join any wrapped output.
        $text = (($raw -join ' ') -replace "`0", '').Trim()
        if ($text) { return [pscustomobject]@{ Source = 'nssm AppParameters'; Value = $text } }
      }
    } catch {
      Write-Verbose "nssm get failed: $($_.Exception.Message)"
    }
  }

  try {
    $svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='$Name'" -ErrorAction Stop
    if ($svc -and $svc.PathName) {
      return [pscustomobject]@{ Source = 'Win32_Service PathName'; Value = $svc.PathName }
    }
  } catch {
    Write-Verbose "Win32_Service query failed: $($_.Exception.Message)"
  }

  return $null
}

function Get-EnvFileArgument {
  <# Extract the --env-file value from a command line. Accepts "--env-file X" and "--env-file=X",
     quoted or bare. Mirrors env_file_from_app_parameters() in app/deploy/service.py. #>
  param([string]$Parameters)
  if ([string]::IsNullOrWhiteSpace($Parameters)) { return $null }
  if ($Parameters -match '--env-file\s*=\s*"([^"]+)"') { return $Matches[1] }
  if ($Parameters -match '--env-file\s+"([^"]+)"')     { return $Matches[1] }
  if ($Parameters -match '--env-file\s*=\s*(\S+)')     { return $Matches[1].Trim('"') }
  if ($Parameters -match '--env-file\s+(\S+)')         { return $Matches[1].Trim('"') }
  return $null
}

function Get-EnvVariableNames {
  <# Variable NAMES only. The value side of each line is split off and discarded immediately and is
     never bound to a variable, logged, or returned. #>
  param([string]$Path)
  return Get-Content -LiteralPath $Path -ErrorAction Stop | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
      ($line -replace '^\s*export\s+', '').Split('=', 2)[0].Trim()
    }
  } | Where-Object { $_ } | Sort-Object -Unique
}

# --- report ------------------------------------------------------------------

Write-Host ''
Write-Host 'Client360 production environment validation' -ForegroundColor Cyan
Write-Host "  PRODUCTION ENVIRONMENT FILE: $EnvFile"
Write-Host "  Service: $ServiceName"
Write-Host ''

$exitCode = 0

# --- 1. what the service is actually configured to load ----------------------

$configured = Get-ServiceAppParameters -Name $ServiceName
if (-not $configured) {
  if ($RequireService) {
    Write-Host "  [FAIL] Service '$ServiceName' configuration could not be read (not installed?)." -ForegroundColor Red
    $exitCode = 4
  } else {
    Write-Host "  [SKIP] Service '$ServiceName' is not installed or not readable; skipping the" -ForegroundColor Yellow
    Write-Host "         service check. Run with -RequireService to treat this as a failure." -ForegroundColor Yellow
  }
} else {
  $found = Get-EnvFileArgument -Parameters $configured.Value
  if (-not $found) {
    Write-Host "  [FAIL] The service passes NO --env-file argument ($($configured.Source))." -ForegroundColor Red
    Write-Host "         It will boot with no production configuration. Expected: $EnvFile" -ForegroundColor Red
    $exitCode = 2
  } elseif ((ConvertTo-ComparablePath $found) -ne (ConvertTo-ComparablePath $EnvFile)) {
    Write-Host "  [FAIL] The service loads a DIFFERENT environment file ($($configured.Source))." -ForegroundColor Red
    Write-Host "         configured: $found" -ForegroundColor Red
    Write-Host "         expected:   $EnvFile" -ForegroundColor Red
    Write-Host '         Production configuration edits made to the expected file are NOT in effect.' -ForegroundColor Red
    $exitCode = 2
  } else {
    Write-Host "  [ OK ] Service loads --env-file $found ($($configured.Source))." -ForegroundColor Green
  }
}

# --- 2. the file itself ------------------------------------------------------

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
  Write-Host "  [FAIL] $EnvFile does not exist." -ForegroundColor Red
  if ($exitCode -eq 0) { $exitCode = 3 }
} else {
  Write-Host "  [ OK ] $EnvFile exists." -ForegroundColor Green

  # --- 3. variable names only ------------------------------------------------
  try {
    $names = @(Get-EnvVariableNames -Path $EnvFile)
    Write-Host "  [ OK ] $($names.Count) configuration variables defined (NAMES ONLY below; no values are read out)." -ForegroundColor Green
    if (-not $NoNames) {
      foreach ($name in $names) { Write-Host "         $name" }
    }
  } catch {
    Write-Host "  [FAIL] $EnvFile could not be read: $($_.Exception.Message)" -ForegroundColor Red
    if ($exitCode -eq 0) { $exitCode = 3 }
  }
}

# --- 4. ambiguous siblings ---------------------------------------------------

Write-Host ''
$foundAmbiguous = @($AmbiguousEnvFiles | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
if ($foundAmbiguous.Count -gt 0) {
  Write-Warning '*** AMBIGUOUS ENVIRONMENT FILES PRESENT ***'
  foreach ($path in $foundAmbiguous) {
    Write-Warning "  $path  <- NOT the production runtime env file; the service never loads it."
  }
  Write-Warning "  Editing any of the above changes NOTHING about the running service."
  Write-Warning "  The only production runtime environment file is: $EnvFile"
  Write-Warning '  Archive them (see deploy/windows/README.md) as a separate, explicit production step.'
} else {
  Write-Host '  [ OK ] No ambiguous sibling environment files present.' -ForegroundColor Green
}

Write-Host ''
if ($exitCode -eq 0) {
  Write-Host 'RESULT: OK' -ForegroundColor Green
} else {
  Write-Host "RESULT: FAILED (exit $exitCode)" -ForegroundColor Red
}
exit $exitCode
