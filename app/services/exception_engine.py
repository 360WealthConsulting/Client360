"""Core Exception Engine service (Release 0.9.10 / Sprint 5.5, Phase 2).

Canonical service for the platform-wide Exception Engine (ADR-17). Sprint 5.5
supports **only** ``domain='tax'`` — other domains are rejected cleanly.

Every mutation: validates the approved state machine against the *current* row
(SELECT ... FOR UPDATE, so stale/duplicate actions are rejected), appends an
immutable ``exception_events`` row in the same transaction, and — after commit —
publishes an audit event and (when a client is in scope) a timeline event.

No detectors, scheduler jobs, routes, UI, portal, or dashboards live here; those
are later phases.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.db import engine, exception_events, exception_types, exceptions
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope
from app.services.timeline import add_timeline_event
from app.services.work_management import authorized_assignments

# Sprint 5.5 implements only the tax domain (ADR-17 guardrail).
SUPPORTED_DOMAINS = frozenset({"tax"})

ACTIVE_STATUSES = frozenset({"open", "acknowledged", "in_progress", "waiting", "escalated", "reopened"})
CLOSED_STATUSES = frozenset({"resolved", "cancelled"})

# Approved state machine (§4). `escalate` is handled separately (it may bump the
# level from any active status), so it is not listed as a generic edge here.
TRANSITIONS = {
    "open": {"acknowledged", "in_progress", "cancelled"},
    "acknowledged": {"in_progress", "cancelled"},
    "in_progress": {"waiting", "resolved", "cancelled"},
    "waiting": {"in_progress", "resolved", "cancelled"},
    "escalated": {"in_progress", "resolved", "cancelled"},
    "reopened": {"in_progress", "cancelled"},
    "resolved": {"reopened"},
    "cancelled": set(),
}


class ExceptionEngineError(RuntimeError):
    """Base class for exception-engine domain errors."""


class UnsupportedDomainError(ExceptionEngineError):
    """Raised when an operation targets a domain not implemented this sprint."""


class ExceptionNotFoundError(ExceptionEngineError):
    """Raised when an exception or type does not exist."""


class InvalidTransitionError(ExceptionEngineError):
    """Raised when a status change is not allowed from the current status."""


class StaleActionError(ExceptionEngineError):
    """Raised when the caller's expected status no longer matches the row."""


class ExceptionAuthorizationError(PermissionError):
    """Raised when the principal lacks capability or record scope."""


def _now():
    return datetime.now(timezone.utc)


def _require(principal, capability):
    if principal is not None and not principal.can(capability):
        raise ExceptionAuthorizationError(f"Missing capability: {capability}")


def _authorize(connection, principal, row, *, write):
    """Domain-aware record-scope check. System callers (principal=None) bypass."""
    if principal is None:
        return
    if principal.can("record.write_all") or principal.can("record.read_all"):
        return
    if row["domain"] == "tax":  # record-scoped domain
        for entity_type, entity_id in (
            ("tax_return", row["tax_engagement_return_id"]),
            ("person", row["person_id"]),
            ("household", row["household_id"]),
        ):
            if entity_id and record_in_scope(principal, entity_type, entity_id, write=write, connection=connection):
                return
    raise ExceptionAuthorizationError("Exception is outside your record scope")


def _append_event(connection, exception_id, event_type, from_status, to_status,
                  actor_user_id, portal_account_id, metadata):
    connection.execute(exception_events.insert().values(
        exception_id=exception_id, event_type=event_type,
        from_status=from_status, to_status=to_status,
        actor_user_id=actor_user_id, portal_account_id=portal_account_id,
        metadata=metadata or {},
    ))


def _publish(row, *, action, event_type, actor_user_id, portal_account_id, request_id, metadata):
    """After-commit audit (always) + timeline (when a client is in scope)."""
    write_audit_event(
        action=f"exception.{action}", entity_type="exception", entity_id=row["id"],
        actor_user_id=actor_user_id, request_id=request_id or f"exception-{row['id']}-{action}",
        metadata={"domain": row["domain"], "code_id": row["exception_type_id"],
                  "portal_account_id": portal_account_id, **(metadata or {})},
    )
    if row["person_id"] or row["household_id"]:
        add_timeline_event(
            source="exception", event_type=f"exception_{event_type}",
            title=f"Exception {event_type.replace('_', ' ')}: {row['title']}",
            person_id=row["person_id"], household_id=row["household_id"],
            external_id=f"exception-{row['id']}-{event_type}-{_now().timestamp()}",
            event_metadata={"exception_id": row["id"], "severity": row["severity"], **(metadata or {})},
        )


