"""The signed-in staff user's Outlook inbox, and READ-ONLY detail for one message.

The mailbox shown belongs to the AUTHENTICATED PRINCIPAL. This route used to read whichever
``microsoft_accounts`` row had been updated most recently, so with two connected mailboxes a user
holding ``communication.read`` could be shown a colleague's inbox. Account selection now goes
through the one canonical resolver (``microsoft_identity.account_for_principal``); there is no
fallback to another account.

The detail route is PREVIEW ONLY. It reads one message plus its attachment METADATA and shows who
the message may be about; it creates nothing, imports nothing, downloads no attachment bytes, and
exposes no mutation.
"""
import re
from urllib.parse import quote

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.forwarded_email import extract_candidate
from app.services.microsoft_identity import (
    account_for_principal,
    get_microsoft_access_token,
)
from app.services.prospect_matching import match_for_candidate
from app.templating import render_error

router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")

GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"

#: Everything the preview renders, and nothing else.
MESSAGE_SELECT = (
    "id,subject,from,sender,toRecipients,ccRecipients,receivedDateTime,"
    "body,bodyPreview,hasAttachments,conversationId,internetMessageId,webLink"
)
#: Attachment METADATA only. contentBytes is deliberately absent -- no attachment content is
#: fetched anywhere in this commit.
ATTACHMENT_SELECT = "id,name,contentType,size,isInline"

#: One page of the mail list. Unchanged from the original route.
PAGE_SIZE = 25
LIST_SELECT = ("id,subject,from,receivedDateTime,"
               "bodyPreview,isRead,hasAttachments,webLink")

#: Characters that would let a typed query escape the quoted $search expression, or split the
#: request line. Replaced with a space rather than dropped, so "a\"b" searches for two words
#: instead of silently becoming one.
_SEARCH_UNSAFE = re.compile(r'["\\\r\n]')
#: A search box, not a query language. Long enough for a subject line.
_SEARCH_MAX = 200


def _clean_skip(raw) -> int:
    """A non-negative page offset. Malformed input is 0, never an error and never negative.

    Declared as a string on the route on purpose: ``skip: int`` would make ``?skip=abc`` a 422
    instead of simply showing the first page.
    """
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def _search_term(value: str) -> str:
    """The user's words, safe to interpolate into ``$search="..."``.

    Quotes, backslashes and CR/LF are neutralised so nothing typed here can close the literal, add
    another KQL clause, or split the HTTP request.
    """
    return _SEARCH_UNSAFE.sub(" ", value or "").strip()[:_SEARCH_MAX].strip()


def _bearer(principal):
    """(token, account, redirect) for the principal's OWN mailbox.

    The token comes from the canonical provider, ``get_microsoft_access_token``, which decrypts the
    stored MSAL cache and refreshes silently. That is the same path the sync jobs and the document
    send already use.

    This route used to read ``account["access_token"]`` and ``account["expires_at"]`` directly.
    Both are legacy: since the token-security work (5af14ac) the OAuth callback deliberately writes
    ``access_token=None`` / ``refresh_token=None`` and keeps the refresh token inside
    ``token_cache_encrypted``, so the plaintext column is ALWAYS NULL in production and this page
    redirected to /microsoft365/connect for every user on every visit -- silently completing OAuth
    and landing on /microsoft365/profile. ``expires_at`` was the same class of mistake in waiting: it
    is stamped once at interactive connect and ``persist_token_cache`` never updates it, so it goes
    stale an hour later and never recovers. Token validity is the provider's business, not this
    route's; neither column is read here any more.

    Returns ``(None, None, RedirectResponse)`` when no account is bound to this principal or the
    provider cannot produce a token, so a caller can never proceed without one.
    """
    account = account_for_principal(principal)
    if account is None:
        return None, None, RedirectResponse(url="/microsoft365/connect", status_code=303)
    try:
        return get_microsoft_access_token(account), account, None
    except RuntimeError:
        # The established reconnect condition (RECONNECT_MESSAGE): no cache, no MSAL account, or a
        # silent refresh that failed. Same not-connected behaviour as before.
        return None, None, RedirectResponse(url="/microsoft365/connect", status_code=303)


