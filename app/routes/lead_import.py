"""Reviewed "Add to 360Plus" import from a forwarded lead email.

WHY THIS LIVES OUTSIDE /microsoft365
------------------------------------
The middleware rewrites a matched ``*.read`` rule to ``*.write`` for any non-GET, so every POST under
``^/microsoft`` demands ``communication.write``. That capability is held by exactly one profile
(client_service), and the profiles that hold ``documents.edit`` -- senior_tax, tax_staff, accounting,
payroll -- do not have it. The intersection is empty, so a reviewed import mounted under
``/microsoft365`` would 403 for every staff profile, including the tax staff this feature exists for.

Rather than widen ``communication.write`` across the whole Microsoft surface to fix one screen, the
mutation is mounted where its capability can describe what it actually does. This operation creates a
client record and canonical documents; it is not a mailbox write. ``/lead-import`` matches no
middleware RULE, which means the fail-closed default applies to it: a mutation under an unmatched
path is DENIED unless the route protects itself. The ``documents.edit`` dependency below is therefore
the authoritative gate, and deleting it would deny the route rather than open it.

The message PREVIEW stays where it was, under ``/microsoft365/mail/{id}``, still read-only and still
``communication.read``.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import prospect_import
from app.services.microsoft_identity import account_for_principal, get_microsoft_access_token
from app.templating import render_error

router = APIRouter()


def _one(form, key):
    return (form.get(key) or [""])[0].strip()


@router.post("/lead-import/{message_id}")
async def lead_import(request: Request, message_id: str,
                      principal: Principal = Depends(require_capability("documents.edit"))):
    """Create/reuse a client and import the attachments staff ticked. Everything is re-validated.

    ``documents.edit`` gates the route because every successful run writes canonical documents;
    ``client.write`` is additionally required inside the service before any person is created, and
    write record scope is required for an existing one. The mailbox is resolved from the principal --
    the form cannot name an account, a mailbox, a storage path or a document owner.
    """
    form = parse_qs((await request.body()).decode("utf-8"))

    # The mailbox is the caller's own, re-resolved on submit. Same fail-closed rules as the preview.
    account = account_for_principal(principal)
    if account is None:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)
    try:
        token = get_microsoft_access_token(account)
    except RuntimeError:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)

    # ONE mutually-exclusive choice: "create_new", or "existing:<id>" naming a person the staff
    # member picked from the reviewed match list. A person id is the only owner the form can carry,
    # and it is authorised (write record scope) before anything is written to it.
    choice = _one(form, "person_choice")
    existing = choice.split(":", 1)[1] if choice.startswith("existing:") else ""
    try:
        result = prospect_import.import_reviewed_lead(
            principal,
            token=token,
            # Opaque Graph id, from the ROUTE and never from the form.
            message_id=message_id,
            person_id=int(existing) if existing.isdigit() else None,
            create_new=(choice == "create_new"),
            first_name=_one(form, "first_name"), last_name=_one(form, "last_name"),
            email=_one(form, "email"), phone=_one(form, "phone"),
            attachment_ids=[a for a in form.get("attachment_ids", []) if a.strip()],
            request_id=getattr(request.state, "request_id", None))
    except prospect_import.NotAccessible as exc:
        return render_error(request, 404, detail=str(exc))
    except prospect_import.LeadImportError as exc:
        return render_error(request, 400, detail=str(exc))

    return RedirectResponse(
        url=(f"{result['workspace_url']}&imported={len(result['imported'])}"
             f"&skipped={len(result['skipped'])}"
             f"&created={'1' if result['person_created'] else '0'}"),
        status_code=303)
