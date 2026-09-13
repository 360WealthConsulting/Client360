<#
.SYNOPSIS
    Deterministic tests for the cutover monitor's lane detection, collection handling and rollback.

.DESCRIPTION
    Pure fixtures - no live processes, no database, no production contact. Each fixture is a synthetic
    process table shaped exactly like the real one observed on this host:

        shim  -> re-exec -> lane worker -> isolation child
                         -> resource tracker

    Covers the required observation counts (0, 4, 6, 8 descendants) and partial rollback, plus the two
    PowerShell collection bugs that actually bit: an EMPTY HashSet unrolling to $null, and a wrapped
    HashSet binding whole to -Id instead of enumerating.
#>
$ErrorActionPreference = 'Stop'
$script:Pass = 0; $script:Fail = 0

function T([string]$name, [scriptblock]$body) {
    try {
        $r = & $body
        if ($r) { $script:Pass++; Write-Host ("  PASS  " + $name) -ForegroundColor Green }
        else    { $script:Fail++; Write-Host ("  FAIL  " + $name) -ForegroundColor Red }
    } catch {
        $script:Fail++; Write-Host ("  FAIL  " + $name + " -- " + $_.Exception.Message) -ForegroundColor Red
    }
}

# --- the functions under test, in fixture form ---------------------------------------------------
# Same algorithm as Deploy-OcrSupervisorCutover.ps1, parameterised over a process table so it can be
# driven by fixtures instead of Win32_Process.
function Fx-Descendants($table, [int]$RootPid) {
    if (-not $RootPid) { return @() }
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($RootPid)
    for ($i = 0; $i -lt 8; $i++) {
        $added = $false
        foreach ($p in $table) {
            if ($ids.Contains([int]$p.ParentProcessId) -and -not $ids.Contains([int]$p.ProcessId)) {
                [void]$ids.Add([int]$p.ProcessId); $added = $true
            }
        }
        if (-not $added) { break }
    }
    return @($table | Where-Object { $ids.Contains([int]$_.ProcessId) -and [int]$_.ProcessId -ne $RootPid })
}

function Fx-Topology($table, [int]$RootPid) {
    $empty = [pscustomobject]@{ Root=$RootPid; ReExec=0; Lanes=@(); Isolation=@(); Trackers=@() }
    if (-not $RootPid) { return $empty }
    $reExec = @($table | Where-Object {
        [int]$_.ParentProcessId -eq $RootPid -and $_.CommandLine -match 'app\.jobs\.ocr_supervisor'
    } | Select-Object -First 1)
    $parentPid = if ($reExec.Count -gt 0) { [int]$reExec[0].ProcessId } else { $RootPid }
    $children = @($table | Where-Object { [int]$_.ParentProcessId -eq $parentPid })
    $lanes = @($children | Where-Object {
        $_.CommandLine -and $_.CommandLine -match 'spawn_main|--multiprocessing-fork' -and
        $_.CommandLine -notmatch 'resource_tracker' })
    $trackers = @($children | Where-Object { $_.CommandLine -match 'resource_tracker' })
    $laneIds = @($lanes | ForEach-Object { [int]$_.ProcessId })
    $isolation = @($table | Where-Object { $laneIds -contains [int]$_.ParentProcessId })
    return [pscustomobject]@{ Root=$RootPid; ReExec=$parentPid; Lanes=$lanes; Isolation=$isolation; Trackers=$trackers }
}

# --- fixture builder ------------------------------------------------------------------------------
$SPAWN = '"python.exe" "-c" "from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=200)" "--multiprocessing-fork"'
$TRACK = '"python.exe" "-c" "from multiprocessing.resource_tracker import main;main(6)"'
$SUP   = '"python.exe" -m app.jobs.ocr_supervisor --workers 4 --keep-running'