def _load(connection, exception_id, *, lock=False):
    stmt = select(exceptions).where(exceptions.c.id == exception_id)
    if lock:
        stmt = stmt.with_for_update()
    row = connection.execute(stmt).mappings().one_or_none()
    if row is None:
        raise ExceptionNotFoundError(f"Exception {exception_id} not found")
    return row


# --- SLA ---------------------------------------------------------------------

def sla_state(row, now=None):
    """Return the SLA state for an exception row: none/on_track/at_risk/breached/closed."""
    if row["status"] in CLOSED_STATUSES:
        return "closed"
    due = row["sla_due_at"]
    if due is None:
        return "none"
    now = now or _now()
    remaining = (due - now).total_seconds()
    if remaining < 0:
        return "breached"
    if remaining <= 8 * 3600:  # within 8 hours
        return "at_risk"
    return "on_track"


# --- raise (with dedupe / idempotency / reopen) ------------------------------

def raise_exception(*, code, actor_user_id=None, principal=None, source="system",
                    severity=None, title=None, description=None, dedupe_key=None,
                    tax_engagement_return_id=None, tax_engagement_id=None,
                    person_id=None, household_id=None, workflow_instance_id=None,
                    workflow_step_id=None, document_id=None, related_entity_type=None,
                    related_entity_id=None, owner_user_id=None, owner_team_id=None,
                    request_id=None, metadata=None):
    """Open (or idempotently return / reopen) an exception for the given type code.

    - existing OPEN exception with the same ``dedupe_key`` → returned unchanged
      (idempotent replay);
    - existing RESOLVED exception with the same ``dedupe_key`` → reopened;
    - otherwise a new ``open`` exception is created.
    The DB partial-unique dedupe index is the backstop for concurrent raises.
    """
    now = _now()
    outcome = None  # (kind, exception_id[, row])
    try:
        with engine.begin() as c:
            etype = c.execute(select(exception_types).where(exception_types.c.code == code)).mappings().one_or_none()
            if etype is None:
                raise ExceptionNotFoundError(f"Unknown exception type: {code}")
            if etype["domain"] not in SUPPORTED_DOMAINS:
                raise UnsupportedDomainError(f"Domain '{etype['domain']}' is not implemented in Sprint 5.5")
            _require(principal, "exception.write")

            if dedupe_key:
                active = c.execute(select(exceptions).where(
                    exceptions.c.dedupe_key == dedupe_key,
                    exceptions.c.status.notin_(tuple(CLOSED_STATUSES)),
                )).mappings().first()
                if active is not None:
                    outcome = ("idempotent", active["id"])
                else:
                    resolved = c.execute(select(exceptions).where(
                        exceptions.c.dedupe_key == dedupe_key, exceptions.c.status == "resolved",
                    ).order_by(exceptions.c.id.desc())).mappings().first()
                    if resolved is not None:
                        c.execute(exceptions.update().where(exceptions.c.id == resolved["id"]).values(
                            status="reopened", resolved_at=None, resolution_code=None,
                            resolution_notes=None, resolved_by_user_id=None, updated_at=now))
                        _append_event(c, resolved["id"], "reopened", "resolved", "reopened",
                                      actor_user_id, None, {"reason": "dedupe_reactivation"})
                        outcome = ("reopened", resolved["id"])

            if outcome is None:
                new_id = c.execute(exceptions.insert().values(
                    exception_type_id=etype["id"], domain=etype["domain"], category=etype["category"],
                    severity=severity or etype["default_severity"], status="open",
                    title=title or etype["name"], description=description, source=source,
                    tax_engagement_return_id=tax_engagement_return_id, tax_engagement_id=tax_engagement_id,
                    person_id=person_id, household_id=household_id,
                    workflow_instance_id=workflow_instance_id, workflow_step_id=workflow_step_id,
                    document_id=document_id, related_entity_type=related_entity_type,
                    related_entity_id=related_entity_id, owner_user_id=owner_user_id,
                    owner_team_id=owner_team_id, opened_at=now,
                    sla_due_at=(now + timedelta(minutes=etype["sla_minutes"])) if etype["sla_minutes"] else None,
                    dedupe_key=dedupe_key, created_by_user_id=actor_user_id,
                ).returning(exceptions.c.id)).scalar_one()
                _append_event(c, new_id, "opened", None, "open", actor_user_id, None, metadata)
                outcome = ("created", new_id)
    except IntegrityError:
        # Concurrent duplicate raise: the partial-unique dedupe index rejected us.
        with engine.connect() as c:
            existing = c.execute(select(exceptions).where(
                exceptions.c.dedupe_key == dedupe_key,
                exceptions.c.status.notin_(tuple(CLOSED_STATUSES)),
            ).order_by(exceptions.c.id)).mappings().first()
        if existing is not None:
            return dict(existing)
        raise

    kind, exception_id = outcome[0], outcome[1]
    if kind == "idempotent":
        return get_exception(exception_id, principal=principal)
    row = _fetch(exception_id)
    if kind == "reopened":
        _publish(row, action="reopened", event_type="reopened", actor_user_id=actor_user_id,
                 portal_account_id=None, request_id=request_id,
                 metadata={"from": "resolved", "to": "reopened", "reason": "dedupe_reactivation"})
    else:
        _publish(row, action="raised", event_type="opened", actor_user_id=actor_user_id,
                 portal_account_id=None, request_id=request_id, metadata=metadata)
    return get_exception(exception_id, principal=principal)


