"""Universal Engagement canonical service (Release 0.9.11, Phase 2 — ADR-18).

An Engagement is a unit of delivered work anchored to an Organization (and/or person/
household) within a service line, with a type, status, and due date. It reuses Audit and
(later phases) Work Management / Documents / Timeline / Exceptions / Portal. ``tax_engagements``
is untouched; ``engagements.legacy_tax_engagement_id`` is the documented convergence bridge.

Authorization: capability ``organization.write`` to create/mutate, ``organization.read`` to
read; record scope resolves against the engagement's anchor (organization/person/household).
"""
import uuid
from datetime import date

from sqlalchemy import select

from app.db import engagements, engine, service_lines
from app.security.audit import write_audit_event
from app.security.authorization import benefits_in_scope

_TERMINAL = frozenset({"closed", "cancelled"})


class EngagementError(RuntimeError):
    """Bad input for an engagement operation."""


class EngagementNotFound(EngagementError):
    """Engagement does not exist."""


def _rid(request_id):
    return request_id or f"benefits-{uuid.uuid4()}"


def _require(principal, capability):
    if not principal.can(capability):
        raise PermissionError(f"Missing capability: {capability}")


def _service_line_id(c, code):
    sid = c.scalar(select(service_lines.c.id).where(service_lines.c.code == code))
    if sid is None:
        raise EngagementError(f"Unknown service line: {code}")
    return sid


def _scope(principal, row, *, write, connection):
    if not benefits_in_scope(principal, organization_id=row["organization_id"],
                             person_id=row["person_id"], household_id=row["household_id"],
                             write=write, connection=connection):
        raise PermissionError("Engagement is outside your record scope")


def create_engagement(principal, *, service_line_code, engagement_type, organization_id=None,
                      person_id=None, household_id=None, title=None, due_date=None,
                      opened_on=None, metadata=None, request_id=None):
    _require(principal, "organization.write")
    if organization_id is None and person_id is None and household_id is None:
        raise EngagementError("An engagement needs an organization, person, or household anchor")
    if not (engagement_type or "").strip():
        raise EngagementError("engagement_type is required")
    with engine.begin() as c:
        sid = _service_line_id(c, service_line_code)
        if not benefits_in_scope(principal, organization_id=organization_id, person_id=person_id,
                                 household_id=household_id, write=True, connection=c):
            raise PermissionError("Engagement anchor is outside your record scope")
        eng_id = c.execute(engagements.insert().values(
            organization_id=organization_id, person_id=person_id, household_id=household_id,
            service_line_id=sid, engagement_type=engagement_type.strip(), title=title,
            status="open", due_date=due_date, opened_on=opened_on or date.today(),
            metadata=metadata or {}, created_by_user_id=principal.user_id
        ).returning(engagements.c.id)).scalar_one()
    write_audit_event(action="engagement.created", entity_type="engagement", entity_id=eng_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"service_line": service_line_code, "engagement_type": engagement_type,
                                "organization_id": organization_id})
    return get_engagement(eng_id, principal=principal)


def get_engagement(engagement_id, *, principal, connection=None):
    _require(principal, "organization.read")

    def _load(c):
        row = c.execute(select(engagements).where(engagements.c.id == engagement_id)).mappings().one_or_none()
        if row is None:
            raise EngagementNotFound(f"Engagement {engagement_id} not found")
        _scope(principal, row, write=False, connection=c)
        return dict(row)

    if connection is not None:
        return _load(connection)
    with engine.connect() as c:
        return _load(c)


def update_engagement_status(engagement_id, status, *, principal, request_id=None):
    _require(principal, "organization.write")
    if status not in {"open", "in_progress", "waiting", "closed", "cancelled"}:
        raise EngagementError(f"Unsupported engagement status: {status}")
    with engine.begin() as c:
        row = c.execute(select(engagements).where(engagements.c.id == engagement_id)).mappings().one_or_none()
        if row is None:
            raise EngagementNotFound(f"Engagement {engagement_id} not found")
        _scope(principal, row, write=True, connection=c)
        values = {"status": status}
        if status in _TERMINAL and row["closed_on"] is None:
            values["closed_on"] = date.today()
        c.execute(engagements.update().where(engagements.c.id == engagement_id).values(**values))
    write_audit_event(action="engagement.status_changed", entity_type="engagement", entity_id=engagement_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"from": row["status"], "to": status})
    return get_engagement(engagement_id, principal=principal)


def list_engagements(principal, *, organization_id=None, service_line_code=None, status=None):
    _require(principal, "organization.read")
    with engine.connect() as c:
        query = select(engagements)
        if organization_id is not None:
            query = query.where(engagements.c.organization_id == organization_id)
        if service_line_code is not None:
            query = query.where(engagements.c.service_line_id == _service_line_id(c, service_line_code))
        if status is not None:
            query = query.where(engagements.c.status == status)
        rows = [dict(r) for r in c.execute(query.order_by(engagements.c.id.desc())).mappings()]
        return [r for r in rows if benefits_in_scope(
            principal, organization_id=r["organization_id"], person_id=r["person_id"],
            household_id=r["household_id"], write=False, connection=c)]