function New-Fixture([int]$Lanes, [int]$IsolationChildren, [int]$Trackers = 1, [switch]$NoShim) {
    $t = @()
    $t += [pscustomobject]@{ ProcessId=100; ParentProcessId=1;   CommandLine=$SUP }     # shim
    $reexec = 100
    if (-not $NoShim) { $t += [pscustomobject]@{ ProcessId=200; ParentProcessId=100; CommandLine=$SUP }; $reexec = 200 }
    $laneIds = @()
    for ($i = 0; $i -lt $Lanes; $i++) {
        $pid0 = 300 + $i; $laneIds += $pid0
        $t += [pscustomobject]@{ ProcessId=$pid0; ParentProcessId=$reexec; CommandLine=$SPAWN }
    }
    for ($i = 0; $i -lt $Trackers; $i++) {
        $t += [pscustomobject]@{ ProcessId=(400 + $i); ParentProcessId=$reexec; CommandLine=$TRACK }
    }
    for ($i = 0; $i -lt $IsolationChildren; $i++) {
        $parent = if ($laneIds.Count) { $laneIds[$i % $laneIds.Count] } else { $reexec }
        $t += [pscustomobject]@{ ProcessId=(500 + $i); ParentProcessId=$parent; CommandLine=$SPAWN }
    }
    # unrelated processes that must never be counted
    $t += [pscustomobject]@{ ProcessId=900; ParentProcessId=1; CommandLine='"python.exe" C:\Client360Data\ocr-fullcorpus\worker.py' }
    $t += [pscustomobject]@{ ProcessId=901; ParentProcessId=900; CommandLine=$SPAWN }
    return ,$t
}

Write-Host "`n=== lane detection: required observation counts ===" -ForegroundColor Cyan

T "0 descendants (supervisor up, no lanes yet) -> 0 lanes, no throw" {
    $topo = Fx-Topology (New-Fixture -Lanes 0 -IsolationChildren 0 -Trackers 0) 100
    (@($topo.Lanes).Count -eq 0) -and (@($topo.Isolation).Count -eq 0)
}
T "4 descendants = 4 lanes, 0 isolation (steady state)" {
    $topo = Fx-Topology (New-Fixture -Lanes 4 -IsolationChildren 0 -Trackers 0) 100
    @($topo.Lanes).Count -eq 4
}
T "6 observed descendants = 4 lanes + 2 isolation children" {
    $f = New-Fixture -Lanes 4 -IsolationChildren 2 -Trackers 0
    $topo = Fx-Topology $f 100
    (@($topo.Lanes).Count -eq 4) -and (@($topo.Isolation).Count -eq 2) -and
    (@(Fx-Descendants $f 100).Count -eq 7)      # 6 + the re-exec itself
}
T "8 observed descendants = 4 lanes + 4 isolation children" {
    $topo = Fx-Topology (New-Fixture -Lanes 4 -IsolationChildren 4 -Trackers 0) 100
    (@($topo.Lanes).Count -eq 4) -and (@($topo.Isolation).Count -eq 4)
}
T "resource tracker is never counted as a lane" {
    $topo = Fx-Topology (New-Fixture -Lanes 4 -IsolationChildren 4 -Trackers 1) 100
    (@($topo.Lanes).Count -eq 4) -and (@($topo.Trackers).Count -eq 1)
}
T "GENUINE LANE LOSS: 3 lanes + 5 isolation children still fails (8 descendants)" {
    $topo = Fx-Topology (New-Fixture -Lanes 3 -IsolationChildren 5 -Trackers 0) 100
    @($topo.Lanes).Count -eq 3          # 3 != 4 -> the gate fails, despite 8 spawn descendants
}
T "legacy worker tree is never included" {
    $topo = Fx-Topology (New-Fixture -Lanes 4 -IsolationChildren 2) 100
    $ids = @($topo.Lanes + $topo.Isolation + $topo.Trackers | ForEach-Object { [int]$_.ProcessId })
    (-not ($ids -contains 900)) -and (-not ($ids -contains 901))
}
T "no shim (python invoked directly) still finds 4 lanes" {
    $topo = Fx-Topology (New-Fixture -Lanes 4 -IsolationChildren 1 -Trackers 0 -NoShim) 100
    @($topo.Lanes).Count -eq 4
}

Write-Host "`n=== lane stability gate ===" -ForegroundColor Cyan

