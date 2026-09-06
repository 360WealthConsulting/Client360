"""Ingest inbound Outlook file attachments into canonical documents (Batch 4b, ADR-074).

Batch 4a normalized inbound mail into ``communication_*`` and deliberately deferred attachments: the
sync received only ``hasAttachments``. This fetches the ones it can safely ingest and links them to
the message that carried them.

    Graph attachment  ->  validate  ->  canonical `documents` (+ document_sources provenance)
                      ->  communication_attachments.document_id

WHAT IS SUPPORTED, AND WHAT IS NOT. Only an ordinary ``fileAttachment`` that is not inline. Everything
else is SKIPPED with a recorded reason, never fabricated into a document:

  * ``itemAttachment``      an attached mail/calendar item. Reading it needs an ``$expand`` this
                            module does not perform, and ingesting a nested message is a different
                            problem from ingesting a file.
  * ``referenceAttachment`` a link to OneDrive/SharePoint, not bytes. Following it is a separate
                            integration with its own authorization.
  * ``isInline``            signature logos and embedded images. Filing those as client documents
                            would bury real paperwork under a per-email drizzle of tracking pixels.

NOTHING NEW IS STORED OR SERVED. Bytes go through ``document_sources.resolve_or_create_canonical`` —
the same content-hash path (ADR-072) SharePoint ingestion uses — so an attachment already held under
another provenance is REUSED rather than duplicated, and it is served by the existing authorized
document download. No vault document, no email blob store, no bytes in ``communication_attachments``,
no provider-specific serving path.

ANCHORING IS INHERITED, NEVER RE-DERIVED. The document takes the person/household the EMAIL was
anchored to. The filename is never consulted for ownership — a document called "Smith 1040.pdf"
attached to a message from the Joneses belongs to the Joneses. An email that could not be anchored is
never normalized in the first place (Batch 4a), so its attachments are never fetched: fail-closed by
construction, with no orphan canonical documents.

IDEMPOTENCY comes from the database, not from a read-then-write. ``resolve_or_create_canonical``
dedups on content hash and ``communication_attachments`` carries ``UNIQUE (message_id, document_id)``,
so two racing workers cannot both attach the same document to the same message — the loser's insert is
refused and treated as "already present". Replay is also cheap: a message that already has attachment
rows skips the Graph call entirely, so polling the same mailbox does not re-download.

ONE EMAIL, ONE TIMELINE ROW — unchanged. This module writes no timeline event of any kind. Three
attachments do not become three history entries.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "Email"
STORAGE_PROVIDER = "Client360 Local"
#: The canonical local-copy root for this source, resolved by the SAME helper every other importer
#: uses (per-source env var -> CLIENT360_DATA_ROOT -> legacy default). No new storage location.
DOCUMENT_SOURCE = "Email"
DOCUMENT_ROOT_ENV = "CLIENT360_EMAIL_DOCUMENT_ROOT"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
#: Metadata only. ``contentBytes`` is fetched per attachment, and only for supported ones.
ATTACHMENT_SELECT = "id,name,contentType,size,isInline"

#: Graph returns ``contentBytes`` base64-encoded, so a fetch costs ~1.33x this in memory. The cap
#: bounds one message's cost; a larger attachment is SKIPPED with a reason, never truncated.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
ITEM_ATTACHMENT = "#microsoft.graph.itemAttachment"
REFERENCE_ATTACHMENT = "#microsoft.graph.referenceAttachment"

SUPPORTED = "file"
SKIP_INLINE = "inline"
SKIP_ITEM = "item_attachment"
SKIP_REFERENCE = "reference_attachment"
SKIP_UNKNOWN = "unsupported_type"
SKIP_TOO_LARGE = "too_large"
SKIP_EMPTY = "empty"
SKIP_UNREADABLE = "unreadable"
SKIP_UNSAFE_NAME = "unsafe_filename"


@dataclass
class AttachmentOutcome:
    """What happened to one attachment. Every attachment gets one, supported or not."""
    provider_id: str | None
    name: str
    disposition: str                       # SUPPORTED, or one of the SKIP_* reasons
    document_id: int | None = None
    reused: bool = False


@dataclass
class IngestSummary:
    ingested: int = 0
    reused: int = 0
    skipped: list = field(default_factory=list)     # (name, reason) — enough to audit what was left
    errors: list = field(default_factory=list)

    def as_metadata(self) -> dict:
        """The shape recorded on the message. References and reasons only — never content."""
        return {"ingested": self.ingested, "reused": self.reused,
                "skipped": [{"name": n, "reason": r} for n, r in self.skipped],
                "errors": self.errors}


def classify(attachment: dict) -> str:
    """Which attachments this module will ingest. Everything unrecognised is skipped, not guessed."""
    if attachment.get("isInline"):
        return SKIP_INLINE
    odata = (attachment.get("@odata.type") or "").strip()
    if odata == ITEM_ATTACHMENT or odata.endswith("itemAttachment"):
        return SKIP_ITEM
    if odata == REFERENCE_ATTACHMENT or odata.endswith("referenceAttachment"):
        return SKIP_REFERENCE
    if odata and not (odata == FILE_ATTACHMENT or odata.endswith("fileAttachment")):
        return SKIP_UNKNOWN
    size = attachment.get("size") or 0
    if size and size > MAX_ATTACHMENT_BYTES:
        return SKIP_TOO_LARGE
    return SUPPORTED


def _decode(attachment: dict) -> bytes | None:
    raw = attachment.get("contentBytes")
    if not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None


def _storage_target(sha256: str, name: str):
    """Where the bytes land under the canonical document root.

    Content-addressed, so the same attachment arriving twice writes one file and two source
    references rather than two copies. The ORIGINAL filename is preserved on the document row; only
    the physical location is derived, which is exactly what the SharePoint importer does.
    """
    from pathlib import Path

    from app.importers.sharepoint import sanitize_relative_path
    from app.services.storage_paths import document_root

    safe = sanitize_relative_path(name or "attachment")
    ext = os.path.splitext(safe.parts[-1])[1]
    # Sharded by hash prefix so one directory never accumulates every attachment the firm receives.
    rel = Path(sha256[:2]) / f"{sha256}{ext}"
    root = Path(document_root(DOCUMENT_SOURCE, DOCUMENT_ROOT_ENV))
    return rel, (root / rel)


def already_ingested(conn, communication_message_id: int) -> bool:
    """Whether this message's attachments were already handled — the replay short-circuit.

    Checked BEFORE any Graph call, so scheduled polling over the same mailbox does not re-download
    what it already has.
    """
    from sqlalchemy import select

    from app.db import communication_attachments as attachments
    from app.db import communication_messages as messages

    has_rows = conn.execute(select(attachments.c.id).where(
        attachments.c.message_id == communication_message_id).limit(1)).scalar()
    if has_rows is not None:
        return True
    # A message whose attachments were all SKIPPED has no rows, but must not be re-fetched forever.
    meta = conn.execute(select(messages.c.message_metadata).where(
        messages.c.id == communication_message_id)).scalar() or {}
    return "attachments" in meta


def _record_outcome(conn, communication_message_id: int, summary: IngestSummary) -> None:
    """Stamp the ingest result onto the message so a skip is auditable and never retried blindly."""
    from sqlalchemy import select

    from app.db import communication_messages as messages

    meta = dict(conn.execute(select(messages.c.message_metadata).where(
        messages.c.id == communication_message_id)).scalar() or {})
    meta["attachments"] = summary.as_metadata()
    conn.execute(messages.update().where(
        messages.c.id == communication_message_id).values(message_metadata=meta))


def _attach(conn, *, communication_message_id: int, document_id: int, provider_id: str | None,
            name: str) -> bool:
    """Link a canonical document to the message. Returns whether a NEW row was created.

    ``UNIQUE (message_id, document_id)`` is the race guard: a concurrent worker that got there first
    makes this a no-op rather than a duplicate. ``vault_document_id`` stays NULL — an inbound email
    attachment is internal work product until somebody deliberately publishes it.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.db import communication_attachments as attachments

    existing = conn.execute(select(attachments.c.id).where(
        attachments.c.message_id == communication_message_id,
        attachments.c.document_id == document_id)).scalar()
    if existing is not None:
        return False
    savepoint = conn.begin_nested()
    try:
        conn.execute(attachments.insert().values(
            message_id=communication_message_id, document_id=document_id,
            vault_document_id=None,
            # The provider's own identifier for this attachment. `attachment_ref` is the declared
            # home for a reference the platform does not own, so no Microsoft-specific column is
            # invented for it.
            attachment_ref=provider_id, description=name))
        savepoint.commit()
        return True
    except IntegrityError:
        savepoint.rollback()                 # another worker won the race; its row is equivalent
        return False