# --- generic transition ------------------------------------------------------

def _transition(exception_id, to_status, *, principal, capability, event_type, action,
                actor_user_id=None, portal_account_id=None, expected_status=None,
                extra_values=None, request_id=None, metadata=None):
    _require(principal, capability)
    now = _now()
    with engine.begin() as c:
        row = _load(c, exception_id, lock=True)
        _authorize(c, principal, row, write=True)
        current = row["status"]
        if expected_status is not None and current != expected_status:
            raise StaleActionError(f"Expected status '{expected_status}', found '{current}'")
        if to_status not in TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(f"Cannot move exception {exception_id} from '{current}' to '{to_status}'")
        values = {"status": to_status, "updated_at": now, **(extra_values or {})}
        if to_status == "acknowledged":
            values["acknowledged_at"] = now
        if to_status == "resolved":
            values["resolved_at"] = now
        c.execute(exceptions.update().where(exceptions.c.id == exception_id).values(**values))
        _append_event(c, exception_id, event_type, current, to_status, actor_user_id, portal_account_id, metadata)
    fresh = _fetch(exception_id)
    _publish(fresh, action=action, event_type=event_type, actor_user_id=actor_user_id,
             portal_account_id=portal_account_id, request_id=request_id,
             metadata={"from": current, "to": to_status, **(metadata or {})})
    return get_exception(exception_id, principal=principal)


def acknowledge(exception_id, *, principal, actor_user_id=None, expected_status=None, request_id=None):
    return _transition(exception_id, "acknowledged", principal=principal, capability="exception.write",
                       event_type="acknowledged", action="acknowledged", actor_user_id=actor_user_id,
                       expected_status=expected_status, request_id=request_id)


def begin_work(exception_id, *, principal, actor_user_id=None, expected_status=None, request_id=None):
    return _transition(exception_id, "in_progress", principal=principal, capability="exception.write",
                       event_type="started", action="started", actor_user_id=actor_user_id,
                       expected_status=expected_status, request_id=request_id)


def place_waiting(exception_id, *, principal, actor_user_id=None, reason=None, expected_status=None, request_id=None):
    return _transition(exception_id, "waiting", principal=principal, capability="exception.write",
                       event_type="waiting", action="waiting", actor_user_id=actor_user_id,
                       expected_status=expected_status, request_id=request_id, metadata={"reason": reason} if reason else None)


def cancel(exception_id, *, principal, actor_user_id=None, reason=None, expected_status=None, request_id=None):
    return _transition(exception_id, "cancelled", principal=principal, capability="exception.write",
                       event_type="cancelled", action="cancelled", actor_user_id=actor_user_id,
                       expected_status=expected_status, request_id=request_id, metadata={"reason": reason} if reason else None)


def reopen(exception_id, *, principal, actor_user_id=None, reason=None, expected_status=None, request_id=None):
    return _transition(exception_id, "reopened", principal=principal, capability="exception.write",
                       event_type="reopened", action="reopened", actor_user_id=actor_user_id,
                       extra_values={"resolved_at": None, "resolution_code": None, "resolution_notes": None,
                                     "resolved_by_user_id": None},
                       expected_status=expected_status, request_id=request_id, metadata={"reason": reason} if reason else None)


