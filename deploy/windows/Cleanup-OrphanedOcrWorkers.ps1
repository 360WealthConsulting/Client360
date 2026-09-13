<#
.SYNOPSIS
    Guarded termination of orphaned OCR supervisor lane workers left by failed cutovers.

.DESCRIPTION
    Two cutover attempts (16:25 and 17:28) stopped and unregistered the supervisor SCHEDULED TASK but
    did not kill the multiprocessing lane workers it had spawned. Those workers outlived their parent,
    hold no advisory lock (the supervisor's database session died with it), and are claiming and OCR'ing
    documents concurrently with the restarted legacy worker - which takes no claims and therefore cannot
    see them. That is the double-processing condition the supervisor's lifetime locks exist to prevent.

    Identification is derived LIVE, never from a hard-coded PID list:
      * the supervisor generations are read from ocr_document_claims worker ids, which embed the pid of
        the run_parallel parent ("ocr<N>-<host>-<parentpid>-<hex>");
      * a lane worker is a live python.exe whose ParentProcessId is one of those generation parents AND
        whose parent is DEAD (orphaned);
      * isolation children are the descendant closure of those lane workers.

    Explicitly excluded: the Client360 service tree, PostgreSQL, the legacy OCR worker tree, any T:
    inventory scanner, and every unrelated python process. The script REFUSES to run if the computed
    orphan set intersects any protected tree.

    Lane workers are killed BEFORE their isolation children, so a dying child cannot be replaced.

    NEVER DOES: stop or restart Client360 or PostgreSQL; stop, disable or modify the legacy task;
    delete claim rows; modify production data; register the supervisor; deploy; run migrations; touch T:.
#>
[CmdletBinding()]
param(
    [switch] $WhatIfOnly,
    [string] $ProjectRoot = 'C:\Client360',
    [string] $LegacyTask  = 'Client360 OCR Full Corpus',
    [string] $SupervisorTask = 'Client360 OCR Parallel Supervisor',
    [string] $ArtifactDir = 'C:\Client360Data\ocr-parallel\cutover'
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $ArtifactDir "orphan-cleanup-$stamp.log"
Start-Transcript -Path $log | Out-Null

function Say([string]$m, [string]$c='Gray'){ Write-Host $m -ForegroundColor $c }
function Head([string]$m){ Say ''; Say ('='*78) 'Cyan'; Say $m 'Cyan'; Say ('='*78) 'Cyan' }
function Fail([string]$m){ throw "GUARD FAILED: $m" }
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

function Invoke-DbJson([string]$Body,[string]$Label='q'){
    $prelude = @'
import json, os, sys, traceback
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
load_dotenv("app/.env")
_e = create_engine(os.environ["DATABASE_URL"],
                   connect_args={"options": "-c default_transaction_read_only=on"})
def q(sql, **p):
    with _e.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r) for r in c.execute(text(sql), p).mappings()]
'@
    $tag=[guid]::NewGuid().ToString('N').Substring(0,8)
    $py=Join-Path $ArtifactDir "orph-$Label-$tag.py"; $o="$py.out"; $e2="$py.err"
    ($prelude+"`n"+$Body) | Set-Content $py -Encoding UTF8
    $pr=Start-Process -FilePath $python -ArgumentList @($py) -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $o -RedirectStandardError $e2 -NoNewWindow -Wait -PassThru
    $out=(Get-Content $o -Raw); $err=(Get-Content $e2 -Raw)
    if($pr.ExitCode -ne 0){ throw "db query '$Label' failed (exit $($pr.ExitCode))`n script:$py`n stderr:`n$err" }
    foreach($f in @($py,$o,$e2)){ Remove-Item $f -Force -ErrorAction SilentlyContinue }
    return ($out | ConvertFrom-Json)
}

