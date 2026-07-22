"""Enterprise Automation platform (Phase D.22).

The authoritative orchestration domain for firm background execution. It owns **execution metadata
only** — jobs, job templates, schedules, runs (execution history), queues, retry/failure policies,
execution/maintenance windows, workers, heartbeats, and execution locks — and **dispatches to
existing services** (reporting/workflow/analytics/communications/Microsoft 365) via the
``job_type`` map. It **owns no business records and duplicates no business logic**. It wraps the
existing in-process APScheduler (one new gated tick job) and mirrors the transactional outbox's
retry/backoff/dead-letter model: **no distributed execution, no external queue broker, no
Kubernetes**. Firm-level job lifecycle events record to the append-only ``automation_events``
ledger; client-anchored runs may publish a guarded timeline event. Business domains remain
authoritative; Automation only executes and records.
"""