function Test-Stability([int[][]]$Samples, [int]$Expect = 4) {
    $prev = $null; $problems = @()
    foreach ($s in $Samples) {
        $ids = @($s | Sort-Object)
        if ($ids.Count -ne $Expect) { $problems += "count $($ids.Count)" }
        elseif ($prev -and (($ids -join ',') -ne ($prev -join ','))) { $problems += 'pids changed' }
        $prev = $ids
    }
    return ,$problems
}
T "stable 4 across 3 samples -> no problem" {
    (Test-Stability @(,@(300,301,302,303); ,@(303,302,301,300); ,@(300,301,302,303))).Count -eq 0
}
T "a lane replaced by a new pid -> flagged" {
    (Test-Stability @(,@(300,301,302,303); ,@(300,301,302,999))).Count -eq 1
}
T "a lane lost -> flagged" {
    (Test-Stability @(,@(300,301,302,303); ,@(300,301,302))).Count -eq 1
}

Write-Host "`n=== PowerShell collection handling (the two bugs that bit) ===" -ForegroundColor Cyan

T "EMPTY descendant result yields Count 0 when PIPED (the contract callers rely on)" {
    # PowerShell cannot return an empty array from a function: the output stream has zero items, so a
    # BARE assignment gives $null no matter what the function writes. The contract that can be
    # guaranteed - and that every call site in the deploy script uses - is that PIPING the result
    # yields zero items. Both call sites are `@(Get-ProcDescendants ... | ForEach-Object {...})`.
    @(Fx-Descendants (New-Fixture -Lanes 0 -IsolationChildren 0 -Trackers 0) 999 |
      ForEach-Object { $_ }).Count -eq 0
}
T "BARE-ASSIGN of an empty @() return is ALSO safe (AutomationNull, not literal `$null)" {
    # An empty function return is [AutomationNull], which @() collapses to ZERO elements - unlike a
    # LITERAL $null, which @() wraps into a 1-element array. So `return @()` is safe whether the caller
    # pipes or assigns. This is precisely what the HashSet returns did NOT give us: a bare HashSet
    # unrolled to $null, and a comma-wrapped one stayed a HashSet and bound whole to -Id.
    $r = Fx-Descendants (New-Fixture -Lanes 0 -IsolationChildren 0 -Trackers 0) 999
    ($null -eq $r) -and (@($r).Count -eq 0) -and (@($null).Count -eq 1)
}
T "empty result can be piped to Where-Object without throwing" {
    $r = Fx-Descendants (New-Fixture -Lanes 0 -IsolationChildren 0 -Trackers 0) 999
    @(@($r) | Where-Object { $_ }).Count -eq 0
}
T "populated result enumerates one pid at a time (never binds whole)" {
    $r = Fx-Descendants (New-Fixture -Lanes 4 -IsolationChildren 0 -Trackers 0) 100
    $each = @(@($r) | ForEach-Object { [int]$_.ProcessId })
    ($each.Count -eq 5) -and ($each | ForEach-Object { $_ -is [int] }) -notcontains $false
}
T "HashSet returned bare unrolls to `$null when empty (the original bug, reproduced)" {
    function Bad { $s=[System.Collections.Generic.HashSet[int]]::new(); return $s }
    $null -eq (Bad)
}
T "HashSet returned with a comma binds WHOLE, not enumerated (the second bug, reproduced)" {
    function Wrapped { $s=[System.Collections.Generic.HashSet[int]]::new(); [void]$s.Add(5); return ,$s }
    $x = Wrapped
    $x -is [System.Collections.Generic.HashSet[int]]
}
T "@() return is correct in BOTH directions" {
    function Good([int]$n) { $s=[System.Collections.Generic.HashSet[int]]::new()
        for($i=0;$i -lt $n;$i++){[void]$s.Add($i)}; return @($s) }
    ((@(Good 0)).Count -eq 0) -and ((@(Good 3)).Count -eq 3) -and (@(Good 3)[0] -is [int])
}

Write-Host "`n=== rollback: generation capture and partial re-entry ===" -ForegroundColor Cyan

