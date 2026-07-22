"""Communication delivery lifecycle (Phase D.18) — metadata only, reuses existing transport.

Delivery is **metadata only**: this module records the delivery lifecycle
(queued→scheduled→sending→sent→delivered→failed→cancelled→read→expired) as a ledger and drives
the message/recipient status. It implements **no mail server / SMS gateway / Graph send** — when a
message is marked ``sent`` it records **intent** in the EXISTING notification ledger
(``app/services/notifications.record_notification``), exactly mirroring that ledger's intent-only
contract, and links the resulting ``notification_uid`` back onto the message. Approved lifecycle
events (``message_sent`` / ``message_delivered`` / ``message_read``) are published to the shared
Activity Timeline (client-anchored only) — not every status transition.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import (
    communication_deliveries as deliveries,
)
from app.db import (
    communication_events as events,
)
from app.db import (
    communication_messages as messages,
)
from app.db import (
    communication_recipients as recipients,
)
from app.db import engine

# Deterministic delivery state machine (metadata only).
_TRANSITIONS = {
    "queued": {"scheduled", "sending", "sent", "cancelled", "failed"},
    "scheduled": {"sending", "sent", "cancelled", "expired", "failed"},
    "sending": {"sent", "failed", "cancelled"},
    "sent": {"delivered", "failed", "read"},
    "delivered": {"read"},
    "failed": {"queued", "sending"},        # retry
    "cancelled": set(),
    "expired": set(),
    "read": set(),
}
# Lifecycle statuses that publish an approved timeline event.
_TIMELINE_STATUSES = {"sent": "communication_sent", "delivered": "communication_delivered",
                      "read": "communication_read"}


class DeliveryError(Exception):
    """Invalid delivery transition."""


def _now():
    return datetime.now(UTC)


def allowed_next(status: str) -> set[str]:
    return set(_TRANSITIONS.get(status, set()))


def _publish_timeline(conv: dict, message_id: int, status: str, occurred_at):
    """Emit an approved communication lifecycle event to the shared timeline (client-anchored)."""
    event_type = _TIMELINE_STATUSES.get(status)
    if event_type is None:
        return
    if not conv.get("person_id") and not conv.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="communication", event_type=event_type,
            title=conv.get("subject") or "Communication",
            summary=(conv.get("category") or ""),
            person_id=conv.get("person_id"), household_id=conv.get("household_id"),
            external_id=f"communication-{message_id}-{status}",
            event_metadata={"conversation_id": conv["id"], "message_id": message_id,
                            "status": status})
    except Exception:
        # Timeline publication is best-effort and must never break the delivery transition.
        pass


def _record_intent(c, message: dict, conv: dict, status: str):
    """Record delivery INTENT in the reused notification ledger (no dispatch). Returns uid|None."""
    if status != "sent":
        return None
    try:
        from app.services.notifications import record_notification
        recip = c.execute(select(recipients).where(recipients.c.message_id == message["id"])
                          .limit(1)).mappings().first()
        recipient_ref = (recip["recipient_ref"] if recip else None) or (
            f"conversation:{conv['id']}")
        recipient_type = (recip["recipient_type"] if recip else "person")
        rec = record_notification(
            notification_type="communication.message", recipient_type=recipient_type,
            recipient_ref=str(recipient_ref), channel=message["channel"],
            title=message.get("subject") or conv.get("subject") or "Communication",
            source_ref=f"communication_message:{message['id']}",
            metadata={"conversation_id": conv["id"], "message_id": message["id"]}, conn=c)
        return rec.notification_uid
    except Exception:
        # The ledger is a non-authoritative convenience; a failure never blocks the transition.
        return None


def record_delivery(c, message_id: int, status: str, *, conv: dict, channel: str,
                    recipient_id: int | None = None, provider: str | None = None,
                    provider_ref: str | None = None, detail: str | None = None,
                    actor_user_id: int | None = None, metadata: dict | None = None) -> dict:
    """Apply one delivery transition within an OPEN transaction ``c``. Validates the transition,
    appends a delivery-ledger row + audit event, updates message + recipient status/timestamps, and
    (for ``sent``) records notification intent. Returns the updated message row."""
    message = c.execute(select(messages).where(messages.c.id == message_id)).mappings().first()
    if message is None:
        raise DeliveryError("message not found")
    current = message["status"]
    if status != current and status not in _TRANSITIONS.get(current, set()):
        raise DeliveryError(f"cannot transition delivery {current!r} -> {status!r}")
    now = _now()

    notification_uid = message["notification_uid"] or _record_intent(c, dict(message), conv, status)

    values: dict = {"status": status, "updated_at": now}
    if notification_uid and not message["notification_uid"]:
        values["notification_uid"] = notification_uid
    if status == "sent" and not message["sent_at"]:
        values["sent_at"] = now
    if status == "delivered" and not message["delivered_at"]:
        values["delivered_at"] = now
    if status == "read" and not message["read_at"]:
        values["read_at"] = now
    c.execute(messages.update().where(messages.c.id == message_id).values(**values))

    # Recipient status follows the message; a specific recipient can be targeted (e.g. per-read).
    rec_values: dict = {"delivery_status": status}
    if status == "delivered":
        rec_values["delivered_at"] = now
    if status == "read":
        rec_values["read_at"] = now
    rec_where = recipients.c.message_id == message_id
    if recipient_id is not None:
        rec_where = recipients.c.id == recipient_id
    c.execute(recipients.update().where(rec_where).values(**rec_values))

    c.execute(deliveries.insert().values(
        message_id=message_id, recipient_id=recipient_id, channel=channel, provider=provider,
        provider_ref=provider_ref, status=status, detail=detail, delivery_metadata=metadata,
        occurred_at=now))
    c.execute(events.insert().values(
        conversation_id=conv["id"], message_id=message_id, event_type=f"delivery_{status}",
        actor_user_id=actor_user_id,
        payload=json.loads(json.dumps({"status": status, "channel": channel})), occurred_at=now))

    _publish_timeline(conv, message_id, status, now)
    return dict(c.execute(select(messages).where(messages.c.id == message_id)).mappings().one())


def delivery_history(message_id: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(deliveries).where(deliveries.c.message_id == message_id)
            .order_by(deliveries.c.occurred_at, deliveries.c.id)).mappings()]
