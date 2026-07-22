"""Reliability incidents & findings (Phase D.26) — metadata only.

Reliability incidents model the platform-reliability lifecycle (open→investigating→mitigated→
resolved→closed) and may carry an optional client anchor for a guarded timeline event (record scope
enforced). Reliability findings reference — never own — a Security finding, an Integration connector,
an alert, an incident, or a service (Security and Integration stay authoritative). Managing requires
``observability.manage``; resolving/lifecycle transitions require ``observability.execute``.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.observability_tables import (
    FINDING_SEVERITIES,
    FINDING_SOURCES,
    FINDING_STATUSES,
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
)
from app.db import engine
from app.db import observability_reliability_findings as findings_t
from app.db import observability_reliability_incidents as incidents_t

from .common import (
    ObservabilityError,
    ObservabilityNotFound,
    now,
    publish_timeline,
    record_event,
    require_anchor_write,
    scope_clause,
    visible,
    write_audit,
)

# --- reliability incidents ---------------------------------------------------

def list_incidents(principal, *, status=None, severity=None):
    with engine.connect() as c:
        stmt = select(incidents_t).order_by(incidents_t.c.id.desc())
        if status:
            stmt = stmt.where(incidents_t.c.status == status)
        if severity:
            stmt = stmt.where(incidents_t.c.severity == severity)
        clause = scope_clause(incidents_t, principal, c)
        if clause is not None:
            stmt = stmt.where(clause)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_incident(principal, incident_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(incidents_t).where(incidents_t.c.id == incident_id)).mappings().first()
    if row is None:
        return None
    row = dict(row)
    if not visible(principal, row):
        raise ObservabilityNotFound(str(incident_id))
    return row


def open_incident(principal, *, code, title, severity="medium", category=None, summary=None,
                  service_id=None, owner_user_id=None, person_id=None, household_id=None,
                  actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise ObservabilityError("code and title are required")
    if severity not in INCIDENT_SEVERITIES:
        raise ObservabilityError(f"invalid severity {severity!r}")
    require_anchor_write(principal, person_id=person_id, household_id=household_id)
    ts = now()
    with engine.begin() as c:
        if c.scalar(select(incidents_t.c.id).where(incidents_t.c.code == code)) is not None:
            raise ObservabilityError(f"incident code {code!r} already exists")
        row = c.execute(incidents_t.insert().values(
            code=code, title=title.strip(), severity=severity, status="open", category=category,
            summary=summary, service_id=service_id, owner_user_id=owner_user_id, detected_at=ts,
            person_id=person_id, household_id=household_id, created_by_user_id=actor_user_id)
            .returning(*incidents_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="incident", entity_id=row["id"], event_type="incident_opened",
                     to_status="open", actor_user_id=actor_user_id, payload={"severity": severity})
    write_audit("observability.incident_opened", entity_type="incident", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"severity": severity})
    publish_timeline(row, "incident_opened", title=f"Reliability incident opened: {row['title']}")
    return row


def set_incident_status(principal, incident_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in INCIDENT_STATUSES:
        raise ObservabilityError(f"invalid status {status!r}")
    with engine.begin() as c:
        inc = c.execute(select(incidents_t).where(incidents_t.c.id == incident_id)).mappings().first()
        if inc is None:
            raise ObservabilityNotFound(str(incident_id))
        inc = dict(inc)
        if not visible(principal, inc):
            raise ObservabilityNotFound(str(incident_id))
        values = {"status": status, "updated_at": now()}
        if status == "mitigated":
            values["mitigated_at"] = now()
        if status in ("resolved", "closed"):
            values["resolved_at"] = now()
        row = c.execute(incidents_t.update().where(incidents_t.c.id == incident_id).values(**values)
                        .returning(*incidents_t.c)).mappings().one()
        record_event(c, entity_type="incident", entity_id=incident_id, event_type=f"incident_{status}",
                     from_status=inc["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"observability.incident_{status}", entity_type="incident", entity_id=incident_id,
                actor_user_id=actor_user_id)
    if status in ("resolved", "closed"):
        publish_timeline(row, "incident_resolved", title=f"Reliability incident {status}: {row['title']}")
    return row


# --- reliability findings (reference Security/Integration; never authoritative) ---

def list_findings(*, status=None, source=None, incident_id=None):
    with engine.connect() as c:
        stmt = select(findings_t).order_by(findings_t.c.id.desc())
        if status:
            stmt = stmt.where(findings_t.c.status == status)
        if source:
            stmt = stmt.where(findings_t.c.source == source)
        if incident_id is not None:
            stmt = stmt.where(findings_t.c.incident_id == incident_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_finding(principal, *, title, finding_type="manual", severity="medium", source="manual",
                   detail=None, incident_id=None, service_id=None, alert_id=None,
                   security_finding_id=None, integration_connector_id=None, actor_user_id=None) -> dict:
    if not (title or "").strip():
        raise ObservabilityError("title is required")
    if severity not in FINDING_SEVERITIES:
        raise ObservabilityError(f"invalid severity {severity!r}")
    if source not in FINDING_SOURCES:
        raise ObservabilityError(f"invalid source {source!r}")
    with engine.begin() as c:
        row = c.execute(findings_t.insert().values(
            title=title.strip(), finding_type=finding_type, severity=severity, status="open",
            source=source, detail=detail, incident_id=incident_id, service_id=service_id, alert_id=alert_id,
            security_finding_id=security_finding_id, integration_connector_id=integration_connector_id,
            created_by_user_id=actor_user_id).returning(*findings_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="finding", entity_id=row["id"], event_type="finding_opened",
                     to_status="open", actor_user_id=actor_user_id, payload={"source": source})
        return row


def set_finding_status(principal, finding_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in FINDING_STATUSES:
        raise ObservabilityError(f"invalid status {status!r}")
    with engine.begin() as c:
        fnd = c.execute(select(findings_t).where(findings_t.c.id == finding_id)).mappings().first()
        if fnd is None:
            raise ObservabilityNotFound(str(finding_id))
        values = {"status": status, "updated_at": now()}
        if status in ("remediated", "accepted", "false_positive"):
            values["resolved_by_user_id"] = actor_user_id
            values["resolved_at"] = now()
        row = c.execute(findings_t.update().where(findings_t.c.id == finding_id).values(**values)
                        .returning(*findings_t.c)).mappings().one()
        record_event(c, entity_type="finding", entity_id=finding_id, event_type=f"finding_{status}",
                     from_status=fnd["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


def metrics(principal) -> dict:
    with engine.connect() as c:
        open_incidents = c.scalar(select(func.count()).select_from(incidents_t)
                                  .where(incidents_t.c.status.notin_(("resolved", "closed")))) or 0
        open_findings = c.scalar(select(func.count()).select_from(findings_t)
                                 .where(findings_t.c.status == "open")) or 0
    return {"reliability_incidents": open_incidents, "reliability_findings": open_findings}
