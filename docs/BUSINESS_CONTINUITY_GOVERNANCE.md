# Business Continuity Governance (Phase D.55)

`app/services/business_continuity/governance.py` is a read-only checker that verifies the Business Continuity
layer stays a **composition** over the authoritative operational-resilience owners and never becomes a second
backup platform, monitoring system, disaster-recovery engine, scheduler, notification system, or incident
manager. It returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_business_continuity()` is surfaced through the internal diagnostics endpoint
(`/business-continuity/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second backup / monitoring / DR engine — no execution.** No module calls a backup / restore /
   monitoring / incident / maintenance / scheduler **mutation** — `run_backup(`, `restore_backup(`,
   `run_sync(`, `run_due_scans(`, `run_due_reviews(`, `raise_alert(`, `acknowledge_alert(`, `resolve_alert(`,
   `set_service_status(`, `set_incident_status(`, `set_finding_status(`, `set_maintenance_status(`,
   `create_incident(`, `enqueue_run(`, `execute_run(`, `run_job(`, `heartbeat(`, `converge_worker(`,
   `publish_safe(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every resilience domain declares authoritative + health +
   monitoring owner + runtime gate + deep links; every recovery asset declares owner + backup + restore owner
   + RPO + RTO + runtime gate; every dashboard declares owner + audience + runtime gate + navigation + panels
   + required capabilities + governing services, and references only registered panels; every panel declares
   owner + source + deep link + explainability + permission; all registry keys are unique.
5. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
6. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## Honest not-configured reporting (no fabricated backup status)

Backup / restore / DR have **no authoritative owner in the platform today**. Rather than fabricate a green
backup status — a safety hazard — the backup/restore panels report `{"status": "not_configured"}` honestly,
with a deep link to the continuity surface. This is a structural invariant: the layer reports the owner's real
state and never invents one (the D.50/OCR precedent).

## No infrastructure payloads, ever

Panels and summaries carry **counts + status only** — never infrastructure payloads, secrets, or
client-sensitive data. Diagnostics and analytics counters are low-cardinality aggregates about the layer
itself.

## Authorization & least privilege

- Continuity routes are gated by `observability.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities`
  (`observability.view`); otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure
  counter increments.
- Each **panel self-restricts** to `observability.view`: a principal lacking it receives a `restricted` panel
  with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner (the
  Observability domain's scope, the Runtime engine).

## AI Assist boundary

AI Assist may **summarize** resilience counts (backup status, restore readiness, resilience readiness score) —
fact class `DERIVED`, counts only, deep links only. It **never** starts backups, restores data, acknowledges
incidents, changes monitoring, alters runtime, or modifies infrastructure — every fact comes from a composed
section/summary.

## Enforcement

`tests/test_business_continuity.py` exercises the registries, explainable composition, the honest
not-configured backup panels, authorization (`None` + restricted), gate/policy behavior, the
analytics-counter reuse, diagnostics, the routes (registered + capability-gated), AI summarize-only, and the
architecture invariants (no second backup/monitoring/DR/scheduler/notification/incident system, no mutation,
resilience reads composed from the Observability owner, every dashboard deep-links, every panel names an
authoritative owner). Route count, section registries, ADR count, and the single migration head are guarded
by `tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [BUSINESS_CONTINUITY.md](BUSINESS_CONTINUITY.md), [RESILIENCE_REGISTRY.md](RESILIENCE_REGISTRY.md),
[RECOVERY_REGISTRY.md](RECOVERY_REGISTRY.md), and [ADR-060](adr/ADR-060-business-continuity.md).
