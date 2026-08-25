"""Reviewed import of a forwarded lead email into canonical Client360 records.

Every value here has already been shown to a human and confirmed. Nothing in this module trusts the
preview screen: on submit it re-resolves the principal's mailbox, re-acquires the token, re-fetches
the message and its attachment metadata, re-checks person scope, re-runs the duplicate-person check
and re-checks import provenance -- then writes. A GET preview is a suggestion; this is the authority.

Writes exactly two kinds of canonical record and no others: a ``people`` row (``contact_type =
prospect``) via :func:`create_prospect`, and ``documents`` rows via the existing canonical uploader
``save_workspace_document``. No opportunity, no task, no second contact table, no second email store.

Idempotency is ADR-072 ``document_sources``: one attachment is identified for all time by
``microsoft365_mail`` + ``outlook:message/{message_id}/attachment/{attachment_id}``. Re-submitting
the same form imports nothing twice. Filenames are never used for this -- two attachments can share
a name, and one attachment can be renamed.
"""
from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import quote

import requests
from sqlalchemy import select

from app.db import documents, engine, people
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope
from app.security.identity_utils import normalize_email
from app.services.document_sources import _ds, add_source_reference
from app.services.documents import save_workspace_document
from app.services.people import _normalize_phone
from app.services.timeline import add_timeline_event

GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
SOURCE_SYSTEM = "microsoft365_mail"
#: Metadata only. contentBytes is fetched per attachment, and only for the ones staff ticked.
ATTACHMENT_LIST_SELECT = "id,name,contentType,size,isInline"
#: Graph inline attachment limit territory; the same ceiling the document email path uses.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


class LeadImportError(RuntimeError):
    """A user-facing refusal, safe to show staff verbatim."""


class NotAccessible(LeadImportError):
    """Missing OR out of scope. Never distinguishes the two -- existence is not disclosed."""


def _rid(request_id):
    return request_id or f"lead-import-{uuid.uuid4()}"


def source_uri_for(message_id: str, attachment_id: str) -> str:
    """The permanent identity of one attachment of one message. The idempotency key."""
    return f"outlook:message/{message_id}/attachment/{attachment_id}"


def already_imported(conn, message_id: str, attachment_id: str) -> int | None:
    """The document id this attachment was previously imported as, or None.

    Looks the attachment up by SOURCE IDENTITY, not by filename: two different attachments may share
    a filename, and the same attachment may be renamed between imports.
    """
    ds = _ds()
    return conn.execute(
        select(ds.c.document_id).where(ds.c.source_system == SOURCE_SYSTEM,
                                       ds.c.source_uri == source_uri_for(message_id, attachment_id))
    ).scalars().first()


# --- canonical prospect creation ------------------------------------------------------------------

def find_person_by_email(conn, email: str | None) -> int | None:
    norm = normalize_email(email) or None
    if not norm:
        return None
    return conn.execute(
        select(people.c.id).where(people.c.normalized_email == norm)).scalars().first()


