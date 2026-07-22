# ADR-027 — Enterprise Automation as an orchestration layer over the existing scheduler

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Automation); Business Operations Owner (Michael Shelton —
background-execution requirements); Compliance Architecture (execution audit history is regulated
recordkeeping). Authorized compliance reviewer: Not yet designated.

## Context
The platform already runs background work through an **in-process, single-instance APScheduler**
(`app/jobs/scheduler.py`) with hardcoded job registrations (M365 sync, workflow/exception SLA
sweeps, benefits/insurance scans, tax reminders, the gated outbox dispatcher), a **transactional
outbox** (`outbox_events`: status + attempts + `available_at` backoff + dead-letter), and a
**notification worker** (single-instance claim + a computed `RetryPolicy`). There was **no** generic
job / schedule / run / worker / queue / lock table — scheduling was purely in-code — and nothing
swept `report_schedules.next_run_at` (D.21 reserved that sweeper for "a future phase"). Every domain
already exposes a clean, idempotent execution entry point (`reporting.run_schedule`,
`workflow_automation.launch_workflow`, `analytics.capture_all`, `notification_worker.run_dispatch_cycle`,
the M365 sync jobs), and the workflow `actions.py` registry (`create_operational_task`,
`run_report_schedule`) already establishes the "record a typed action → invoke the existing service"
pattern.

## Decision
Enterprise Automation is a new authoritative **orchestration domain** that owns **execution
metadata only** and **duplicates no business logic**. **It wraps, and does not replace, the existing
scheduler and outbox.**
- **Owns:** `automation_jobs`, `automation_job_templates`, `automation_schedules`, `automation_runs`
  (execution history), `automation_queues`, `automation_retry_policies`, `automation_failure_policies`,
  `automation_windows` (execution + maintenance), `automation_workers`,
  `automation_worker_heartbeats`, `automation_execution_locks`, and `automation_events` (an
  **append-only** polymorphic audit ledger).
- **Dispatches, never reimplements.** A job carries a `job_type` from a fixed registry
  (`app/services/automation/dispatch.py`) that maps to an EXISTING service:
  `run_report_schedule`/`report_schedules_sweep` → Reporting; `launch_workflow`/`workflow_sla_sweep`
  → Workflow; `capture_analytics_snapshots` → Analytics; `dispatch_notifications`/`send_communication`
  → Communications/notifications; `dispatch_outbox` → the outbox; `m365_*_sync` → the M365 jobs;
  `maintenance`/`custom` → deterministic no-ops. **`report_schedules_sweep` is the D.21 "due
  schedules" sweeper.**
- **Execution is deterministic and single-instance** — the established house model (no distributed
  execution, no external queue broker, no Kubernetes). A run acquires an `automation_execution_locks`
  single-flight lock, dispatches, records history, and on error applies the job's **retry policy**
  (attempts + delay/backoff, mirroring the outbox) then its **failure policy** (default: dead-letter)
  — exactly the outbox's attempts + `available_at` + dead-letter shape. APScheduler's
  `max_instances=1, coalesce=True` remains the outer overlap guard.
- **The runner tick** (`runner.run_worker_cycle`) sweeps due schedules into runs, drains runnable
  runs, and writes a worker heartbeat. It is driven by **ONE new APScheduler job** registered in
  `start_scheduler()` **gated OFF by default** (`automation_enabled()`, same posture as the outbox
  dispatcher) — no new threads; runtime behavior is unchanged unless enabled.
- **Authority split:** triggering a run requires the human principal's `automation.execute`; the
  dispatch itself executes with a **system principal** (derived from the job's creator, holding
  `record.read_all`) because firm-level jobs (snapshot capture, schedule sweeps) require firm-wide
  reads. Job creation requires `automation.manage` (administrator/operations).
- **Timeline:** approved execution lifecycle events (`automation_job_started`/`_completed`/`_failed`,
  `automation_scheduled_execution`) publish to the shared timeline **only** for client-anchored runs
  (the timeline requires a person/household anchor); firm-level jobs record only to
  `automation_events`. **Not** every state transition emits. **Record scope** is enforced on
  client-anchored runs; firm config is capability-gated.
