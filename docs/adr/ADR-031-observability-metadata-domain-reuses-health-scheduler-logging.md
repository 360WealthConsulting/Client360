# ADR-031 — Enterprise Observability as a metadata domain that reuses health/scheduler/logging/notifications; never replaces runtime health, logging, or exception handling

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Observability); Reliability / Operations; Business Operations
Owner (Michael Shelton — operational-monitoring requirements). Authorized compliance reviewer: Not
yet designated.

## Context
The platform already exposes runtime operational surfaces: `/health` (liveness, `app/routes/
dashboard.py`) and `/readiness` (`app/routes/ops.py` — DB probe, Alembic migration-drift `current_head`
vs `expected_head`, `scheduler_status()`, Microsoft 365 sync-health); a central logging config with
request-id correlation (`app/observability/logging.py` + `request.state.request_id` threaded through
`write_audit_event`); the in-process APScheduler (`app/jobs/scheduler.py`, `scheduler_status()`);
run-ledgers that already record runtime facts (`automation_runs`/`automation_worker_heartbeats`,
`outbox_events`/`outbox_dead_letters`, `integration_connectors`/`integration_sync_runs`/
`sync_profiles.sync_health`, `microsoft_accounts` sync health); two app-level FastAPI exception
handlers; and the notification ledger (`record_notification` — records intent, delivery owned by the
dispatch). There is **no** Prometheus/OpenTelemetry/statsd stack and **no** metrics scrape endpoint.

There was **no** domain that governs *platform observability*: a service inventory + dependency graph,
health/diagnostic definitions and their results, telemetry metric definitions and thresholds, alert
rules/alerts/suppressions, runtime snapshots, maintenance windows, and reliability incidents/findings.
The risk of adding one is that it re-implements health/logging/exception handling, delivers
notifications itself, becomes a second source of truth for operational state, or bolts on an external
monitoring stack.

## Decision
Enterprise Observability is a new authoritative **platform-operations domain** that owns
**observability metadata only** and is **never a source of truth** for operational or business
entities.
- **Owns:** `observability_services` + `_service_dependencies`, `_health_checks` + `_health_snapshots`,
  `_diagnostic_checks` + `_diagnostic_results`, `_telemetry_sources` + `_telemetry_metrics`,
  `_alert_rules` + `_alerts` + `_alert_suppressions`, `_maintenance_windows`, `_runtime_snapshots`,
  `_environment_profiles` + `_deployment_references`, `_reliability_incidents` + `_reliability_findings`,
  and the **append-only** `observability_events` ledger.
- **Reuses, never replaces.** Runtime snapshots call the existing readiness logic — the DB probe, the
  Alembic head vs expected head, and `scheduler_status()` — and record a metadata row; they never
  reimplement the probes. Health/diagnostic *checks* are definitions and *snapshots/results* are
  recorded observations; the authoritative live health remains `/health` and `/readiness`. Logging
  stays `app/observability/logging.py`; telemetry references `request_id`/existing run-ledgers and
  copies no log or metric storage. The two app-level exception handlers are untouched.
- **Telemetry references Analytics; Analytics stays authoritative.** A telemetry metric is a
  definition (kind/unit/interval/thresholds/aggregation) with an optional `analytics_metric_key` that
  *references* an Analytics `Metric`. `collect_metric` records a supplied/observed value — it performs
  no business computation.
- **Alerts reference, never deliver.** Alert rules carry severity + routing (channel/notification
  *references*); raising an alert records metadata and may reference an existing notification-ledger
  row (`notification_ref`) — **no notification delivery is implemented this phase**; delivery stays
  owned by the notification dispatch (Communications/Automation). Maintenance windows and suppressions
  record intent that suppresses alert *records* (an alert covered by an active suppression is recorded
  `suppressed`).
- **References other domains, never owns them.** A service may reference an Integration connector
  (`reference_type`/`reference_id`); a reliability finding references a Security finding
  (`security_finding_id`) or an Integration connector (`integration_connector_id`) — Security and
  Integration stay authoritative.
- **Integrations:** **Automation** runs the `observability_scan` job (a new dispatch handler +
  widened `JOB_TYPES` CHECK) — it captures a runtime snapshot and evaluates enabled alert rules
  against their metric's last recorded value. **Analytics** consumes observability statistics (failed
  health checks, open alerts, operational services, diagnostic failures, reliability incidents);
  Observability never depends on Analytics. **Timeline** receives approved, **client-anchored**
  lifecycle events only (a reliability incident on a client-scoped item); firm-level observability
  events (service degraded/restored, alert acknowledged, maintenance started) record to
  `observability_events` only and are **never emitted per health check**.
