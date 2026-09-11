# Continuous Document Pipeline — operations

A server-side service that discovers every new and changed document, drives it through embedded-text
extraction, OCR, classification and ownership resolution, and keeps doing so without any interactive
session — no ChatGPT, no Claude, no browser, no RDP, nobody logged in. It starts with the server,
resumes unfinished work after a restart, and processes the complete backlog rather than a fixed number
of documents.

It ships **disabled**. Nothing below happens on any host until somebody sets
`DOCUMENT_PIPELINE_ENABLED=true` there.

---

## What it does, in order

| Stage | What happens | Delegates to |
| --- | --- | --- |
| `extract` | Embedded text: PDF text layer, Excel, Word, plaintext. If text comes out, **the OCR stage is skipped entirely.** | `document_owner_proposal.extract_document_text` |
| `ocr` | Reuses a byte-identical document's text when one exists (SHA-256 dedupe); otherwise runs the engine. | `document_ocr.run_ocr` |
| `classify` | Document type, year, and a non-authoritative owner proposal. | `document_pipeline.analyze_and_persist` |
| `ownership` | The three lanes below. The only stage that can write an owner. | `households.resolve_document_ownership` |

### Ownership lanes

Consulted in order of authority. The first lane that applies decides; nothing weaker gets a second say.

- **Drake — authoritative.** Identity attribution from taxpayer/spouse identifier hashes. Resolves, or
  holds for review. A Drake HOLD never falls through to filename evidence.
- **TaxDome — authoritative.** The account/folder mapping *is* the client mapping. An unresolved folder
  goes to review with candidate people attached, never to a content guess.
- **SharePoint — evidence.** Path, filename, OCR text, identity, address, account and household
  evidence. Only a HIGH proposal links. MEDIUM and AMBIGUOUS go to the single review queue.

**Ownership is never overwritten.** Two independent guards: the canonical write re-checks
"unowned" inside the same UPDATE statement, and a lane that contradicts a stored owner opens an
`ownership_conflict` review instead of changing anything.

---

## Before you enable it: plan the corpus

`scripts/plan_document_ownership.py` answers, read-only and ahead of time, what the pipeline would do
to every document you already have — which lane claims it, what it would link to, and how many
decisions land on a person.

```bash
python scripts/plan_document_ownership.py --out reports/ownership_plan.json
```

It is safe to run against production while everything else is running. The connection sets
`default_transaction_read_only=on`, so PostgreSQL refuses any write from that session — a bug in the
planner can crash, it cannot write. It never opens a document to extract text (every judgement comes
from the database and from OCR text already extracted), it takes no advisory lock, and it drops itself
to below-normal process priority, so a full-corpus OCR sweep keeps the CPU it needs.

There is no `--limit`. `--chunk-size` paginates the walk so a crash costs one chunk; `--resume` (the
default) continues from the checkpoint.

Read the `conflicts_with_existing_ownership` and `ambiguous` counts before enabling anything. They are
the work the pipeline will hand back to staff, and they are much cheaper to look at now.

## Install

1. **Apply the migration.** The pipeline's tables arrive with `docpipe01`.

```bash
alembic upgrade head
```

Verify: `alembic current` reports `docpipe01`, and `python -m app.jobs.document_pipeline_runner status`
returns a snapshot rather than `REFUSED`.

2. **Install the dependency.** `psutil` powers the CPU and memory gates. Without it the pipeline still
   runs, governed by database-pool headroom alone, and reports `system_metrics_available: false`.

```bash
pip install -r requirements.txt
```

3. **Choose a host mode.** Either one, not both:

   - **Scheduler-hosted** (simplest): the existing Client360 service runs a discover-and-drain tick.
     Set `DOCUMENT_PIPELINE_ENABLED=true` in the application environment and restart the app service.
   - **Dedicated service** (recommended for a large backlog): its own process and its own worker pool,
     so heavy OCR never competes with request handling inside the web process. See below.

Running both is safe — every worker leases the documents it claims, so they cannot collide — but the
dedicated service is the one to size for throughput.

### Dedicated Windows service

