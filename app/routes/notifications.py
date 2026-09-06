"""The staff notification inbox — ``/notifications``.

WHY NOT ``/admin/notifications``. The generic ``^/admin`` middleware rule demands ``identity.manage``,
which only the Administrator holds, so putting a personal inbox there would have hidden every staff
member's own notifications from them. ``/notifications`` matches NO pattern in the RULES map, so —
exactly like ``/communications`` and ``/engagement`` — each endpoint enforces its own capability
in-route. No middleware rule is added, and nothing about ``/admin`` changes.

CAPABILITY. ``communications.message.read`` (msgcap01), the SAME gate the Messages work queue uses.
The ledger's only staff notification type today is ``portal.secure_message``, so the audience for
this page is exactly the audience for the conversations it points at — the six roles that already
open Messages. Nothing new is granted. WHEN A SECOND STAFF NOTIFICATION TYPE IS ADDED, this gate must
be revisited: a scheduling or communications notification would reach a user this capability does not
admit. Recorded as a limitation rather than pre-solved.

The mark-read POSTs require the same ``.read`` capability rather than a write one. Marking your OWN
notification read is not a write to any client record, and requiring ``.write`` would stop a view-only
role (Tax Staff) from clearing its own inbox. The path matches no RULES pattern, so the ``.read`` ->
``.write`` inference never runs here; ``require_capability`` also makes these routes self-protected,
which is what the fail-closed staff-mutation layer requires.

Every read and write is scoped to the caller inside ``notification_inbox`` — the recipient is part of
every WHERE clause, so this surface can neither show nor modify another user's notifications.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.notification_inbox import mark_all_read, mark_read, staff_notifications
from app.templating import install_filters

router = APIRouter(tags=["notifications"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)

#: The one capability this surface enforces (see the module docstring).
CAPABILITY = "communications.message.read"


@router.get("/notifications", response_class=HTMLResponse)
def notification_inbox(request: Request, unread: str | None = None,
                       principal: Principal = Depends(require_capability(CAPABILITY))):
    """The signed-in staff user's OWN notifications, newest first."""
    result = staff_notifications(principal, unread_only=(unread == "1"))
    return templates.TemplateResponse(
        request=request, name="notifications/inbox.html",
        context={"principal": principal, **result,
                 "notice": request.query_params.get("notice")})


@router.post("/notifications/{notification_id}/read")
def notification_mark_read(notification_id: int, request: Request,
                           principal: Principal = Depends(require_capability(CAPABILITY))):
    """Mark one of the caller's own notifications read. Another user's id changes nothing."""
    mark_read(principal, notification_id)
    return RedirectResponse(_back(request), status_code=303)


@router.post("/notifications/read-all")
def notification_mark_all_read(request: Request,
                              principal: Principal = Depends(require_capability(CAPABILITY))):
    """Mark every unread notification belonging to the caller read."""
    changed = mark_all_read(principal)
    return RedirectResponse(_back(request, notice=f"{changed} marked read"), status_code=303)


def _back(request, *, notice: str | None = None) -> str:
    """Return to the inbox, preserving the unread filter. Never reads a caller-supplied URL — the
    destination is built here, so these POSTs cannot be turned into a redirect gadget."""
    base = "/notifications?unread=1" if request.query_params.get("unread") == "1" else "/notifications"
    if notice is None:
        return base
    from urllib.parse import quote
    return f"{base}{'&' if '?' in base else '?'}notice={quote(notice)}"
