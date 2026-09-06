"""Canonical notification ledger & model (F5.1 / Epic 5, ADR-017).

The canonical, platform-level persistence model for notifications: a
**non-authoritative** delivery ledger that records **notification intent and delivery
outcomes only**. It is **never authoritative** for workflow, domain/business, or
evidence state — recording, reading, or removing a notification never mutates any
workflow or domain record.

F5.1 scope: the ledger table (migration ``f51n0t1c3d4e``) plus this model — creation
(idempotent, deterministic) and retrieval. **No** providers, dispatch worker, event
consumers, preferences/consent, or routes (those are F5.2–F5.7). The existing portal
and benefits notification code and ``portal_notifications`` are untouched.

Content/reference boundary (ADR-017 §14): recipient-facing ``title``/``body`` content
lives only inside this persistence boundary; ``metadata`` carries references only. This
module emits **no** events, audit records, or logs (so no content can leak); event and
audit/evidence integration are later features (F5.4/F5.6).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Table, select

# --- lifecycle specification (ADR-017 §8) ------------------------------------

PENDING = "pending"
SUPPRESSED = "suppressed"
DELIVERED = "delivered"
DISABLED = "disabled"
FAILED = "failed"
DEAD = "dead"

#: All valid ledger statuses. Note: ``read`` is NOT a status (it is a separate
#: ``read_at`` timestamp) and is never a delivery or business-completion state.
NOTIFICATION_STATUSES: frozenset[str] = frozenset(
    {PENDING, SUPPRESSED, DELIVERED, DISABLED, FAILED, DEAD}
)

#: Terminal (outcome) statuses.
TERMINAL_STATUSES: frozenset[str] = frozenset({SUPPRESSED, DELIVERED, DISABLED, FAILED, DEAD})

#: Deterministic lifecycle: ``pending → suppressed | delivered | disabled | failed | dead``.
#: Encoded here for F5.5 (dispatch) to consume; F5.1 does not perform transitions.
LIFECYCLE: dict[str, frozenset[str]] = {
    PENDING: TERMINAL_STATUSES,
    SUPPRESSED: frozenset(),
    DELIVERED: frozenset(),
    DISABLED: frozenset(),
    FAILED: frozenset(),
    DEAD: frozenset(),
}


def validate_status(status: str) -> str:
    """Validate a ledger status (raises ``ValueError`` for an unknown status)."""
    if status not in NOTIFICATION_STATUSES:
        raise ValueError(f"Invalid notification status: {status!r}")
    return status


# --- table access (reflection; not declared in Core metadata) ----------------

def _notifications_table() -> Table:
    from app.db import engine, metadata

    table = metadata.tables.get("notifications")
    if table is None:
        table = Table("notifications", metadata, autoload_with=engine)
    return table


_FIELDS = (
    "id", "notification_uid", "recipient_type", "recipient_ref", "channel",
    "notification_type", "status", "dedupe_key", "source_event_id", "source_ref",
    "provider_ref", "attempts", "last_error", "title", "body", "notification_metadata",
    "created_at", "updated_at", "delivered_at", "failed_at", "disabled_at",
    "suppressed_at", "dead_at", "read_at",
)


@dataclass(frozen=True)
class NotificationRecord:
    id: int
    notification_uid: str
    recipient_type: str
    recipient_ref: str
    channel: str
    notification_type: str
    status: str
    dedupe_key: str
    source_event_id: str | None
    source_ref: str | None
    provider_ref: str | None
    attempts: int
    last_error: str | None
    title: str
    body: str | None
    notification_metadata: dict
    created_at: object
    updated_at: object
    delivered_at: object
    failed_at: object
    disabled_at: object
    suppressed_at: object
    dead_at: object
    read_at: object


def _to_record(row) -> NotificationRecord:
    m = dict(row)
    return NotificationRecord(**{f: m.get(f) for f in _FIELDS})


# --- deterministic deduplication ---------------------------------------------

def notification_dedupe_key(
    *, notification_type: str, recipient_ref: str, channel: str,
    source_event_id: str | None = None, source_ref: str | None = None,
) -> str:
    """Deterministic idempotency/dedup key: the same logical notification (type,
    recipient, channel, source) always yields the same key, so a redelivered source
    event cannot create a duplicate ledger entry (unique ``dedupe_key`` is the backstop)."""
    return "notif:" + ":".join(
        (notification_type, recipient_ref, channel, source_event_id or "-", source_ref or "-")
    )


# --- model: create (intent only) + read --------------------------------------

def record_notification(
    *, notification_type: str, recipient_ref: str, recipient_type: str, title: str,
    channel: str = "in_app", body: str | None = None, status: str = PENDING,
    dedupe_key: str | None = None, source_event_id: str | None = None,
    source_ref: str | None = None, provider_ref: str | None = None,
    metadata: dict | None = None, conn=None,
) -> NotificationRecord:
    """Record notification **intent** in the canonical ledger (idempotent, deterministic).

    Records intent/outcome only — it performs **no delivery/dispatch** (F5.5) and mutates
    **no** workflow/domain/evidence state. Idempotent: a repeated logical notification
    (same ``dedupe_key``) returns the existing record rather than duplicating.
    ``title``/``body`` may carry recipient-facing content (kept only in this ledger);
    ``metadata`` is references only.
    """
    validate_status(status)
    key = dedupe_key or notification_dedupe_key(
        notification_type=notification_type, recipient_ref=recipient_ref, channel=channel,
        source_event_id=source_event_id, source_ref=source_ref,
    )
    notifications = _notifications_table()

    def _do(c) -> NotificationRecord:
        existing = c.execute(select(notifications).where(notifications.c.dedupe_key == key)).mappings().first()
        if existing is not None:
            return _to_record(existing)  # idempotent
        row = c.execute(
            notifications.insert().values(
                notification_uid=str(uuid.uuid4()), recipient_type=recipient_type,
                recipient_ref=recipient_ref, channel=channel, notification_type=notification_type,
                status=status, dedupe_key=key, source_event_id=source_event_id, source_ref=source_ref,
                provider_ref=provider_ref, title=title, body=body,
                notification_metadata=metadata or {},
            ).returning(*notifications.c)
        ).mappings().first()
        return _to_record(row)

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.begin() as connection:
        return _do(connection)


# --- model: recipient-scoped read + read-state ------------------------------
#
# Every function below takes the RECIPIENT as a required argument and puts it in the WHERE clause.
# That is the security boundary for the staff inbox: a caller cannot read or mark a row belonging to
# someone else, because there is no code path that omits the recipient. Setting ``read_at`` stays
# inside the ledger boundary — ``read`` is deliberately not a status (ADR-017 §8), so this mutates no
# workflow, domain or evidence state, and it needs no schema change.

def list_notifications(
    *, recipient_type: str, recipient_ref: str, limit: int = 50, unread_only: bool = False, conn=None,
) -> list[NotificationRecord]:
    """One recipient's notifications, newest first. Read-only."""
    notifications = _notifications_table()
    query = select(notifications).where(
        notifications.c.recipient_type == recipient_type,
        notifications.c.recipient_ref == recipient_ref,
    )
    if unread_only:
        query = query.where(notifications.c.read_at.is_(None))
    query = query.order_by(notifications.c.created_at.desc(), notifications.c.id.desc()).limit(limit)

    def _do(c):
        return [_to_record(row) for row in c.execute(query).mappings().all()]

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.connect() as connection:
        return _do(connection)