@router.get("/mail")
def microsoft365_mail(request: Request, q: str = "", skip: str = "0",
                      principal: Principal = Depends(require_capability("communication.read"))):
    """The signed-in user's mailbox: newest 25, a page at a time, or a mailbox-wide search.

    A forwarded lead that arrived earlier in the day is unreachable on a fixed newest-25 list, so
    the page gains two ways past it. Both read the same mailbox-wide ``/me/messages`` collection on
    the same principal-bound bearer.

    Search is deliberately FIRST PAGE ONLY. Graph documents ``$search`` on message collections as
    incompatible with ``$filter``/``$orderby``, capped at 250 results, and paged by
    ``@odata.nextLink`` -- ``$skip`` is not supported alongside it. Rather than invent a workaround
    or round-trip an opaque nextLink, a search returns one page and the template shows no paging
    controls for it, so the UI never offers a Next that cannot work.
    """
    # communication.read is the capability the middleware rule (^/microsoft) already applies to this
    # path; stating it here is what gives the route the principal it needs to pick the mailbox.
    # No connected account for THIS user -> the existing not-connected behaviour. Never another
    # user's mailbox, and never the most recently connected one.
    access_token, account, redirect = _bearer(principal)
    if redirect is not None:
        return redirect

    query = _search_term(q)
    offset = _clean_skip(skip)

    params = {"$top": str(PAGE_SIZE), "$select": LIST_SELECT}
    if query:
        # $search only. Sending $orderby or $filter with it is a Graph 400.
        params["$search"] = f'"{query}"'
    else:
        params["$orderby"] = "receivedDateTime desc"
        # Omitted at offset 0 so an unfiltered first page is byte-identical to the original request.
        if offset:
            params["$skip"] = str(offset)

    response = requests.get(
        GRAPH_MESSAGES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params=params,
        timeout=30,
    )

    if response.status_code == 401:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)

    if not response.ok:
        return render_error(
            request, 500,
            detail=f"Outlook request failed (status {response.status_code}).",
        )

    messages = []
    for message in response.json().get("value", []):
        sender = message.get("from", {}).get("emailAddress", {})
        messages.append({
            "sender_name": sender.get("name") or "Unknown sender",
            "sender_address": sender.get("address") or "",
            "subject": message.get("subject") or "(No subject)",
            "received": message.get("receivedDateTime") or "",
            "preview": message.get("bodyPreview") or "",
            "web_link": message.get("webLink") or "#",
            # The subject now opens the in-app preview instead of leaving for Outlook. The id is an
            # opaque Graph identifier and is percent-encoded so it stays one path segment.
            "detail_url": f"/microsoft365/mail/{quote(message.get('id') or '', safe='')}",
            "is_read": bool(message.get("isRead")),
            "has_attachments": bool(message.get("hasAttachments")),
        })

    searching = bool(query)
    return templates.TemplateResponse(
        request=request,
        name="microsoft365/mail.html",
        context={
            "messages": messages,
            "account_email": account["email"] or "",
            "query": query,
            "searching": searching,
            # No paging controls while searching -- see the docstring; Graph cannot page a $search
            # with $skip, so offering Next would be a button that 400s.
            "skip": offset,
            "prev_skip": max(0, offset - PAGE_SIZE) if offset else None,
            "has_prev": (not searching) and offset > 0,
            "has_next": (not searching) and len(messages) == PAGE_SIZE,
            "next_skip": offset + PAGE_SIZE,
        },
    )


def _addresses(recipients):
    """['Name <addr>' ...] from a Graph recipient collection."""
    out = []
    for r in recipients or []:
        ea = (r or {}).get("emailAddress") or {}
        name, addr = (ea.get("name") or "").strip(), (ea.get("address") or "").strip()
        if name and addr and name.lower() != addr.lower():
            out.append(f"{name} <{addr}>")
        elif addr or name:
            out.append(addr or name)
    return out