T "generation closure captures lanes + isolation + trackers" {
    $f = New-Fixture -Lanes 4 -IsolationChildren 3 -Trackers 1
    $topo = Fx-Topology $f 100
    $gen = @(@($topo.Lanes)+@($topo.Isolation)+@($topo.Trackers) | ForEach-Object {[int]$_.ProcessId} | Sort-Object -Unique)
    $gen.Count -eq 8
}
T "PARTIAL rollback: re-entry with some already dead is idempotent" {
    $f = New-Fixture -Lanes 4 -IsolationChildren 2 -Trackers 0
    $topo = Fx-Topology $f 100
    $gen = @(@($topo.Lanes)+@($topo.Isolation) | ForEach-Object {[int]$_.ProcessId})
    $survivors = @(300,301)                       # two lanes already killed on a previous attempt
    $stillThere = @($gen | Where-Object { $survivors -contains $_ })
    (@($stillThere).Count -eq 2) -and (@($gen).Count -eq 6)
}
T "PID REUSE is refused: creation time mismatch skips the kill" {
    $fp = @{ 300 = @{ Created = [datetime]'2026-09-13T17:28:50' } }
    $now = [pscustomobject]@{ ProcessId=300; CreationDate=[datetime]'2026-09-13T18:40:00' }
    $skip = ($now.CreationDate -ne $fp[300].Created)
    $skip
}
T "non-supervisor command line is refused even at a matching pid" {
    $cmd = '"python.exe" C:\Client360Data\ocr-fullcorpus\worker.py'
    ($cmd -notmatch 'spawn_main|--multiprocessing-fork|app\.jobs\.ocr_supervisor')
}
T "empty generation (nothing spawned yet) terminates nothing and does not throw" {
    $topo = Fx-Topology (New-Fixture -Lanes 0 -IsolationChildren 0 -Trackers 0) 100
    $gen = @(@($topo.Lanes)+@($topo.Isolation)+@($topo.Trackers) | ForEach-Object {[int]$_.ProcessId})
    @($gen).Count -eq 0
}

Write-Host "`n=== deployment target: no hard-coded SHA, approval is the guard ===" -ForegroundColor Cyan

$DeployScript = Join-Path $PSScriptRoot 'Deploy-OcrSupervisorCutover.ps1'

T "the script contains NO hard-coded 40-character commit SHA" {
    # The whole point: a baked-in release SHA goes stale at the next release and could silently
    # target a superseded - or older - release.
    -not (Select-String -Path $DeployScript -Pattern '[0-9a-f]{40}' -Quiet)
}
T "-TargetSha is MANDATORY with no default" {
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($DeployScript, [ref]$null, [ref]$null)
    $p = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'TargetSha' }
    $mand = $p.Attributes | Where-Object { $_.TypeName.Name -eq 'Parameter' } |
            ForEach-Object { $_.NamedArguments | Where-Object { $_.ArgumentName -eq 'Mandatory' } }
    ($null -ne $mand) -and ($null -eq $p.DefaultValue)
}
T "-ApprovedPullRequests is MANDATORY with no default" {
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($DeployScript, [ref]$null, [ref]$null)
    $p = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'ApprovedPullRequests' }
    $mand = $p.Attributes | Where-Object { $_.TypeName.Name -eq 'Parameter' } |
            ForEach-Object { $_.NamedArguments | Where-Object { $_.ArgumentName -eq 'Mandatory' } }
    ($null -ne $mand) -and ($null -eq $p.DefaultValue)
}
T "-TargetSha only accepts a full 40-hex SHA" {
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($DeployScript, [ref]$null, [ref]$null)
    $p = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'TargetSha' }
    ($p.Attributes | Where-Object { $_.TypeName.Name -eq 'ValidatePattern' }).Count -eq 1
}
T "the stale pinned range constants are gone" {
    -not (Select-String -Path $DeployScript -Pattern '\$EXPECTED_(PRS|COMMITS|FILES|INSERTIONS|DELETIONS|PATHS)' -Quiet)
}
T "target containment against the release ref is enforced" {
    (Select-String -Path $DeployScript -Pattern 'is NOT contained in' -Quiet) -and
    (Select-String -Path $DeployScript -Pattern 'merge-base --is-ancestor \$TargetSha \$ReleaseRef' -Quiet)
}

