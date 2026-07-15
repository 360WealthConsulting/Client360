"""Exception SLA sweep & notifications (Release 0.9.10 / Sprint 5.5, Phase 4).

A deterministic sweep over OPEN tax-domain exceptions that computes SLA state,
escalates per the approved severity cadence, and dispatches notifications through
the existing notification port. It is idempotent and safe to replay: escalation
timing is derived from ``sla_due_at + escalation_level * cadence`` (not wall-clock
of the last run), so repeated sweeps inside a cadence window do nothing.

Every automated escalation goes through the canonical Exception Engine
(``ee.escalate`` — which appends an immutable event and publishes audit + timeline).
Every notification attempt and its true outcome are recorded (append-only
``exception_events`` + audit); email/SMS remain stubbed (``DisabledNotificationHook``)
and are recorded as ``disabled`` — never fabricated as delivered.

Tax domain only. No work queues, API, UI, portal display, dashboards, or reporting.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import (engine, exception_events, exception_types, exceptions, portal_accounts)
from app.portal.providers import NOTIFICATION_PROVIDERS
from app.portal.service import notify
from app.security.audit import write_audit_event
from app.services import exception_engine as ee

# Escalation cadence per severity (design §3.2): blocker 4h, high daily, medium 2d, low weekly.
CADENCE_MINUTES = {"blocker": 240, "high": 1440, "medium": 2880, "low": 10080}
MAX_ESCALATION_LEVEL = 3

# Client-actionable exception codes are notified to the client (portal); everything
# else is staff-facing only. Single source of truth lives in the canonical engine
# (also drives the portal "action needed" allowlist in Phase 7).
CLIENT_FACING_CODES = ee.CLIENT_VISIBLE_CODES
_CLOSED = ("resolved", "cancelled")


def _now():
    return datetime.now(timezone.utc)


def sla_report(row, now=None):
    """SLA calculations for one exception row (needs sla_due_at, severity, status,
    escalation_level, last_notified_at): state, time remaining, level, and the next
    escalation time. The next escalation is the breach time (first) or the last
    notification plus the severity cadence (subsequent) — so it advances by one
    cadence in real time, never per-sweep."""
    now = now or _now()
    due = row["sla_due_at"]
    cadence = CADENCE_MINUTES.get(row["severity"])
    last = row.get("last_notified_at")
    if due is None or cadence is None:
        next_escalation_at = None
    elif last is None:
        next_escalation_at = due
    else:
        next_escalation_at = last + timedelta(minutes=cadence)
    return {
        "state": ee.sla_state(row, now),  # none / on_track / at_risk / breached / closed
        "time_remaining_seconds": (due - now).total_seconds() if due is not None else None,
        "escalation_level": row["escalation_level"],
        "next_escalation_at": next_escalation_at,
        "severity": row["severity"],
    }


def _record_notification(exception_id, level, dispatches, actor_user_id):
    """Append an immutable 'notified' event and an audit record for a dispatch batch."""
    with engine.begin() as c:
        c.execute(exception_events.insert().values(
            exception_id=exception_id, event_type="notified", from_status=None, to_status=None,
            actor_user_id=actor_user_id, metadata={"level": level, "dispatches": dispatches}))
    write_audit_event(action="exception.notified", entity_type="exception", entity_id=exception_id,
                      actor_user_id=actor_user_id, request_id=f"exception-{exception_id}-notify-{level}",
                      metadata={"level": level, "dispatches": dispatches})


def _dispatch(row, level, actor_user_id):
    """Notify staff (always) and the client (client-facing codes) via the port,
    recording true outcomes. Returns the dispatch list."""
    title = f"Exception escalated (L{level}): {row['title']}"
    body = f"Severity {row['severity']} exception is past SLA and has escalated to level {level}."
    dispatches = []

    # Staff-facing: in-app provider (real, delivers).
    staff_result = NOTIFICATION_PROVIDERS["in_app"].deliver(
        recipient=str(row["owner_user_id"] or "unassigned"), title=title, body=body,
        metadata={"exception_id": row["id"], "audience": "staff"})
    dispatches.append({"audience": "staff", "channel": "in_app",
                       "outcome": "delivered" if staff_result["delivered"] else "disabled"})

    # Client-facing: notify each active portal account, in-app (delivered) + email (stubbed → disabled).
    if row["code"] in CLIENT_FACING_CODES and row["person_id"]:
        with engine.connect() as c:
            accounts = list(c.scalars(select(portal_accounts.c.id).where(
                portal_accounts.c.person_id == row["person_id"],
                portal_accounts.c.status.in_(("active", "invited")))))
        for account in accounts:
            for channel in ("in_app", "email"):
                notify(account, "exception_escalated", title, body=body, channel=channel,
                       entity_type="exception", entity_id=row["id"],
                       idempotency_key=f"exc-{row['id']}-esc-{level}-{channel}-{account}")
                delivered = NOTIFICATION_PROVIDERS[channel].deliver(
                    recipient=str(account), title=title, body=body, metadata={})["delivered"]
                dispatches.append({"audience": "client", "channel": channel, "account": account,
                                   "outcome": "delivered" if delivered else "disabled"})

    _record_notification(row["id"], level, dispatches, actor_user_id)
    return dispatches


def _touch(exception_id, now, notification_count):
    with engine.begin() as c:
        c.execute(exceptions.update().where(exceptions.c.id == exception_id).values(
            last_notified_at=now, notification_count=notification_count + 1))


def sweep_exception_slas(*, now=None, actor_user_id=None):
    """Deterministic SLA sweep over open tax exceptions. Idempotent / safe to replay."""
    now = now or _now()
    summary = {"scanned": 0, "waiting": 0, "on_track": 0, "at_risk": 0,
               "at_risk_notified": 0, "breached": 0, "escalated": 0}
    with engine.connect() as c:
        rows = c.execute(
            select(exceptions.c.id, exceptions.c.severity, exceptions.c.status,
                   exceptions.c.sla_due_at, exceptions.c.escalation_level,
                   exceptions.c.last_notified_at, exceptions.c.notification_count,
                   exceptions.c.owner_user_id, exceptions.c.person_id, exceptions.c.title,
                   exception_types.c.code)
            .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
            .where(exceptions.c.domain == "tax", exceptions.c.status.notin_(_CLOSED))
        ).mappings().all()

    for row in rows:
        summary["scanned"] += 1
        if row["status"] == "waiting":
            summary["waiting"] += 1  # SLA paused while waiting on a third party
            continue
        if row["sla_due_at"] is None:
            continue
        report = sla_report(row, now)
        state = report["state"]
        if state == "breached":
            summary["breached"] += 1
            if now >= report["next_escalation_at"] and row["escalation_level"] < MAX_ESCALATION_LEVEL:
                ee.escalate(row["id"], principal=None, actor_user_id=actor_user_id, reason="sla_breach")
                _dispatch(row, row["escalation_level"] + 1, actor_user_id)
                _touch(row["id"], now, row["notification_count"])
                summary["escalated"] += 1
        elif state == "at_risk":
            summary["at_risk"] += 1
            if row["last_notified_at"] is None:  # single early warning
                _dispatch(row, row["escalation_level"], actor_user_id)
                _touch(row["id"], now, row["notification_count"])
                summary["at_risk_notified"] += 1
        elif state == "on_track":
            summary["on_track"] += 1
    return summary