Run under a service account with read access to the document storage roots and the same environment
variables as the application (at minimum `DATABASE_URL`). The process is an ordinary console program;
it needs no desktop, no signed-in user, and no interactive session.

`deploy/windows/Install-DocumentPipelineService.ps1` does all of it, including loading the pipeline
settings out of the canonical environment file:

```powershell
.\deploy\windows\Install-DocumentPipelineService.ps1 -Action install -Workers 4
.\deploy\windows\Install-DocumentPipelineService.ps1 -Action start
```

It installs with `Start SERVICE_AUTO_START` (start with the server), `AppExit Default Restart` (a crash
self-heals; the restarted process reclaims its own leases and continues) and a console stop signal, so
a planned stop releases the leases immediately instead of waiting them out.

---

## Start, stop, resume

```powershell
# Start / stop the dedicated service
.\deploy\windows\Install-DocumentPipelineService.ps1 -Action start
.\deploy\windows\Install-DocumentPipelineService.ps1 -Action stop
```

```bash
# Run it in the foreground (a console, for a first run or a diagnosis)
python -m app.jobs.document_pipeline_runner run --workers 4

# One-shot operations
python -m app.jobs.document_pipeline_runner discover   # enqueue new/changed documents, then exit
python -m app.jobs.document_pipeline_runner drain      # process the queue until empty, then exit
```

**Resuming is not a command.** Stopping the service and starting it again *is* the resume: every piece
of state lives in the database, so a new process finds the same queue, at the same stages, and carries
on. A clean stop hands back its leases immediately; a hard kill simply lets them lapse, after which any
worker may take the work. Nothing is lost either way, and no document is processed twice.

To push one specific document back through after fixing whatever blocked it:

```bash
python -m app.jobs.document_pipeline_runner requeue --document-id 1234
```

---

## Watch it

```bash
python -m app.jobs.document_pipeline_runner status     # the full snapshot
python -m app.jobs.document_pipeline_runner health     # exit code 0 healthy/idle, 1 stalled/stopped
python -m app.jobs.document_pipeline_runner blockers   # permanent failures, newest first
python -m app.jobs.document_pipeline_runner reviews    # ambiguous ownership awaiting a person
```

The same three views are read-only HTTP endpoints, gated on the existing `documents.view` capability:

| Endpoint | Answers |
| --- | --- |
| `GET /api/document-pipeline/metrics` | backlog, running, completed, linked, review, blocked, failed, throughput, last heartbeat |
| `GET /api/document-pipeline/health` | 200 healthy or idle, **503 stalled or stopped** |
| `GET /api/document-pipeline/blockers` | the blocker queue; `?queue=review` for the ownership review queue |

Point an external monitor at `/api/document-pipeline/health`, the same way you would at `/readiness`.

### What "stalled" means

Not "idle". An empty queue with no workers is a *finished* pipeline, and reporting that as an incident
is how monitoring gets muted. A stall requires all three of: work is waiting, nothing has completed
inside the stall window, and the newest worker heartbeat is older than that window. Because the
heartbeat advances *during* a document, a worker grinding through a 400-page scan reads as alive.

When the pipeline is stalled or stopped with work waiting, the monitor logs at ERROR and raises an
observability alert (`document_pipeline.stalled.<timestamp>`).

---

## Tuning

