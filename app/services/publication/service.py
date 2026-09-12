"""Publish a canonical document to a client audience — without copying a single byte.

A publication is a REFERENCE plus a decision. The file stays exactly where ingestion put it, in one
canonical ``documents`` row with its OCR, classification and version history intact; this module adds
a row saying who may see it, who decided that, and how to withdraw it.

THE RULE THIS MODULE EXISTS TO ENFORCE. Ownership is not visibility. ``documents.person_id`` records
who a file belongs to. It has never recorded who may READ it, and it cannot be made to: the canonical
resolver deduplicates by content hash and fills only NULL ownership, so a single row can legitimately
be the same file for two unrelated clients while carrying one owner. Reading access off that owner
would hand a client someone else's document the first time a hash collided — which production data
already contains. Client access therefore derives ONLY from a publication row, and publishing the
same canonical document to two audiences creates two independent, separately audited rows.

AUTHORITY. Publishing reuses ``vault.manage`` — the capability that already governs "may this person
decide what a client sees", held by administrator, executive and the compliance roles. No new
capability is introduced, because no new KIND of authority is being exercised. Record scope is
checked against the audience as well, so holding ``vault.manage`` does not let anyone publish to a
client outside their own book.

FAILS CLOSED. When ``docpub01`` has not been applied the tables bind to None; every read returns
nothing and every write refuses. An environment mid-migration serves no publication rather than
erroring at import.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from app.db import (
    document_publication_events,
    document_publications,
    documents,
    engine,
    households,
    people,
)
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope

#: Audience anchors, and the column each one writes. Mirrors the CHECK constraint in docpub01 —
#: one map so a new audience type cannot be added here and forgotten in the schema.
AUDIENCE_COLUMNS = {"person": "person_id", "household": "household_id",
                    "organization": "organization_id"}

DECISION_SOURCES = ("staff_manual", "policy_preview", "import_rule", "migration_backfill")

#: Canonical document states that may never be published. Checked at publish time AND re-checked on
#: every client read, so a document deleted or archived AFTER publication disappears from the client
#: immediately without anybody having to remember to revoke it.
_EXCLUDED_DOCUMENT_STATUSES = ("deleted", "archived")

PUBLISH_CAPABILITY = "vault.manage"


class PublicationError(RuntimeError):
    """Base error for the publication service."""


class PublicationNotFound(PublicationError):
    """No such publication, or no such canonical document."""


class PublicationPermissionError(PublicationError):
    """The principal may not make this publication decision."""


class PublicationUnavailable(PublicationError):
    """The docpub01 migration has not been applied in this environment."""


def _require_tables():
    if document_publications is None or document_publication_events is None:
        raise PublicationUnavailable(
            "Document publication is unavailable: migration docpub01 has not been applied.")


def available() -> bool:
    """Whether this environment can serve publications at all."""
    return document_publications is not None and document_publication_events is not None


# --- the live predicate ------------------------------------------------------
#
# "Live and visible" is ONE definition, used by the portal list, the download authorization and the
# uniqueness indexes alike. Writing it once is the point: an exclusion that appears in the list query
# but not the download query is exactly how a document becomes un-listable but still fetchable.

def live_clause():
    """Publication is neither revoked nor archived."""
    return and_(document_publications.c.revoked_at.is_(None),
                document_publications.c.archived_at.is_(None))


def visible_clause():
    """Publication is live AND carries a standing client-visible decision."""
    return and_(live_clause(), document_publications.c.client_visible.is_(True))


def document_readable_clause():
    """The CANONICAL document is itself in a state a client may see.

    Delegates to ``document_platform.lifecycle.active_unarchived_clause`` — the ONE definition of
    "this still belongs on a client's list" — rather than restating it. That module exists because
    deletion and archiving are each written two independent ways, and the second copy of a predicate
    is where the two representations drift apart into a leak. Its own header says every read that
    answers "what documents does this client have" must use it; a publication read is exactly that
    question, asked through a grant.

    Re-checked on every read rather than trusted from publish time, so deleting or archiving a
    document withdraws client access on its own, without anyone remembering to revoke.
    """
    from app.services.document_platform.lifecycle import active_unarchived_clause

    return active_unarchived_clause()


# --- authorization -----------------------------------------------------------

def _authorize_decision(principal, audience_type, audience_id):
    """Two independent gates, and both must pass.

    ``vault.manage`` is the DECISION authority — "may this person decide what a client sees" — and
    record scope BOUNDS it to the clients whose records this person may reach. Neither substitutes
    for the other: firm-wide record scope without the capability publishes nothing, and the
    capability without record scope cannot reach a client outside the holder's own book.

    Record scope is checked against the AUDIENCE, not against the document's owner. The decision
    being made is "this client may see this", so the client is the record that must be in scope —
    and reading it off the document's owner is precisely the mistake that lets a deduplicated
    canonical row authorize the wrong person.

    The scope check is a READ check. Publishing mutates no part of the person or household record;
    it records a disclosure decision about them. "You may see this client's file" is the right
    precondition for deciding what that client may see, and it is the same authority
    ``record.read_all`` already expresses.
    """
    if not principal.can(PUBLISH_CAPABILITY):
        raise PublicationPermissionError(f"Capability required: {PUBLISH_CAPABILITY}.")
    scope_type = {"person": "person", "household": "household"}.get(audience_type)
    if scope_type is None:
        # Organizations have no record-scope model; the capability is the whole gate, as it is for
        # every other organization-anchored surface.
        return
    if not record_in_scope(principal, scope_type, audience_id, write=False):
        raise PublicationPermissionError(f"{audience_type.title()} is out of your record scope.")


# --- helpers -----------------------------------------------------------------

def _load_document(conn, document_id):
    doc = conn.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
    if doc is None:
        raise PublicationNotFound(f"Canonical document {document_id} not found.")
    return doc


def _audience_exists(conn, audience_type, audience_id) -> bool:
    if audience_type == "person":
        return conn.scalar(select(people.c.id).where(people.c.id == audience_id)) is not None
    if audience_type == "household":
        return conn.scalar(select(households.c.id).where(households.c.id == audience_id)) is not None
    return audience_id is not None      # organizations live in relationship_entities; FK enforces it


def resolved_document_type(conn, document_id) -> str | None:
    """The document's type AS OF NOW — classifier verdict first, canonical category second.

    Copied onto the publication so a later reclassification cannot silently change what a client was
    granted. A publication records the decision that was actually made.
    """
    from app.db import metadata
    classifications = metadata.tables.get("document_classifications")
    if classifications is not None:
        doc_type = conn.scalar(select(classifications.c.doc_type).where(
            classifications.c.document_id == document_id))
        if doc_type and doc_type != "unknown":
            return doc_type
    return conn.scalar(select(documents.c.category).where(documents.c.id == document_id))


def _record_event(conn, *, publication_id, document_id, action, audience_type=None, audience_id=None,
                  client_visible=None, actor_user_id=None, ip_address=None, metadata=None):
    conn.execute(document_publication_events.insert().values(
        publication_id=publication_id, document_id=document_id, action=action,
        audience_type=audience_type, audience_id=audience_id, client_visible=client_visible,
        actor_user_id=actor_user_id, ip_address=ip_address, metadata_json=metadata or {}))
    # Also written to the firm-wide hash-chained audit log, in the SAME transaction, so a
    # publication decision appears in the one place a compliance review actually looks.
    write_audit_event(action=f"document.publication.{action}", entity_type="document_publication",
                      entity_id=publication_id, actor_user_id=actor_user_id, request_id="publication",
                      ip_address=ip_address, conn=conn,
                      metadata={"document_id": document_id, "audience_type": audience_type,
                                "audience_id": audience_id, "client_visible": client_visible,
                                **(metadata or {})})


# --- mutations ---------------------------------------------------------------

def publish(principal, document_id, *, audience_type, audience_id, client_visible=False,
            decision_source="staff_manual", note=None, actor_user_id=None, ip_address=None) -> int:
    """Publish an EXISTING canonical document to one audience. Creates no document row and copies
    no bytes; the only write to the documents table is none at all.

    Returns the publication id. Re-publishing a document to an audience that already has a LIVE
    publication updates that row's decision rather than creating a second one — the partial unique
    index in docpub01 makes that the only representable outcome, and the update is audited.
    """
    _require_tables()
    if audience_type not in AUDIENCE_COLUMNS:
        raise PublicationError(f"Unknown audience type {audience_type!r}.")
    if decision_source not in DECISION_SOURCES:
        raise PublicationError(f"Unknown decision source {decision_source!r}.")
    if audience_id is None:
        raise PublicationError(f"A {audience_type} publication needs a {audience_type} id.")

    _authorize_decision(principal, audience_type, audience_id)

    with engine.begin() as conn:
        doc = _load_document(conn, document_id)
        if doc["status"] in _EXCLUDED_DOCUMENT_STATUSES or doc["deleted_at"] is not None \
                or doc["archived"]:
            raise PublicationError(
                f"Canonical document {document_id} is {doc['status']} and cannot be published.")
        if not _audience_exists(conn, audience_type, audience_id):
            raise PublicationNotFound(f"{audience_type.title()} {audience_id} not found.")

        column = AUDIENCE_COLUMNS[audience_type]
        existing = conn.execute(
            select(document_publications).where(
                document_publications.c.document_id == document_id,
                getattr(document_publications.c, column) == audience_id,
                live_clause())).mappings().first()

        now = datetime.now(UTC)
        if existing is not None:
            if bool(existing["client_visible"]) == bool(client_visible):
                return existing["id"]           # already decided this way; nothing to audit
            conn.execute(document_publications.update()
                         .where(document_publications.c.id == existing["id"])
                         .values(client_visible=bool(client_visible), updated_at=now, note=note))
            _record_event(conn, publication_id=existing["id"], document_id=document_id,
                          action="visibility_granted" if client_visible else "visibility_withdrawn",
                          audience_type=audience_type, audience_id=audience_id,
                          client_visible=bool(client_visible), actor_user_id=actor_user_id,
                          ip_address=ip_address)
            return existing["id"]

        values = {
            "document_id": document_id, "audience_type": audience_type,
            "client_visible": bool(client_visible), "decision_source": decision_source,
            "document_type": resolved_document_type(conn, document_id),
            "tax_year": doc["tax_year"], "note": note,
            "created_by_user_id": actor_user_id, "created_at": now, "updated_at": now,
            column: audience_id,
        }
        publication_id = conn.execute(
            document_publications.insert().values(**values)
            .returning(document_publications.c.id)).scalar_one()
        _record_event(conn, publication_id=publication_id, document_id=document_id,
                      action="published", audience_type=audience_type, audience_id=audience_id,
                      client_visible=bool(client_visible), actor_user_id=actor_user_id,
                      ip_address=ip_address,
                      metadata={"decision_source": decision_source})
        return publication_id


def _load_publication(conn, publication_id):
    row = conn.execute(select(document_publications).where(
        document_publications.c.id == publication_id)).mappings().first()
    if row is None:
        raise PublicationNotFound(f"Publication {publication_id} not found.")
    return row


def _audience_of(row):
    return row["audience_type"], row[AUDIENCE_COLUMNS[row["audience_type"]]]


def revoke(principal, publication_id, *, actor_user_id=None, ip_address=None, note=None):
    """Withdraw a publication. One column write, no cascade, and the row survives as evidence that
    the document WAS published — which is the fact an audit needs."""
    _require_tables()
    with engine.begin() as conn:
        row = _load_publication(conn, publication_id)
        audience_type, audience_id = _audience_of(row)
        _authorize_decision(principal, audience_type, audience_id)
        if row["revoked_at"] is not None:
            return publication_id
        now = datetime.now(UTC)
        conn.execute(document_publications.update()
                     .where(document_publications.c.id == publication_id)
                     .values(revoked_at=now, revoked_by_user_id=actor_user_id, updated_at=now,
                             client_visible=False))
        _record_event(conn, publication_id=publication_id, document_id=row["document_id"],
                      action="revoked", audience_type=audience_type, audience_id=audience_id,
                      client_visible=False, actor_user_id=actor_user_id, ip_address=ip_address,
                      metadata={"note": note} if note else None)
        return publication_id


def archive(principal, publication_id, *, actor_user_id=None, ip_address=None):
    """Retire a publication. Distinct from revoke: revoke is a withdrawal decision, archive is
    housekeeping. Both exclude the row from every client read."""
    _require_tables()
    with engine.begin() as conn:
        row = _load_publication(conn, publication_id)
        audience_type, audience_id = _audience_of(row)
        _authorize_decision(principal, audience_type, audience_id)
        if row["archived_at"] is not None:
            return publication_id
        now = datetime.now(UTC)
        conn.execute(document_publications.update()
                     .where(document_publications.c.id == publication_id)
                     .values(archived_at=now, updated_at=now))
        _record_event(conn, publication_id=publication_id, document_id=row["document_id"],
                      action="archived", audience_type=audience_type, audience_id=audience_id,
                      client_visible=bool(row["client_visible"]), actor_user_id=actor_user_id,
                      ip_address=ip_address)
        return publication_id


def set_visibility(principal, publication_id, visible, *, actor_user_id=None, ip_address=None):
    """Flip the standing client-visible decision on a live publication."""
    _require_tables()
    with engine.begin() as conn:
        row = _load_publication(conn, publication_id)
        audience_type, audience_id = _audience_of(row)
        _authorize_decision(principal, audience_type, audience_id)
        if row["revoked_at"] is not None or row["archived_at"] is not None:
            raise PublicationError("Publication is revoked or archived; re-publish instead.")
        if bool(row["client_visible"]) == bool(visible):
            return publication_id
        conn.execute(document_publications.update()
                     .where(document_publications.c.id == publication_id)
                     .values(client_visible=bool(visible), updated_at=datetime.now(UTC)))
        _record_event(conn, publication_id=publication_id, document_id=row["document_id"],
                      action="visibility_granted" if visible else "visibility_withdrawn",
                      audience_type=audience_type, audience_id=audience_id,
                      client_visible=bool(visible), actor_user_id=actor_user_id,
                      ip_address=ip_address)
        return publication_id


# --- staff reads -------------------------------------------------------------

def publications_for_document(document_id) -> list[dict]:
    """Every publication of a document, live or not — the staff answer to "who can see this?"."""
    if not available():
        return []
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(document_publications)
            .where(document_publications.c.document_id == document_id)
            .order_by(document_publications.c.id)).mappings().all()]


def events_for_document(document_id) -> list[dict]:
    """The append-only decision ledger for a document."""
    if not available():
        return []
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(document_publication_events)
            .where(document_publication_events.c.document_id == document_id)
            .order_by(document_publication_events.c.id)).mappings().all()]


# --- client reads ------------------------------------------------------------

def _audience_clause(*, person_ids=(), household_ids=(), organization_ids=()):
    """Match ONLY the audiences this caller resolved. An empty set matches nothing, never everything.

    This is the cross-client isolation boundary: the caller passes the audiences its portal grant
    actually reaches, and no row outside that set can be returned regardless of who owns the
    underlying canonical document or what its content hash is.
    """
    clauses = []
    if person_ids:
        clauses.append(document_publications.c.person_id.in_(list(person_ids)))
    if household_ids:
        clauses.append(document_publications.c.household_id.in_(list(household_ids)))
    if organization_ids:
        clauses.append(document_publications.c.organization_id.in_(list(organization_ids)))
    return or_(*clauses) if clauses else None


def client_publications(*, person_ids=(), household_ids=(), organization_ids=()) -> list[dict]:
    """Canonical documents published-and-visible to the given audiences.

    Every exclusion the portal owes a client is applied here, in one query: revoked publication,
    archived publication, not-client-visible publication, deleted canonical document, archived
    canonical document. A caller cannot forget one of them, because there is nothing to remember.
    """
    if not available():
        return []
    audience = _audience_clause(person_ids=person_ids, household_ids=household_ids,
                                organization_ids=organization_ids)
    if audience is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                document_publications.c.id.label("publication_id"),
                document_publications.c.document_id,
                document_publications.c.audience_type,
                document_publications.c.person_id,
                document_publications.c.household_id,
                document_publications.c.organization_id,
                document_publications.c.document_type,
                document_publications.c.tax_year,
                document_publications.c.created_at.label("published_at"),
                documents.c.original_name,
                documents.c.display_name,
                documents.c.category,
                documents.c.size_bytes,
                documents.c.content_type,
                documents.c.created_at.label("document_created_at"),
            )
            .select_from(document_publications.join(
                documents, documents.c.id == document_publications.c.document_id))
            .where(audience, visible_clause(), document_readable_clause())
            .order_by(document_publications.c.created_at.desc())).mappings().all()
    return [dict(r) for r in rows]


def authorized_publication(publication_id, *, person_ids=(), household_ids=(),
                           organization_ids=()) -> dict | None:
    """Resolve ONE publication for a client download, or None.

    Deliberately keyed on the publication id rather than the document id. The publication IS the
    grant, so the id a client holds names something that was decided for them specifically; a
    document id would name a shared object and invite an enumeration attempt against it.

    Returns None for every failure — unknown, revoked, archived, not visible, out of audience,
    document deleted — so the caller has no way to leak which one it was.
    """
    if not available():
        return None
    audience = _audience_clause(person_ids=person_ids, household_ids=household_ids,
                                organization_ids=organization_ids)
    if audience is None:
        return None
    with engine.connect() as conn:
        return conn.execute(
            select(
                document_publications.c.id.label("publication_id"),
                document_publications.c.document_id,
                # Named ``id`` because document_naming.document_delivery_filename falls back to
                # "Document <id>" when no safe label survives, and that id must be the DOCUMENT's.
                documents.c.id.label("id"),
                documents.c.original_name,
                documents.c.display_name,
                documents.c.storage_path,
                documents.c.storage_uri,
                documents.c.storage_provider,
                documents.c.content_type,
            )
            .select_from(document_publications.join(
                documents, documents.c.id == document_publications.c.document_id))
            .where(document_publications.c.id == publication_id,
                   audience, visible_clause(), document_readable_clause())
        ).mappings().first()