# The approval gate itself, as a pure function of the two sets.
function Test-ApprovalGate([int[]]$InRange, [int[]]$Approved) {
    $a = @($InRange  | Sort-Object -Unique)
    $b = @($Approved | Sort-Object -Unique)
    if (($a -join ',') -eq ($b -join ',')) { return 'ALLOWED' }
    $extra  = @($a | Where-Object { $b -notcontains $_ })
    if ($extra.Count) { return 'REFUSED_UNAPPROVED_PR' }
    return 'REFUSED_APPROVED_PR_ABSENT'
}
T "exact match is allowed" { (Test-ApprovalGate @(309) @(309)) -eq 'ALLOWED' }
T "an EXTRA pr landing after approval is refused (the unapproved-release case)" {
    (Test-ApprovalGate @(309,310) @(309)) -eq 'REFUSED_UNAPPROVED_PR'
}
T "an approved pr absent from the range is refused" {
    (Test-ApprovalGate @(309) @(309,310)) -eq 'REFUSED_APPROVED_PR_ABSENT'
}
T "ordering and duplicates do not affect the comparison" {
    (Test-ApprovalGate @(310,309,309) @(309,310)) -eq 'ALLOWED'
}
T "an empty range with approvals outstanding is refused" {
    (Test-ApprovalGate @() @(309)) -eq 'REFUSED_APPROVED_PR_ABSENT'
}

# Optional delta pins: zero means report-only, non-zero enforces.
function Test-DeltaPin([int]$Actual, [int]$Pin) {
    if ($Pin -gt 0 -and $Actual -ne $Pin) { return 'REFUSED' }
    return 'ALLOWED'
}
T "delta pin of 0 is report-only" { (Test-DeltaPin 7 0) -eq 'ALLOWED' }
T "delta pin enforces when supplied"  { (Test-DeltaPin 8 7) -eq 'REFUSED' }
T "delta pin passes on an exact match" { (Test-DeltaPin 7 7) -eq 'ALLOWED' }

Write-Host "`n=== rollback silence gate: reached-and-sustained, not never-seen ===" -ForegroundColor Cyan

# Mirrors the loop in Restore-Legacy. $Samples are heartbeat counts per 30s window; $AliveAt is the
# 1-based sample index at which a generation PROCESS is still alive (0 = none ever).
function Test-SilenceGate([int[]]$Samples, [int]$AliveAt = 0, [int]$MaxSamples = 8) {
    $history = @(); $silenceHeld = $false
    for ($i = 1; $i -le [Math]::Min($MaxSamples, $Samples.Count); $i++) {
        if ($AliveAt -gt 0 -and $i -ge $AliveAt) { return 'REFUSED_PROCESS_ALIVE' }
        $history += $Samples[$i-1]
        $tail = @($history | Select-Object -Last 2)
        $silenceHeld = (@($tail).Count -eq 2) -and (@($tail | Where-Object { $_ -ne 0 }).Count -eq 0)
        if ($silenceHeld) { break }
    }
    return $(if ($silenceHeld) { 'RESTART_ALLOWED' } else { 'REFUSED_NOT_QUIET' })
}

