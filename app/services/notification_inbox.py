"""The staff notification inbox — a recipient-scoped READ MODEL over the canonical ledger.

Batch 2 began recording staff notifications in the ``notifications`` ledger, but nothing displayed
them: the client audience has ``/portal/notifications`` and staff had nothing. This module is the
projection behind ``/notifications``. It creates no table, no channel, no provider and no transport,
and it is the staff mirror of ``portal.service._client_notification_view``.

RECIPIENT MAPPING. A staff principal is ``recipient_type="user"`` and
``recipient_ref=str(principal.user_id)`` — exactly what ``portal.message_notifications`` writes and
what ``scheduling.service`` already used. Every query here is scoped by that pair inside the ledger
service, so there is no code path that returns another user's row.

TEAM ROWS ARE NOT SHOWN. ``message_notifications`` can address a thread's ``assigned_team_id`` as
``recipient_type="team"``. Resolving team membership to individual staff is deliberately out of scope
here, so a team-addressed notification is recorded but not yet surfaced to anybody. That is a known
gap, not an oversight.

LINKS ARE ALLOW-LISTED, NEVER TRUSTED. ``notification_metadata`` is a JSON blob any producer can
fill, so treating ``metadata["link"]`` as an href would make every ledger row a potential open
redirect. A link is rendered only when it is a safe SITE-RELATIVE path AND matches one of the
explicitly known destinations in ``_ALLOWED_LINKS``. Anything else — an absolute URL, a
protocol-relative ``//host`` path, a scheme, a backslash, whitespace, a control character, or simply
an unrecognised path — yields no link at all and the row renders as plain text.

PROJECTION, NOT PASSTHROUGH. The view returns a FIXED key set. Raw ``notification_metadata`` never
reaches a template, so a future producer cannot leak a payload onto this page by writing it into
metadata.
"""
from __future__ import annotations

import re

#: Site-relative, single leading slash, no scheme/backslash/whitespace/control characters, bounded
#: length. This is the shape test, applied BEFORE the destination allowlist below.
_SAFE_PATH = re.compile(r"^/(?!/)[A-Za-z0-9\-._~/]{0,200}$")

#: The destinations this surface is allowed to link to. Each entry is an anchored pattern for a real
#: internal route. A notification type that wants a link adds its route here, deliberately.
_ALLOWED_LINKS = (
    re.compile(r"^/admin/client-portal/threads/\d+$"),   # portal.secure_message (Batch 2)
)

#: Human labels for the notification types this surface knows about. An unknown type still renders
#: (the ledger is shared) with its raw type shown, never hidden.
_TYPE_LABELS = {
    "portal.secure_message": "Secure message",
    "communication.message": "Communication",
    "scheduling.reminder": "Reminder",
}

STAFF_RECIPIENT_TYPE = "user"
#: The inbox is a recent-activity view, not an archive.
PAGE_LIMIT = 50


def staff_recipient_ref(principal) -> str:
    """The ledger recipient reference for a staff principal."""
    return str(principal.user_id)


def safe_link(value) -> str | None:
    """A renderable href, or ``None``. Fails closed on anything not explicitly recognised.

    The stored value must match EXACTLY — it is never trimmed, unescaped or otherwise normalised
    first. Normalising would mean the thing checked is not the thing rendered, which is how
    allowlists get bypassed; a stray space costs a link, which is the safe way to be wrong.
    """
    if not isinstance(value, str) or not _SAFE_PATH.match(value):
        return None
    return value if any(p.match(value) for p in _ALLOWED_LINKS) else None


def _view(record) -> dict:
    """Presentation fields only — never the raw metadata blob, dedupe key or provider reference."""
    metadata = record.notification_metadata or {}
    return {
        "id": record.id,
        "notification_type": record.notification_type,
        "label": _TYPE_LABELS.get(record.notification_type, record.notification_type),
        "title": record.title,
        "body": record.body,
        "created_at": record.created_at,
        "read_at": record.read_at,
        "unread": record.read_at is None,
        "link": safe_link(metadata.get("link")),
    }


def staff_notifications(principal, *, limit: int = PAGE_LIMIT, unread_only: bool = False) -> dict:
    """One staff user's own notifications plus their unread count. Read-only."""
    from app.services.notifications import list_notifications, unread_notification_count

    ref = staff_recipient_ref(principal)
    records = list_notifications(recipient_type=STAFF_RECIPIENT_TYPE, recipient_ref=ref,
                                 limit=limit, unread_only=unread_only)
    return {
        "rows": [_view(r) for r in records],
        "unread_count": unread_notification_count(
            recipient_type=STAFF_RECIPIENT_TYPE, recipient_ref=ref),
        "unread_only": unread_only,
    }


def mark_read(principal, notification_id: int) -> bool:
    """Mark one of the CALLER'S OWN notifications read. Another user's id matches nothing."""
    from app.services.notifications import mark_notification_read

    return mark_notification_read(
        notification_id=notification_id, recipient_type=STAFF_RECIPIENT_TYPE,
        recipient_ref=staff_recipient_ref(principal))


def mark_all_read(principal) -> int:
    """Mark every unread notification belonging to the caller read. Returns how many changed."""
    from app.services.notifications import mark_all_notifications_read

    return mark_all_notifications_read(
        recipient_type=STAFF_RECIPIENT_TYPE, recipient_ref=staff_recipient_ref(principal))