def create_prospect(principal, *, first_name, last_name, email, phone=None,
                    request_id=None, source="microsoft365_lead") -> int:
    """Create ONE ``people`` row for a new prospect. The canonical path -- not a fourth ad-hoc insert.

    Requires ``client.write``. Both a surname and an email address are required: a prospect with
    neither is an unidentifiable record, and the whole point of the review step is that a human
    supplied them. Normalisation reuses the functions that write ``normalized_email`` /
    ``normalized_phone`` so later exact-match lookups actually match.

    An exact normalised-email duplicate is refused rather than merged -- the caller must go back and
    use the existing person.
    """
    if not principal.can("client.write"):
        raise LeadImportError("Creating a client record requires client.write.")
    first_name = (first_name or "").strip() or None
    last_name = (last_name or "").strip() or None
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    if not last_name:
        raise LeadImportError("A last name is required to create a prospect.")
    if not email:
        raise LeadImportError("An email address is required to create a prospect.")

    norm_email = normalize_email(email)
    full_name = " ".join(p for p in (first_name, last_name) if p)
    with engine.begin() as c:
        # Re-checked inside the write transaction: the preview may be minutes old, and another
        # staff member may have created this person in the meantime.
        if find_person_by_email(c, email) is not None:
            raise LeadImportError(
                "A client with this email address already exists. Use the existing person instead.")
        pid = c.execute(people.insert().values(
            first_name=first_name, last_name=last_name, full_name=full_name or None,
            primary_email=email, normalized_email=norm_email,
            primary_phone=phone, normalized_phone=_normalize_phone(phone),
            contact_type="prospect", active=True,
            created_by_user_id=principal.user_id, updated_by_user_id=principal.user_id,
        ).returning(people.c.id)).scalar_one()
        # Same fact the other canonical creation path publishes.
        from app.services.events import publisher
        publisher.publish_safe("people.person_created", {"person_id": pid}, conn=c,
                               producer="microsoft.lead_import", subject_ref=f"person:{pid}")

    write_audit_event(action="person.created", entity_type="person", entity_id=pid,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"source": source, "contact_type": "prospect"})
    add_timeline_event(person_id=pid, source="microsoft", event_type="person_created",
                       title="Prospect created from email",
                       summary=full_name or email, event_metadata={"source": source})
    return pid


# --- Graph reads -----------------------------------------------------------------------------------

def _graph_get(url, token, **params):
    return requests.get(url, headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/json"},
                        params=params or None, timeout=30)


def fetch_message(token: str, message_id: str) -> dict:
    """The reviewed message, re-read from the PRINCIPAL'S OWN mailbox on submit."""
    r = _graph_get(f"{GRAPH_MESSAGES_URL}/{quote(message_id, safe='')}", token,
                   **{"$select": "id,subject,from,sender,receivedDateTime,hasAttachments"})
    if r.status_code in (400, 404):
        raise NotAccessible("Message not found.")
    if not r.ok:
        raise LeadImportError(f"Outlook request failed (status {r.status_code}).")
    return r.json()


def eligible_attachments(token: str, message_id: str) -> dict[str, dict]:
    """Importable attachments of THIS message, keyed by Graph attachment id.

    Inline signature images and non-file attachment types are excluded here, so an id that is merely
    present on the message is still not importable unless it is a real file. This is also the
    membership check: an id absent from this map came from somewhere other than the reviewed message.
    """
    r = _graph_get(f"{GRAPH_MESSAGES_URL}/{quote(message_id, safe='')}/attachments", token,
                   **{"$select": ATTACHMENT_LIST_SELECT})
    if r.status_code in (400, 404):
        raise NotAccessible("Message not found.")
    if not r.ok:
        raise LeadImportError(f"Outlook request failed (status {r.status_code}).")
    out = {}
    for a in r.json().get("value", []):
        odata = a.get("@odata.type") or ""
        if a.get("isInline") or odata.endswith(("itemAttachment", "referenceAttachment")):
            continue
        if a.get("id"):
            out[a["id"]] = a
    return out


def _attachment_bytes(token: str, message_id: str, attachment_id: str) -> bytes:
    """contentBytes for ONE attachment. Called only for ids staff explicitly selected."""
    r = _graph_get(f"{GRAPH_MESSAGES_URL}/{quote(message_id, safe='')}"
                   f"/attachments/{quote(attachment_id, safe='')}", token)
    if not r.ok:
        raise LeadImportError(f"Attachment could not be read (status {r.status_code}).")
    raw = r.json().get("contentBytes")
    if not raw:
        raise LeadImportError("Attachment has no content.")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LeadImportError("Attachment content was not readable.") from exc


# --- the reviewed import ----------------------------------------------------------------------------