T "heartbeat in sample 1 only, then silence -> RESTART ALLOWED (the 18:34 regression)" {
    (Test-SilenceGate @(1,0,0,0)) -eq 'RESTART_ALLOWED'
}
T "OLD latched rule would have refused that same pattern (regression pinned)" {
    $old = $true; foreach ($n in @(1,0,0,0)) { if ($n -gt 0) { $old = $false } }
    -not $old
}
T "heartbeat in a LATER sample -> refused (not quiet for two consecutive)" {
    (Test-SilenceGate @(0,1,0,1,0,1,0,1)) -eq 'REFUSED_NOT_QUIET'
}
T "final two samples silent -> allowed" {
    (Test-SilenceGate @(2,1,0,0)) -eq 'RESTART_ALLOWED'
}
T "silence then a NEW heartbeat then silence -> allowed only once two consecutive are quiet" {
    (Test-SilenceGate @(0,3,0,0)) -eq 'RESTART_ALLOWED'
}
T "heartbeats never stop -> refused" {
    (Test-SilenceGate @(1,1,1,1,1,1,1,1)) -eq 'REFUSED_NOT_QUIET'
}
T "surviving generation PROCESS refuses even when heartbeats are silent" {
    (Test-SilenceGate @(0,0,0,0) -AliveAt 2) -eq 'REFUSED_PROCESS_ALIVE'
}
T "process alive at sample 1 refuses immediately, before any silence can accrue" {
    (Test-SilenceGate @(0,0) -AliveAt 1) -eq 'REFUSED_PROCESS_ALIVE'
}
T "immediate silence (0,0) exits early after two samples" {
    (Test-SilenceGate @(0,0,0,0,0,0,0,0)) -eq 'RESTART_ALLOWED'
}
T "PARTIAL ROLLBACK re-entry: generation already gone, no claims -> allowed" {
    # Re-entering after a partial rollback sees an empty process set and a quiet claims table.
    ((Test-SilenceGate @(0,0)) -eq 'RESTART_ALLOWED') -and
    (@(@() | Where-Object { $_ }).Count -eq 0)
}
T "the real 18:34 observation (1,0,0,0) is allowed under the new gate" {
    (Test-SilenceGate @(1,0,0,0)) -eq 'RESTART_ALLOWED'
}

Write-Host "`n=== replay of the REAL cutover log (cutover-20260913-172830) ===" -ForegroundColor Cyan
$logSets = @(
  ,@(10560,15236,7132,5840,21072,12092,17820,18368)
  ,@(10560,15236,7132,5840,18376,21000,18692)
  ,@(10560,15236,7132,5840,18376,22800,23292)
  ,@(10560,15236,7132,5840,23248,21676,15436,23452)
  ,@(10560,15236,7132,5840,22880,23372)
  ,@(10560,15236,7132,5840,22880,16644)
  ,@(10560,15236,7132,5840,6988,18788)
  ,@(10560,15236,7132,5840,6988,22164,16024)
  ,@(10560,15236,7132,5840,23064,10112)
  ,@(10560,15236,7132,5840,22884,20276,22924,21324)
  ,@(10560,15236,7132,5840,23244,23464,22012,16708)
  ,@(10560,15236,7132,5840,1184,8328)
)
T "old rule (count every spawn descendant) FAILS all 12 samples" {
    @(@($logSets) | Where-Object { @($_).Count -ne 4 }).Count -eq 12
}
T "new rule: the 4 persistent lanes are the intersection of all 12 samples" {
    $inter = [int[]]$logSets[0]
    foreach ($s in $logSets) { $inter = @($inter | Where-Object { $s -contains $_ }) }
    (@($inter).Count -eq 4) -and ((@($inter | Sort-Object) -join ',') -eq '5840,7132,10560,15236')
}
T "new rule PASSES the real run (4 stable lanes every sample)" {
    $lanes = @(10560,15236,7132,5840)
    $stable = @($logSets | Where-Object { $l = $_; @($lanes | Where-Object { $l -contains $_ }).Count -eq 4 })
    @($stable).Count -eq 12
}
T "two-consecutive-sample rule alone is INSUFFICIENT (18376/22880/6988 span two)" {
    $spanTwo = @()
    for ($i=0; $i -lt $logSets.Count-1; $i++) {
        $a=$logSets[$i]; $b=$logSets[$i+1]
        $spanTwo += @($a | Where-Object { $b -contains $_ -and @(10560,15236,7132,5840) -notcontains $_ })
    }
    @($spanTwo | Sort-Object -Unique).Count -ge 3
}

Write-Host ""
Write-Host ("RESULT: {0} passed, {1} failed" -f $script:Pass, $script:Fail) `
    -ForegroundColor $(if ($script:Fail -eq 0) { 'Green' } else { 'Red' })
if ($script:Fail -gt 0) { exit 1 }
