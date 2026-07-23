"""Document platform service (Phase D.16) — CRUD, deterministic lifecycle, scope, folders,
retention.

Documents are the authoritative repository. Scope is enforced in-service (this router is outside
the middleware RECORD_PATH): a document is visible via its person/household/organization anchor,
via a relationship to an in-scope entity, or firm-wide with ``record.read_all``; firm/internal
documents with no client anchor are visible to ``documents.view`` holders. Lifecycle (draft →
active → review → approved → superseded → archived; soft-delete + restore) is a deterministic
allowed-transition map and appends to the ``document_events`` log; APPROVED lifecycle events for
client-anchored documents are published to the Activity Timeline (never a metadata edit).
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, or_, select

from app.db import (
    document_events,
    document_folders,
    document_relationships,
    document_retention_policies,
    documents,
    engine,
    households,
    people,
    relationship_entities,
)
from app.security.authorization import (
    accessible_person_ids,
    organization_in_scope,
    record_in_scope,
)
from app.services.timeline import add_timeline_event

CLASSIFICATIONS = frozenset({"client", "compliance", "tax", "insurance", "benefits", "retirement",
                             "estate", "investment", "operations", "marketing", "legal", "hr",
                             "internal", "archived"})
STATUSES = frozenset({"draft", "active", "review", "approved", "superseded", "archived", "deleted"})
_TRANSITIONS = {
    "draft": frozenset({"active", "review"}),
    "active": frozenset({"draft", "review"}),
    "review": frozenset({"draft", "active", "approved"}),
    "approved": frozenset({"active", "review", "superseded"}),
    "superseded": frozenset({"active", "approved"}),
    "archived": frozenset({"draft", "active", "review", "approved", "superseded"}),
}
_UPDATABLE = frozenset({"classification", "subcategory", "folder_id", "retention_policy_id",
                        "effective_date", "expiration_date", "tags", "notes", "category",
                        "description", "owner_user_id", "review_due_at"})


class DocumentError(Exception):
    """Validation/lifecycle error."""


class DocumentNotFound(Exception):
    """Document not found or out of scope."""


def _now():
    return datetime.now(UTC)


# --- scope -------------------------------------------------------------------

def _visible(principal, doc: dict, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if doc.get("person_id") and record_in_scope(principal, "person", doc["person_id"], connection=c):
        return True
    if doc.get("household_id") and record_in_scope(principal, "household", doc["household_id"], connection=c):
        return True
    if doc.get("organization_id") and organization_in_scope(principal, doc["organization_id"], connection=c):
        return True
    for r in c.execute(select(document_relationships).where(
            document_relationships.c.document_id == doc["id"])).mappings():
        if r["entity_type"] == "person" and record_in_scope(principal, "person", r["entity_id"], connection=c):
            return True
        if r["entity_type"] == "household" and record_in_scope(principal, "household", r["entity_id"], connection=c):
            return True
        if r["entity_type"] == "organization" and organization_in_scope(principal, r["entity_id"], connection=c):
            return True
    # Firm/internal document with no client anchor -> visible to documents.view holders.
    return not (doc.get("person_id") or doc.get("household_id") or doc.get("organization_id"))


def _scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    ids = accessible_person_ids(c, principal)
    conds = [and_(documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                  documents.c.organization_id.is_(None))]  # firm docs
    if ids:
        conds.append(documents.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(documents.c.household_id.in_(tuple(hh)))
        conds.append(documents.c.id.in_(select(document_relationships.c.document_id).where(
            document_relationships.c.entity_type == "person",
            document_relationships.c.entity_id.in_(tuple(ids)))))
    return or_(*conds)


# --- CRUD --------------------------------------------------------------------

def create_document(principal, *, original_name, actor_user_id, person_id=None, household_id=None,
                    organization_id=None, classification=None, subcategory=None, status="active",
                    folder_id=None, storage_provider="local", storage_uri=None, stored_name=None,
                    storage_path=None, size_bytes=0, sha256=None, content_type=None,
                    owner_user_id=None, tags=None, notes=None, effective_date=None) -> dict:
    if not (original_name or "").strip():
        raise DocumentError("original_name is required")
    if classification is not None and classification not in CLASSIFICATIONS:
        raise DocumentError(f"unknown classification {classification!r}")
    if status not in ("draft", "active"):
        raise DocumentError("new documents start draft or active")
    with engine.begin() as c:
        _validate_anchors(c, person_id, household_id, organization_id)
        now = _now()
        # stored_name is globally unique (legacy constraint); generate one if not supplied.
        final_stored = stored_name or f"{uuid.uuid4().hex}-{original_name.strip()}"
        row = c.execute(documents.insert().values(
            original_name=original_name.strip(), stored_name=final_stored,
            storage_path=storage_path or (storage_uri or final_stored),
            size_bytes=size_bytes or 0, sha256=sha256 or "", content_type=content_type,
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            classification=classification, subcategory=subcategory, status=status,
            folder_id=folder_id, storage_provider=storage_provider, storage_uri=storage_uri,
            owner_user_id=owner_user_id or actor_user_id, tags=tags or [], notes=notes or "",
            effective_date=effective_date, current_version=1, uploaded_by=actor_user_id,
            created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id,
            created_at=now, updated_at=now).returning(documents)).mappings().one()
        doc = dict(row)
        _event(c, doc["id"], event_type="uploaded", to_status=status, actor=actor_user_id)
        # Seed version 1 in the extended document_versions.
        from app.db import document_versions
        c.execute(document_versions.insert().values(
            document_id=doc["id"], version_number=1, major=1, minor=0, stored_name=doc["stored_name"],
            storage_path=doc["storage_path"], storage_uri=storage_uri, sha256=doc["sha256"],
            size_bytes=doc["size_bytes"], content_type=content_type, author_user_id=actor_user_id,
            is_current=True, created_at=now))
        _publish_timeline(doc, event_type="uploaded", title=f"Document uploaded — {doc['original_name']}")
        # (D.35) Publish the registered business FACT (references only) in the caller's transaction.
        from app.services.events import publisher
        publisher.publish_safe("document.registered",
                               {"document_id": doc["id"], "classification": doc.get("classification") or "",
                                "status": doc["status"]}, conn=c, producer="document.platform",
                               subject_ref=f"document:{doc['id']}")
    return doc


def get_document(principal, document_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
        if row is None or not _visible(principal, dict(row), c):
            return None
        doc = dict(row)
        doc["events"] = [dict(r) for r in c.execute(
            select(document_events).where(document_events.c.document_id == document_id)
            .order_by(document_events.c.occurred_at.desc())).mappings()]
        doc["relationships"] = [dict(r) for r in c.execute(
            select(document_relationships).where(
                document_relationships.c.document_id == document_id)).mappings()]
    from app.services.document_platform import versions
    doc["versions"] = versions.list_versions(document_id)
    return doc


def list_documents(principal, *, classification=None, status=None, folder_id=None, search=None,
                   page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = [documents.c.status != "deleted"]
        if scope is not None:
            conds.append(scope)
        if classification:
            conds.append(documents.c.classification == classification)
        if status:
            conds.append(documents.c.status == status)
        if folder_id:
            conds.append(documents.c.folder_id == folder_id)
        if search:
            conds.append(documents.c.original_name.ilike(f"%{search.strip()}%"))
        where = and_(*conds)
        total = c.scalar(select(func.count()).select_from(documents).where(where))
        rows = [dict(r) for r in c.execute(
            select(documents).where(where).order_by(documents.c.id.desc())
            .limit(page_size).offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def update_document(principal, document_id: int, *, actor_user_id, fields: dict) -> dict:
    if fields.get("classification") and fields["classification"] not in CLASSIFICATIONS:
        raise DocumentError("unknown classification")
    values = {k: v for k, v in fields.items() if k in _UPDATABLE}
    with engine.begin() as c:
        _load_scoped(c, principal, document_id)
        if values:
            values["updated_by_user_id"] = actor_user_id
            values["updated_at"] = _now()
            c.execute(documents.update().where(documents.c.id == document_id).values(**values))
        return _reload(c, document_id)


# --- lifecycle ---------------------------------------------------------------

def set_status(principal, document_id: int, *, new_status: str, actor_user_id, note=None) -> dict:
    if new_status not in _TRANSITIONS:
        raise DocumentError(f"cannot set status {new_status!r} directly")
    with engine.begin() as c:
        doc = _load_scoped(c, principal, document_id)
        if doc["status"] not in _TRANSITIONS[new_status]:
            raise DocumentError(f"cannot move to {new_status} from {doc['status']}")
        return _transition(c, doc, new_status, actor_user_id, note, "status_changed")


def approve(principal, document_id: int, *, actor_user_id, note=None) -> dict:
    with engine.begin() as c:
        doc = _load_scoped(c, principal, document_id)
        if doc["status"] not in _TRANSITIONS["approved"]:
            raise DocumentError(f"cannot approve from {doc['status']}")
        return _transition(c, doc, "approved", actor_user_id, note, "approved")


def archive(principal, document_id: int, *, actor_user_id, note=None) -> dict:
    with engine.begin() as c:
        doc = _load_scoped(c, principal, document_id)
        return _transition(c, doc, "archived", actor_user_id, note, "archived",
                           extra={"archived_at": _now()})


def soft_delete(principal, document_id: int, *, actor_user_id) -> dict:
    with engine.begin() as c:
        doc = _load_scoped(c, principal, document_id)
        return _transition(c, doc, "deleted", actor_user_id, None, "deleted",
                           extra={"deleted_at": _now()}, publish=False)


def restore(principal, document_id: int, *, actor_user_id) -> dict:
    with engine.begin() as c:
        doc = _load_scoped(c, principal, document_id)
        if doc["status"] not in ("archived", "deleted"):
            raise DocumentError("only archived/deleted documents can be restored")
        return _transition(c, doc, "active", actor_user_id, None, "restored",
                           extra={"archived_at": None, "deleted_at": None})


def _transition(c, doc, new_status, actor, note, event_type, *, extra=None, publish=True):
    values = {"status": new_status, "updated_by_user_id": actor, "updated_at": _now()}
    if extra:
        values.update(extra)
    c.execute(documents.update().where(documents.c.id == doc["id"]).values(**values))
    _event(c, doc["id"], event_type=event_type, from_status=doc["status"], to_status=new_status,
           actor=actor, note=note)
    updated = _reload(c, doc["id"])
    # (D.35) Publish the status-change business FACT (references only) in the caller's transaction, only
    # on a genuine change. Archival is a distinct business event.
    if doc["status"] != new_status:
        from app.services.events import publisher
        _et = "document.archived" if new_status == "archived" else "document.status_changed"
        publisher.publish_safe(_et,
                               {"document_id": doc["id"], "from_status": doc["status"], "to_status": new_status},
                               conn=c, producer="document.platform", subject_ref=f"document:{doc['id']}")
    if publish and event_type in ("approved", "archived", "restored"):
        _publish_timeline(updated, event_type=event_type,
                          title=f"Document {event_type} — {updated['original_name']}")
    return updated


# --- folders -----------------------------------------------------------------

def create_folder(principal, *, code, name, actor_user_id, parent_folder_id=None, classification=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(document_folders.c.id).where(document_folders.c.code == code)) is not None:
            raise DocumentError("folder code already exists")
        row = c.execute(document_folders.insert().values(
            code=code, name=name, parent_folder_id=parent_folder_id, classification=classification,
            created_by=actor_user_id).returning(document_folders)).mappings().one()
    return dict(row)


def list_folders() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(document_folders)
                                           .order_by(document_folders.c.name)).mappings()]


# --- retention ---------------------------------------------------------------

def list_retention_policies() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(document_retention_policies)
                                           .order_by(document_retention_policies.c.code)).mappings()]


def create_retention_policy(principal, *, code, name, actor_user_id, retention_years=None,
                            action_on_expiry="review", description=None) -> dict:
    if action_on_expiry not in ("review", "archive", "delete"):
        raise DocumentError("invalid action_on_expiry")
    with engine.begin() as c:
        if c.scalar(select(document_retention_policies.c.id)
                    .where(document_retention_policies.c.code == code)) is not None:
            raise DocumentError("retention policy code already exists")
        row = c.execute(document_retention_policies.insert().values(
            code=code, name=name, retention_years=retention_years, action_on_expiry=action_on_expiry,
            description=description, created_by=actor_user_id)
            .returning(document_retention_policies)).mappings().one()
    return dict(row)


def apply_retention(principal, document_id: int, retention_policy_id: int, *, actor_user_id) -> dict:
    """Attach a retention policy and derive the document's expiration from its effective date +
    the policy's retention_years (deterministic; no fabricated dates when inputs are missing)."""
    with engine.begin() as c:
        doc = _load_scoped(c, principal, document_id)
        policy = c.execute(select(document_retention_policies).where(
            document_retention_policies.c.id == retention_policy_id)).mappings().first()
        if policy is None:
            raise DocumentError("retention policy does not exist")
        values = {"retention_policy_id": retention_policy_id, "updated_by_user_id": actor_user_id,
                  "updated_at": _now()}
        base = doc.get("effective_date") or (doc["created_at"].date() if doc.get("created_at") else None)
        if policy["retention_years"] is not None and base is not None:
            values["expiration_date"] = date(base.year + policy["retention_years"], base.month, base.day)
        c.execute(documents.update().where(documents.c.id == document_id).values(**values))
        return _reload(c, document_id)


