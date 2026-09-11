<#
.SYNOPSIS
  Install / manage the Client360 continuous document pipeline as a persistent Windows service.

.DESCRIPTION
  Runs `python -m app.jobs.document_pipeline_runner run` as a Windows service, so the pipeline keeps
  discovering and processing documents with NO interactive session of any kind — no signed-in user, no
  desktop, no remote-desktop connection, no AI assistant — and starts again by itself after a reboot.

  This is a SEPARATE service from the Client360 application (Install-Client360Service.ps1). It is the
  recommended host for a large backlog: heavy OCR then runs in its own process rather than competing
  with request handling inside the web service. Running the scheduler-hosted tick as well is safe —
  every worker leases the documents it claims — but size ONE of them for throughput, not both.

  PRODUCTION ENVIRONMENT FILE: C:\Client360\app\.env

  The pipeline reads DATABASE_URL and its DOCUMENT_PIPELINE_* settings from the process environment.
  NSSM's AppEnvironmentExtra cannot read a .env file, so this script loads the canonical env file and
  passes the variables the pipeline actually needs. Installing against any other env file is refused
  unless -AllowNonCanonicalEnvFile is given — the same guard, and for the same reason, as the
  application service: a relative or stray env file is how a service ends up reading a file nobody
  edits. See deploy/windows/README.md and docs/CONTINUOUS_DOCUMENT_PIPELINE.md.

  PREREQUISITES
    * `alembic upgrade head` has been run (the pipeline's tables arrive with docpipe01). Verify with
      `python -m app.jobs.document_pipeline_runner status`, which refuses clearly if they are absent.
    * DOCUMENT_PIPELINE_ENABLED is NOT needed for this service — that flag gates the scheduler-hosted
      tick inside the application. This service runs the pipeline directly.
    * No full-corpus OCR sweep (app/jobs/ocr_runner.py) is in progress. The pipeline detects the
      sweep's advisory lock and defers every OCR document while it is held, which is correct but looks
      like a queue that will not drain.

  Actions: install | start | stop | restart | status | uninstall | health

.EXAMPLE
  .\Install-DocumentPipelineService.ps1 -Action install -Workers 4
  .\Install-DocumentPipelineService.ps1 -Action health
  .\Install-DocumentPipelineService.ps1 -Action stop
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('install','start','stop','restart','status','uninstall','health')]
  [string]$Action,
  [string]$ServiceName = 'Client360DocumentPipeline',
  [string]$WorkDir     = 'C:\Client360',
  [string]$Python      = 'C:\Client360\.venv\Scripts\python.exe',
  [string]$LogDir      = 'C:\Client360\logs',
  [string]$EnvFile     = 'C:\Client360\app\.env',
  [int]   $Workers     = 2,
  [int]   $DiscoveryIntervalSeconds = 60,
  [switch]$AllowNonCanonicalEnvFile
)

$ErrorActionPreference = 'Stop'

# THE canonical production environment file. Mirrors Install-Client360Service.ps1 — keep these in step.
$CanonicalEnvFile = 'C:\Client360\app\.env'

# Settings the pipeline process needs. DATABASE_URL is required; everything else has a safe default in
# app/config.py, so an env file that sets none of them still produces a correctly-configured service.
$PipelineEnvKeys = @(
  'DATABASE_URL',
  'CLIENT360_ENVIRONMENT',
  'DOCUMENT_PIPELINE_WORKERS',
  'DOCUMENT_PIPELINE_BATCH_SIZE',
  'DOCUMENT_PIPELINE_LEASE_SECONDS',
  'DOCUMENT_PIPELINE_MAX_ATTEMPTS',
  'DOCUMENT_PIPELINE_DISCOVERY_PAGE_SIZE',
  'DOCUMENT_PIPELINE_CPU_LIMIT_PERCENT',
  'DOCUMENT_PIPELINE_MEMORY_LIMIT_PERCENT',
  'DOCUMENT_PIPELINE_DB_HEADROOM',
  'DOCUMENT_PIPELINE_STALL_SECONDS',
  'DOCUMENT_PIPELINE_WORKER_TIMEOUT_SECONDS',
  'OCR_SUBPROCESS_ISOLATION',
  'OCR_STATUS_DIR'
)

function ConvertTo-ComparablePath {
  param([string]$Path)
  if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
  return $Path.Trim().Trim('"').Replace('/', '\').TrimEnd('\').ToLowerInvariant()
}

function Read-EnvFile {
  <# Parse KEY=VALUE lines into a hashtable. Comments and blanks ignored; quotes stripped. #>
  param([string]$Path)
  $values = @{}
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $values }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
    $split = $trimmed.IndexOf('=')
    if ($split -lt 1) { continue }
    $key = $trimmed.Substring(0, $split).Trim()
    $value = $trimmed.Substring($split + 1).Trim().Trim('"').Trim("'")
    $values[$key] = $value
  }
  return $values
}

