"""Benefit compliance & renewal obligation service (Release 0.9.11, Phase 5 — ADR-18 §17A).

Canonical read/write path for instantiated ``benefit_obligations`` and their reference
``benefit_obligation_templates``. Obligations carry the **actual** dates that drive the
date-driven detectors; templates carry only defaults (no dates). Renewal-calendar milestones
are obligations tied to a renewal engagement — no second task/reminder system.

Authorization: ``benefits.write`` to mutate / ``benefits.read`` to read; Organization record
scope on every operation. Every mutation writes an audit event. Titles/notes are internal and
must stay free of sensitive employee/health/compensation data.
"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import (benefit_obligation_templates, benefit_obligations, engine, service_lines)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope

_STATUS = frozenset({"scheduled", "in_progress", "completed", "cancelled", "waived"})
_RECURRENCE = frozenset({"one_time", "annual", "none"})
_SOURCE = frozenset({"manual", "template", "renewal", "system"})
_ACTIVE = ("scheduled", "in_progress")


class ObligationError(RuntimeError):
    """Bad input for an obligation operation."""


class ObligationNotFound(ObligationError):
    """Obligation (or template) does not exist."""


def _rid(request_id):
    return request_id or f"benefits-{uuid.uuid4()}"


def _require(principal, capability):
    if not principal.can(capability):
        raise PermissionError(f"Missing capability: {capability}")


def _require_scope(principal, organization_id, *, write, connection):
    if not organization_in_scope(principal, organization_id, write=write, connection=connection):
        raise PermissionError("Obligation is outside your record scope")


def _service_line_id(c, code):
    if code is None:
        return None
    sid = c.scalar(select(service_lines.c.id).where(service_lines.c.code == code))
    if sid is None:
        raise ObligationError(f"Unknown service line: {code}")
    return sid


def _next_year(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + 1, day=28)


# --- templates ---------------------------------------------------------------

def list_templates(*, service_line=None):
    with engine.connect() as c:
        q = select(benefit_obligation_templates).where(benefit_obligation_templates.c.active.is_(True))
        if service_line:
            q = q.where(benefit_obligation_templates.c.service_line == service_line)
        return [dict(r) for r in c.execute(q.order_by(benefit_obligation_templates.c.code)).mappings()]


# --- obligations -------------------------------------------------------------

def create_obligation(principal, *, organization_id, obligation_type, due_date, title=None,
                      description=None, service_line_code=None, engagement_id=None, plan_id=None,
                      plan_year_id=None, warning_days=None, recurrence="one_time", responsible_role=None,
                      template_id=None, source="manual", notes=None, materialization_key=None,
                      request_id=None):
    _require(principal, "benefits.write")
    if not isinstance(due_date, date):
        raise ObligationError("due_date must be a date (explicitly entered or from verified data)")
    if recurrence not in _RECURRENCE:
        raise ObligationError(f"Unsupported recurrence: {recurrence}")
    if source not in _SOURCE:
        raise ObligationError(f"Unsupported source: {source}")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        sid = _service_line_id(c, service_line_code)
        try:
            ob_id = c.execute(benefit_obligations.insert().values(
                organization_id=organization_id, service_line_id=sid, engagement_id=engagement_id,
                plan_id=plan_id, plan_year_id=plan_year_id, template_id=template_id,
                obligation_type=obligation_type, title=(title or obligation_type.replace("_", " ").title()),
                description=description, due_date=due_date, warning_days=warning_days,
                recurrence=recurrence, responsible_role=responsible_role, status="scheduled",
                source=source, notes=notes, materialization_key=materialization_key,
                created_by_user_id=principal.user_id).returning(benefit_obligations.c.id)).scalar_one()
        except IntegrityError:
            raise ObligationError("An obligation with this materialization key already exists")
    write_audit_event(action="benefit.obligation.created", entity_type="benefit_obligation", entity_id=ob_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"organization_id": organization_id, "obligation_type": obligation_type,
                                "due_date": str(due_date), "recurrence": recurrence})
    return get_obligation(ob_id, principal=principal)


def instantiate_from_template(principal, *, template_code, organization_id, due_date,
                              engagement_id=None, plan_id=None, plan_year_id=None,
                              warning_days=None, request_id=None):
    with engine.connect() as c:
        tmpl = c.execute(select(benefit_obligation_templates)
                         .where(benefit_obligation_templates.c.code == template_code)).mappings().one_or_none()
    if tmpl is None:
        raise ObligationNotFound(f"Unknown obligation template: {template_code}")
    return create_obligation(
        principal, organization_id=organization_id, obligation_type=tmpl["obligation_type"],
        due_date=due_date, title=tmpl["name"], service_line_code=tmpl["service_line"],
        engagement_id=engagement_id, plan_id=plan_id, plan_year_id=plan_year_id,
        warning_days=warning_days if warning_days is not None else tmpl["default_warning_days"],
        recurrence=tmpl["recurrence"], responsible_role=tmpl["default_responsible_role"],
        template_id=tmpl["id"], source="template", request_id=request_id)


def create_renewal_calendar(principal, *, organization_id, engagement_id, milestones, plan_id=None,
                            request_id=None):
    """Create the renewal-timeline milestones as obligations tied to a renewal engagement.
    ``milestones`` maps milestone obligation_type -> due date (verified/entered)."""
    created = []
    for obligation_type, due in milestones.items():
        created.append(create_obligation(
            principal, organization_id=organization_id, obligation_type=obligation_type, due_date=due,
            service_line_code="benefits", engagement_id=engagement_id, plan_id=plan_id,
            recurrence="one_time", source="renewal", request_id=request_id)["id"])
    return created


def get_obligation(obligation_id, *, principal, connection=None):
    _require(principal, "benefits.read")

    def _load(c):
        row = c.execute(select(benefit_obligations).where(benefit_obligations.c.id == obligation_id)).mappings().one_or_none()
        if row is None:
            raise ObligationNotFound(f"Obligation {obligation_id} not found")
        _require_scope(principal, row["organization_id"], write=False, connection=c)
        return dict(row)

    if connection is not None:
        return _load(connection)
    with engine.connect() as c:
        return _load(c)


def update_obligation(obligation_id, *, principal, request_id=None, **fields):
    _require(principal, "benefits.write")
    allowed = {"title", "description", "due_date", "warning_days", "responsible_role",
               "status", "notes", "recurrence", "plan_id", "plan_year_id", "engagement_id"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if values.get("status") and values["status"] not in _STATUS:
        raise ObligationError(f"Unsupported status: {values['status']}")
    with engine.begin() as c:
        row = c.execute(select(benefit_obligations.c.organization_id)
                        .where(benefit_obligations.c.id == obligation_id)).mappings().one_or_none()
        if row is None:
            raise ObligationNotFound(f"Obligation {obligation_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        if values:
            c.execute(benefit_obligations.update().where(benefit_obligations.c.id == obligation_id).values(**values))
    write_audit_event(action="benefit.obligation.updated", entity_type="benefit_obligation", entity_id=obligation_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"fields": sorted(values)})
    return get_obligation(obligation_id, principal=principal)


def complete_obligation(obligation_id, *, principal, completed_date=None, evidence_document_id=None,
                        request_id=None):
    _require(principal, "benefits.write")
    with engine.begin() as c:
        row = c.execute(select(benefit_obligations).where(benefit_obligations.c.id == obligation_id)).mappings().one_or_none()
        if row is None:
            raise ObligationNotFound(f"Obligation {obligation_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        c.execute(benefit_obligations.update().where(benefit_obligations.c.id == obligation_id).values(
            status="completed", completed_date=completed_date or date.today(),
            evidence_document_id=evidence_document_id))
    write_audit_event(action="benefit.obligation.completed", entity_type="benefit_obligation", entity_id=obligation_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"obligation_type": row["obligation_type"]})
    if row["recurrence"] == "annual":
        _materialize_next(dict(row), actor_user_id=principal.user_id)
    return get_obligation(obligation_id, principal=principal)


def set_status(obligation_id, status, *, principal, request_id=None):
    if status not in ("cancelled", "waived", "in_progress", "scheduled"):
        raise ObligationError(f"Unsupported status transition: {status}")
    return update_obligation(obligation_id, principal=principal, status=status, request_id=request_id)


def list_obligations(principal, organization_id, *, status=None):
    _require(principal, "benefits.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        q = select(benefit_obligations).where(benefit_obligations.c.organization_id == organization_id)
        if status:
            q = q.where(benefit_obligations.c.status == status)
        return [dict(r) for r in c.execute(q.order_by(benefit_obligations.c.due_date)).mappings()]


# --- recurrence materialization (system) -------------------------------------

def _materialization_key(row, next_due):
    return f"{row['obligation_type']}:{row['organization_id']}:{row['plan_id'] or 0}:" \
           f"{row['plan_year_id'] or 0}:{next_due.isoformat()}"


def _materialize_next(row, *, actor_user_id=None):
    """Idempotently create the next annual occurrence of a completed obligation."""
    next_due = _next_year(row["due_date"])
    key = _materialization_key(row, next_due)
    with engine.begin() as c:
        if c.scalar(select(benefit_obligations.c.id).where(benefit_obligations.c.materialization_key == key)):
            return None
        try:
            new_id = c.execute(benefit_obligations.insert().values(
                organization_id=row["organization_id"], service_line_id=row["service_line_id"],
                engagement_id=row["engagement_id"], plan_id=row["plan_id"], plan_year_id=row["plan_year_id"],
                template_id=row["template_id"], obligation_type=row["obligation_type"], title=row["title"],
                description=row["description"], due_date=next_due, warning_days=row["warning_days"],
                recurrence="annual", responsible_role=row["responsible_role"], status="scheduled",
                source="system", materialization_key=key, created_by_user_id=actor_user_id
            ).returning(benefit_obligations.c.id)).scalar_one()
        except IntegrityError:
            return None
    write_audit_event(action="benefit.obligation.materialized", entity_type="benefit_obligation",
                      entity_id=new_id, actor_user_id=actor_user_id, request_id=f"benefits-{uuid.uuid4()}",
                      metadata={"obligation_type": row["obligation_type"], "due_date": str(next_due)})
    return new_id


def materialize_recurring(*, actor_user_id=None, today=None):
    """Safety-net job: ensure every completed annual obligation has its next occurrence.
    Idempotent (existence + unique key). Per-obligation failure isolated. Honest counts."""
    with engine.connect() as c:
        completed = [dict(r) for r in c.execute(select(benefit_obligations)
                     .where(benefit_obligations.c.recurrence == "annual",
                            benefit_obligations.c.status == "completed")).mappings()]
    materialized, failures = 0, 0
    for row in completed:
        try:
            if _materialize_next(row, actor_user_id=actor_user_id):
                materialized += 1
        except Exception:  # pragma: no cover - defensive isolation
            failures += 1
    return {"considered": len(completed), "materialized": materialized, "failures": failures}
