<#
.SYNOPSIS
  Install / manage Client360 as a persistent Windows service.

.DESCRIPTION
  Runs the production ASGI app (uvicorn app.main:app — NEVER the demo app) as a Windows service so it
  keeps running without an interactive terminal and starts again after a server reboot. Prefers NSSM
  (auto-restart + log redirection); falls back to the built-in sc.exe if NSSM is not on PATH.

  PRODUCTION ENVIRONMENT FILE: C:\Client360\app\.env

  The --env-file passed to uvicorn is ABSOLUTE on purpose. A relative 'app\.env' only resolves
  correctly while the working directory is C:\Client360, and it leaves an operator to infer the real
  path — which is how C:\Client360\.env and C:\Client360\app.env came to be edited instead.
  Installing with any other env file is refused unless -AllowNonCanonicalEnvFile is given.
  See deploy/windows/README.md and validate_production_env.ps1.

  Actions: install | start | stop | restart | status | uninstall

.EXAMPLE
  .\Install-Client360Service.ps1 -Action install -Port 8360
  .\Install-Client360Service.ps1 -Action status
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('install','start','stop','restart','status','uninstall')]
  [string]$Action,
  [string]$ServiceName = 'Client360',
  [string]$WorkDir     = 'C:\Client360',
  [string]$Python      = 'C:\Client360\.venv\Scripts\python.exe',
  [string]$BindHost    = '127.0.0.1',
  [int]   $Port        = 8360,
  [string]$LogDir      = 'C:\Client360\logs',
  [string]$EnvFile     = 'C:\Client360\app\.env',
  [switch]$AllowNonCanonicalEnvFile
)

$ErrorActionPreference = 'Stop'

# THE canonical production environment file. Mirrors PRODUCTION_ENV_FILE / AMBIGUOUS_ENV_FILES in
# app/deploy/service.py — keep these in step.
$CanonicalEnvFile  = 'C:\Client360\app\.env'
$AmbiguousEnvFiles = @('C:\Client360\.env', 'C:\Client360\app.env')

function ConvertTo-ComparablePath {
  param([string]$Path)
  if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
  return $Path.Trim().Trim('"').Replace('/', '\').TrimEnd('\').ToLowerInvariant()
}

# Guard the one setting that has repeatedly gone wrong. Installing the service with a non-canonical
# --env-file is how production ends up reading a file nobody edits.
if ($Action -eq 'install' -and -not $AllowNonCanonicalEnvFile) {
  if ((ConvertTo-ComparablePath $EnvFile) -ne (ConvertTo-ComparablePath $CanonicalEnvFile)) {
    throw ("Refusing to install $ServiceName with -EnvFile '$EnvFile'. The production runtime " +
           "environment file is '$CanonicalEnvFile'. Pass -AllowNonCanonicalEnvFile only for a " +
           "non-production host. See deploy/windows/README.md.")
  }
  foreach ($ambiguous in $AmbiguousEnvFiles) {
    if (Test-Path -LiteralPath $ambiguous -PathType Leaf) {
      Write-Warning "$ambiguous exists and is NOT loaded by $ServiceName. The production runtime environment file is $CanonicalEnvFile."
    }
  }
}

# --env-file is REQUIRED so the service loads the production config (SESSION_SECRET/DATABASE_URL/OIDC).
$uvicornArgs = @('-m','uvicorn','app.main:app','--host',$BindHost,'--port',"$Port",'--env-file',$EnvFile)
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue)

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Invoke-Nssm { param([string[]]$Args) & $nssm.Source @Args; if ($LASTEXITCODE -ne 0) { throw "nssm $($Args -join ' ') failed ($LASTEXITCODE)" } }

if ($nssm) {
  switch ($Action) {
    'install' {
      Invoke-Nssm @('install',$ServiceName,$Python) + $uvicornArgs
      Invoke-Nssm @('set',$ServiceName,'AppDirectory',$WorkDir)
      Invoke-Nssm @('set',$ServiceName,'AppStdout',"$LogDir\service-stdout.log")
      Invoke-Nssm @('set',$ServiceName,'AppStderr',"$LogDir\service-stderr.log")
      Invoke-Nssm @('set',$ServiceName,'AppRotateFiles','1')
      Invoke-Nssm @('set',$ServiceName,'Start','SERVICE_AUTO_START')     # start after reboot
      Invoke-Nssm @('set',$ServiceName,'AppExit','Default','Restart')    # auto-restart on failure
      Write-Host "Installed $ServiceName (nssm) -> $Python $($uvicornArgs -join ' ')"
    }
    'uninstall' { & $nssm.Source stop $ServiceName 2>$null; Invoke-Nssm @('remove',$ServiceName,'confirm') }
    default     { Invoke-Nssm @($Action,$ServiceName) }
  }
} else {
  Write-Warning 'NSSM not found on PATH; using sc.exe (no auto-restart tuning). Installing NSSM is recommended.'
  $binPath = '"' + $Python + '" ' + ($uvicornArgs -join ' ')
  switch ($Action) {
    'install'   { sc.exe create $ServiceName binPath= $binPath start= auto | Write-Host }
    'uninstall' { sc.exe stop $ServiceName 2>$null; sc.exe delete $ServiceName | Write-Host }
    'start'     { sc.exe start $ServiceName | Write-Host }
    'stop'      { sc.exe stop $ServiceName | Write-Host }
    'restart'   { sc.exe stop $ServiceName 2>$null; Start-Sleep -Seconds 2; sc.exe start $ServiceName | Write-Host }
    'status'    { sc.exe query $ServiceName | Write-Host }
  }
}
