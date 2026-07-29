<#
.SYNOPSIS
  Install / manage Client360 as a persistent Windows service.

.DESCRIPTION
  Runs the production ASGI app (uvicorn app.main:app — NEVER the demo app) as a Windows service so it
  keeps running without an interactive terminal and starts again after a server reboot. Prefers NSSM
  (auto-restart + log redirection); falls back to the built-in sc.exe if NSSM is not on PATH.

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
  [string]$EnvFile     = 'app\.env'
)

$ErrorActionPreference = 'Stop'
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