def ingest_attachments(conn, *, communication_message_id: int, attachments_payload,
                       anchor: dict, fetch_bytes) -> IngestSummary:
    """Ingest the supported attachments of one already-normalized email.

    ``anchor`` is the EMAIL's resolved ownership (``person_id`` / ``household_id``); it is applied
    verbatim and never re-derived. ``fetch_bytes(provider_id)`` returns the attachment payload
    including ``contentBytes`` — injected so tests never touch the network.

    One attachment failing never abandons the others, and never invalidates the email itself.
    """
    from app.services.document_sources import resolve_or_create_canonical

    summary = IngestSummary()
    for attachment in attachments_payload or []:
        name = attachment.get("name") or "(unnamed)"
        provider_id = attachment.get("id")
        disposition = classify(attachment)
        if disposition != SUPPORTED:
            summary.skipped.append((name, disposition))
            continue
        try:
            payload = fetch_bytes(provider_id) or {}
            data = _decode(payload if "contentBytes" in payload else attachment)
            if not data:
                summary.skipped.append((name, SKIP_EMPTY if data == b"" else SKIP_UNREADABLE))
                continue
            if len(data) > MAX_ATTACHMENT_BYTES:
                summary.skipped.append((name, SKIP_TOO_LARGE))
                continue
            sha256 = hashlib.sha256(data).hexdigest()
            try:
                rel, absolute = _storage_target(sha256, name)
            except ValueError:
                # `sanitize_relative_path` refused the name (traversal, absolute, drive-qualified).
                # Fail closed and say WHY, rather than letting it fall into the generic error bucket.
                summary.skipped.append((name, SKIP_UNSAFE_NAME))
                continue
            absolute.parent.mkdir(parents=True, exist_ok=True)
            if not absolute.exists():                     # content-addressed: identical bytes, one file
                absolute.write_bytes(data)
            result = resolve_or_create_canonical(
                sha256=sha256, original_name=name,
                stored_name=f"email:{sha256}", storage_provider=STORAGE_PROVIDER,
                storage_uri=str(absolute), storage_path=str(rel),
                size_bytes=len(data), content_type=attachment.get("contentType") or None,
                person_id=anchor.get("person_id"), household_id=anchor.get("household_id"),
                source_system=SOURCE_SYSTEM,
                source_uri=f"outlook-attachment:{provider_id}" if provider_id else "",
                source_external_id=provider_id, conn=conn)
            created = _attach(conn, communication_message_id=communication_message_id,
                              document_id=result["document_id"], provider_id=provider_id, name=name)
            if created:
                summary.ingested += 1
            if result.get("reused"):
                summary.reused += 1
        except Exception as exc:              # noqa: BLE001 — one attachment must not sink the rest
            logger.exception("Attachment ingestion failed for %r", name)
            summary.errors.append(f"{name}: {type(exc).__name__}")
    _record_outcome(conn, communication_message_id, summary)
    return summary


# --- Graph transport (injected in tests; never called by them) ------------------------------------

def graph_attachment_reader(access_token: str, graph_message_id: str, *, requests_module=None):
    """``(list_metadata, fetch_bytes)`` for one Graph message. No call is made until invoked."""
    import requests as _requests

    http = requests_module or _requests
    base = f"{GRAPH_MESSAGES_URL}/{graph_message_id}/attachments"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    def list_metadata():
        response = http.get(base, headers=headers, params={"$select": ATTACHMENT_SELECT}, timeout=30)
        if not getattr(response, "ok", False):
            return []
        return (response.json() or {}).get("value", [])

    def fetch_bytes(provider_id):
        if not provider_id:
            return {}
        response = http.get(f"{base}/{provider_id}", headers=headers, timeout=60)
        if not getattr(response, "ok", False):
            return {}
        return response.json() or {}

    return list_metadata, fetch_bytes