def escalate(exception_id, *, principal, actor_user_id=None, to_user_id=None, to_team_id=None,
             reason=None, request_id=None):
    """Escalate: set status=escalated and bump the level. Valid from any active status."""
    _require(principal, "exception.write")
    now = _now()
    with engine.begin() as c:
        row = _load(c, exception_id, lock=True)
        _authorize(c, principal, row, write=True)
        if row["status"] not in ACTIVE_STATUSES:
            raise InvalidTransitionError(f"Cannot escalate exception {exception_id} in status '{row['status']}'")
        level = row["escalation_level"] + 1
        values = {"status": "escalated", "escalation_level": level, "updated_at": now}
        if to_user_id is not None:
            values["owner_user_id"] = to_user_id
        if to_team_id is not None:
            values["owner_team_id"] = to_team_id
        c.execute(exceptions.update().where(exceptions.c.id == exception_id).values(**values))
        _append_event(c, exception_id, "escalated", row["status"], "escalated", actor_user_id, None,
                      {"level": level, "reason": reason, "to_user_id": to_user_id, "to_team_id": to_team_id})
    fresh = _fetch(exception_id)
    _publish(fresh, action="escalated", event_type="escalated", actor_user_id=actor_user_id,
             portal_account_id=None, request_id=request_id, metadata={"level": level, "reason": reason})
    return get_exception(exception_id, principal=principal)


def resolve(exception_id, resolution_code, *, principal, actor_user_id=None, notes=None,
            expected_status=None, request_id=None):
    """Resolve. Capability depends on the row: compliance category → exception.compliance;
    blocker severity → exception.resolve; otherwise exception.write."""
    if not resolution_code:
        raise ExceptionEngineError("A resolution_code is required to resolve an exception")
    now = _now()
    with engine.begin() as c:
        row = _load(c, exception_id, lock=True)
        capability = ("exception.compliance" if row["category"] == "compliance"
                      else "exception.resolve" if row["severity"] == "blocker"
                      else "exception.write")
        _require(principal, capability)
        _authorize(c, principal, row, write=True)
        current = row["status"]
        if expected_status is not None and current != expected_status:
            raise StaleActionError(f"Expected status '{expected_status}', found '{current}'")
        if "resolved" not in TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(f"Cannot resolve exception {exception_id} from '{current}'")
        c.execute(exceptions.update().where(exceptions.c.id == exception_id).values(
            status="resolved", resolved_at=now, resolution_code=resolution_code,
            resolution_notes=notes, resolved_by_user_id=actor_user_id, updated_at=now))
        _append_event(c, exception_id, "resolved", current, "resolved", actor_user_id, None,
                      {"resolution_code": resolution_code})
    fresh = _fetch(exception_id)
    _publish(fresh, action="resolved", event_type="resolved", actor_user_id=actor_user_id,
             portal_account_id=None, request_id=request_id,
             metadata={"from": current, "resolution_code": resolution_code})
    return get_exception(exception_id, principal=principal)


def assign(exception_id, *, principal, actor_user_id=None, owner_user_id=None, owner_team_id=None,
           reason=None, request_id=None):
    """Assign / reassign the owner (user or team). Sets the owner columns and records the
    change; the record_assignments/assign_work linkage lands in the Work Management phase."""
    _require(principal, "exception.write")
    if owner_user_id is None and owner_team_id is None:
        raise ExceptionEngineError("assign requires an owner_user_id or owner_team_id")
    now = _now()
    with engine.begin() as c:
        row = _load(c, exception_id, lock=True)
        _authorize(c, principal, row, write=True)
        c.execute(exceptions.update().where(exceptions.c.id == exception_id).values(
            owner_user_id=owner_user_id, owner_team_id=owner_team_id, updated_at=now))
        _append_event(c, exception_id, "assigned", row["status"], row["status"], actor_user_id, None,
                      {"owner_user_id": owner_user_id, "owner_team_id": owner_team_id, "reason": reason})
    fresh = _fetch(exception_id)
    _publish(fresh, action="assigned", event_type="assigned", actor_user_id=actor_user_id,
             portal_account_id=None, request_id=request_id,
             metadata={"owner_user_id": owner_user_id, "owner_team_id": owner_team_id})
    return get_exception(exception_id, principal=principal)


