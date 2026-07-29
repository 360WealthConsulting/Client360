<#
.SYNOPSIS
  One-command Client360 deployment for Windows Server. Stops on the first failed step.

.DESCRIPTION
  The supported deployment sequence, reusing the Python deploy CLI + the service installer:
    1. validate configuration        (python -m app.deploy check-config)
    2. test the database + report current->target revision (python -m app.deploy migrate --plan)
    3. optional pre-migration backup  (-Backup -> python -m app.deploy migrate --backup)
    4. apply migrations to head + verify (python -m app.deploy migrate)
    5. verify Vault storage           (covered by check-config)
    6. install/update the Windows service (Install-Client360Service.ps1 -Action install)
    7. start/restart the service
    8. run smoke tests                (python -m app.deploy smoke --url)
    9. report the deployed URLs

  It never resets or recreates the database and never uses the demo app.

.EXAMPLE
  .\Deploy-Client360.ps1 -Port 8360 -Backup
#>
param(
  [string]$WorkDir  = 'C:\Client360',
  [string]$Python   = 'C:\Client360\.venv\Scripts\python.exe',
  [string]$BindHost = '127.0.0.1',
  [int]   $Port     = 8360,
  [switch]$Backup
)

$ErrorActionPreference = 'Stop'
Set-Location $WorkDir
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step($n, $msg) { Write-Host "`n=== [$n] $msg ===" -ForegroundColor Cyan }
function Run($file, $arguments) {
  & $file @arguments
  if ($LASTEXITCODE -ne 0) { throw "Step failed ($file $($arguments -join ' ')) exit=$LASTEXITCODE" }
}

Step 1 'Validate configuration';           Run $Python @('-m','app.deploy','check-config')
Step 2 'Database + migration plan';         Run $Python @('-m','app.deploy','migrate','--plan')
if ($Backup) { Step 3 'Pre-migration backup + migrate'; Run $Python @('-m','app.deploy','migrate','--backup') }
else         { Step 4 'Apply migrations to head';       Run $Python @('-m','app.deploy','migrate') }
Step 6 'Install/update Windows service';    Run 'powershell' @('-File',"$here\Install-Client360Service.ps1",'-Action','install','-Python',$Python,'-BindHost',$BindHost,'-Port',"$Port",'-WorkDir',$WorkDir)
Step 7 'Start/restart service';             Run 'powershell' @('-File',"$here\Install-Client360Service.ps1",'-Action','restart','-Python',$Python,'-BindHost',$BindHost,'-Port',"$Port",'-WorkDir',$WorkDir)
Start-Sleep -Seconds 5
Step 8 'Smoke test';                        Run $Python @('-m','app.deploy','smoke','--url',"http://$BindHost`:$Port")

Step 9 'Deployed'
Write-Host "Client360 is running:" -ForegroundColor Green
Write-Host "  Staff:  http://$BindHost`:$Port/home"
Write-Host "  Portal: http://$BindHost`:$Port/portal/login"
Write-Host "  Health: http://$BindHost`:$Port/health   Readiness: http://$BindHost`:$Port/readiness"
