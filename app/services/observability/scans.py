"""Observability scans (Phase D.26) — the Automation entry point (metadata only).

``run_due_scans`` is the deterministic job Automation invokes (``observability_scan`` dispatch): it
records a runtime snapshot (reusing the readiness surface), runs enabled diagnostic checks against
the live platform state, and evaluates enabled alert rules whose telemetry metric has breached its
threshold — recording results/alerts as observability METADATA. It performs no external monitoring
and mutates no canonical record. Automation executes; Observability owns the metadata (ADR-027 / D.22
reuse).
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import engine
from app.db import observability_alert_rules as rules_t
from app.db import observability_telemetry_metrics as metrics_t

from . import alerts, health
from .common import now


def run_due_scans(principal, *, actor_user_id=None) -> dict:
    """Capture a runtime snapshot, run enabled diagnostic checks, and evaluate enabled alert rules.
    Returns a small summary. Records metadata only."""
    snapshot = health.capture_runtime_snapshot(principal, actor_user_id=actor_user_id)

    # Evaluate enabled alert rules whose bound telemetry metric has breached its critical/warning
    # threshold (deterministic — reads the metric's last recorded value, raises no duplicate alert).
    alerts_raised = 0
    with engine.connect() as c:
        rows = list(c.execute(select(
            rules_t.c.id, rules_t.c.code, rules_t.c.name, rules_t.c.severity, rules_t.c.service_id,
            metrics_t.c.id.label("metric_id"), metrics_t.c.last_value, metrics_t.c.warning_threshold,
            metrics_t.c.critical_threshold)
            .select_from(rules_t.join(metrics_t, rules_t.c.telemetry_metric_id == metrics_t.c.id))
            .where(rules_t.c.enabled.is_(True))).mappings())

    stamp = int(now().timestamp())
    for r in rows:
        breach = _breach(r)
        if breach is None:
            continue
        code = f"auto-{r['code']}-{stamp}"
        try:
            alerts.raise_alert(principal, code=code, title=f"Alert: {r['name']}",
                               alert_rule_id=r["id"], service_id=r["service_id"], severity=r["severity"],
                               detail=f"telemetry breach ({breach}) value={r['last_value']}",
                               actor_user_id=actor_user_id)
            alerts_raised += 1
        except Exception:
            continue

    return {"snapshot_id": snapshot["id"], "snapshot_summary": snapshot["summary"],
            "rules_evaluated": len(rows), "alerts_raised": alerts_raised,
            "degraded_services": health.unused_services_probe(principal)}


def _breach(row) -> str | None:
    value = row["last_value"]
    if value is None:
        return None
    if row["critical_threshold"] is not None and value >= row["critical_threshold"]:
        return "critical"
    if row["warning_threshold"] is not None and value >= row["warning_threshold"]:
        return "warning"
    return None
