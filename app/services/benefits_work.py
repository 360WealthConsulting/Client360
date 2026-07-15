"""Benefits Work Management glue (Release 0.9.11, Phase 4 — ADR-18).

Thin helpers that connect benefits exceptions to the **existing** Work Management
assignment-rule engine. There is no benefits-specific assignment model — assignment reuses
``record_assignments`` via ``apply_assignment_rules`` / ``assign_work``.

**Assignment precedence** (unchanged from the platform engine): active
``assignment_rules`` for ``entity_type='exception'`` are evaluated in ascending
``(priority, id)`` order; a rule fires when **all** of its ``conditions`` match the benefits
exception's attributes (``domain``/``category``/``severity``/``code``/``organization_id``);
the **first matching primary** rule wins and stops evaluation, while non-primary rules
(secondary/supervisor/owner) may stack. A least-specific "default benefits operations" rule
(e.g. ``conditions={"domain":"benefits"}``) at the highest priority number is the fallback.

Permanent Organization relationship roles (Benefits Consultant, Producer, Advisor, Renewal
Owner) are **never** treated as the work assignee here — only an explicit assignment rule can
assign work. Queue membership never grants record scope (scope is enforced in ``work_items``).
"""
from sqlalchemy import select

from app.db import engine, exceptions, exception_types, record_assignments
from app.services.work_management import _active, apply_assignment_rules


def benefits_exception_attributes(connection, exception_id):
    """Non-sensitive attributes used to match assignment rules for a benefits exception."""
    row = connection.execute(
        select(exceptions.c.id, exceptions.c.domain, exceptions.c.category, exceptions.c.severity,
               exceptions.c.related_entity_type, exceptions.c.related_entity_id, exception_types.c.code)
        .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
        .where(exceptions.c.id == exception_id)).mappings().one_or_none()
    if row is None or row["domain"] != "benefits":
        return None
    org_id = row["related_entity_id"] if row["related_entity_type"] == "organization" else None
    return {"domain": "benefits", "category": row["category"], "severity": row["severity"],
            "code": row["code"], "organization_id": org_id}


def apply_benefits_exception_rules(exception_id, *, actor_user_id=None, request_id=None):
    """Apply the existing assignment-rule engine to one benefits exception. Returns the list
    of assignment ids created (empty if no rule matched)."""
    with engine.connect() as c:
        attributes = benefits_exception_attributes(c, exception_id)
    if attributes is None:
        return []
    return apply_assignment_rules("exception", exception_id, attributes, actor_user_id, request_id=request_id)


def _has_active_assignment(connection, exception_id):
    return connection.scalar(
        select(record_assignments.c.id).where(
            record_assignments.c.entity_type == "exception",
            record_assignments.c.entity_id == exception_id, _active(record_assignments)).limit(1)) is not None


def auto_assign_unassigned(*, actor_user_id=None, request_id=None):
    """Apply assignment rules to every open benefits exception that has no active assignment.
    Idempotent — an exception that gains an assignment is skipped on the next run. Returns
    honest counts. (No rules configured → nothing assigned; items remain in the Unassigned queue.)"""
    with engine.connect() as c:
        open_ids = list(c.scalars(select(exceptions.c.id).where(
            exceptions.c.domain == "benefits", exceptions.c.status.notin_(("resolved", "cancelled")))))
        unassigned = [eid for eid in open_ids if not _has_active_assignment(c, eid)]
    assigned = 0
    for eid in unassigned:
        try:
            if apply_benefits_exception_rules(eid, actor_user_id=actor_user_id, request_id=request_id):
                assigned += 1
        except Exception:  # pragma: no cover - one bad rule must not abort the rest
            pass
    return {"considered": len(unassigned), "assigned": assigned}
