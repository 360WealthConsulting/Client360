<#
.SYNOPSIS
    Guarded deployment of release 5adfb55 + four-worker OCR supervisor cutover.

.DESCRIPTION
    ONE script for the entire authorized sequence, because a partial unelevated deployment is worse
    than none: the legacy task would be stopped with nothing able to replace it.

    It re-verifies EVERY guard itself rather than trusting the survey that produced it. State can
    change between that survey and this run, and a guard checked only beforehand is not a guard.
    Nothing is written until all of them pass.

    FAIL-CLOSED AND REVERSIBLE. If any check fails before final acceptance the script does NOT bypass
    it and does NOT touch a database row: it stops and unregisters the supervisor as appropriate,
    restores the exported legacy task, starts it, and PROVES OCR writes resumed before stopping.

    NO EXTERNAL LOCK HOLDER. The merged supervisor (PR #308) owns both lifetime advisory locks itself
    — 511005888 (single supervisor) and 511005002 (excludes the in-app ocr-incremental-sweep). This
    script VERIFIES that ownership against live database lock state; it never creates it.

    NEVER DOES: move or relink documents, modify production data, change ACLs, run Alembic, alter
    ownership logic, merge anything, or touch T: / the completed inventory.

.PARAMETER DryRun
    Run every read-only guard, the baseline capture, the exact release-range validation and the
    merged source-root preflight, then stop BEFORE the first operational change. It does not stop
    Client360, does not stop or disable any task, does not update the checkout, does not register a
    task, and alters no production data. Use this first.

.EXAMPLE
    .\Deploy-OcrSupervisorCutover.ps1 -DryRun
    .\Deploy-OcrSupervisorCutover.ps1
#>
[CmdletBinding()]
param(
    [switch] $DryRun,
    [string] $ProjectRoot     = 'C:\Client360',
    [string] $TargetSha       = '5adfb55f9fec884a8b223d26dbd69666ed70d0df',
    [string] $ExpectedFrom    = 'bc6b20c8f6a58ecadd7330cbfc1fdb7b514d56e9',
    [string] $ExpectedAlembic = 'ocrclaim01',
    [string] $ServiceName     = 'Client360',
    [string] $LegacyTask      = 'Client360 OCR Full Corpus',
    [string] $SupervisorTask  = 'Client360 OCR Parallel Supervisor',
    [int]    $Workers         = 4,
    [int]    $MonitorMinutes  = 12,
    [string] $HealthBase      = 'http://127.0.0.1:8360',
    [string] $OpsDir          = 'C:\Client360Data\ocr-parallel',
    [string] $ArtifactDir     = 'C:\Client360Data\ocr-parallel\cutover'
)

$ErrorActionPreference = 'Stop'

# --- DERIVED, NOT GUESSED -------------------------------------------------------------------------
# Every value below was computed from the real repository for the exact range
# bc6b20c8f6a58ecadd7330cbfc1fdb7b514d56e9..5adfb55f9fec884a8b223d26dbd69666ed70d0df and is pinned
# here. The script recomputes each one at run time and refuses if any differs, so an unexpected
# commit arriving on the release branch is a hard stop rather than a surprise in production.
$EXPECTED_PRS        = @(303, 306, 307, 308)
$EXPECTED_COMMITS    = 10
$EXPECTED_FILES      = 28
$EXPECTED_INSERTIONS = 3664
$EXPECTED_DELETIONS  = 176
$EXPECTED_PATHS = @(
    'CHANGELOG.md',
    'app/deploy/ocr_source_roots.py',
    'app/jobs/ocr_supervisor.py',
    'app/routes/person_edit.py',
    'app/services/client360/profile_overview.py',
    'app/services/client360/registry.py',
    'app/services/client360/sections.py',
    'app/services/people.py',
    'app/static/css/client360.css',
    'app/templates/client360/_section_nav.html',
    'app/templates/client360/household.html',
    'app/templates/client360/workspace.html',
    'app/templates/people/edit.html',
    'app/templating.py',
    'deploy/windows/install_ocr_supervisor_task.ps1',
    'docs/OCR_PARALLEL_RUNNER.md',
    'tests/test_client360_profile_tabs.py',
    'tests/test_client_contact_visibility.py',
    'tests/test_client_overview_layout.py',
    'tests/test_client_section_nav.py',
    'tests/test_client_workspace_dashboard.py',
    'tests/test_ocr_source_roots.py',
    'tests/test_ocr_supervisor.py',
    'tests/test_ocr_supervisor_sweep_lock.py',
    'tests/test_person_profile_edit_authorization.py',
    'tests/test_person_profile_editing.py',
    'tests/test_task_dashboard_client_filter.py',
    'tests/test_ui_stabilization.py'
)

#: The supervisor's two LIFETIME advisory locks (PR #308). It owns both itself, on one backend.
$LOCK_SUPERVISOR = 511005888
$LOCK_SWEEP      = 511005002

$script:Downtime        = $null
$script:LegacyDisabled  = $false
$script:SupervisorBuilt = $false
$script:BackupXml       = $null
$script:AlreadyDeployed = $false

New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:LogPath = Join-Path $ArtifactDir "cutover-$stamp.log"
Start-Transcript -Path $script:LogPath | Out-Null

function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Head([string]$m) { Say ''; Say ("=" * 78) 'Cyan'; Say $m 'Cyan'; Say ("=" * 78) 'Cyan' }
function Fail([string]$m) { throw "GUARD FAILED: $m" }

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

# Run a python snippet in the project root with a READ-ONLY database session, returning parsed JSON.
#
# The previous version piped the body to `& $python -` and reported "$out" on failure. stdout is EMPTY
# when python raises, and stderr was never captured, so a real error (a missing column, in the run
# that prompted this) surfaced as the bare string "database query failed: " with nothing after the
# colon and the traceback destroyed. This version writes the script to a file, captures stdout and
# stderr to SEPARATE files, and on failure reports the label, exit code, full stderr, the JSON parse
# error where relevant, and the path to the exact script that ran - so the next failure is diagnosable
# from the log alone.
function Invoke-DbJson {
    param([string]$Body, [string]$Label = 'query')

    $prelude = @'
import json, os, sys, traceback
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
load_dotenv("app/.env")
_e = create_engine(os.environ["DATABASE_URL"],
                   connect_args={"options": "-c default_transaction_read_only=on"})
def q(sql, **p):
    try:
        with _e.connect() as c:
            c.execute(text("SET TRANSACTION READ ONLY"))
            return [dict(r) for r in c.execute(text(sql), p).mappings()]
    except Exception:
        # Put the offending SQL next to the exception, so the log shows both.
        sys.stderr.write("FAILED SQL >>>\n%s\n<<< params=%r\n" % (sql, p))
        traceback.print_exc()
        raise
'@
    $tag     = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $pyFile  = Join-Path $ArtifactDir "dbq-$Label-$tag.py"
    $outFile = Join-Path $ArtifactDir "dbq-$Label-$tag.out"
    $errFile = Join-Path $ArtifactDir "dbq-$Label-$tag.err"
    ($prelude + "`n" + $Body) | Set-Content -Path $pyFile -Encoding UTF8

    $proc = Start-Process -FilePath $python -ArgumentList @($pyFile) -WorkingDirectory $ProjectRoot `
                          -RedirectStandardOutput $outFile -RedirectStandardError $errFile `
                          -NoNewWindow -Wait -PassThru
    $exit   = $proc.ExitCode
    $stdout = if (Test-Path $outFile) { (Get-Content $outFile -Raw) } else { '' }
    $stderr = if (Test-Path $errFile) { (Get-Content $errFile -Raw) } else { '' }

    if ($exit -ne 0) {
        $msg  = "database query '$Label' FAILED (exit $exit)`n"
        $msg += "  script : $pyFile`n"
        $msg += "  stdout : $(if ([string]::IsNullOrWhiteSpace($stdout)) { '<empty>' } else { $stdout.Trim() })`n"
        $msg += "  stderr :`n$(if ([string]::IsNullOrWhiteSpace($stderr)) { '    <empty>' } else { ($stderr.Trim() -split "`n" | ForEach-Object { '    ' + $_ }) -join "`n" })"
        throw $msg
    }
    if ([string]::IsNullOrWhiteSpace($stdout)) {
        throw "database query '$Label' exited 0 but printed nothing. script: $pyFile`n  stderr:`n$stderr"
    }
    try {
        $parsed = $stdout | ConvertFrom-Json
    } catch {
        throw ("database query '$Label' returned unparseable output: $($_.Exception.Message)`n" +
               "  script : $pyFile`n  stdout : $($stdout.Trim())`n  stderr : $($stderr.Trim())")
    }
    # Clean up only on success; a failed query keeps its artifacts for inspection.
    foreach ($f in @($pyFile, $outFile, $errFile)) {
        if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
    return $parsed
}

function Get-Counters {
    Invoke-DbJson -Label 'counters' -Body @'
ACTIVE = ("d.status='active' AND d.deleted_at IS NULL AND d.archived=false "
          "AND d.archived_at IS NULL")
r = q(f"""SELECT count(DISTINCT d.id) AS active,
                 count(DISTINCT d.id) FILTER (WHERE o.status='completed') AS ocr_complete,
                 count(DISTINCT d.id) FILTER (WHERE o.status IS DISTINCT FROM 'completed') AS ocr_backlog,
                 count(DISTINCT d.id) FILTER (WHERE o.document_id IS NULL) AS never_attempted,
                 count(DISTINCT d.id) FILTER (WHERE o.status='completed'
                       AND k.document_id IS NOT NULL) AS classified
          FROM documents d
          LEFT JOIN document_ocr o ON o.document_id=d.id
          LEFT JOIN document_classifications k ON k.document_id=d.id
          WHERE {ACTIVE}""")[0]
w = q("""SELECT max(updated_at) AS last_write,
                count(*) FILTER (WHERE updated_at > now() - interval '2 minutes')  AS writes_2m,
                count(*) FILTER (WHERE updated_at > now() - interval '5 minutes')  AS writes_5m,
                count(*) FILTER (WHERE updated_at > now() - interval '15 minutes') AS writes_15m,
                count(*) FILTER (WHERE status='failed')    AS failed,
                count(*) FILTER (WHERE status='timed_out') AS timed_out
         FROM document_ocr""")[0]
print(json.dumps({**r, **w}, default=str))
'@
}

# Requirement 6 and 9: prove the SUPERVISOR owns both lifetime locks on ONE live backend, from
# database lock state. Heartbeat text is not evidence - a JSON file can say anything.
function Get-LockOwnership {
    Invoke-DbJson -Label 'lock-ownership' -Body @'
rows = q("""
  SELECT a.pid,
         a.backend_start,
         bool_or(((l.classid::bigint<<32)|l.objid::bigint) = 511005888) AS has_supervisor,
         bool_or(((l.classid::bigint<<32)|l.objid::bigint) = 511005002) AS has_sweep,
         count(*) AS advisory_locks
  FROM pg_locks l
  JOIN pg_stat_activity a ON a.pid = l.pid
  WHERE l.locktype = 'advisory' AND a.datname = current_database() AND l.granted
  GROUP BY a.pid, a.backend_start
""")
both = [r for r in rows if r["has_supervisor"] and r["has_sweep"]]
legacy = q("""
  SELECT count(*) AS n FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
  WHERE l.locktype='advisory' AND a.datname=current_database() AND l.granted
    AND ((l.classid::bigint<<32)|l.objid::bigint) = 511005777
""")[0]["n"]
print(json.dumps({
    "owner_pid": both[0]["pid"] if len(both) == 1 else None,
    "owner_backend_start": both[0]["backend_start"] if len(both) == 1 else None,
    "owners": len(both),
    "any_supervisor_lock": any(r["has_supervisor"] for r in rows),
    "any_sweep_lock": any(r["has_sweep"] for r in rows),
    "legacy_lock_holders": legacy,
}, default=str))
'@
}

# Claim health. duplicate_live_claims is KEPT as a primary-key invariant check, but it is explicitly
# NOT evidence about the in-app sweep: the sweep takes no claim, so it can never appear here. Sweep
# exclusion is proven by lock 511005002 ownership above, and nowhere else.
function Get-ClaimHealth {
    Invoke-DbJson -Label 'claim-health' -Body @'
dupes = q("""SELECT count(*) AS duplicate_live
             FROM (SELECT document_id FROM ocr_document_claims
                   WHERE state = 'claimed'
                   GROUP BY document_id HAVING count(*) > 1) x""")[0]
stale = q("""SELECT count(*) AS stale FROM ocr_document_claims
             WHERE state='claimed' AND lease_expires_at < now()""")[0]
states = q("""SELECT state, count(*) AS n FROM ocr_document_claims
              WHERE heartbeat_at > now() - interval '30 minutes' GROUP BY state""")
live = q("""SELECT count(DISTINCT worker_id) AS lanes FROM ocr_document_claims
            WHERE heartbeat_at > now() - interval '3 minutes'""")[0]
print(json.dumps({**dupes, **stale, **live,
                  "states": {s["state"]: s["n"] for s in states}}, default=str))
'@
}

# Requirement 8: which distinct lanes have genuinely COMPLETED work since a given instant.
# worker_id is "ocr<N>-<host>-<pid>-<hex>" (ocr_parallel: new_worker_id(f"ocr{i}")), and a finished
# claim is state='done' (ocr_claims.complete). Configured worker count proves nothing; this does.
function Get-LanesCompleted([string]$SinceIso) {
    Invoke-DbJson -Label 'lanes-completed' -Body @"
rows = q("""SELECT DISTINCT split_part(worker_id, '-', 1) AS lane
            FROM ocr_document_claims
            WHERE state = 'done' AND heartbeat_at >= (:since)::timestamptz""", since="$SinceIso")
print(json.dumps({"lanes": sorted(r["lane"] for r in rows)}))
"@
}

function Test-Health {
    $r = @{}
    foreach ($p in '/health', '/readiness') {
        try {
            $resp = Invoke-WebRequest -Uri "$HealthBase$p" -UseBasicParsing -TimeoutSec 20
            $r[$p] = @{ code = $resp.StatusCode; body = ($resp.Content | ConvertFrom-Json) }
        } catch { $r[$p] = @{ code = -1; body = $null; error = $_.Exception.Message } }
    }
    return $r
}

function Test-HealthOk {
    $h = Test-Health
    if ($h['/health'].code -ne 200 -or $h['/readiness'].code -ne 200) { return $false }
    $m = $h['/readiness'].body.checks.migrations
    return ($m.current_head -eq $ExpectedAlembic -and $m.expected_head -eq $ExpectedAlembic -and $m.in_sync)
}

function Assert-Healthy([string]$phase) {
    $h = Test-Health
    if ($h['/health'].code -ne 200)    { Fail "$phase - /health is $($h['/health'].code)" }
    if ($h['/readiness'].code -ne 200) { Fail "$phase - /readiness is $($h['/readiness'].code)" }
    $m = $h['/readiness'].body.checks.migrations
    if ($m.current_head -ne $ExpectedAlembic -or $m.expected_head -ne $ExpectedAlembic -or -not $m.in_sync) {
        Fail "$phase - migrations not in sync at ${ExpectedAlembic}: current=$($m.current_head) expected=$($m.expected_head) in_sync=$($m.in_sync)"
    }
    Say "  OK $phase - /health 200, /readiness 200, migrations $($m.current_head) in_sync=$($m.in_sync)" 'Green'
    return $h
}

# --- deployed route verification (ONE implementation, used by BOTH -DryRun and final acceptance) ---
#
# There is deliberately no separate, weaker dry-run version: the dry run must exercise the same
# selection, the same URLs and the same validation that the real cutover's last phase runs, or it
# proves nothing about it. The previous script only ran this at the very end, which is how a broken
# query survived to abort a completed 12-minute cutover.
#
# SCOPE NOTE. `people` has NO organization column - verified against the live schema. Organizations
# are separate entities (organization_profiles.relationship_entity_id), and access to a person's
# workspace is decided by RECORD SCOPE inside get_workspace (record_in_scope) together with the
# `client.read` capability, not by an org foreign key. The selection below therefore scopes on what
# actually exists: an ACTIVE person, with a non-blank full_name (the resolver key - a blank one makes
# a real client unresolvable and would render as a legitimate error), that belongs to a household.
#
# TWO LAYERS, because one alone cannot prove what is claimed:
#   A. HTTP, unauthenticated. Proves routing, middleware and that nothing 500s. These routes answer
#      303 -> /auth/login?next=... when unauthenticated; that is EXPECTED and is asserted to be a
#      redirect to the login page rather than any 5xx. It does NOT prove the templates render.
#   B. In-process render, using the repository's OWN safe test mechanism - the pattern in
#      tests/test_client_overview_layout.py: an explicit Principal, a synthetic Request, and a direct
#      call into get_workspace and the route render helpers. No HTTP, no session, no cookie, no
#      scheduler, no write. This is what actually proves the deployed templates render.
function Invoke-RouteChecks([string]$Context) {
    $result = @{ problems = @(); incomplete = @(); personId = $null }

    $rid = Invoke-DbJson -Label 'route-person' -Body @'
rows = q("""SELECT id, full_name, household_id, active
            FROM people
            WHERE active = true
              AND coalesce(btrim(full_name), '') <> ''
              AND household_id IS NOT NULL
            ORDER BY id LIMIT 1""")
print(json.dumps(rows, default=str))
'@
    Say "  scope used        : record scope (record_in_scope) + capability 'client.read'."
    Say "                      people has NO organization column; organizations are separate entities"
    Say "                      (organization_profiles.relationship_entity_id). Selection therefore"
    Say "                      scopes on active=true AND non-blank full_name AND household_id IS NOT NULL."
    if (-not $rid -or @($rid).Count -eq 0) {
        Say "  WARNING no active person with a usable full_name and household; routes cannot be checked" 'Yellow'
        $result.incomplete += "$Context - profile route checks SKIPPED: no eligible person found."
        return $result
    }
    $personId = $rid[0].id
    $result.personId = $personId
    Say "  selected person   : id=$personId full_name='$($rid[0].full_name)' household_id=$($rid[0].household_id) active=$($rid[0].active)"
    Say ""

    # --- layer A: HTTP, unauthenticated -----------------------------------------------------------
    Say "  [A] HTTP (unauthenticated) - proves routing and that nothing 500s:"
    # These probes use .NET HttpClient with AllowAutoRedirect = $false rather than Invoke-WebRequest.
    # Invoke-WebRequest cannot observe a redirect without complaining: -MaximumRedirection 0 makes a
    # 303 raise "maximum redirection count has been exceeded", and $ErrorActionPreference = 'Stop'
    # turns that into a TERMINATING error, so the log filled with TerminatingError entries for an
    # outcome that is entirely expected. -SkipHttpErrorCheck does not help - it suppresses HTTP STATUS
    # errors, not the redirect-count error - and made it worse, because the resulting exception
    # carries no .Response and every route degraded to "no response".
    #
    # HttpClient returns the 303 and its Location as ordinary values, with no exception at all. The
    # URLs probed, the accepted status codes and the classification below are unchanged; only the
    # mechanism that fetches the status differs.
    $routes = @("/people/$personId", "/people/$personId/edit", "/client/$personId")
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $http = [System.Net.Http.HttpClient]::new($handler)
    $http.Timeout = [TimeSpan]::FromSeconds(25)
    try {
    foreach ($r in $routes) {
        $code = $null; $loc = $null; $err = $null
        try {
            $resp = $http.GetAsync("$HealthBase$r").GetAwaiter().GetResult()
            $code = [int]$resp.StatusCode
            $loc  = if ($resp.Headers.Location) { $resp.Headers.Location.OriginalString } else { $null }
            $resp.Dispose()
        } catch {
            # Reached only when there is genuinely no HTTP response: connection refused, timeout, DNS.
            $code = -1
            $err  = $_.Exception.GetBaseException().Message
        }
        $verdict = 'UNEXPECTED'
        if ($code -ge 500)                                   { $verdict = 'FAIL (5xx)' }
        elseif ($code -eq -1)                                { $verdict = 'FAIL (no response)' }
        elseif ($code -ge 200 -and $code -lt 300)            { $verdict = 'rendered' }
        elseif ($code -in 301,302,303,307,308 -and "$loc" -match '^/auth/login') { $verdict = 'expected auth redirect' }
        elseif ($code -in 401,403)                           { $verdict = 'expected auth gate' }
        elseif ($code -eq 404)                               { $verdict = 'NOT FOUND' }

        Say ("      {0,-26} HTTP {1,-4} location={2,-42} {3}" -f `
             $r, $code, $(if ($loc) { $loc } else { '-' }), $verdict) `
            $(if ($verdict -like 'FAIL*') { 'Red' } elseif ($verdict -eq 'UNEXPECTED' -or $verdict -eq 'NOT FOUND') { 'Yellow' } else { 'Green' })

        if ($verdict -like 'FAIL*') {
            $result.problems += "$Context route $r returned $(if ($code -eq -1) { "no response: $err" } else { $code })"
        } elseif ($verdict -eq 'NOT FOUND' -or $verdict -eq 'UNEXPECTED') {
            $result.problems += "$Context route $r returned an unexpected $code (location='$loc')"
        }
    }
    } finally {
        $http.Dispose()
        $handler.Dispose()
    }

    # --- layer B: in-process render, the repository's own mechanism --------------------------------
    Say ""
    Say "  [B] In-process render (explicit Principal, no HTTP/session/scheduler) - proves templates:"
    $proof = $null
    try {
        $proof = Invoke-DbJson -Label 'route-template-proof' -Body @"
import traceback
sys.path.insert(0, os.getcwd())
from starlette.requests import Request
from app.security.models import Principal

CAPS = frozenset({"client.read", "client.write", "tax.read", "record.read_all", "timeline.read",
                  "documents.view", "tasks.read", "compliance.read", "communications.view",
                  "people.read", "people.write"})
STAFF = Principal(0, "cutover-verify@local", "Cutover Verification", CAPS)
PERSON = $personId

def req(path):
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                    "server": ("127.0.0.1", 8360), "path": path, "raw_path": path.encode(),
                    "root_path": "", "query_string": b"", "headers": [(b"host", b"127.0.0.1:8360")],
                    "client": ("127.0.0.1", 0), "app": None})

out, ws = {}, None
try:
    from app.services.client360.service import get_workspace
    ws = get_workspace(STAFF, person_id=PERSON)
    out["get_workspace"] = ("OUT_OF_SCOPE" if ws is None
                            else "ok sections=%d" % len(ws.get("section_keys") or []))
except Exception:
    out["get_workspace"] = "EXC " + traceback.format_exc()

try:
    if ws is None:
        out["client_template"] = "SKIPPED (no workspace)"
    else:
        from app.routes.client360 import _render
        r = _render(req("/client/%d" % PERSON), ws, STAFF, "summary")
        out["client_template"] = "ok status=%s bytes=%d" % (r.status_code, len(r.body))
except Exception:
    out["client_template"] = "EXC " + traceback.format_exc()

try:
    from sqlalchemy import select
    from app.db import engine as _eng, people as _people
    from app.routes.person_edit import _render_form
    with _eng.connect() as c:
        row = c.execute(select(_people).where(_people.c.id == PERSON)).mappings().one_or_none()
    if row is None:
        out["edit_template"] = "SKIPPED (person row not found)"
    else:
        r2 = _render_form(req("/people/%d/edit" % PERSON), dict(row), errors=[])
        out["edit_template"] = "ok status=%s bytes=%d" % (r2.status_code, len(r2.body))
except Exception:
    out["edit_template"] = "EXC " + traceback.format_exc()

print(json.dumps(out, default=str))
"@
    } catch {
        Say "      template proof could not run:" 'Red'
        ("$($_.Exception.Message)" -split "`n") | ForEach-Object { Say "      $_" 'Red' }
        $result.problems += "$Context in-process template proof failed to execute"
        return $result
    }
    foreach ($k in 'get_workspace', 'client_template', 'edit_template') {
        $v = "$($proof.$k)"
        $bad = $v.StartsWith('EXC') -or $v -eq 'OUT_OF_SCOPE'
        $skip = $v.StartsWith('SKIPPED')
        Say ("      {0,-18} {1}" -f $k, ($v -split "`n")[0]) `
            $(if ($bad) { 'Red' } elseif ($skip) { 'Yellow' } else { 'Green' })
        if ($v -match "`n") { ($v -split "`n") | Select-Object -Skip 1 | ForEach-Object { Say "        $_" 'Red' } }
        if ($bad)      { $result.problems   += "$Context template proof '$k': $(($v -split "`n")[0])" }
        elseif ($skip) { $result.incomplete += "$Context template proof '$k' was skipped: $v" }
    }

    $verdictLine = if ($result.problems.Count -gt 0) { 'ROUTE VERDICT: FAILED' }
                   elseif ($result.incomplete.Count -gt 0) { 'ROUTE VERDICT: INCOMPLETE' }
                   else { 'ROUTE VERDICT: PASSED - no 5xx, and both templates rendered in-process' }
    Say ""
    Say "  $verdictLine" $(if ($result.problems.Count -gt 0) { 'Red' } elseif ($result.incomplete.Count -gt 0) { 'Yellow' } else { 'Green' })
    return $result
}

