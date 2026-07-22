"""Enterprise Observability domain (Phase D.26) — authoritative platform-operations domain.

Owns observability metadata only: a service inventory + dependency graph, health checks/snapshots,
diagnostic checks/results, telemetry sources/metrics, alert rules/alerts/suppressions, runtime
snapshots, environment/deployment references, maintenance windows, and reliability incidents/findings
— plus an append-only ``observability_events`` audit ledger. It owns no business records and is never
a source of truth for operational or business entities.

Reuses (never replaces/duplicates) the existing health endpoints (``/readiness``), the scheduler
snapshot (``app.jobs.scheduler.scheduler_status``), the logging config + request-id correlation
(``app/observability/logging.py``), the notification ledger (alerts reference it — no delivery), the
Automation dispatch, the Analytics metric registry (telemetry references it), and the audit
hash-chain (``app.security.audit.write_audit_event``).
"""