- **Security of the domain itself:** capabilities `observability.view/manage/execute/audit*/admin*`
  (`*` = sensitive), gated **in-route** (`/observability` matches no middleware RULE). Sensitive
  diagnostic detail is withheld from responses unless the caller holds `observability.audit` (server-
  side, per ADR-005). Record scope is enforced for client-anchored reliability incidents (ADR-004).

## Alternatives considered
1. **Add Prometheus/OpenTelemetry / a `/metrics` scrape endpoint.** Rejected: this phase is metadata
   governance, not a metrics stack. Telemetry references the existing run-ledgers and the Analytics
   registry; a real collector is a separately-approved change (future ADR).
2. **Re-implement health/liveness/readiness inside Observability.** Rejected: `/health` and
   `/readiness` are the runtime probes; runtime snapshots reuse them and record metadata.
3. **Deliver alert notifications from Observability.** Rejected: delivery stays owned by the
   notification dispatch; alerts reference the ledger. Prevents a second delivery path.
4. **Make Observability a source of truth for operational state (e.g. own connector health).**
   Rejected: Integration/Automation/Security own their state; Observability references it.
5. **Emit a timeline/audit event per health check.** Rejected: ADR-009 keeps the timeline curated;
   only approved, client-anchored lifecycle events are published, and health results record to the
   ledger.

## Reasons for the decision
The firm needs one authoritative model of *what services exist and depend on what, which health/
diagnostic checks run and their latest results, which telemetry metrics and thresholds are defined,
which alerts are open/acknowledged/suppressed, and which reliability incidents are active* — with
audit and analytics — without re-implementing health/logging/exception handling, without delivering
notifications itself, without a second source of truth for operational state, and without an external
monitoring stack. A metadata domain that reuses the readiness surface, the scheduler snapshot, the
logging config, the notification ledger, and the run-ledgers delivers this while preserving ADR-004,
ADR-005, ADR-009, and ADR-020.

## Consequences
### Positive consequences
- One authoritative observability-metadata domain reusing the existing health endpoints, scheduler
  snapshot, logging, notification ledger, and run-ledgers.
- No re-implemented health/logging/exception handling, no notification delivery, no external
  monitoring stack, no second source of truth for operational state. Automation runs scans; Analytics
  gains observability metrics; the timeline receives only approved client-anchored events.

### Negative consequences and tradeoffs
- Telemetry metrics are **definitions with recorded values** — Observability does not scrape or
  compute them at runtime; `collect_metric` records a supplied value (a real collector is future).
- Alerts and maintenance windows are **metadata** — no notification is delivered and no traffic is
  actually gated; suppression affects the alert *record* only.
- Runtime snapshots reflect the readiness surface at capture time; they are point-in-time metadata,
  not a continuous monitor.
- The D.22 `JOB_TYPES` CHECK constraints were widened again to admit `observability_scan` (a
  documented, reversible cross-domain migration touch).

## Enforcement
- `app/database/observability_tables.py::define_observability_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `x8b9c0d1e2f3` (18 tables +
  append-only `observability_events` ledger with `prevent_observability_event_mutation()` +
  `observability_events_immutable` trigger + 5 `observability.*` capabilities + widened automation
  `JOB_TYPES` + a telemetry-source registry seed). Services `app/services/observability/{common,
  catalog,health,telemetry,alerts,incidents,scans,service}.py`. Routes `app/routes/observability.py`
  (in-route `observability.*` gating; `/observability` matches no middleware RULE; sensitive
  diagnostic detail gated by `observability.audit`). Automation `observability_scan` handler in
  `app/services/automation/dispatch.py`. The `/health`/`/readiness` endpoints, `scheduler_status`,
  `app/observability/logging.py`, the exception handlers, the notification dispatch, and the D.5
  golden are untouched. Observability is registered in `source_producer_modules` (must not import
  composition layers). Tests: `tests/test_observability_platform.py`; manifest / platform-architecture
  / route-count guards updated.

## Exceptions
None currently approved. `administrator`/`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Adding a real metrics collector (Prometheus/OpenTelemetry) or `/metrics` endpoint, delivering alert
notifications from Observability, having Observability actively probe/scrape health at runtime, or
making it authoritative for any operational state would each warrant a new or superseding ADR.

## References
- `app/services/observability/`, `app/routes/observability.py`, `app/database/observability_tables.py`,
  migration `migrations/versions/x8b9c0d1e2f3_observability_platform.py`
- Reused infra: `app/routes/ops.py` (`/readiness`), `app/routes/dashboard.py` (`/health`),
  `app/jobs/scheduler.py` (`scheduler_status`), `app/observability/logging.py`,
  `app/services/notifications.py` (`record_notification`), the automation/outbox/integration
  run-ledgers, and the Analytics `Metric` registry
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_observability_platform.py`; relates to ADR-004, ADR-005, ADR-009, ADR-015, ADR-016,
  ADR-020, ADR-027, ADR-029, ADR-030