def _attachment_rows(payload):
    """Attachment METADATA rows. Never reads contentBytes -- it is not even selected."""
    rows = []
    for a in (payload or {}).get("value", []):
        odata = a.get("@odata.type") or ""
        rows.append({
            "id": a.get("id"),
            "name": a.get("name") or "(unnamed)",
            "content_type": a.get("contentType") or "",
            "size": a.get("size") or 0,
            "is_inline": bool(a.get("isInline")),
            "odata_type": odata,
            # An attached .eml carries the ORIGINAL message and would be authoritative about the
            # prospect, unlike parsed body text. Reading it needs an $expand this commit does not
            # perform, so it is only flagged.
            "is_item_attachment": odata.endswith("itemAttachment"),
        })
    return rows


@router.get("/mail/{message_id}")
def microsoft365_mail_detail(
        request: Request, message_id: str,
        principal: Principal = Depends(require_capability("communication.read"))):
    """READ-ONLY preview of one message from the PRINCIPAL'S OWN mailbox.

    ``message_id`` is treated as an opaque Graph identifier: it is only ever passed to
    ``/me/messages/{id}`` on this principal's token. It never reaches a filesystem path, never
    selects a mailbox, and cannot address another user's message -- ``/me`` is resolved by the
    token, so an id belonging to someone else's mailbox simply 404s. A 404 or an invalid id gets the
    application's ordinary non-disclosing not-found response, identical either way, so the endpoint
    cannot be used to probe whether a message exists in another mailbox.

    Performs no writes of any kind: no person, opportunity, task, document, communication row or
    timeline event, and no attachment bytes.
    """
    access_token, _account, redirect = _bearer(principal)
    if redirect is not None:
        return redirect

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    # requests quotes the id into exactly one path segment; it is never concatenated into a URL by
    # hand and never used as a path on disk.
    message_url = f"{GRAPH_MESSAGES_URL}/{quote(message_id, safe='')}"
    response = requests.get(message_url, headers=headers,
                            params={"$select": MESSAGE_SELECT}, timeout=30)

    if response.status_code == 401:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)
    if response.status_code in (400, 404):
        # Same response for "no such message" and "not in your mailbox" — existence is never
        # disclosed, matching how the rest of the app answers an out-of-scope record.
        return render_error(request, 404, detail="Message not found.")
    if not response.ok:
        return render_error(
            request, 500,
            detail=f"Outlook request failed (status {response.status_code}).")

    message = response.json()

    attachments = []
    if message.get("hasAttachments"):
        att = requests.get(f"{message_url}/attachments", headers=headers,
                           params={"$select": ATTACHMENT_SELECT}, timeout=30)
        # Attachment metadata is supporting detail; if it cannot be read the preview still renders.
        if att.ok:
            attachments = _attachment_rows(att.json())

    sender = (message.get("from") or message.get("sender") or {}).get("emailAddress") or {}
    body = message.get("body") or {}
    candidate = extract_candidate(
        body=body.get("content"),
        body_is_html=(body.get("contentType") or "html").lower() == "html",
        subject=message.get("subject"),
        # Passed as the FORWARDER. extract_candidate never promotes it to the prospect.
        graph_from_name=sender.get("name"),
        graph_from_email=sender.get("address"))

    # Only look for existing people once something was actually detected. With a blank candidate
    # this stays an empty result rather than a query that could echo unrelated client rows back.
    matches = ({"strategy": None, "matches": [], "outcome": "none"}
               if not (candidate["candidate_email"] or candidate["candidate_phone"]
                       or candidate["candidate_name"])
               else match_for_candidate(candidate))

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/mail_detail.html",
        context={
            "subject": message.get("subject") or "(No subject)",
            "received": message.get("receivedDateTime") or "",
            "body_text": (message.get("bodyPreview") or "").strip(),
            "to": _addresses(message.get("toRecipients")),
            "cc": _addresses(message.get("ccRecipients")),
            "conversation_id": message.get("conversationId") or "",
            "internet_message_id": message.get("internetMessageId") or "",
            "web_link": message.get("webLink") or "#",
            "candidate": candidate,
            "matches": matches,
            "attachments": attachments,
            "has_item_attachment": any(a["is_item_attachment"] for a in attachments),
        },
    )