if ($Action -eq 'install' -and -not $AllowNonCanonicalEnvFile) {
  if ((ConvertTo-ComparablePath $EnvFile) -ne (ConvertTo-ComparablePath $CanonicalEnvFile)) {
    throw ("Refusing to install $ServiceName with -EnvFile '$EnvFile'. The production runtime " +
           "environment file is '$CanonicalEnvFile'. Pass -AllowNonCanonicalEnvFile only for a " +
           "non-production host. See deploy/windows/README.md.")
  }
}

$runnerArgs = @('-m','app.jobs.document_pipeline_runner','run',
                '--workers',"$Workers",
                '--discovery-interval',"$DiscoveryIntervalSeconds")
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue)

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Invoke-Nssm {
  param([Parameter(Mandatory = $true)][string[]]$NssmArgs)
  & $nssm.Source @NssmArgs
  if ($LASTEXITCODE -ne 0) { throw "nssm $($NssmArgs -join ' ') failed ($LASTEXITCODE)" }
}

if ($Action -eq 'health') {
  # Read-only. Exit code 0 means healthy or correctly idle; 1 means stalled, stopped or not installed.
  Push-Location $WorkDir
  try {
    $env:PYTHONPATH = $WorkDir
    foreach ($pair in (Read-EnvFile $EnvFile).GetEnumerator()) {
      if ($PipelineEnvKeys -contains $pair.Key) { Set-Item -Path "env:$($pair.Key)" -Value $pair.Value }
    }
    & $Python @('-m','app.jobs.document_pipeline_runner','health')
    exit $LASTEXITCODE
  } finally { Pop-Location }
}

if ($nssm) {
  switch ($Action) {
    'install' {
      # Build the COMPLETE argument list before invoking: `Invoke-Nssm @(...) + $runnerArgs` parses in
      # PowerShell *argument* mode, where `+` is another positional argument and the concatenation
      # silently does not happen. (Same trap as Install-Client360Service.ps1.)
      $installArgs = @('install', $ServiceName, $Python) + $runnerArgs
      Invoke-Nssm $installArgs
      Invoke-Nssm @('set',$ServiceName,'AppDirectory',$WorkDir)
      Invoke-Nssm @('set',$ServiceName,'AppStdout',"$LogDir\document-pipeline-stdout.log")
      Invoke-Nssm @('set',$ServiceName,'AppStderr',"$LogDir\document-pipeline-stderr.log")
      Invoke-Nssm @('set',$ServiceName,'AppRotateFiles','1')
      Invoke-Nssm @('set',$ServiceName,'Start','SERVICE_AUTO_START')      # start after reboot
      Invoke-Nssm @('set',$ServiceName,'AppExit','Default','Restart')     # auto-restart on failure
      Invoke-Nssm @('set',$ServiceName,'AppRestartDelay','15000')
      # A clean stop lets the workers release their leases, so a restart does not wait out a 10-minute
      # lease before the work becomes claimable again. NSSM sends CTRL-BREAK first; the runner installs
      # a SIGBREAK handler for exactly this.
      Invoke-Nssm @('set',$ServiceName,'AppStopMethodConsole','30000')
      Invoke-Nssm @('set',$ServiceName,'AppStopMethodSkip','0')

      # ONE AppEnvironmentExtra call: a second one REPLACES the first rather than adding to it, so the
      # PYTHONPATH entry belongs in this same list.
      $envValues = Read-EnvFile $EnvFile
      $extra = @('PYTHONPATH=' + $WorkDir)
      foreach ($key in $PipelineEnvKeys) {
        if ($envValues.ContainsKey($key) -and $envValues[$key] -ne '') { $extra += "$key=$($envValues[$key])" }
      }
      if (-not ($envValues.ContainsKey('DATABASE_URL') -and $envValues['DATABASE_URL'] -ne '')) {
        Write-Warning "DATABASE_URL was not found in $EnvFile. It is REQUIRED — the service will fail to start without it."
      }
      Invoke-Nssm (@('set',$ServiceName,'AppEnvironmentExtra') + $extra)

      Write-Host "Installed $ServiceName (nssm) -> $Python $($runnerArgs -join ' ')"
      Write-Host "Verify before starting:  $Python -m app.jobs.document_pipeline_runner status"
    }
    'uninstall' { & $nssm.Source stop $ServiceName 2>$null; Invoke-Nssm @('remove',$ServiceName,'confirm') }
    default     { Invoke-Nssm @($Action,$ServiceName) }
  }
} else {
  Write-Warning 'NSSM not found on PATH; using sc.exe (no auto-restart, no log redirection, no clean-stop signal). Installing NSSM is strongly recommended for this service.'
  $binPath = '"' + $Python + '" ' + ($runnerArgs -join ' ')
  switch ($Action) {
    'install'   { sc.exe create $ServiceName binPath= $binPath start= auto | Write-Host }
    'uninstall' { sc.exe stop $ServiceName 2>$null; sc.exe delete $ServiceName | Write-Host }
    'start'     { sc.exe start $ServiceName | Write-Host }
    'stop'      { sc.exe stop $ServiceName | Write-Host }
    'restart'   { sc.exe stop $ServiceName 2>$null; Start-Sleep -Seconds 2; sc.exe start $ServiceName | Write-Host }
    'status'    { sc.exe query $ServiceName | Write-Host }
  }
}