# --- internals ---------------------------------------------------------------

def _validate_anchors(c, person_id, household_id, organization_id):
    if person_id is not None and c.scalar(select(people.c.id).where(people.c.id == person_id)) is None:
        raise DocumentError("person does not exist")
    if household_id is not None and c.scalar(
            select(households.c.id).where(households.c.id == household_id)) is None:
        raise DocumentError("household does not exist")
    if organization_id is not None and c.scalar(
            select(relationship_entities.c.id).where(relationship_entities.c.id == organization_id)) is None:
        raise DocumentError("organization does not exist")


def _load_scoped(c, principal, document_id: int) -> dict:
    row = c.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
    if row is None or not _visible(principal, dict(row), c):
        raise DocumentNotFound(str(document_id))
    return dict(row)


def _reload(c, document_id: int) -> dict:
    return dict(c.execute(select(documents).where(documents.c.id == document_id)).mappings().one())


def _event(c, document_id, *, event_type, from_status=None, to_status=None, actor=None, note=None):
    c.execute(document_events.insert().values(
        document_id=document_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor, note=note, occurred_at=_now()))


def _publish_timeline(doc: dict, *, event_type: str, title: str) -> None:
    """Approved lifecycle events for client-anchored documents flow to the Activity Timeline via
    the shared writer (no second event table). Firm/internal documents with no client anchor are
    recorded in document_events only."""
    if doc.get("person_id") is None and doc.get("household_id") is None:
        return
    add_timeline_event(
        source="document", event_type=f"document_{event_type}", title=title,
        person_id=doc.get("person_id"), household_id=doc.get("household_id"),
        external_id=f"document-{doc['id']}-{event_type}-{int(doc['updated_at'].timestamp())}",
        event_metadata={"document_id": doc["id"], "event": event_type})