# Processes belonging to the LEGACY worker only. Scoped by command line and descent, so nothing else
# on the host - the completed T: inventory above all - can ever be caught by this.
function Get-LegacyWorkerProcs {
    $all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")
    $seed = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -match 'ocr-fullcorpus\\worker\.py' })
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($p in $seed) { [void]$ids.Add([int]$p.ProcessId) }
    for ($i = 0; $i -lt 6; $i++) {
        foreach ($p in $all) { if ($ids.Contains([int]$p.ParentProcessId)) { [void]$ids.Add([int]$p.ProcessId) } }
    }
    return @($all | Where-Object { $ids.Contains([int]$_.ProcessId) })
}

# Worker counting, corrected.
#
# The previous version counted python.exe processes whose PARENT was the supervisor pid, and reported
# workers=1 while all four lanes were demonstrably completing work. Two facts make that wrong on
# Windows:
#
#   1. `.venv\Scripts\python.exe` is a LAUNCHER SHIM. It re-execs the real interpreter
#      (Python312\python.exe) as its own child, so the supervisor is two processes deep before any
#      worker exists. The multiprocessing workers are therefore GRANDCHILDREN of the pid whose
#      command line matches app.jobs.ocr_supervisor - the only direct child is the re-exec.
#   2. multiprocessing's spawn context also starts a RESOURCE TRACKER process, which looks like a
#      worker (same interpreter, `-c` bootstrap) but processes nothing. Counting it would report five.
#
# So: take the whole descendant closure of the supervisor root, keep only genuine spawn workers, and
# exclude the resource tracker explicitly. Scoping to the closure also keeps orphaned workers from an
# earlier run - which do exist on this host - from being counted.
function Get-ProcDescendants([int]$RootPid) {
    # Always returns an ARRAY of process objects - never a HashSet and never $null. PowerShell
    # enumerates a collection on return, so an EMPTY set becomes $null and the caller's .Contains()
    # throws; wrapping it to prevent that instead yields a HashSet that binds whole to -Id. Returning
    # @(...) sidesteps both. Both failure modes were hit for real during the orphan cleanup.
    if (-not $RootPid) { return @() }
    $all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($RootPid)
    for ($i = 0; $i -lt 8; $i++) {                 # fixed-point; depth here is 3, 8 is ample
        $added = $false
        foreach ($p in $all) {
            if ($ids.Contains([int]$p.ParentProcessId) -and -not $ids.Contains([int]$p.ProcessId)) {
                [void]$ids.Add([int]$p.ProcessId); $added = $true
            }
        }
        if (-not $added) { break }
    }
    return @($all | Where-Object { $ids.Contains([int]$_.ProcessId) -and [int]$_.ProcessId -ne $RootPid })
}

