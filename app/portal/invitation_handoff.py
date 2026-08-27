"""One-time, in-process handoff of a freshly minted portal invitation link to the staff who created it.

``invite_portal_account`` stores only ``SHA-256(token)``; the raw token exists once, in memory, at
creation. Without it the invitation is undeliverable — and the platform has no production-capable
email channel to send it (``app/services/notification_providers.py`` registers ``email`` as a
``DisabledNotificationHook``), so the link has to reach the staff member who will pass it to the
client through a channel they trust.

That link is a LIVE credential: anyone holding it can activate the account. It is therefore held
here, and nowhere else:

* never written to the database — ``portal_invitations`` keeps only the hash;
* never written to an audit event, a log line, an exception, or a redirect URL;
* never placed in the staff session cookie. Starlette's session is SIGNED, not encrypted, so a token
  stored there would travel to the browser and back on every request until it was consumed. Only an
  opaque random HANDLE goes in the cookie; the link itself stays server-side;
* removed on the first read, so a refresh or any later page load shows nothing;
* expired on a timer, so an entry nobody collects does not outlive the staff member's next click;
* process-local by design — never replicated, never spilled to disk, and lost on restart. Losing it
  is the safe failure: staff revoke the account and invite again.
"""
from __future__ import annotations

import secrets
import threading
import time

#: How long an uncollected link survives. Long enough to reach the very next page render, short
#: enough that an abandoned tab does not leave a live credential resident in memory.
HANDOFF_TTL_SECONDS = 300

#: Hard cap so a burst of invitations cannot grow the store without bound.
MAX_PENDING = 128

_lock = threading.Lock()
_pending: dict[str, tuple[float, dict]] = {}


def _purge(now: float) -> None:
    """Drop every expired entry. Caller holds the lock."""
    for handle in [h for h, (expires_at, _) in _pending.items() if expires_at <= now]:
        _pending.pop(handle, None)


def stash(payload: dict, *, now: float | None = None) -> str:
    """Hold ``payload`` for exactly ONE read and return an opaque handle."""
    now = time.monotonic() if now is None else now
    handle = secrets.token_urlsafe(16)
    with _lock:
        _purge(now)
        if len(_pending) >= MAX_PENDING:
            # Evict the entry closest to expiry; a live credential is never worth keeping around
            # just because the store is busy.
            _pending.pop(min(_pending, key=lambda h: _pending[h][0]), None)
        _pending[handle] = (now + HANDOFF_TTL_SECONDS, dict(payload))
    return handle


def take(handle: str | None, *, now: float | None = None) -> dict | None:
    """Return the payload for ``handle`` and REMOVE it.

    ``None`` when the handle is unknown, already read, or expired — the three cases are
    deliberately indistinguishable to the caller."""
    if not handle:
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        _purge(now)
        entry = _pending.pop(handle, None)
    return entry[1] if entry else None


def pending_count() -> int:
    """Number of uncollected entries. Diagnostics only — never exposes a handle or a payload."""
    with _lock:
        _purge(time.monotonic())
        return len(_pending)