def unread_notification_count(*, recipient_type: str, recipient_ref: str, conn=None) -> int:
    """How many of one recipient's notifications are unread. Read-only."""
    from sqlalchemy import func

    notifications = _notifications_table()
    query = select(func.count()).select_from(notifications).where(
        notifications.c.recipient_type == recipient_type,
        notifications.c.recipient_ref == recipient_ref,
        notifications.c.read_at.is_(None),
    )

    def _do(c):
        return int(c.scalar(query) or 0)

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.connect() as connection:
        return _do(connection)


def mark_notification_read(
    *, notification_id: int, recipient_type: str, recipient_ref: str, conn=None,
) -> bool:
    """Mark ONE of this recipient's notifications read. ``True`` when a row was changed.

    Idempotent (an already-read row is left alone and reports ``False``) and recipient-scoped, so a
    notification id belonging to somebody else simply matches nothing — it is never an error that
    would disclose the row's existence.
    """
    from datetime import UTC, datetime

    notifications = _notifications_table()
    statement = notifications.update().where(
        notifications.c.id == notification_id,
        notifications.c.recipient_type == recipient_type,
        notifications.c.recipient_ref == recipient_ref,
        notifications.c.read_at.is_(None),
    ).values(read_at=datetime.now(UTC))

    def _do(c) -> bool:
        return c.execute(statement).rowcount > 0

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.begin() as connection:
        return _do(connection)


def mark_all_notifications_read(*, recipient_type: str, recipient_ref: str, conn=None) -> int:
    """Mark every unread notification for ONE recipient read. Returns how many changed."""
    from datetime import UTC, datetime

    notifications = _notifications_table()
    statement = notifications.update().where(
        notifications.c.recipient_type == recipient_type,
        notifications.c.recipient_ref == recipient_ref,
        notifications.c.read_at.is_(None),
    ).values(read_at=datetime.now(UTC))

    def _do(c) -> int:
        return int(c.execute(statement).rowcount)

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.begin() as connection:
        return _do(connection)


def get_notification(
    *, notification_uid: str | None = None, notification_id: int | None = None,
    dedupe_key: str | None = None, conn=None,
) -> NotificationRecord | None:
    """Retrieve a single ledger record by uid, id, or dedupe key (read-only, side-effect-free)."""
    notifications = _notifications_table()
    if notification_uid is not None:
        where = notifications.c.notification_uid == notification_uid
    elif notification_id is not None:
        where = notifications.c.id == notification_id
    elif dedupe_key is not None:
        where = notifications.c.dedupe_key == dedupe_key
    else:
        raise ValueError("notification_uid, notification_id, or dedupe_key is required")
    query = select(notifications).where(where)

    def _do(c) -> NotificationRecord | None:
        row = c.execute(query).mappings().first()
        return _to_record(row) if row is not None else None

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.connect() as connection:
        return _do(connection)