function Get-SupervisorProc {
    # The TOPMOST supervisor process: the shim, not the interpreter it re-execs. Identified as the
    # match whose own parent is not also a supervisor match.
    $matches = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                 Where-Object { $_.CommandLine -and $_.CommandLine -match 'app\.jobs\.ocr_supervisor' })
    if ($matches.Count -eq 0) { return $null }
    $ids = @($matches | ForEach-Object { [int]$_.ProcessId })
    $roots = @($matches | Where-Object { $ids -notcontains [int]$_.ParentProcessId })
    if ($roots.Count -gt 0) { return ($roots | Sort-Object CreationDate | Select-Object -First 1) }
    return ($matches | Sort-Object CreationDate | Select-Object -First 1)
}

# PERSISTENT LANE WORKERS vs PER-DOCUMENT ISOLATION CHILDREN.
#
# Counting every spawn descendant is wrong and failed a healthy cutover. app/services/ocr_isolation.py
# runs EACH DOCUMENT in its own spawned child ("per-document subprocess isolation", on by default), so
# the descendant set at any instant is 4 persistent lanes PLUS however many documents are in flight -
# observed as 6, 7 and 8 in cutover-20260913-172830.
#
# Timing cannot separate them: in that log 18376 spans samples 2-3, 22880 spans 5-6 and 6988 spans 7-8,
# because a document slower than the 60s sample interval survives two samples. STRUCTURE can:
#
#     shim (.venv python, matches app.jobs.ocr_supervisor)
#       └── re-exec (real interpreter, also matches app.jobs.ocr_supervisor)   <- run_parallel parent
#             ├── LANE WORKER  ocr0 ... ocr3      (depth 2 - exactly $Workers of them)
#             │     └── isolation child           (depth 3 - transient, one per document)
#             └── resource tracker                (depth 2, excluded by command line)
#
# Confirmed against the live orphan cleanup: lane workers 5840/7132/15236 all had ParentProcessId 21600
# (the run_parallel parent named in the ocr_document_claims worker ids), while every isolation child
# was parented by a lane worker. Lane identity is therefore "direct child of the re-exec", not "any
# spawn descendant".
function Get-SupervisorTopology([int]$RootPid) {
    $empty = [pscustomobject]@{ Root = $RootPid; ReExec = 0; Lanes = @(); Isolation = @(); Trackers = @() }
    if (-not $RootPid) { return $empty }
    $all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")

    # The re-exec: a direct child of the shim that is itself the supervisor. Absent when python is
    # launched without the launcher shim, in which case the root IS the run_parallel parent.
    $reExec = @($all | Where-Object {
        [int]$_.ParentProcessId -eq $RootPid -and $_.CommandLine -match 'app\.jobs\.ocr_supervisor'
    } | Sort-Object CreationDate | Select-Object -First 1)
    $parentPid = if ($reExec.Count -gt 0) { [int]$reExec[0].ProcessId } else { $RootPid }

    $children = @($all | Where-Object { [int]$_.ParentProcessId -eq $parentPid })
    $lanes = @($children | Where-Object {
        $_.CommandLine -and $_.CommandLine -match 'spawn_main|--multiprocessing-fork' -and
        $_.CommandLine -notmatch 'resource_tracker' })
    $trackers = @($children | Where-Object { $_.CommandLine -match 'resource_tracker' })

    $laneIds = @($lanes | ForEach-Object { [int]$_.ProcessId })
    $isolation = @($all | Where-Object { $laneIds -contains [int]$_.ParentProcessId })

    return [pscustomobject]@{
        Root = $RootPid; ReExec = $parentPid
        Lanes = $lanes; Isolation = $isolation; Trackers = $trackers
    }
}

