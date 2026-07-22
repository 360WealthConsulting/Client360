"""Alert rules, operational alerts, suppressions & maintenance windows (Phase D.26) — metadata only.

Alert rules define a condition over a telemetry metric/service and a severity + routing (channel/
notification *references* — no delivery here). Raising an alert records metadata and, when a
``notification_ref`` is supplied or a rule routes to a channel, it **references** the existing
notification ledger — delivery stays owned by the notification dispatch (Communications/Automation).
Maintenance windows suppress alerts for their duration. **No notification delivery is implemented in
this phase.** Managing requires ``observability.manage``; ack/resolve/raise require
``observability.execute`` (enforced in-route).
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.database.observability_tables import (
    ALERT_SEVERITIES,
    MAINTENANCE_STATUSES,
)
from app.db import engine
from app.db import observability_alert_rules as rules_t
from app.db import observability_alert_suppressions as suppress_t
from app.db import observability_alerts as alerts_t
from app.db import observability_maintenance_windows as windows_t

from .common import (
    ObservabilityError,
    ObservabilityNotFound,
    now,
    record_event,
    write_audit,
)

# --- alert rules -------------------------------------------------------------

def list_rules(*, service_id=None, enabled=None):
    with engine.connect() as c:
        stmt = select(rules_t).order_by(rules_t.c.code)
        if service_id is not None:
            stmt = stmt.where(rules_t.c.service_id == service_id)
        if enabled is not None:
            stmt = stmt.where(rules_t.c.enabled.is_(bool(enabled)))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_rule(principal, *, code, name, telemetry_metric_id=None, service_id=None, severity="warning",
                condition=None, routing=None, description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    if severity not in ALERT_SEVERITIES:
        raise ObservabilityError(f"invalid severity {severity!r}")
    with engine.begin() as c:
        if c.scalar(select(rules_t.c.id).where(rules_t.c.code == code)) is not None:
            raise ObservabilityError(f"alert rule code {code!r} already exists")
        row = c.execute(rules_t.insert().values(
            code=code, name=name.strip(), telemetry_metric_id=telemetry_metric_id, service_id=service_id,
            severity=severity, condition=condition, routing=routing, enabled=True,
            description=description, created_by_user_id=actor_user_id).returning(*rules_t.c)).mappings().one()
        return dict(row)


# --- alerts ------------------------------------------------------------------

def list_alerts(*, status=None, severity=None, service_id=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        conds = []
        if status:
            conds.append(alerts_t.c.status == status)
        if severity:
            conds.append(alerts_t.c.severity == severity)
        if service_id is not None:
            conds.append(alerts_t.c.service_id == service_id)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(alerts_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(alerts_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(alerts_t.c.id.desc()).limit(page_size).offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size}


def raise_alert(principal, *, code, title, alert_rule_id=None, service_id=None, severity="warning",
                detail=None, notification_ref=None, actor_user_id=None) -> dict:
    """Raise an operational alert (metadata). If an active suppression/maintenance window covers the
    rule/service, the alert is recorded as ``suppressed``. This never delivers a notification — a
    ``notification_ref`` may reference an existing notification-ledger row."""
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise ObservabilityError("code and title are required")
    if severity not in ALERT_SEVERITIES:
        raise ObservabilityError(f"invalid severity {severity!r}")
    ts = now()
    with engine.begin() as c:
        if c.scalar(select(alerts_t.c.id).where(alerts_t.c.code == code)) is not None:
            raise ObservabilityError(f"alert code {code!r} already exists")
        suppression = _active_suppression(c, alert_rule_id=alert_rule_id, service_id=service_id, ts=ts)
        status = "suppressed" if suppression else "open"
        row = c.execute(alerts_t.insert().values(
            code=code, alert_rule_id=alert_rule_id, service_id=service_id, severity=severity,
            status=status, title=title.strip(), detail=detail, triggered_at=ts,
            suppression_id=suppression, notification_ref=notification_ref,
            created_by_user_id=actor_user_id).returning(*alerts_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="alert", entity_id=row["id"], event_type=f"alert_{status}",
                     to_status=status, actor_user_id=actor_user_id, payload={"severity": severity})
    write_audit(f"observability.alert_{status}", entity_type="alert", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"severity": severity})
    return row


def acknowledge_alert(principal, alert_id: int, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        al = c.execute(select(alerts_t).where(alerts_t.c.id == alert_id)).mappings().first()
        if al is None:
            raise ObservabilityNotFound(str(alert_id))
        row = c.execute(alerts_t.update().where(alerts_t.c.id == alert_id).values(
            status="acknowledged", acknowledged_by_user_id=actor_user_id, acknowledged_at=now(),
            updated_at=now()).returning(*alerts_t.c)).mappings().one()
        record_event(c, entity_type="alert", entity_id=alert_id, event_type="alert_acknowledged",
                     from_status=al["status"], to_status="acknowledged", actor_user_id=actor_user_id)
        row = dict(row)
    write_audit("observability.alert_acknowledged", entity_type="alert", entity_id=alert_id,
                actor_user_id=actor_user_id)
    return row


def resolve_alert(principal, alert_id: int, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        al = c.execute(select(alerts_t).where(alerts_t.c.id == alert_id)).mappings().first()
        if al is None:
            raise ObservabilityNotFound(str(alert_id))
        row = c.execute(alerts_t.update().where(alerts_t.c.id == alert_id).values(
            status="resolved", resolved_at=now(), updated_at=now()).returning(*alerts_t.c)).mappings().one()
        record_event(c, entity_type="alert", entity_id=alert_id, event_type="alert_resolved",
                     from_status=al["status"], to_status="resolved", actor_user_id=actor_user_id)
        return dict(row)


# --- suppressions ------------------------------------------------------------

def create_suppression(principal, *, code, name, alert_rule_id=None, service_id=None,
                       maintenance_window_id=None, reason=None, starts_at=None, ends_at=None,
                       actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise ObservabilityError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(suppress_t.c.id).where(suppress_t.c.code == code)) is not None:
            raise ObservabilityError(f"suppression code {code!r} already exists")
        row = c.execute(suppress_t.insert().values(
            code=code, name=name.strip(), alert_rule_id=alert_rule_id, service_id=service_id,
            maintenance_window_id=maintenance_window_id, reason=reason, starts_at=starts_at,
            ends_at=ends_at, active=True, created_by_user_id=actor_user_id)
            .returning(*suppress_t.c)).mappings().one()
        return dict(row)


def list_suppressions(*, active_only=False):
    with engine.connect() as c:
        stmt = select(suppress_t).order_by(suppress_t.c.id.desc())
        if active_only:
            stmt = stmt.where(suppress_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def _active_suppression(c, *, alert_rule_id, service_id, ts):
    conds = []
    if alert_rule_id is not None:
        conds.append(suppress_t.c.alert_rule_id == alert_rule_id)
    if service_id is not None:
        conds.append(suppress_t.c.service_id == service_id)
    if not conds:
        return None
    stmt = select(suppress_t.c.id).where(
        suppress_t.c.active.is_(True), or_(*conds),
        or_(suppress_t.c.starts_at.is_(None), suppress_t.c.starts_at <= ts),
        or_(suppress_t.c.ends_at.is_(None), suppress_t.c.ends_at >= ts)).limit(1)
    return c.scalar(stmt)


# --- maintenance windows -----------------------------------------------------

def list_maintenance_windows(*, status=None):
    with engine.connect() as c:
        stmt = select(windows_t).order_by(windows_t.c.id.desc())
        if status:
            stmt = stmt.where(windows_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_maintenance_window(principal, *, code, title, service_id=None, starts_at=None, ends_at=None,
                              suppress_alerts=True, description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise ObservabilityError("code and title are required")
    with engine.begin() as c:
        if c.scalar(select(windows_t.c.id).where(windows_t.c.code == code)) is not None:
            raise ObservabilityError(f"maintenance window code {code!r} already exists")
        row = c.execute(windows_t.insert().values(
            code=code, title=title.strip(), status="scheduled", service_id=service_id, starts_at=starts_at,
            ends_at=ends_at, suppress_alerts=bool(suppress_alerts), description=description,
            created_by_user_id=actor_user_id).returning(*windows_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="maintenance_window", entity_id=row["id"],
                     event_type="maintenance_scheduled", to_status="scheduled", actor_user_id=actor_user_id)
        return row


def set_maintenance_status(principal, window_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in MAINTENANCE_STATUSES:
        raise ObservabilityError(f"invalid status {status!r}")
    with engine.begin() as c:
        win = c.execute(select(windows_t).where(windows_t.c.id == window_id)).mappings().first()
        if win is None:
            raise ObservabilityNotFound(str(window_id))
        row = c.execute(windows_t.update().where(windows_t.c.id == window_id).values(
            status=status, updated_at=now()).returning(*windows_t.c)).mappings().one()
        # If a window activates and suppresses alerts, spin up an active suppression referencing it.
        if status == "active" and win["suppress_alerts"]:
            existing = c.scalar(select(suppress_t.c.id).where(
                suppress_t.c.maintenance_window_id == window_id, suppress_t.c.active.is_(True)))
            if existing is None:
                c.execute(suppress_t.insert().values(
                    code=f"mw-{window_id}-{int(now().timestamp())}", name=f"Maintenance {win['code']}",
                    service_id=win["service_id"], maintenance_window_id=window_id,
                    reason="maintenance window", starts_at=win["starts_at"], ends_at=win["ends_at"],
                    active=True, created_by_user_id=actor_user_id))
        elif status in ("completed", "cancelled"):
            c.execute(suppress_t.update().where(suppress_t.c.maintenance_window_id == window_id)
                      .values(active=False, updated_at=now()))
        record_event(c, entity_type="maintenance_window", entity_id=window_id,
                     event_type=f"maintenance_{status}", from_status=win["status"], to_status=status,
                     actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"observability.maintenance_{status}", entity_type="maintenance_window",
                entity_id=window_id, actor_user_id=actor_user_id)
    return row


def metrics(principal) -> dict:
    with engine.connect() as c:
        open_alerts = c.scalar(select(func.count()).select_from(alerts_t)
                               .where(alerts_t.c.status == "open")) or 0
        active_windows = c.scalar(select(func.count()).select_from(windows_t)
                                  .where(windows_t.c.status == "active")) or 0
    return {"open_alerts": open_alerts, "active_maintenance_windows": active_windows}
