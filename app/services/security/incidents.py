"""Security incidents, findings & exceptions (Phase D.25) — metadata only.

Incidents model the security-incident lifecycle (open→investigating→contained→resolved→closed) and
may carry an optional client anchor for a guarded timeline event. Findings reference — never own —
Data Governance findings (Governance stays authoritative), policies, incidents, secrets, or
certificates. Exceptions record approved deviations from a policy. Record scope is enforced for
client-anchored incidents; approving/resolving requires ``security.execute`` (enforced in-route).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.security_tables import (
    EXCEPTION_STATUSES,
    FINDING_SEVERITIES,
    FINDING_SOURCES,
    FINDING_STATUSES,
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
)
from app.db import engine
from app.db import security_exceptions as exceptions_t
from app.db import security_findings as findings_t
from app.db import security_incidents as incidents_t

from .common import (
    SecurityError,
    SecurityNotFound,
    now,
    publish_timeline,
    record_event,
    require_anchor_write,
    scope_clause,
    visible,
    write_audit,
)

# --- incidents ---------------------------------------------------------------

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
        raise SecurityNotFound(str(incident_id))
    return row


def open_incident(principal, *, code, title, severity="medium", category=None, summary=None,
                  owner_user_id=None, person_id=None, household_id=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise SecurityError("code and title are required")
    if severity not in INCIDENT_SEVERITIES:
        raise SecurityError(f"invalid severity {severity!r}")
    require_anchor_write(principal, person_id=person_id, household_id=household_id)
    ts = now()
    with engine.begin() as c:
        if c.scalar(select(incidents_t.c.id).where(incidents_t.c.code == code)) is not None:
            raise SecurityError(f"incident code {code!r} already exists")
        row = c.execute(incidents_t.insert().values(
            code=code, title=title.strip(), severity=severity, status="open", category=category,
            summary=summary, owner_user_id=owner_user_id, detected_at=ts, person_id=person_id,
            household_id=household_id, created_by_user_id=actor_user_id)
            .returning(*incidents_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="incident", entity_id=row["id"], event_type="incident_opened",
                     to_status="open", actor_user_id=actor_user_id, payload={"severity": severity})
    write_audit("security.incident_opened", entity_type="incident", entity_id=row["id"],
                actor_user_id=actor_user_id, metadata={"severity": severity})
    publish_timeline(row, "incident_opened", title=f"Security incident opened: {row['title']}")
    return row


def set_incident_status(principal, incident_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in INCIDENT_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        inc = c.execute(select(incidents_t).where(incidents_t.c.id == incident_id)).mappings().first()
        if inc is None:
            raise SecurityNotFound(str(incident_id))
        inc = dict(inc)
        if not visible(principal, inc):
            raise SecurityNotFound(str(incident_id))
        values = {"status": status, "updated_at": now()}
        if status == "contained":
            values["contained_at"] = now()
        if status in ("resolved", "closed"):
            values["resolved_at"] = now()
        row = c.execute(incidents_t.update().where(incidents_t.c.id == incident_id).values(**values)
                        .returning(*incidents_t.c)).mappings().one()
        record_event(c, entity_type="incident", entity_id=incident_id, event_type=f"incident_{status}",
                     from_status=inc["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"security.incident_{status}", entity_type="incident", entity_id=incident_id,
                actor_user_id=actor_user_id)
    if status in ("resolved", "closed"):
        publish_timeline(row, "incident_resolved", title=f"Security incident {status}: {row['title']}")
    return row


# --- findings (reference Governance findings; never authoritative over them) ---

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
                   detail=None, governance_finding_id=None, policy_id=None, incident_id=None,
                   secret_reference_id=None, certificate_reference_id=None, actor_user_id=None) -> dict:
    if not (title or "").strip():
        raise SecurityError("title is required")
    if severity not in FINDING_SEVERITIES:
        raise SecurityError(f"invalid severity {severity!r}")
    if source not in FINDING_SOURCES:
        raise SecurityError(f"invalid source {source!r}")
    with engine.begin() as c:
        row = c.execute(findings_t.insert().values(
            title=title.strip(), finding_type=finding_type, severity=severity, status="open",
            source=source, detail=detail, governance_finding_id=governance_finding_id, policy_id=policy_id,
            incident_id=incident_id, secret_reference_id=secret_reference_id,
            certificate_reference_id=certificate_reference_id, created_by_user_id=actor_user_id)
            .returning(*findings_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="finding", entity_id=row["id"], event_type="finding_opened",
                     to_status="open", actor_user_id=actor_user_id, payload={"source": source})
        return row


def set_finding_status(principal, finding_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in FINDING_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        fnd = c.execute(select(findings_t).where(findings_t.c.id == finding_id)).mappings().first()
        if fnd is None:
            raise SecurityNotFound(str(finding_id))
        values = {"status": status, "updated_at": now()}
        if status in ("remediated", "accepted", "false_positive"):
            values["resolved_by_user_id"] = actor_user_id
            values["resolved_at"] = now()
        row = c.execute(findings_t.update().where(findings_t.c.id == finding_id).values(**values)
                        .returning(*findings_t.c)).mappings().one()
        record_event(c, entity_type="finding", entity_id=finding_id, event_type=f"finding_{status}",
                     from_status=fnd["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


# --- exceptions --------------------------------------------------------------

def list_exceptions(*, status=None):
    with engine.connect() as c:
        stmt = select(exceptions_t).order_by(exceptions_t.c.id.desc())
        if status:
            stmt = stmt.where(exceptions_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def request_exception(principal, *, code, title, policy_id=None, justification=None, scope=None,
                      expires_at=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise SecurityError("code and title are required")
    with engine.begin() as c:
        if c.scalar(select(exceptions_t.c.id).where(exceptions_t.c.code == code)) is not None:
            raise SecurityError(f"exception code {code!r} already exists")
        row = c.execute(exceptions_t.insert().values(
            code=code, title=title.strip(), policy_id=policy_id, justification=justification,
            scope=scope, status="requested", requested_by_user_id=actor_user_id, expires_at=expires_at,
            created_by_user_id=actor_user_id).returning(*exceptions_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="exception", entity_id=row["id"], event_type="exception_requested",
                     to_status="requested", actor_user_id=actor_user_id)
        return row


def decide_exception(principal, exception_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in EXCEPTION_STATUSES:
        raise SecurityError(f"invalid status {status!r}")
    with engine.begin() as c:
        exc = c.execute(select(exceptions_t).where(exceptions_t.c.id == exception_id)).mappings().first()
        if exc is None:
            raise SecurityNotFound(str(exception_id))
        values = {"status": status, "updated_at": now()}
        if status == "approved":
            values["approved_by_user_id"] = actor_user_id
            values["approved_at"] = now()
        row = c.execute(exceptions_t.update().where(exceptions_t.c.id == exception_id).values(**values)
                        .returning(*exceptions_t.c)).mappings().one()
        record_event(c, entity_type="exception", entity_id=exception_id, event_type=f"exception_{status}",
                     from_status=exc["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    write_audit(f"security.exception_{status}", entity_type="exception", entity_id=exception_id,
                actor_user_id=actor_user_id)
    return row


def metrics(principal) -> dict:
    with engine.connect() as c:
        open_incidents = c.scalar(select(func.count()).select_from(incidents_t)
                                  .where(incidents_t.c.status.notin_(("resolved", "closed")))) or 0
        open_findings = c.scalar(select(func.count()).select_from(findings_t)
                                 .where(findings_t.c.status == "open")) or 0
        pending_exceptions = c.scalar(select(func.count()).select_from(exceptions_t)
                                      .where(exceptions_t.c.status == "requested")) or 0
    return {"open_incidents": open_incidents, "open_findings": open_findings,
            "pending_exceptions": pending_exceptions}