def comment(exception_id, body, *, principal, actor_user_id=None, portal_account_id=None, request_id=None):
    _require(principal, "exception.read")
    if not body:
        raise ExceptionEngineError("A comment body is required")
    with engine.begin() as c:
        row = _load(c, exception_id, lock=False)
        _authorize(c, principal, row, write=False)
        _append_event(c, exception_id, "comment", row["status"], row["status"], actor_user_id,
                      portal_account_id, {"body": body})
    _publish(_fetch(exception_id), action="commented", event_type="comment", actor_user_id=actor_user_id,
             portal_account_id=portal_account_id, request_id=request_id, metadata={"body": body})
    return get_exception(exception_id, principal=principal)


# --- reads -------------------------------------------------------------------

def _fetch(exception_id):
    with engine.connect() as c:
        return dict(_load(c, exception_id))


def get_exception(exception_id, *, principal=None, with_events=False):
    with engine.connect() as c:
        row = dict(_load(c, exception_id))
        _require(principal, "exception.read")
        _authorize(c, principal, row, write=False)
        row["sla_state"] = sla_state(row)
        if with_events:
            row["events"] = [dict(e) for e in c.execute(
                select(exception_events).where(exception_events.c.exception_id == exception_id)
                .order_by(exception_events.c.id)
            ).mappings()]
    return row


def event_history(exception_id, *, principal):
    """Immutable event ledger for an exception (record-scope enforced)."""
    with engine.connect() as c:
        row = dict(_load(c, exception_id))
        _require(principal, "exception.read")
        _authorize(c, principal, row, write=False)
        return [dict(e) for e in c.execute(
            select(exception_events).where(exception_events.c.exception_id == exception_id)
            .order_by(exception_events.c.id)
        ).mappings()]


def list_exceptions(principal, *, domain="tax", status=None, severity=None, category=None,
                    owner_user_id=None, open_only=False):
    """List exceptions the principal may see, filtered by record scope (domain-aware).

    Sprint 5.5 serves only ``domain='tax'`` (record-scoped): non-``record.read_all``
    principals see exceptions scoped to their assigned returns/people/households.
    """
    _require(principal, "exception.read")
    if domain not in SUPPORTED_DOMAINS:
        raise UnsupportedDomainError(f"Domain '{domain}' is not implemented in Sprint 5.5")
    query = select(exceptions).where(exceptions.c.domain == domain)
    if status:
        query = query.where(exceptions.c.status == status)
    if severity:
        query = query.where(exceptions.c.severity == severity)
    if category:
        query = query.where(exceptions.c.category == category)
    if owner_user_id:
        query = query.where(exceptions.c.owner_user_id == owner_user_id)
    if open_only:
        query = query.where(exceptions.c.status.notin_(tuple(CLOSED_STATUSES)))

    with engine.connect() as c:
        if not (principal.can("record.read_all") or principal.can("record.write_all")):
            assignments = authorized_assignments(c, principal)
            return_ids = {a["entity_id"] for a in assignments if a["entity_type"] == "tax_return"}
            person_ids = {a["entity_id"] for a in assignments if a["entity_type"] == "person"}
            household_ids = {a["entity_id"] for a in assignments if a["entity_type"] == "household"}
            scope = [clause for clause in (
                exceptions.c.tax_engagement_return_id.in_(return_ids) if return_ids else None,
                exceptions.c.person_id.in_(person_ids) if person_ids else None,
                exceptions.c.household_id.in_(household_ids) if household_ids else None,
            ) if clause is not None]
            from sqlalchemy import false as sql_false
            query = query.where(or_(*scope) if scope else sql_false())
        rows = [dict(r) for r in c.execute(query.order_by(exceptions.c.opened_at.desc())).mappings()]
    for row in rows:
        row["sla_state"] = sla_state(row)
    return rows


def metrics(principal):
    """Console metrics summary over the principal's record-scoped tax exceptions."""
    from collections import Counter
    rows = list_exceptions(principal)
    open_rows = [r for r in rows if r["status"] not in CLOSED_STATUSES]
    return {
        "total": len(rows),
        "open": len(open_rows),
        "by_status": dict(Counter(r["status"] for r in rows)),
        "by_severity": dict(Counter(r["severity"] for r in open_rows)),
        "by_category": dict(Counter(r["category"] for r in open_rows)),
        "overdue": sum(1 for r in open_rows if r.get("sla_state") == "breached"),
        "escalated": sum(1 for r in open_rows if r["status"] == "escalated"),
    }
