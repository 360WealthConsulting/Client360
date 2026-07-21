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

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.db import engine, exception_events, exception_types, exceptions
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope
from app.services.timeline import add_timeline_event
from app.services.work_management import authorized_assignments

# Sprint 5.5 implemented the tax domain; Release 0.9.11 (ADR-18) adds the benefits domain.
SUPPORTED_DOMAINS = frozenset({"tax", "benefits", "insurance"})

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
    elif row["domain"] in ("benefits", "insurance"):
        # Organization-anchored (ADR-18) with a person/household fallback. Insurance
        # reuses the same scope model: carrier/owner org via related_entity, or the
        # insured person/household (Release 0.10.0, AD-1).
        from app.security.authorization import organization_in_scope
        org_id = row["related_entity_id"] if row["related_entity_type"] == "organization" else None
        if org_id and organization_in_scope(principal, org_id, write=write, connection=connection):
            return
        for entity_type, entity_id in (("person", row["person_id"]), ("household", row["household_id"])):
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
                    sla_due_at=None, request_id=None, metadata=None):
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
                    sla_due_at=(sla_due_at if sla_due_at is not None
                               else (now + timedelta(minutes=etype["sla_minutes"])) if etype["sla_minutes"] else None),
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
            org_ids = {a["entity_id"] for a in assignments if a["entity_type"] == "organization"}
            from sqlalchemy import and_
            scope = [clause for clause in (
                exceptions.c.tax_engagement_return_id.in_(return_ids) if return_ids else None,
                exceptions.c.person_id.in_(person_ids) if person_ids else None,
                exceptions.c.household_id.in_(household_ids) if household_ids else None,
                and_(exceptions.c.related_entity_type == "organization",
                     exceptions.c.related_entity_id.in_(org_ids)) if org_ids else None,
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


# --- client portal ("action needed") -----------------------------------------
#
# The portal shows a *strict allowlist* of client-actionable exception codes and
# projects them to plain-language, client-safe fields only. This is the single
# source of truth for what a client may ever see; the SLA notifier (Phase 4)
# reuses it for client-facing notifications. Internal categories (compliance,
# workflow/reviewer/preparer, operational, internal filing) are never included,
# and no internal terminology (codes, owners, escalation levels, dedupe keys,
# audit metadata, event history) is exposed.
CLIENT_VISIBLE_CODES = frozenset({
    "CLIENT_UNRESPONSIVE", "CLIENT_EFILE_AUTH_MISSING", "CLIENT_ENGAGEMENT_UNSIGNED",
    "CLIENT_INFO_INCONSISTENT", "DOC_MISSING_OVERDUE",
})

# Per-code client wording + the existing portal action each item routes to. A
# code absent from this map is staff-only and can never reach a portal account.
_CLIENT_PRESENTATION = {
    "DOC_MISSING_OVERDUE": {
        "title": "Upload a requested document",
        "explanation": "Your tax team is waiting on a document to keep your return moving.",
        "action_label": "Go to document requests",
        "action_url": "/portal/requests",
    },
    "CLIENT_ENGAGEMENT_UNSIGNED": {
        "title": "Sign your engagement letter",
        "explanation": "Please review and sign your engagement letter so we can begin your return.",
        "action_label": "Review and sign",
        "action_url": "/portal/tasks",
    },
    "CLIENT_EFILE_AUTH_MISSING": {
        "title": "Approve your e-file authorization",
        "explanation": "Your return is ready and needs your authorization before we can e-file it.",
        "action_label": "Review and approve",
        "action_url": "/portal/tasks",
    },
    "CLIENT_UNRESPONSIVE": {
        "title": "Complete your outstanding items",
        "explanation": "Your tax team needs information from you to move your return forward.",
        "action_label": "Open your tasks",
        "action_url": "/portal/tasks",
    },
    "CLIENT_INFO_INCONSISTENT": {
        "title": "Confirm a few details",
        "explanation": "Your tax team has a question about some information on your return.",
        "action_label": "Send a secure message",
        "action_url": "/portal/messages",
    },
}

# Client-facing severity wording (never the internal blocker/high/medium/low).
_CLIENT_PRIORITY = {
    "blocker": "Needs your attention",
    "high": "Needs your attention",
    "medium": "Please complete soon",
    "low": "When you have a chance",
}


def _client_status(status):
    if status == "resolved":
        return "Completed"
    if status == "cancelled":
        return "No longer needed"
    return "Action needed"


def _project_client(row):
    """Project an exception row to client-safe fields only (no codes, owners,
    escalation levels, dedupe keys, audit/event data, or internal wording)."""
    policy = _CLIENT_PRESENTATION[row["code"]]
    year = row["tax_year"]
    form = row["form_number"]
    return {
        "id": row["id"],
        "title": policy["title"],
        "explanation": policy["explanation"],
        "priority": _CLIENT_PRIORITY.get(row["severity"], "Please complete soon"),
        "status": _client_status(row["status"]),
        "resolved": row["status"] in CLOSED_STATUSES,
        "due_date": row["sla_due_at"].date().isoformat() if row["sla_due_at"] else None,
        "tax_year": year,
        "return_label": (f"{year} Form {form}" if year and form else (str(year) if year else None)),
        "action_label": policy["action_label"],
        "action_url": policy["action_url"],
    }


def client_action_items(scope, *, include_resolved=False):
    """Portal-safe list of client-visible exceptions within a portal ``scope``.

    ``scope`` is the portal scope dict (``person_ids`` / ``shared_household_ids``)
    resolved from the portal account's grants — never a staff principal. Only
    ``CLIENT_VISIBLE_CODES`` are returned, projected to client-safe fields. Active
    items only by default, so resolved/cancelled exceptions automatically drop out
    of the client's action view (the real underlying action / detector clears them).
    """
    person_ids = tuple(scope.get("person_ids") or ())
    household_ids = tuple(scope.get("shared_household_ids") or ())
    if not person_ids and not household_ids:
        return []
    from app.db import tax_engagement_returns, tax_engagements, tax_return_types, tax_years
    query = (
        select(exceptions, exception_types.c.code.label("code"),
               tax_years.c.year.label("tax_year"),
               tax_return_types.c.form_number.label("form_number"))
        .select_from(
            exceptions
            .join(exception_types, exception_types.c.id == exceptions.c.exception_type_id)
            .outerjoin(tax_engagement_returns,
                       tax_engagement_returns.c.id == exceptions.c.tax_engagement_return_id)
            .outerjoin(tax_engagements,
                       tax_engagements.c.id == tax_engagement_returns.c.tax_engagement_id)
            .outerjoin(tax_years, tax_years.c.id == tax_engagements.c.tax_year_id)
            .outerjoin(tax_return_types,
                       tax_return_types.c.id == tax_engagement_returns.c.return_type_id))
        .where(exceptions.c.domain == "tax",
               exception_types.c.code.in_(tuple(CLIENT_VISIBLE_CODES)))
    )
    scope_clauses = []
    if person_ids:
        scope_clauses.append(exceptions.c.person_id.in_(person_ids))
    if household_ids:
        scope_clauses.append(exceptions.c.household_id.in_(household_ids))
    query = query.where(or_(*scope_clauses))
    if not include_resolved:
        query = query.where(exceptions.c.status.notin_(tuple(CLOSED_STATUSES)))
    query = query.order_by(exceptions.c.opened_at.desc())
    with engine.connect() as c:
        rows = c.execute(query).mappings().all()
    return [_project_client(r) for r in rows]


def client_action_item(scope, exception_id):
    """Fetch one client-visible exception by id, enforcing portal scope. Anything
    not client-visible, out-of-scope, or non-existent is reported as not-found
    (never trust a client-supplied id, and hide existence of other records)."""
    for item in client_action_items(scope, include_resolved=True):
        if item["id"] == exception_id:
            return item
    raise ExceptionNotFoundError(f"Exception {exception_id} not found")


# --- employer portal ("action needed") ---------------------------------------
#
# Strict allowlist of **employer-actionable** benefits exceptions surfaced to an
# employer portal account (Release 0.9.11, Phase 7). Everything else — compliance
# (5500/fiduciary/testing/notices), internal document delivery (SPD/SBC), renewals,
# retirement participant items, and staff-only exceptions — is never shown. Items are
# projected to **organization-level, PII-free** fields: no employee identity, EIN,
# compensation, deferral, internal codes, owners, escalation levels, notes, or staff data.
EMPLOYER_VISIBLE_CODES = frozenset({
    "BEN_CENSUS_OVERDUE", "BEN_NEW_HIRE_ENROLLMENT_DUE", "BEN_OPEN_ENROLLMENT_INCOMPLETE",
    "BEN_ELIGIBILITY_UNRESOLVED", "BEN_WAIVER_MISSING",
})

_EMPLOYER_PRESENTATION = {
    "BEN_CENSUS_OVERDUE": {
        "title": "Employee census needed",
        "explanation": "Your benefits team needs an updated employee census to keep your plan on track.",
        "action_label": "Upload census", "action_kind": "census"},
    "BEN_NEW_HIRE_ENROLLMENT_DUE": {
        "title": "New-hire enrollment action needed",
        "explanation": "One or more new hires need to be enrolled or waived. Contact your benefits team to complete it.",
        "action_label": "Message benefits team", "action_kind": "message"},
    "BEN_OPEN_ENROLLMENT_INCOMPLETE": {
        "title": "Open enrollment is incomplete",
        "explanation": "Elections are still outstanding for this open-enrollment period.",
        "action_label": "Message benefits team", "action_kind": "message"},
    "BEN_ELIGIBILITY_UNRESOLVED": {
        "title": "Eligibility information needed",
        "explanation": "Your benefits team needs information to confirm employee eligibility.",
        "action_label": "Message benefits team", "action_kind": "message"},
    "BEN_WAIVER_MISSING": {
        "title": "Coverage waivers outstanding",
        "explanation": "Some eligible employees have not yet elected or waived coverage.",
        "action_label": "Message benefits team", "action_kind": "message"},
}


def _project_employer(row):
    """Organization-level, PII-free projection for an employer portal account."""
    policy = _EMPLOYER_PRESENTATION[row["code"]]
    return {
        "id": row["id"],
        "organization_id": row["related_entity_id"],
        "title": policy["title"],
        "explanation": policy["explanation"],
        "status": "Completed" if row["status"] in CLOSED_STATUSES else "Action needed",
        "resolved": row["status"] in CLOSED_STATUSES,
        "due_date": row["sla_due_at"].date().isoformat() if row["sla_due_at"] else None,
        "action_label": policy["action_label"],
        "action_kind": policy["action_kind"],
    }


def employer_action_items(scope, *, include_resolved=False):
    """Employer-safe list of employer-actionable benefits exceptions for the portal
    account's organizations. Only ``EMPLOYER_VISIBLE_CODES``; organization-level, no PII.
    Active items only by default (completed items drop from the employer's view)."""
    org_ids = tuple(scope.get("organization_ids") or ())
    if not org_ids:
        return []
    query = (
        select(exceptions.c.id, exceptions.c.status, exceptions.c.sla_due_at,
               exceptions.c.related_entity_id, exception_types.c.code.label("code"))
        .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
        .where(exceptions.c.domain == "benefits",
               exceptions.c.related_entity_type == "organization",
               exceptions.c.related_entity_id.in_(org_ids),
               exception_types.c.code.in_(tuple(EMPLOYER_VISIBLE_CODES)))
    )
    if not include_resolved:
        query = query.where(exceptions.c.status.notin_(tuple(CLOSED_STATUSES)))
    with engine.connect() as c:
        rows = c.execute(query.order_by(exceptions.c.opened_at.desc())).mappings().all()
    return [_project_employer(r) for r in rows]


def employer_action_item(scope, exception_id):
    """One employer-visible benefits exception by id, enforcing organization scope.
    Anything not employer-visible, out-of-scope, or non-existent is reported not-found."""
    for item in employer_action_items(scope, include_resolved=True):
        if item["id"] == exception_id:
            return item
    raise ExceptionNotFoundError(f"Exception {exception_id} not found")


def open_count_for_client(person_id, household_id=None):
    """Read-only count of a client's open exceptions (person, or household).
    Factual composition for the Client 360 summary (Phase D.2); keyed by
    person/household, so it only reflects the requested client. Callers reach this
    from a record-scoped client profile, so no additional principal scoping."""
    conds = [exceptions.c.person_id == person_id]
    if household_id:
        conds.append(exceptions.c.household_id == household_id)
    with engine.connect() as conn:
        return conn.scalar(
            select(func.count()).select_from(exceptions)
            .where(or_(*conds), exceptions.c.status.notin_(tuple(CLOSED_STATUSES)))
        ) or 0


def open_exceptions_for_client(person_id, household_id=None, *, limit=20):
    """Read-only list of a client's open exceptions (person, or household when a
    household_id is given). Factual composition for the Meeting Workspace brief
    (Phase D.3). Keyed by person/household, so it only reflects the requested
    client; callers reach this from a record-scoped surface."""
    conds = [exceptions.c.person_id == person_id]
    if household_id:
        conds.append(exceptions.c.household_id == household_id)
    stmt = (
        select(exceptions.c.id, exceptions.c.domain, exceptions.c.severity,
               exceptions.c.status, exceptions.c.title, exceptions.c.person_id,
               exceptions.c.household_id, exceptions.c.sla_due_at)
        .where(or_(*conds), exceptions.c.status.notin_(tuple(CLOSED_STATUSES)))
        .order_by(exceptions.c.opened_at.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]