# The run_parallel parent pids of any generation that has claimed recently. ocr_claims.new_worker_id
# is called in the PARENT before spawning, so worker ids read "ocr<N>-<host>-<parentpid>-<hex>" - which
# makes the claims table an independent, process-table-free witness of which generation is alive.
function Get-LaneGenerationParents {
    try {
        $rows = Invoke-DbJson -Label 'lane-generations' -Body @'
rows = q("""SELECT DISTINCT split_part(worker_id,'-',3) AS ppid
            FROM ocr_document_claims
            WHERE heartbeat_at > now() - interval '10 minutes'""")
print(json.dumps([r["ppid"] for r in rows]))
'@
        return @($rows | ForEach-Object { [int]$_ })
    } catch { return @() }
}

# Claim heartbeats belonging to specific generation parents, in the last 40 seconds.
function Get-GenerationClaims([int[]]$Parents) {
    if (-not $Parents -or @($Parents).Count -eq 0) { return @() }
    $list = (@($Parents) | ForEach-Object { "'$_'" }) -join ','
    try {
        return @(Invoke-DbJson -Label 'generation-claims' -Body @"
rows = q("""SELECT worker_id, max(heartbeat_at) AS hb
            FROM ocr_document_claims
            WHERE heartbeat_at > now() - interval '40 seconds'
              AND split_part(worker_id,'-',3) IN ($list)
            GROUP BY worker_id""")
print(json.dumps(rows, default=str))
"@)
    } catch { return @() }
}

# Requirement 10. Restores the previous state and PROVES OCR resumed, rather than assuming it.
function Restore-Legacy([string]$why) {
    Say ''
    Say "!! RESTORING THE PREVIOUS OCR ARRANGEMENT: $why" 'Yellow'
    # ---- ROLLBACK HARDENING -------------------------------------------------------------------
    # Unregistering the TASK does not kill what the supervisor SPAWNED. Two cutovers proved it: the
    # 16:25 and 17:28 runs each left their lane workers alive, and they kept claiming and OCR'ing for
    # 93 and 27 minutes respectively, concurrently with the restarted legacy worker - the exact
    # double-processing the lifetime locks exist to prevent (the orphans hold no lock, because the
    # supervisor's database session died with it). 281 rows ended up with attempts > 1.
    #
    # So: capture the generation BEFORE stopping anything, then terminate the whole closure, then
    # verify silence, and only then restart legacy. Idempotent - safe to re-enter after a partial run.
    if ($script:SupervisorBuilt) {
        # 1. capture the closure and generation identity while the supervisor is still intact
        $supProc = Get-SupervisorProc
        $genRoot = if ($supProc) { [int]$supProc.ProcessId } else { $script:SupPid }
        $topo    = if ($genRoot) { Get-SupervisorTopology $genRoot } else { $null }
        $genPids = @()
        if ($topo) {
            $genPids = @(@($topo.Lanes) + @($topo.Isolation) + @($topo.Trackers) |
                         ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
        }
        $genPids = @(@($genPids) + @(Get-ProcDescendants $genRoot | ForEach-Object { [int]$_.ProcessId }) |
                     Sort-Object -Unique)
        # Identity fingerprint for revalidation: never kill by stale pid alone.
        $genFingerprint = @{}
        foreach ($pid0 in $genPids) {
            $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$pid0" -ErrorAction SilentlyContinue
            if ($pp) { $genFingerprint[$pid0] = @{ Created = $pp.CreationDate; Cmd = "$($pp.CommandLine)"; Parent = [int]$pp.ParentProcessId } }
        }
        $genParents = @(Get-LaneGenerationParents)
        Say "   generation root pid=$genRoot ; descendant closure: $(@($genPids).Count) process(es)" 'Yellow'
        Say "   generation parent pids from live claim ids: $(@($genParents) -join ', ')" 'Yellow'

        # 2. stop and unregister the task
        foreach ($act in @('Stop-ScheduledTask', 'Disable-ScheduledTask', 'Unregister-ScheduledTask')) {
            try {
                switch ($act) {
                    'Stop-ScheduledTask'       { Stop-ScheduledTask -TaskName $SupervisorTask -ErrorAction SilentlyContinue }
                    'Disable-ScheduledTask'    { Disable-ScheduledTask -TaskName $SupervisorTask -ErrorAction SilentlyContinue | Out-Null }
                    'Unregister-ScheduledTask' { Unregister-ScheduledTask -TaskName $SupervisorTask -Confirm:$false -ErrorAction SilentlyContinue }
                }
            } catch { Say "   ($act on the supervisor: $($_.Exception.Message))" 'DarkYellow' }
        }
        Say "   supervisor task stopped and unregistered" 'Yellow'

        # 3. terminate the generation - REVALIDATING identity immediately before each kill
        $killDeadline = (Get-Date).AddMinutes(3)
        do {
            $live = @()
            foreach ($pid0 in @($genPids)) {
                $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$pid0" -ErrorAction SilentlyContinue
                if (-not $pp) { continue }
                $fp = $genFingerprint[$pid0]
                if ($fp -and ($pp.CreationDate -ne $fp.Created)) {
                    Say "   SKIP pid $pid0 - creation time differs; the pid has been REUSED by another process" 'Red'
                    continue
                }
                if ("$($pp.CommandLine)" -notmatch 'spawn_main|--multiprocessing-fork|app\.jobs\.ocr_supervisor') {
                    Say "   SKIP pid $pid0 - command line is not a supervisor/spawn process" 'Red'
                    continue
                }
                $live += $pid0
            }
            # also catch anything newly spawned by a lane worker during teardown
            $extra = @(Get-ProcDescendants $genRoot | ForEach-Object { [int]$_.ProcessId })
            foreach ($e in $extra) { if (@($genPids) -notcontains $e) { $genPids += $e; $live += $e } }
            if (@($live).Count -eq 0) { break }
            foreach ($pid0 in @($live)) { try { Stop-Process -Id $pid0 -Force -ErrorAction SilentlyContinue } catch {} }
            Start-Sleep -Seconds 5
        } while ((Get-Date) -lt $killDeadline)

        $stillAlive = @(@($genPids) | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if (@($stillAlive).Count -gt 0) {
            Say "   WARNING generation processes still alive: $(@($stillAlive) -join ',')" 'Red'
            Say "   Legacy will NOT be restarted while they run - that would recreate the overlap." 'Red'
            return
        }
        Say "   OK every process of the supervisor generation has exited" 'Green'

        # 4. verify claim silence BEFORE restarting legacy - "reached and SUSTAINED", not "never seen".
        #
        # The previous version latched $quiet = $false on ANY sample and never cleared it, so a single
        # heartbeat in the first window failed the whole check. That is exactly what happened at 18:34:
        # the script had already confirmed every generation process had exited, but one claim row's
        # heartbeat_at was written moments before termination and still fell inside the 40-second
        # lookback. Legacy was therefore NOT restarted and OCR stopped dead for ~4 minutes.
        #
        # heartbeat_at is a TIMESTAMP, not a liveness signal: a dead worker's last row necessarily
        # remains "recent" for up to the lookback window. So the correct condition is that silence is
        # REACHED and then HOLDS for two consecutive samples - with the process check, which is a true
        # liveness signal, enforced every sample and able to refuse on its own.
        if (@($genParents).Count -gt 0) {
            Say "   verifying claim silence is reached and sustained (final 2 consecutive samples quiet)..." 'Yellow'
            $maxSamples   = 8          # up to 4 minutes; normally satisfied in 2-3 samples
            $history      = @()
            $silenceHeld  = $false
            for ($i = 1; $i -le $maxSamples; $i++) {
                Start-Sleep -Seconds 30
                # A surviving PROCESS refuses immediately and on its own - never overridden by silence.
                $aliveNow = @(@($genPids) | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
                if (@($aliveNow).Count -gt 0) {
                    Say "     t+$($i*30)s GENERATION PROCESS ALIVE: $(@($aliveNow) -join ',') - refusing to restart legacy" 'Red'
                    return
                }
                $n = @(Get-GenerationClaims $genParents).Count
                $history += $n
                $tail = @($history | Select-Object -Last 2)
                $silenceHeld = (@($tail).Count -eq 2) -and (@($tail | Where-Object { $_ -ne 0 }).Count -eq 0)
                Say ("     t+{0,3}s generation claim heartbeats: {1}{2}" -f ($i*30),
                     $(if ($n -eq 0) { 'NONE' } else { "$n" }),
                     $(if ($silenceHeld) { '   <- silence sustained over 2 consecutive samples' }
                       elseif ($n -ne 0 -and $i -eq 1) { '   (stale timestamp from an already-dead worker; tolerated)' }
                       else { '' })) `
                    $(if ($n -eq 0) { 'Green' } else { 'Yellow' })
                if ($silenceHeld) { break }
            }
            if (-not $silenceHeld) {
                Say "   WARNING generation claim heartbeats never went quiet for two consecutive samples" 'Red'
                Say "   observed pattern: $($history -join ', ') - NOT restarting legacy." 'Red'
                return
            }
            Say "   OK claim silence reached and sustained (pattern: $($history -join ', '))" 'Green'
        }
    }
    try {
        $before = (Get-Counters)
        Enable-ScheduledTask -TaskName $LegacyTask -ErrorAction Stop | Out-Null
        Start-ScheduledTask  -TaskName $LegacyTask -ErrorAction Stop
        Say "   legacy task enabled and started; proving OCR writes resume..." 'Yellow'
        $resumed = $false
        for ($i = 0; $i -lt 12; $i++) {
            Start-Sleep -Seconds 15
            $now = Get-Counters
            if ([datetime]$now.last_write -gt [datetime]$before.last_write) { $resumed = $true; break }
        }
        $state = (Get-ScheduledTask -TaskName $LegacyTask).State
        $after = Get-Counters
        if ($resumed) {
            Say "   PROVEN: legacy OCR writing again (state=$state last_write=$($after.last_write) writes_2m=$($after.writes_2m))" 'Green'
        } else {
            Say "   WARNING: no new document_ocr write observed in 3 minutes (state=$state)." 'Red'
            Say "   INVESTIGATE MANUALLY - OCR may be stopped." 'Red'
        }
    } catch {
        Say "   RESTORE FAILED: $($_.Exception.Message)" 'Red'
        Say "   MANUAL: Enable-ScheduledTask -TaskName '$LegacyTask'; Start-ScheduledTask -TaskName '$LegacyTask'" 'Red'
        if ($script:BackupXml) {
            Say "   Or restore the exported definition verbatim:" 'Red'
            Say "     Register-ScheduledTask -TaskName '$LegacyTask' -Xml (Get-Content '$($script:BackupXml)' -Raw) -Force" 'Red'
        }
    }
}

try {
# ================================================================================================
Head "PHASE 0 - GUARDS (no operational change until every one passes)"

# G0 elevation
$idn = [Security.Principal.WindowsIdentity]::GetCurrent()
$elevated = (New-Object Security.Principal.WindowsPrincipal($idn)).IsInRole(
                [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $elevated) {
    Fail "not elevated. Re-run from an Administrator PowerShell (required even for -DryRun, so the dry run exercises the same identity the real run will use)."
}
Say "  OK elevation - $($idn.Name) is Administrator" 'Green'

# G1 checkout is at one of exactly TWO acceptable states, with a clean tracked tree:
#   * ExpectedFrom - the deployment has not happened yet, so phases 4-6 will perform it; or
#   * TargetSha    - the deployment already happened (a previous run deployed and then aborted in a
#                    later phase). Re-deploying would be a pointless service restart; the git work is
#                    skipped and every other guard still runs in full.
# Anything else is a hard stop: an unknown HEAD is exactly the case where a blind fast-forward is
# unsafe.
Push-Location $ProjectRoot
$head   = (& git rev-parse HEAD).Trim()
$dirty  = @(& git status --porcelain --untracked-files=no)
$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
Pop-Location
if ($dirty.Count -gt 0) { Fail "$ProjectRoot has $($dirty.Count) modified tracked file(s): $($dirty -join '; ')" }
switch ($head) {
    $ExpectedFrom { $script:AlreadyDeployed = $false }
    $TargetSha    { $script:AlreadyDeployed = $true  }
    default       { Fail "$ProjectRoot is at $head, which is neither the expected start $ExpectedFrom nor the target $TargetSha - refusing" }
}
if ($script:AlreadyDeployed) {
    Say "  OK checkout - ALREADY AT TARGET $head on $branch, tracked tree clean" 'Green'
    Say "     git deployment will be SKIPPED (no service stop, no fast-forward, no restart)" 'Yellow'
} else {
    Say "  OK checkout - $head on $branch, tracked tree clean (deployment required)" 'Green'
}

# G2 target object present locally and reachable (needs no network)
Push-Location $ProjectRoot
$type = (& git cat-file -t $TargetSha 2>&1).Trim()
if ($type -ne 'commit') { Pop-Location; Fail "target $TargetSha is not a local commit object (got '$type'). Run 'git fetch origin' as the repo owner first." }
if (-not $script:AlreadyDeployed) {
    & git merge-base --is-ancestor $head $TargetSha
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "$head is not an ancestor of $TargetSha - refusing (not fast-forwardable)" }
}
# Verify the working tree really matches the target when we believe it is already deployed - an equal
# HEAD with differing files would otherwise be accepted on the strength of the SHA alone.
if ($script:AlreadyDeployed) {
    $drift = @(& git diff --name-only HEAD $TargetSha)
    if ($drift.Count -ne 0) { Pop-Location; Fail "HEAD equals the target but $($drift.Count) file(s) differ from it: $($drift -join ', ')" }
}

# G3 EXACT release-range authority. The AUTHORIZED range is always ExpectedFrom..TargetSha - a fixed
#    span independent of where the checkout currently sits - so the pinned numbers stay meaningful
#    whether or not this host has already been deployed. Every value is recomputed here and any
#    difference is a hard stop.
$merges  = @(& git log --format='%s' --merges "$ExpectedFrom..$TargetSha")
$commits = [int](& git rev-list --count "$ExpectedFrom..$TargetSha").Trim()
$paths   = @(& git diff --name-only $ExpectedFrom $TargetSha)
$stat    = (& git diff --shortstat $ExpectedFrom $TargetSha).Trim()
Pop-Location

$prs = @()
foreach ($m in $merges) {
    if ($m -match 'Merge pull request #(\d+) ') { $prs += [int]$Matches[1] }
    else { Fail "non-PR merge commit in range: $m" }
}
$prsSorted = ($prs | Sort-Object)
$expSorted = ($EXPECTED_PRS | Sort-Object)
if (($prsSorted -join ',') -ne ($expSorted -join ',')) {
    Fail "range contains PRs [$($prsSorted -join ',')], expected exactly [$($expSorted -join ',')]"
}
if ($commits -ne $EXPECTED_COMMITS) { Fail "range has $commits commits, expected $EXPECTED_COMMITS" }
if ($paths.Count -ne $EXPECTED_FILES) { Fail "range changes $($paths.Count) files, expected $EXPECTED_FILES" }
$unexpected = @($paths | Where-Object { $EXPECTED_PATHS -notcontains $_ })
$missing    = @($EXPECTED_PATHS | Where-Object { $paths -notcontains $_ })
if ($unexpected.Count -gt 0) { Fail "unexpected path(s) in range: $($unexpected -join ', ')" }
if ($missing.Count -gt 0)    { Fail "expected path(s) absent from range: $($missing -join ', ')" }
if ($stat -notmatch "(\d+) insertion.*?(\d+) deletion") { Fail "could not parse diff stat: $stat" }
$ins = [int]$Matches[1]; $del = [int]$Matches[2]
if ($ins -ne $EXPECTED_INSERTIONS -or $del -ne $EXPECTED_DELETIONS) {
    Fail "range delta is +$ins/-$del, expected +$EXPECTED_INSERTIONS/-$EXPECTED_DELETIONS"
}
$bad = @($paths | Where-Object {
    $_ -match '(?i)^migrations/|alembic|\.env|secret|credential|\.pem$|\.key$|\.pfx$|inventory|\.csv$|\.xlsx$|\.sql$|ownership|relink' })
if ($bad.Count -gt 0) { Fail "forbidden file class in range: $($bad -join ', ')" }
Say "  OK range - PRs $($prsSorted -join ', ') | $commits commits | $($paths.Count) files | +$ins/-$del" 'Green'
Say "            no migration / env / secret / data / inventory / ownership file" 'Green'

# G4 database single head
$ver = Invoke-DbJson -Label 'alembic-head' -Body 'print(json.dumps(q("SELECT version_num FROM alembic_version")))'
if (@($ver).Count -ne 1)                      { Fail "alembic_version has $(@($ver).Count) rows, expected 1" }
if ($ver[0].version_num -ne $ExpectedAlembic) { Fail "alembic head is $($ver[0].version_num), expected $ExpectedAlembic" }
Say "  OK database - single head $ExpectedAlembic" 'Green'

# G5 health before any change
$healthBefore = Assert-Healthy 'pre-deploy health'

# G6 legacy task is the only OCR task, is writing, and the supervisor is absent
$legacy = Get-ScheduledTask -TaskName $LegacyTask -ErrorAction SilentlyContinue
if (-not $legacy)                { Fail "legacy task '$LegacyTask' does not exist" }
if ($legacy.State -ne 'Running') { Fail "legacy task is '$($legacy.State)', expected Running" }
if (Get-ScheduledTask -TaskName $SupervisorTask -ErrorAction SilentlyContinue) {
    Fail "'$SupervisorTask' is already installed - this script installs it and will not overwrite silently"
}
$otherOcr = @(Get-ScheduledTask | Where-Object { $_.TaskName -match '(?i)ocr' -and $_.TaskName -ne $LegacyTask })
if ($otherOcr.Count -gt 0) { Fail "other OCR task(s) present: $($otherOcr.TaskName -join ', ')" }
$before = Get-Counters
if (-not $before.last_write) { Fail "document_ocr has no writes at all" }
$age = (Get-Date) - [datetime]$before.last_write
if ($age.TotalMinutes -gt 10) { Fail "legacy task has not written for $([int]$age.TotalMinutes) minutes - it is not actively writing" }
Say "  OK tasks - '$LegacyTask' Running and writing (last $([int]$age.TotalSeconds)s ago), sole OCR task, supervisor absent" 'Green'

# G7 ACLs on every write target.
# This is the ONE filesystem touch the dry run makes outside its own artifact directory: a zero-byte
# probe file, created and deleted again. Get-Acl alone is not a substitute - an ACL can read as
# permissive and the write still fail (inherited deny, read-only attribute, a filter driver). The
# probe is UNTRACKED, so it cannot dirty the checkout, and the finally block guarantees cleanup even
# if the script is interrupted between create and delete.
foreach ($p in @($ProjectRoot, "$ProjectRoot\.git", "$ProjectRoot\app", $OpsDir)) {
    if (-not (Test-Path $p)) { Fail "deployment target missing: $p" }
    $probe = Join-Path $p (".acl-probe-{0}.tmp" -f ([guid]::NewGuid().ToString('N')))
    try {
        New-Item -ItemType File -Path $probe -ErrorAction Stop | Out-Null
    } catch {
        Fail "not writable by this identity: $p ($($_.Exception.Message))"
    } finally {
        if (Test-Path $probe) { Remove-Item $probe -Force -ErrorAction SilentlyContinue }
    }
}
Say "  OK ACLs - every deployment target is writable (probe files created and removed)" 'Green'

# ------------------------------------------------------------------------------------------------
Head "PHASE 1 - BASELINE"
$legacyProcs = Get-LegacyWorkerProcs
$oldPids = @($legacyProcs | ForEach-Object { $_.ProcessId })
Say "  legacy worker PIDs : $($oldPids -join ', ')"
$legacyProcs | ForEach-Object { Say "    PID $($_.ProcessId) (parent $($_.ParentProcessId)) started $($_.CreationDate)" }
$script:BackupXml = Join-Path $ArtifactDir "legacy-task-$stamp.xml"
Export-ScheduledTask -TaskName $LegacyTask | Set-Content -Path $script:BackupXml -Encoding UTF8
$backupHash = (Get-FileHash $script:BackupXml -Algorithm SHA256).Hash
if (-not (Test-Path $script:BackupXml) -or (Get-Item $script:BackupXml).Length -lt 200) {
    Fail "the legacy task export is missing or implausibly small - refusing to proceed without a restorable backup"
}
Say "  legacy task backup : $($script:BackupXml)"
Say "  backup SHA256      : $backupHash"
$lockBefore = Get-LockOwnership
Say "  advisory locks now : legacy(511005777) holders=$($lockBefore.legacy_lock_holders) supervisor_lock=$($lockBefore.any_supervisor_lock) sweep_lock=$($lockBefore.any_sweep_lock)"
Say "  START COUNTERS     : active=$($before.active) ocr_complete=$($before.ocr_complete) backlog=$($before.ocr_backlog) never_attempted=$($before.never_attempted) classified=$($before.classified)"
Say "                       failed=$($before.failed) timed_out=$($before.timed_out) writes_15m=$($before.writes_15m)"
($before | ConvertTo-Json -Depth 6) | Set-Content (Join-Path $ArtifactDir "counters-before-$stamp.json") -Encoding UTF8

# ------------------------------------------------------------------------------------------------
Head "PHASE 2 - SOURCE-ROOT PREFLIGHT (read-only, from the TARGET tree)"
# In a dry run the deployed checkout is still the OLD release and has neither the corrected installer
# nor app/deploy/ocr_source_roots.py. So the authority is read straight out of the TARGET commit into
# the artifact directory and executed read-only against the live host and production database. It
# performs SELECTs and filesystem probes only - no registration, no writes, nothing in the repo.
$rootsModule = Join-Path $ArtifactDir "ocr_source_roots-$stamp.py"
Push-Location $ProjectRoot
(& git show "${TargetSha}:app/deploy/ocr_source_roots.py") | Set-Content -Path $rootsModule -Encoding UTF8
Pop-Location
if (-not (Test-Path $rootsModule)) { Fail "could not extract app/deploy/ocr_source_roots.py from $TargetSha" }

$logicalDisks = @(Get-CimInstance Win32_LogicalDisk -ErrorAction Stop)
$mappedDisks  = @(Get-CimInstance Win32_MappedLogicalDisk -ErrorAction SilentlyContinue)
$inventory = @{
    volumes = @($logicalDisks | ForEach-Object {
        @{ anchor = $_.DeviceID; drive_type = [int]$_.DriveType
           provider_name = $_.ProviderName; file_system = $_.FileSystem } })
    mapped_anchors = @($mappedDisks | ForEach-Object { $_.DeviceID })
} | ConvertTo-Json -Depth 5 -Compress

$harness = @'
import importlib.util, json, os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv("app/.env")
spec = importlib.util.spec_from_file_location("ocr_source_roots", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
volumes = m.volumes_from_inventory(json.loads(sys.argv[2]))
declared = m.declared_roots()
configured = m.configured_roots(project_root=os.getcwd(), declared=declared)
from sqlalchemy import create_engine
eng = create_engine(os.environ["DATABASE_URL"],
                    connect_args={"options": "-c default_transaction_read_only=on"})
db = m.database_roots(m.read_database_anchors(eng), project_root=os.getcwd())
rep = m.evaluate(configured=configured, database=db, volumes=volumes, declared=declared)
print(rep.render())
sys.exit(0 if rep.ok else 1)
'@
$harnessFile = Join-Path $ArtifactDir "roots-harness-$stamp.py"
$harness | Set-Content -Path $harnessFile -Encoding UTF8

Push-Location $ProjectRoot
$rootResult = & $python $harnessFile $rootsModule $inventory 2>&1
$rootExit = $LASTEXITCODE
Pop-Location
$rootResult | ForEach-Object { Say "  $_" }
($rootResult | Out-String) | Set-Content (Join-Path $ArtifactDir "preflight-$stamp.txt") -Encoding UTF8
if ($rootExit -ne 0) {
    Fail "source-root preflight FAILED. Not bypassed, nothing changed, no row altered. Blocker above."
}
Say "  OK every required OCR source root is readable by an S4U task" 'Green'

if ($DryRun) {
    # The SAME route verification the real cutover runs as its final phase - same selection, same
    # URLs, same validation, same function. Exercising it here is the whole point: a dry run that
    # skipped it is exactly how a broken query reached the end of a live cutover.
    Head "PHASE 3 (DRY RUN) - DEPLOYED ROUTE CHECKS (read-only)"
    $dryRoutes = Invoke-RouteChecks 'dry-run'

    Say ''
    if ($dryRoutes.problems.Count -gt 0) {
        Say "DRY RUN FAILED - the route checks found problems:" 'Red'
        $dryRoutes.problems | ForEach-Object { Say "  - $_" 'Red' }
        Say ''
        Say "Nothing was changed. Fix these before attempting the cutover." 'Red'
        Say "Log: $($script:LogPath)" 'Cyan'
        Stop-Transcript | Out-Null
        exit 1
    }
    if ($dryRoutes.incomplete.Count -gt 0) {
        Say "DRY RUN INCOMPLETE - guards passed, but route verification could not fully run:" 'Yellow'
        $dryRoutes.incomplete | ForEach-Object { Say "  - $_" 'Yellow' }
    } else {
        Say "DRY RUN COMPLETE - every read-only guard AND the deployed route checks passed." 'Yellow'
    }
    Say "  * production still at $head" 'Yellow'
    Say "  * Client360 untouched, legacy OCR task untouched and still Running" 'Yellow'
    Say "  * no task registered, no checkout update, no production data or session altered" 'Yellow'
    Say "  Re-run without -DryRun to perform the cutover." 'Yellow'
    Say ''
    Say "Log: $($script:LogPath)" 'Cyan'
    Stop-Transcript | Out-Null
    return
}

# ================================================================================================
#  FIRST OPERATIONAL CHANGE BELOW THIS LINE
# ================================================================================================

Head "PHASE 3 - STOP AND DISABLE THE LEGACY OCR TASK"
Stop-ScheduledTask    -TaskName $LegacyTask -ErrorAction Stop
Disable-ScheduledTask -TaskName $LegacyTask -ErrorAction Stop | Out-Null
$script:LegacyDisabled = $true
Say "  stopped and disabled; waiting for worker/child processes to exit..."
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    if ((Get-LegacyWorkerProcs).Count -eq 0) { break }
    Start-Sleep -Seconds 5
}
$still = Get-LegacyWorkerProcs
if ($still.Count -gt 0) {
    Restore-Legacy "legacy worker PID(s) $($still.ProcessId -join ',') did not exit within 5 minutes"
    Fail "legacy workers did not exit; nothing was deployed"
}
Say "  OK all legacy worker processes exited" 'Green'

# ------------------------------------------------------------------------------------------------
if ($script:AlreadyDeployed) {
    Head "PHASES 4-6 - SKIPPED (production is already at the exact clean target)"
    $newHead = $head
    $script:Downtime = [TimeSpan]::Zero
    Say "  HEAD is $newHead and 0 files differ from the target, verified in PHASE 0." 'Green'
    Say "  Client360 is NOT stopped, the checkout is NOT touched, and no restart occurs." 'Green'
    Say "  NOTE Alembic deliberately NOT run - this range contains no migration." 'Yellow'
    # Client360 is already up on the target code; still prove it is healthy before cutting over OCR.
    if (-not (Test-HealthOk)) {
        Restore-Legacy 'Client360 is not healthy on the already-deployed target'
        Fail "Client360 health/readiness is not 200 at $ExpectedAlembic"
    }
    Assert-Healthy 'already-deployed health' | Out-Null
    Say "  DOWNTIME: 0 seconds (no deployment was required)" 'Green'
} else {
    Head "PHASE 4 - STOP CLIENT360 (consistent python/template deployment)"
    $downStart = Get-Date
    Stop-Service -Name $ServiceName -Force -ErrorAction Stop
    (Get-Service $ServiceName).WaitForStatus('Stopped', '00:02:00')
    Say "  OK $ServiceName stopped at $($downStart.ToString('HH:mm:ss'))" 'Green'

    # --------------------------------------------------------------------------------------------
    Head "PHASE 5 - FAST-FORWARD TO $TargetSha"
    Push-Location $ProjectRoot
    & git merge --ff-only $TargetSha 2>&1 | ForEach-Object { Say "  $_" }
    $ffExit = $LASTEXITCODE
    $newHead   = (& git rev-parse HEAD).Trim()
    $newDirty  = @(& git status --porcelain --untracked-files=no)
    $diffCount = @(& git diff --name-only HEAD $TargetSha).Count
    Pop-Location
    if ($ffExit -ne 0)           { Restore-Legacy 'git merge --ff-only failed'; Fail "git merge --ff-only failed" }
    if ($newHead -ne $TargetSha) { Restore-Legacy 'HEAD mismatch after fast-forward'; Fail "HEAD is $newHead, expected $TargetSha" }
    if ($newDirty.Count -gt 0)   { Restore-Legacy 'tracked tree dirty after fast-forward'; Fail "tracked tree dirty: $($newDirty -join '; ')" }
    if ($diffCount -ne 0)        { Restore-Legacy 'files differ from target'; Fail "$diffCount file(s) differ from target" }
    Say "  OK HEAD=$newHead, tracked tree clean, 0 files differ from target" 'Green'
    Say "  NOTE Alembic deliberately NOT run - this range contains no migration." 'Yellow'

    # --------------------------------------------------------------------------------------------
    Head "PHASE 6 - START CLIENT360"
    Start-Service -Name $ServiceName -ErrorAction Stop
    (Get-Service $ServiceName).WaitForStatus('Running', '00:02:00')
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Seconds 5; if (Test-HealthOk) { $ok = $true; break } }
    if (-not $ok) { Restore-Legacy 'Client360 did not become ready'; Fail "$ServiceName did not become ready within 5 minutes" }
    $script:Downtime = (Get-Date) - $downStart
    Assert-Healthy 'post-deploy health' | Out-Null
    Say "  DOWNTIME: $([int]$script:Downtime.TotalSeconds) seconds" 'Green'
}

# ------------------------------------------------------------------------------------------------
Head "PHASE 7 - INSTALLER PREFLIGHT (-WhatIf, registers nothing)"
$installer = Join-Path $ProjectRoot 'deploy\windows\install_ocr_supervisor_task.ps1'
if (-not (Test-Path $installer)) { Restore-Legacy 'installer missing'; Fail "installer not found: $installer" }
$whatIf = & $installer -WhatIf -Workers $Workers 2>&1
$whatIfExit = $LASTEXITCODE
$whatIf | ForEach-Object { Say "  $_" }
($whatIf | Out-String) | Set-Content (Join-Path $ArtifactDir "installer-whatif-$stamp.txt") -Encoding UTF8
if ($whatIfExit -ne 0 -or ($whatIf | Out-String) -match 'PREFLIGHT FAILED|S4U preflight failed') {
    Restore-Legacy 'source-root preflight FAILED at install time - not bypassed, no row altered'
    Fail "source-root preflight failed; nothing was registered"
}
Say "  OK installer preflight passed" 'Green'

# ------------------------------------------------------------------------------------------------
Head "PHASE 8 - INSTALL THE SUPERVISOR (-Workers $Workers)"
try {
    $install = & $installer -Workers $Workers 2>&1
    $install | ForEach-Object { Say "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "installer exited $LASTEXITCODE" }
    $script:SupervisorBuilt = $true
} catch {
    Restore-Legacy "installation failed: $($_.Exception.Message)"
    Fail "installation failed"
}

Head "PHASE 9 - VERIFY THE REGISTERED DEFINITION"
$t = Get-ScheduledTask -TaskName $SupervisorTask -ErrorAction SilentlyContinue
if (-not $t) { Restore-Legacy 'task not registered'; Fail "'$SupervisorTask' does not exist after install" }
$script:SupervisorBuilt = $true
$a = @($t.Actions)[0]
$checks = [ordered]@{
    'venv python'        = ($a.Execute -eq (Join-Path $ProjectRoot '.venv\Scripts\python.exe'))
    'supervisor module'  = ($a.Arguments -match '-m\s+app\.jobs\.ocr_supervisor')
    "--workers $Workers" = ($a.Arguments -match "--workers\s+$Workers\b")
    '--keep-running'     = ($a.Arguments -match '--keep-running')
    'workdir root'       = ($a.WorkingDirectory -eq $ProjectRoot)
    'S4U'                = ($t.Principal.LogonType -eq 'S4U')
    'identity'           = ($t.Principal.UserId -match [regex]::Escape($env:USERNAME))
    'IgnoreNew'          = ($t.Settings.MultipleInstances -eq 'IgnoreNew')
    'restart on exit'    = ($t.Settings.RestartCount -ge 1)
    'boot trigger'       = (@($t.Triggers | Where-Object { $_.CimClass.CimClassName -match 'Boot' }).Count -ge 1)
    'logon trigger'      = (@($t.Triggers | Where-Object { $_.CimClass.CimClassName -match 'Logon' }).Count -ge 1)
}
$checks.GetEnumerator() | ForEach-Object {
    Say ("  {0,-20} {1}" -f $_.Key, $(if ($_.Value) { 'OK' } else { 'MISMATCH' })) $(if ($_.Value) { 'Green' } else { 'Red' })
}
Say "  Execute  : $($a.Execute)"
Say "  Args     : $($a.Arguments)"
Say "  WorkDir  : $($a.WorkingDirectory)"
Say "  Principal: $($t.Principal.UserId) / $($t.Principal.LogonType) / $($t.Principal.RunLevel)"
if ($checks.Values -contains $false) {
    Restore-Legacy 'registered definition did not match the intended definition'
    Fail "registered task definition mismatch"
}

# ------------------------------------------------------------------------------------------------
Head "PHASE 10 - START THE SUPERVISOR AND PROVE IT OWNS BOTH LIFETIME LOCKS"
Start-ScheduledTask -TaskName $SupervisorTask -ErrorAction Stop
$lockOwner = $null
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Seconds 10
    $lo = Get-LockOwnership
    if ($lo.owners -eq 1 -and $lo.owner_pid) { $lockOwner = $lo; break }
}
if (-not $lockOwner) {
    Restore-Legacy "no single backend owns BOTH 511005888 and 511005002 within 4 minutes - the supervisor is not excluding the in-app sweep"
    Fail "supervisor did not take both lifetime locks"
}
$script:LockPid   = $lockOwner.owner_pid
$script:LockStart = $lockOwner.owner_backend_start
Say "  OK backend pid $($script:LockPid) holds BOTH $LOCK_SUPERVISOR and $LOCK_SWEEP (backend_start $($script:LockStart))" 'Green'

$supProc = Get-SupervisorProc
if (-not $supProc) { Restore-Legacy 'supervisor process not found'; Fail "no app.jobs.ocr_supervisor process is running" }
$script:SupPid = [int]$supProc.ProcessId
Say "  supervisor OS pid  : $($script:SupPid) (started $($supProc.CreationDate))"

$lt = Get-ScheduledTask -TaskName $LegacyTask
if ($lt.State -ne 'Disabled') { Restore-Legacy "legacy task is '$($lt.State)'"; Fail "legacy task is '$($lt.State)', expected Disabled" }
$leftover = Get-LegacyWorkerProcs
if ($leftover.Count -gt 0) { Restore-Legacy 'legacy worker still running'; Fail "legacy worker still running: $($leftover.ProcessId -join ',')" }
Say "  OK legacy task Disabled with no worker remaining" 'Green'

# ------------------------------------------------------------------------------------------------
Head "PHASE 11 - MONITOR FOR $MonitorMinutes MINUTES"
$monStart    = Get-Date
$monStartIso = $monStart.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$monEnd      = $monStart.AddMinutes($MonitorMinutes)
$problems  = @()
$samples   = @()
$lanesSeen = [System.Collections.Generic.HashSet[string]]::new()
$prevComplete = $before.ocr_complete
$prevLaneIds  = $null        # previous sample's lane pid set, for the stability gate
$restartsAtStart = (Get-ScheduledTaskInfo -TaskName $SupervisorTask).NumberOfMissedRuns

while ((Get-Date) -lt $monEnd) {
    Start-Sleep -Seconds 60
    $elapsed = [int]((Get-Date) - $monStart).TotalMinutes
    $c   = Get-Counters
    $cl  = Get-ClaimHealth
    $lo  = Get-LockOwnership
    $ti  = Get-ScheduledTaskInfo -TaskName $SupervisorTask
    $ts  = (Get-ScheduledTask -TaskName $SupervisorTask).State
    $sp  = Get-SupervisorProc
    $topo = if ($sp) { Get-SupervisorTopology ([int]$sp.ProcessId) } else { $null }
    $wk  = if ($topo) { @($topo.Lanes) }     else { @() }
    $iso = if ($topo) { @($topo.Isolation) } else { @() }
    $rt  = if ($topo) { @($topo.Trackers) }  else { @() }
    $wkIds = @($wk | ForEach-Object { [int]$_.ProcessId } | Sort-Object)
    $lanes = Get-LanesCompleted $monStartIso
    foreach ($l in @($lanes.lanes)) { [void]$lanesSeen.Add($l) }

    # supervisor heartbeat freshness - DIAGNOSTIC ONLY, see the gate block below
    $hbAge = 99999
    $hbPath = Join-Path $OpsDir 'supervisor_heartbeat.json'
    if (Test-Path $hbPath) {
        try {
            $hb = Get-Content $hbPath -Raw | ConvertFrom-Json
            $hbAge = [int]((Get-Date).ToUniversalTime() - [datetime]::Parse($hb.heartbeat_utc).ToUniversalTime()).TotalSeconds
        } catch { $hbAge = 99998 }
    }
    $statePath = Join-Path $OpsDir 'supervisor_state.json'
    $stateFresh = (Test-Path $statePath) -and ((Get-Item $statePath).LastWriteTime -gt $monStart.AddMinutes(-5))

    Say ("  t+{0,2}m task={1} suppid={2} lanes={3} iso={4} rtrk={5} lockpid={6} complete={7} backlog={8} never={9} done=[{10}] hb={11}s" -f `
         $elapsed, $ts, $(if($sp){$sp.ProcessId}else{'GONE'}), $wk.Count, $iso.Count, $rt.Count,
         $(if($lo.owner_pid){$lo.owner_pid}else{'NONE'}), $c.ocr_complete, $c.ocr_backlog,
         $c.never_attempted, (($lanesSeen | Sort-Object) -join ','), $hbAge)

    $samples += [pscustomobject]@{
        t = $elapsed; task_state = $ts; supervisor_pid = $(if($sp){[int]$sp.ProcessId}else{$null})
        lane_count = $wk.Count; lane_pids = ($wkIds -join ',')
        isolation_children = $iso.Count; resource_trackers = $rt.Count
        lock_pid = $lo.owner_pid; lock_owners = $lo.owners
        complete = $c.ocr_complete; backlog = $c.ocr_backlog; never = $c.never_attempted
        failed = $c.failed; timed_out = $c.timed_out; writes_2m = $c.writes_2m
        lanes_done = (($lanesSeen | Sort-Object) -join ','); hb_age = $hbAge
        state_json_age_min = $(if (Test-Path $statePath) { [math]::Round(((Get-Date) - (Get-Item $statePath).LastWriteTime).TotalMinutes,1) } else { $null })
        stale_claims = $cl.stale; duplicate_live = $cl.duplicate_live; live_lanes = $cl.lanes
    }

    # --- per-sample requirements (requirement 7) ---
    if ($ts -ne 'Running')       { $problems += "t+${elapsed}m supervisor task state is '$ts'" }
    if (-not $sp)                { $problems += "t+${elapsed}m supervisor process is gone" }
    elseif ([int]$sp.ProcessId -ne $script:SupPid) { $problems += "t+${elapsed}m supervisor pid changed $($script:SupPid) -> $($sp.ProcessId) (crash loop)" }
    # LANE GATE: exactly $Workers PERSISTENT lane workers, and the SAME pids as the previous sample.
    # Isolation children and resource trackers are reported but never counted. Stability is what
    # distinguishes "four lanes running" from "a lane died and a document happened to be in flight".
    if ($wk.Count -ne $Workers) {
        $problems += ("t+${elapsed}m $($wk.Count) persistent lane workers, expected exactly $Workers " +
                      "(lane pids: $($wkIds -join ',') ; isolation children $($iso.Count) and " +
                      "resource trackers $($rt.Count) are excluded by construction)")
    } elseif ($prevLaneIds -and (($wkIds -join ',') -ne ($prevLaneIds -join ','))) {
        $problems += ("t+${elapsed}m lane worker pids changed $($prevLaneIds -join ',') -> $($wkIds -join ',') " +
                      "- a lane died and was replaced, so the pool is not stable")
    }
    $prevLaneIds = $wkIds
    # Requirement 9: sweep exclusion is proven HERE, by lock ownership - never by duplicate claims.
    if ($lo.owners -ne 1 -or $lo.owner_pid -ne $script:LockPid) {
        $problems += "t+${elapsed}m lifetime locks not held by the original backend (owners=$($lo.owners) pid=$($lo.owner_pid) expected=$($script:LockPid)) - the in-app sweep is NO LONGER EXCLUDED"
    }
    if (-not (Test-HealthOk))    { $problems += "t+${elapsed}m Client360 health/readiness not 200 at $ExpectedAlembic" }
    # HEARTBEAT FILES ARE DIAGNOSTIC ONLY - they are NOT a liveness gate.
    #
    # ocr_supervisor.py:416-417 beats ONCE per lane and then calls run_parallel, which BLOCKS until the
    # lane drains; supervisor_state.json is written only after classification at the END of a pass.
    # With ~16,600 never-attempted documents at ~71 docs/min the initial lane needs ~4 hours, so within
    # any sane monitoring window neither file can refresh. The previous script failed a healthy cutover
    # on exactly this. Liveness is proven instead by: task state, supervisor process stability, the four
    # stable lane pids, both advisory locks on the same backend, rising ocr_complete, falling backlog,
    # fresh document_ocr writes, and claim heartbeats.
    if ($hbAge -gt 300 -or -not $stateFresh) {
        Say ("       note: heartbeat ${hbAge}s old, supervisor_state.json " +
             "$(if ($stateFresh) { 'fresh' } else { 'not refreshed' }) - expected during a long blocking pass, not a fault") 'DarkYellow'
    }
    if ($ti.NumberOfMissedRuns -gt $restartsAtStart) { $problems += "t+${elapsed}m supervisor restarted (retry/crash loop)" }
    if ($c.writes_2m -le 0)      { $problems += "t+${elapsed}m no document_ocr write in the last 2 minutes" }
    if ($c.ocr_complete -lt $prevComplete) { $problems += "t+${elapsed}m ocr_complete went backwards" }
    if ($cl.duplicate_live -gt 0) { $problems += "t+${elapsed}m PK invariant broken: $($cl.duplicate_live) duplicate live claims" }
    if ($cl.stale -gt 0)          { Say "       note: $($cl.stale) stale claim(s) awaiting reclaim (normal in small numbers)" 'DarkYellow' }
    $prevComplete = $c.ocr_complete
}

$after  = Get-Counters
$claims = Get-ClaimHealth
$mins   = ((Get-Date) - $monStart).TotalMinutes
$gained = $after.ocr_complete - $before.ocr_complete
$rate   = [math]::Round(($gained / [math]::Max($mins, 0.1)), 1)

# ------------------------------------------------------------------------------------------------
Head "PHASE 12 - DEPLOYED ROUTE CHECKS (#306 Overview / Edit Profile)"
# Identical to what -DryRun exercised: one implementation, one selection, one set of URLs, one
# validation. Read-only GETs plus an in-process render; no form is submitted, so no production data
# and no session is created or modified.
$routeResult = Invoke-RouteChecks 'phase-12'
$problems   += $routeResult.problems
$incompleteRoutes = @($routeResult.incomplete)

# ------------------------------------------------------------------------------------------------
Head "RESULT"
$lanesFinal = @($lanesSeen | Sort-Object)
$expectedLanes = 0..($Workers - 1) | ForEach-Object { "ocr$_" }
$missingLanes = @($expectedLanes | Where-Object { $lanesFinal -notcontains $_ })
$incomplete = @()

if ($incompleteRoutes -and @($incompleteRoutes).Count -gt 0) { $incomplete += @($incompleteRoutes) }
$lastSample = $samples | Select-Object -Last 1

Say "Deployed SHA        : $newHead"
Say "Previous SHA        : $ExpectedFrom"
Say "Deployment          : $(if ($script:AlreadyDeployed) { 'SKIPPED - production was already at the exact clean target' } else { 'performed by this run (ff-only)' })"
Say "Range (authorized)  : PRs $($prsSorted -join ', ') | $commits commits | $($paths.Count) files | +$ins/-$del"
Say "Downtime            : $([int]$script:Downtime.TotalSeconds) s"
Say "Lane workers        : $($lastSample.lane_count) persistent (pids $($lastSample.lane_pids)), stable across every sample"
Say "                      excluded: $($lastSample.isolation_children) per-document isolation child(ren), $($lastSample.resource_trackers) resource tracker(s)"
Say "Heartbeat (info)    : supervisor_heartbeat.json $($lastSample.hb_age)s old; supervisor_state.json $($lastSample.state_json_age_min) min old"
Say "                      (diagnostic only - a blocking run_parallel pass cannot refresh them)"
Say "Old legacy PIDs     : $($oldPids -join ', ')"
Say "New supervisor PID  : $($script:SupPid)   (lock backend pid $($script:LockPid))"
Say "Legacy task backup  : $($script:BackupXml) (SHA256 $backupHash)"
Say "Lifetime locks      : $LOCK_SUPERVISOR + $LOCK_SWEEP held by one backend for the whole window"
Say "START counters      : complete=$($before.ocr_complete) backlog=$($before.ocr_backlog) never=$($before.never_attempted) failed=$($before.failed) timed_out=$($before.timed_out)"
Say "END   counters      : complete=$($after.ocr_complete) backlog=$($after.ocr_backlog) never=$($after.never_attempted) failed=$($after.failed) timed_out=$($after.timed_out)"
Say "Completed delta     : $gained over $([math]::Round($mins,1)) min = $rate docs/min"
Say "Backlog trend       : $($before.ocr_backlog) -> $($after.ocr_backlog) (delta $($after.ocr_backlog - $before.ocr_backlog))"
Say "Failures (counted)  : failed $($before.failed) -> $($after.failed); timed_out $($before.timed_out) -> $($after.timed_out)"
Say "Lanes that COMPLETED: [$($lanesFinal -join ', ')]"
Say "Claim health        : stale=$($claims.stale) duplicate_live=$($claims.duplicate_live) live_lanes=$($claims.lanes)"
($after   | ConvertTo-Json -Depth 6) | Set-Content (Join-Path $ArtifactDir "counters-after-$stamp.json") -Encoding UTF8
($samples | ConvertTo-Json -Depth 6) | Set-Content (Join-Path $ArtifactDir "monitor-samples-$stamp.json") -Encoding UTF8

if ($gained -le 0)                       { $problems += "ocr_complete did not rise over the window" }
if ($after.ocr_backlog -ge $before.ocr_backlog) { $problems += "backlog did not fall over the window" }

# Requirement 8: four idle processes are NOT proof. If the workload could not demonstrate every lane,
# say so plainly instead of passing.
if ($missingLanes.Count -gt 0) {
    $incomplete += ("four-lane processing NOT demonstrated: lanes [$($lanesFinal -join ', ')] completed work, " +
                    "missing [$($missingLanes -join ', ')]. Configured worker count and live child " +
                    "processes are not evidence of processing.")
}

if ($problems.Count -gt 0) {
    Say ''
    Say "CUTOVER FAILED ITS ACCEPTANCE CHECKS:" 'Red'
    $problems | ForEach-Object { Say "  - $_" 'Red' }
    Restore-Legacy 'acceptance checks failed'
    Say ''
    Say "Log: $($script:LogPath)" 'Cyan'
    Stop-Transcript | Out-Null
    exit 1
} elseif ($incomplete.Count -gt 0) {
    Say ''
    Say "CUTOVER OPERATIONAL, VERIFICATION INCOMPLETE:" 'Yellow'
    $incomplete | ForEach-Object { Say "  - $_" 'Yellow' }
    Say ''
    Say "The supervisor is installed, running, and holding both lifetime locks; every per-sample" 'Yellow'
    Say "requirement passed. It is NOT yet proven that all $Workers lanes process work. Extend" 'Yellow'
    Say "monitoring or re-check before declaring the cutover complete." 'Yellow'
    Say "Roll back with: Stop-ScheduledTask '$SupervisorTask'; Disable-ScheduledTask '$SupervisorTask'; Enable-ScheduledTask '$LegacyTask'; Start-ScheduledTask '$LegacyTask'" 'Yellow'
} else {
    Say ''
    Say "CUTOVER SUCCESSFUL - all $Workers lanes ($($lanesFinal -join ', ')) genuinely completed work," 'Green'
    Say "both lifetime locks held throughout, health green, backlog falling." 'Green'
}
Say ''
Say "Log: $($script:LogPath)" 'Cyan'

} catch {
    Say ''
    Say "ABORTED: $($_.Exception.Message)" 'Red'
    if ($script:LegacyDisabled -or $script:SupervisorBuilt) {
        Restore-Legacy 'script aborted'
    } else {
        Say "No operational change had been performed - nothing to restore." 'Yellow'
    }
    Say ''
    Say "Log: $($script:LogPath)" 'Cyan'
    Stop-Transcript | Out-Null
    exit 1
}
Stop-Transcript | Out-Null