| Variable | Default | What it governs |
| --- | --- | --- |
| `DOCUMENT_PIPELINE_ENABLED` | `false` | Whether the scheduler runs the tick and monitor at all |
| `DOCUMENT_PIPELINE_WORKERS` | `2` | Parallel workers in the dedicated service |
| `DOCUMENT_PIPELINE_BATCH_SIZE` | `1` | Documents claimed per pass — throughput comes from more workers, not bigger claims |
| `DOCUMENT_PIPELINE_LEASE_SECONDS` | `600` | How long a claim survives without a heartbeat |
| `DOCUMENT_PIPELINE_MAX_ATTEMPTS` | `5` | Attempts before a document becomes a blocker |
| `DOCUMENT_PIPELINE_DISCOVERY_PAGE_SIZE` | `1000` | Rows per discovery page — paginates the walk, does **not** cap the backlog |
| `DOCUMENT_PIPELINE_CPU_LIMIT_PERCENT` | `85` | Start no new work above this CPU load |
| `DOCUMENT_PIPELINE_MEMORY_LIMIT_PERCENT` | `85` | Start no new work above this memory usage |
| `DOCUMENT_PIPELINE_DB_HEADROOM` | `5` | Connections kept free for the staff-facing application |
| `DOCUMENT_PIPELINE_STALL_SECONDS` | `900` | Silence with work waiting that counts as a stall |
| `DOCUMENT_PIPELINE_TICK_INTERVAL_SECONDS` | `60` | Scheduler tick cadence |
| `DOCUMENT_PIPELINE_MONITOR_INTERVAL_SECONDS` | `300` | Stall-check cadence |

Raise the worker count deliberately, watching `cpu_percent` and `memory_percent` in the metrics
snapshot. Two workers on a busy application server is a conservative starting point, not a target.

---

## Relationship to the existing OCR sweeps

`app/jobs/ocr_runner.py` still exists and still works. It is the right tool for a one-off migration of
a known corpus; the pipeline is the right tool for a corpus that keeps growing.

They do not fight. The sweeps guard themselves with a PostgreSQL advisory lock, and the pipeline
**reads that lock without taking it**: while a sweep holds it, the pipeline's OCR stage defers and
retries later. Advisory locks are cluster-wide, so a sweep running against any database on the server
is detected. Everything else in the pipeline keeps running while OCR waits.

**Do not start the pipeline while a full-corpus OCR migration is in progress.** It will behave
correctly — deferring every OCR document — but you will be watching a queue that cannot drain, which is
a confusing way to spend an afternoon. Wait for the sweep to finish.

---

## Rollback

The pipeline is additive: it adds five tables, three read-only endpoints and two scheduler jobs, and it
changes no existing behaviour while disabled. Roll back in the smallest step that solves the problem.

**1. Stop it (seconds, no data loss).** This is almost always the right answer.

```powershell
.\deploy\windows\Install-DocumentPipelineService.ps1 -Action stop
```

and/or set `DOCUMENT_PIPELINE_ENABLED=false` and restart the application service. In-flight leases
lapse; the queue keeps its state; nothing is lost. Starting it again resumes exactly where it stopped.

**2. Revert the code.** Deploy the previous release. The `document_pipeline_*` tables stay behind,
holding their state, referenced by nothing. They are inert.

**3. Revert the schema.** Only if you need the tables gone.

```bash
alembic downgrade drake03
```

This **drops the queue, the blocker queue and the ownership review queue**, including any unresolved
reviews. It does not touch documents, ownership, OCR text or classifications — those live in the tables
that already owned them, written through the same services the rest of the application uses. Re-running
`alembic upgrade head` later starts the pipeline from an empty queue, which re-discovers the whole
corpus and skips everything already processed (the OCR text and the classifications are still there).

**What rollback cannot undo:** ownership links the pipeline wrote are ordinary document ownership,
identical to what a staff member's click produces, and they are audited as
`document.ownership_resolved`. Removing them is a data decision, not a deploy step. Query the audit
chain for that action with `request_id = 'document-pipeline'` to find exactly what it linked.

---

## Where things live

| | |
| --- | --- |
| Service package | `app/services/document_pipeline_continuous/` |
| Service host + CLI | `app/jobs/document_pipeline_runner.py` |
| Scheduler jobs | `app/jobs/scheduler.py` (`document-pipeline-tick`, `document-pipeline-monitor`) |
| Routes | `app/routes/document_pipeline.py` |
| Settings | `app/config.py` |
| Migration | `migrations/versions/docpipe01_continuous_document_pipeline.py` |
| Deployment scripts | `deploy/windows/Install-DocumentPipelineService.ps1` |
| Read-only corpus plan | `scripts/plan_document_ownership.py` |
| Tests | `tests/test_document_pipeline_queue.py`, `..._stages.py`, `..._service.py`, `tests/test_document_ownership_plan.py` |