- **Security:** the `automation.view/manage/execute/audit*/admin*` capability family (`*` =
  sensitive) gates a new `/automation` surface (in-route; matches no middleware RULE).

## Alternatives considered
1. **Replace APScheduler with a DB-backed / distributed scheduler (Celery, a broker, K8s CronJobs).**
   Rejected: the spec forbids distributed execution / external brokers / Kubernetes, and the
   single-instance scheduler + outbox already work. Automation wraps them.
2. **Store job execution in the outbox.** Rejected: the outbox is event-delivery-shaped (publish →
   subscriber fanout); jobs are invoke-handler-shaped. Automation models on the outbox's retry shape
   without overloading it.
3. **Let each domain keep scheduling itself.** Rejected: there was no unified job history, retry
   policy, run audit, or operator surface; Automation centralizes execution metadata while leaving
   business logic in the domains.
4. **Real distributed locks / leader election.** Rejected: single-instance is an established,
   documented assumption; `automation_execution_locks` (row + TTL) plus `max_instances=1` suffice,
   with the `pg_advisory_xact_lock` idiom available if stronger single-flight is ever needed.

## Reasons for the decision
The firm needs one authoritative model of *what background work exists, when it runs, whether it
succeeded, and how it retries* — with an operator surface and audit — without a distributed
platform. An orchestration layer that dispatches to the existing services and reuses the outbox's
retry model and the scheduler's single-instance idiom delivers that while preserving every ADR and
the D.5 golden.

## Consequences
### Positive consequences
- One authoritative execution-metadata domain: jobs, schedules, runs (history), queues,
  retry/failure policies, execution/maintenance windows, workers, heartbeats, execution locks, and
  an append-only audit ledger — with a `job_type` map to existing services.
- The D.21 report-schedule sweeper now exists; analytics snapshots, workflow launches, notification
  drains, and M365 syncs can be scheduled uniformly.
- Zero new distributed infrastructure; the scheduler and outbox are wrapped, not replaced; the tick
  is gated OFF by default.

### Negative consequences and tradeoffs
- Two scheduling surfaces coexist: the hardcoded APScheduler jobs and the Automation-managed jobs —
  a documented coexistence (Automation can subsume the hardcoded ones over time via `job_type`s).
- Scheduled runs execute with an elevated **system principal** (`record.read_all`) — necessary for
  firm-level jobs; mitigated by `automation.manage`-gated job creation and full run audit.
- Single-instance only: correctness relies on `max_instances=1` + the execution-lock TTL; a stale
  `running` run after a crash is reaped on the lock's expiry, not instantly.
- Firm-level job lifecycle events do not appear on the client timeline (by design); their history
  lives in `automation_events`.

## Enforcement
- `app/database/automation_tables.py::define_automation_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `t0e1f2a3b4c5` (12 tables +
  append-only trigger on `automation_events` + 5 `automation.*` capabilities + default
  policy/queue seeds). Services `app/services/automation/{common,dispatch,catalog,service,runner}.py`
  (`dispatch.py` is the sole business-logic seam — it invokes existing services, never source
  tables). Routes `app/routes/automation.py` (in-route `automation.*` gating; `/automation` matches
  no middleware RULE). One gated APScheduler job (`automation-tick`) in `app/jobs/scheduler.py` +
  `automation_enabled()`/`automation_tick_interval_seconds()` in `app/config.py`. The D.5 golden, the
  outbox, the notification worker, and every dispatched domain are untouched. Tests:
  `tests/test_automation.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Introducing a distributed scheduler / external broker / multi-instance workers, a real distributed
lock or leader election, or executing business logic inside Automation (rather than dispatching)
would each warrant a new or superseding ADR.

## References
- `app/services/automation/`, `app/routes/automation.py`, `app/database/automation_tables.py`,
  migration `migrations/versions/t0e1f2a3b4c5_automation_platform.py`
- Wrapped infrastructure: `app/jobs/scheduler.py`, `app/platform/outbox.py`,
  `app/services/notification_worker.py`; dispatched services: reporting/workflow/analytics/
  communications/Microsoft 365
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_automation.py`; relates to ADR-002, ADR-009, ADR-020, ADR-021, ADR-022, ADR-026