def import_reviewed_lead(principal, *, token, message_id, person_id=None, create_new=False,
                         first_name=None, last_name=None, email=None, phone=None,
                         attachment_ids=(), request_id=None) -> dict:
    """Resolve the person, then import the selected attachments. Returns a summary for the UI.

    Nothing from the preview is trusted: the message and its attachments are re-fetched on the
    principal's own token, every selected id is re-verified as an importable attachment OF THIS
    MESSAGE, person scope is re-checked, and provenance is re-consulted before each write.
    """
    rid = _rid(request_id)
    message = fetch_message(token, message_id)          # also proves the mailbox owns it
    sender = (message.get("from") or message.get("sender") or {}).get("emailAddress") or {}

    # --- who ----------------------------------------------------------------------------------
    if person_id:
        # WRITE scope, and the same not-found wording either way so scope never discloses existence.
        if not record_in_scope(principal, "person", int(person_id), write=True):
            raise NotAccessible("Client not found.")
        with engine.connect() as c:
            if c.execute(select(people.c.id)
                         .where(people.c.id == int(person_id))).scalars().first() is None:
                raise NotAccessible("Client not found.")
        resolved_person_id, created = int(person_id), False
    elif create_new:
        resolved_person_id = create_prospect(
            principal, first_name=first_name, last_name=last_name, email=email, phone=phone,
            request_id=rid)
        created = True
    else:
        raise LeadImportError("Choose an existing client or create a new prospect.")

    # --- what ---------------------------------------------------------------------------------
    eligible = eligible_attachments(token, message_id) if attachment_ids else {}
    unknown = [a for a in attachment_ids if a not in eligible]
    if unknown:
        # An id that is not an importable attachment of THIS message is rejected outright rather
        # than skipped, so a tampered form fails loudly instead of half-succeeding.
        raise LeadImportError("One or more selected attachments do not belong to this message.")

    imported, skipped = [], []
    for aid in attachment_ids:
        meta = eligible[aid]
        with engine.connect() as c:
            existing = already_imported(c, message_id, aid)
        if existing is not None:
            skipped.append({"attachment_id": aid, "name": meta.get("name"),
                            "document_id": existing})
            continue
        if (meta.get("size") or 0) > MAX_ATTACHMENT_BYTES:
            raise LeadImportError(f"{meta.get('name')} is too large to import.")

        payload = _attachment_bytes(token, message_id, aid)
        document_id = save_workspace_document(
            owner_type="person", owner_id=resolved_person_id,
            original_name=meta.get("name") or "attachment",
            source=BytesIO(payload), content_type=meta.get("contentType"),
            uploaded_by=principal.email or str(principal.user_id), verify_content=True)

        with engine.begin() as c:
            sha = c.execute(select(documents.c.sha256)
                            .where(documents.c.id == document_id)).scalars().first()
            add_source_reference(
                c, document_id, source_system=SOURCE_SYSTEM,
                source_uri=source_uri_for(message_id, aid), source_external_id=aid,
                source_hash=sha,
                # Identifiers and provenance only -- never bytes, never a token.
                metadata_json={
                    "graph_message_id": message_id,
                    "attachment_id": aid,
                    "attachment_name": meta.get("name"),
                    "subject": message.get("subject"),
                    "forwarder_name": sender.get("name"),
                    "forwarder_email": sender.get("address"),
                    "candidate_email": email,
                    "imported_by_user_id": principal.user_id,
                    "imported_at": datetime.now(UTC).isoformat(),
                })
        imported.append({"attachment_id": aid, "name": meta.get("name"),
                         "document_id": document_id})

    write_audit_event(
        action="lead.imported", entity_type="person", entity_id=resolved_person_id,
        actor_user_id=principal.user_id, request_id=rid,
        metadata={"graph_message_id": message_id, "subject": message.get("subject"),
                  "forwarder_email": sender.get("address"), "person_created": created,
                  "documents_imported": [d["document_id"] for d in imported],
                  "documents_skipped": [d["document_id"] for d in skipped]})

    return {"person_id": resolved_person_id, "person_created": created,
            "imported": imported, "skipped": skipped,
            "workspace_url": f"/client/{resolved_person_id}?tab=documents"}