function Get-AllPy { @(Get-CimInstance Win32_Process -Filter "Name='python.exe'") }
function Closure([int[]]$roots){
    $ids=[System.Collections.Generic.HashSet[int]]::new()
    if($roots){ foreach($r in $roots){ [void]$ids.Add([int]$r) } }
    $all=Get-AllPy
    for($i=0;$i -lt 10;$i++){ $added=$false
        foreach($p in $all){ if($ids.Contains([int]$p.ParentProcessId) -and -not $ids.Contains([int]$p.ProcessId)){[void]$ids.Add([int]$p.ProcessId);$added=$true} }
        if(-not $added){break} }
    # ",$ids" prevents PowerShell from ENUMERATING the set on return. Without the comma an EMPTY set
    # unrolls to $null, and the caller's $tree.Contains(...) then throws - which is exactly how the
    # first attempt aborted when no T: inventory scanner was present.
    return ,$ids
}

try {
Head "PHASE 0 - GUARDS"
$idn=[Security.Principal.WindowsIdentity]::GetCurrent()
if(-not (New-Object Security.Principal.WindowsPrincipal($idn)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    Fail "not elevated. Re-run from an Administrator PowerShell." }
Say "  OK elevation - $($idn.Name)" 'Green'

Push-Location $ProjectRoot
$head=(& git rev-parse HEAD).Trim(); $dirty=@(& git status --porcelain --untracked-files=no)
Pop-Location
if($head -ne '5adfb55f9fec884a8b223d26dbd69666ed70d0df'){ Fail "HEAD is $head, expected 5adfb55f9fec884a8b223d26dbd69666ed70d0df" }
if($dirty.Count -gt 0){ Fail "tracked tree dirty: $($dirty -join '; ')" }
Say "  OK HEAD $head, tracked tree clean" 'Green'

if(Get-ScheduledTask -TaskName $SupervisorTask -ErrorAction SilentlyContinue){ Fail "'$SupervisorTask' is PRESENT - expected absent" }
$lt=(Get-ScheduledTask -TaskName $LegacyTask).State
if($lt -ne 'Running'){ Fail "legacy task is '$lt', expected Running" }
Say "  OK supervisor task absent; legacy task Running" 'Green'

foreach($p in '/health','/readiness'){
    $r=Invoke-WebRequest "http://127.0.0.1:8360$p" -UseBasicParsing -TimeoutSec 20
    if($r.StatusCode -ne 200){ Fail "$p returned $($r.StatusCode)" }
    if($p -eq '/readiness'){
        $m=($r.Content|ConvertFrom-Json).checks.migrations
        if($m.current_head -ne 'ocrclaim01' -or -not $m.in_sync){ Fail "migrations $($m.current_head) in_sync=$($m.in_sync)" }
    }
}
Say "  OK Client360 /health 200, /readiness 200 at ocrclaim01" 'Green'

Head "PHASE 1 - RESOLVE ORPHANS FROM LIVE STATE"
$gen = Invoke-DbJson @'
rows = q("""SELECT DISTINCT split_part(worker_id,'-',3) AS ppid
            FROM ocr_document_claims
            WHERE heartbeat_at > now() - interval '10 minutes'""")
print(json.dumps([r["ppid"] for r in rows]))
'@ 'generations'
$genParents = @($gen | ForEach-Object { [int]$_ })
Say "  supervisor generation parent pids (from live claim ids): $($genParents -join ', ')"
if($genParents.Count -eq 0){ Say "  No recent claim activity - nothing to clean." 'Yellow'; Stop-Transcript|Out-Null; return }

$all=Get-AllPy
$aliveIds=@($all | ForEach-Object {[int]$_.ProcessId})
foreach($gp in $genParents){ Say "    parent $gp is $(if($aliveIds -contains $gp){'ALIVE'}else{'DEAD (orphaned children)'})" }

# protected trees
$legacyRoots=@($all | Where-Object { $_.CommandLine -match 'ocr-fullcorpus\\worker\.py' } | ForEach-Object {[int]$_.ProcessId})
$legacyTree = Closure $legacyRoots
$svcPids=@(Get-CimInstance Win32_Service -Filter "Name='Client360'" | ForEach-Object {[int]$_.ProcessId} | Where-Object {$_ -gt 0})
$svcTree = Closure $svcPids
$invRoots=@($all | Where-Object { $_.CommandLine -match '(?i)inventory|[^A-Za-z]T:\\' } | ForEach-Object {[int]$_.ProcessId})
$invTree = Closure $invRoots
Say "  protected legacy tree   : $(($legacyTree)-join ',')"
Say "  protected Client360 tree: $(($svcTree)-join ',')"
Say "  protected inventory tree: $(if($invTree.Count){($invTree)-join ','}else{'(none present)'})"

$laneRoots=@($all | Where-Object { $genParents -contains [int]$_.ParentProcessId -and $aliveIds -notcontains [int]$_.ParentProcessId } |
             ForEach-Object {[int]$_.ProcessId})
if($laneRoots.Count -eq 0){ Say "  No orphaned lane workers present. Nothing to do." 'Green'; Stop-Transcript|Out-Null; return }
$orphanTree = Closure $laneRoots

$overlap=@($orphanTree | Where-Object { $legacyTree.Contains($_) -or $svcTree.Contains($_) -or $invTree.Contains($_) })
if($overlap.Count -gt 0){ Fail "orphan set intersects a PROTECTED tree (pids $($overlap -join ',')) - refusing" }

$detail=@()
foreach($id in ($orphanTree | Sort-Object)){
    $p=$all | Where-Object {[int]$_.ProcessId -eq $id} | Select-Object -First 1
    if(-not $p){ $p=Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue }
    if(-not $p){ continue }
    $isLane = $laneRoots -contains $id
    $detail += [pscustomobject]@{
        Pid=$id; Parent=[int]$p.ParentProcessId
        Role=$(if($isLane){'LANE WORKER'}else{'isolation child'})
        Started=$p.CreationDate
        Cmd=$(if($p.CommandLine){$p.CommandLine}else{'<unavailable>'})
        Reason=$(if($isLane){"parent $($p.ParentProcessId) is a DEAD run_parallel generation parent seen in live ocr_document_claims worker ids"}
                 else{"descendant of orphaned lane worker $($p.ParentProcessId) (per-document isolation child)"})
    }
}
Say ""
Say "  === ORPHAN SET ($($detail.Count) processes; $($laneRoots.Count) lane workers) ==="
foreach($d in $detail){
    Say ("    {0,-7} parent={1,-7} {2,-16} started={3}" -f $d.Pid,$d.Parent,$d.Role,$d.Started)
    Say ("            cmd: {0}" -f ($d.Cmd.Substring(0,[Math]::Min(110,$d.Cmd.Length))))
    Say ("            why: {0}" -f $d.Reason)
}
($detail | ConvertTo-Json -Depth 5) | Set-Content (Join-Path $ArtifactDir "orphan-set-$stamp.json") -Encoding UTF8

if($WhatIfOnly){ Say ''; Say "WHATIF - nothing terminated." 'Yellow'; Say "Log: $log" 'Cyan'; Stop-Transcript|Out-Null; return }

Head "PHASE 2 - TERMINATE (lane workers first, so children cannot be respawned)"
foreach($id in $laneRoots){
    try { Stop-Process -Id $id -Force -ErrorAction Stop; Say "  killed LANE WORKER $id" 'Yellow' }
    catch { Say "  lane worker $id : $($_.Exception.Message)" 'DarkYellow' }
}
Start-Sleep -Seconds 3
$remaining=@($orphanTree | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
foreach($id in $remaining){
    try { Stop-Process -Id $id -Force -ErrorAction Stop; Say "  killed isolation child $id" 'Yellow' }
    catch { Say "  child $id : $($_.Exception.Message)" 'DarkYellow' }
}

Head "PHASE 3 - WAIT FOR EXIT"
$deadline=(Get-Date).AddMinutes(2); $left=@()
while((Get-Date) -lt $deadline){
    $left=@($orphanTree | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    # also catch any NEW child spawned by a lane worker in its last moments
    $extra=@(Closure $laneRoots | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    $left=@($left + $extra | Sort-Object -Unique)
    if($left.Count -eq 0){ break }
    foreach($id in $left){ try{ Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }catch{} }
    Start-Sleep -Seconds 5
}
if($left.Count -gt 0){ Say "  WARNING still alive: $($left -join ',')" 'Red' }
else { Say "  OK every orphan process has exited" 'Green' }

Head "PHASE 4 - VERIFY (2 minutes of claim-heartbeat observation)"
$genList = ($genParents -join ',')
for($i=1;$i -le 5;$i++){
    Start-Sleep -Seconds 30
    $chk = Invoke-DbJson @"
rows = q("""SELECT worker_id, max(heartbeat_at) AS hb, count(*) AS n
            FROM ocr_document_claims
            WHERE heartbeat_at > now() - interval '40 seconds'
              AND split_part(worker_id,'-',3) IN ($genList)
            GROUP BY worker_id""")
w = q("""SELECT count(*) AS writes_1m, max(updated_at) AS last_write FROM document_ocr
         WHERE updated_at > now() - interval '1 minute'""")[0]
print(json.dumps({"orphan_claims": rows, "writes_1m": w["writes_1m"], "last_write": w["last_write"]}, default=str))
"@ "verify$i"
    $n=@($chk.orphan_claims).Count
    Say ("  t+{0,3}s orphan-generation claim heartbeats: {1}   document_ocr writes/1m: {2}  last_write={3}" -f `
         ($i*30), $(if($n -eq 0){'NONE'}else{"$n !!"}), $chk.writes_1m, $chk.last_write) `
        $(if($n -eq 0){'Green'}else{'Red'})
}

Head "PHASE 5 - FINAL STATE"
$locks = Invoke-DbJson @'
rows = q("""SELECT (l.classid::bigint<<32)|l.objid::bigint AS key, l.pid
            FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
            WHERE l.locktype='advisory' AND a.datname=current_database() AND l.granted""")
print(json.dumps(rows, default=str))
'@ 'locks'
Say "  advisory locks held:"
foreach($l in @($locks)){ Say ("    key={0} pid={1}" -f $l.key,$l.pid) }
$keys=@($locks | ForEach-Object { [int64]$_.key })
Say ("    511005888 stranded : {0}" -f $(if($keys -contains 511005888){'YES !!'}else{'no'})) $(if($keys -contains 511005888){'Red'}else{'Green'})
Say ("    511005002 held     : {0} (legacy takes this per batch - normal)" -f $(if($keys -contains 511005002){'yes'}else{'no'}))

$c = Invoke-DbJson @'
r = q("""SELECT max(updated_at) AS last_write,
                count(*) FILTER (WHERE updated_at > now() - interval '2 minutes') AS writes_2m,
                count(*) FILTER (WHERE status='completed') AS completed
         FROM document_ocr""")[0]
print(json.dumps(r, default=str))
'@ 'final'
Say "  legacy task  : $((Get-ScheduledTask -TaskName $LegacyTask).State)"
Say "  document_ocr : last_write=$($c.last_write) writes_2m=$($c.writes_2m) completed=$($c.completed)"
foreach($p in '/health','/readiness'){
    $r=Invoke-WebRequest "http://127.0.0.1:8360$p" -UseBasicParsing -TimeoutSec 20
    Say "  $p : HTTP $($r.StatusCode)"
}
Say ''
Say "Log: $log" 'Cyan'
} catch {
    Say ''; Say "ABORTED: $($_.Exception.Message)" 'Red'
    Say "Nothing further was terminated. Legacy OCR and Client360 were not modified." 'Yellow'
    Say "Log: $log" 'Cyan'
    Stop-Transcript|Out-Null; exit 1
}
Stop-Transcript|Out-Null
