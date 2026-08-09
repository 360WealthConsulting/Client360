"""Linkage resolution adapter (PR-4).

Applies an authorized human resolution to a ``linkage`` Exception Engine item (from PR-3), reusing the
existing canonical/apply primitives — it builds NO parallel review or resolution framework and duplicates
NO promotion / household / entity / folder-link logic.

Approved actions: link_person, create_person, link_household, create_household, link_business,
create_business, firm_material, defer.

A positive human-approved resolution, in ONE atomic transaction (canonical + document owner FK + durable
ledger), then the Exception Engine lifecycle:
  * updates only the subject's still-NULL document owner FK(s) (person_id / household_id / organization_id),
    via the existing ``_apply_folder_link`` primitive (person/household) or a NULL-only org update;
  * writes canonical provenance where applicable (person_source_links, via ``resolve_link_to_person`` /
    ``resolve_create_person``; relationship_entities via ``create_named_entity``);
  * records the durable decision in ``folder_resolution_decisions`` (approver, timestamp, evidence
    snapshot, exception linkage, match reason, resulting entity) — approved positives become reusable
    knowledge; reject/defer/ambiguous never do;
  * resolves / updates the exception through its normal engine lifecycle + audit events.

Fail closed: operates only on the subject of that exception; only still-NULL owner FKs are linked; a
conflicting existing owner aborts BEFORE any write; the target entity must exist and match the requested
type; an existing durable active resolution is never silently overwritten (supersede must be explicit);
and an idempotent repeat never duplicates canonical links or knowledge.

Absolutely NO file movement, NO relocation, NO storage_uri change, NO document_sources change.
"""
from __future__ import annotations

from sqlalchemy import and_, select

from app.services.resolution_knowledge import (
    ResolutionConflictError,
    get_current_decision,
    record_decision,
)

_PERSON = {"link_person", "create_person"}
_HOUSEHOLD = {"link_household", "create_household"}
_BUSINESS = {"link_business", "create_business"}
_LINK = {"link_person", "link_household", "link_business"}
_CREATE = {"create_person", "create_household", "create_business"}
_FIRM = "firm_material"
_DEFER = "defer"
_POSITIVE = _PERSON | _HOUSEHOLD | _BUSINESS | {_FIRM}
_ALL = _POSITIVE | {_DEFER}

_OWNER_COL = {
    "link_person": "person_id", "create_person": "person_id",
    "link_household": "household_id", "create_household": "household_id",
    "link_business": "organization_id", "create_business": "organization_id",
}
_ENTITY_TYPE = {
    "link_person": "person", "create_person": "person",
    "link_household": "household", "create_household": "household",
    "link_business": "relationship_entity", "create_business": "relationship_entity",
}
_ENTITY_TABLE = {"person": "people", "household": "households",
                 "relationship_entity": "relationship_entities"}
_OWNER_COLS = ("person_id", "household_id", "organization_id")


class LinkageResolutionError(RuntimeError):
    """A linkage resolution was rejected before any write (bad input / conflict / stale exception)."""


class LinkageConflictError(LinkageResolutionError):
    """A folder document is already owned by a conflicting entity — aborted before partial writes."""


# --------------------------------------------------------------------------- helpers (read-only)

def _folder_docs(conn, documents, source_system, display_name):
    where = and_(documents.c.status != "deleted",
                 documents.c.tags["source_system"].astext == source_system,
                 documents.c.tags["taxdome_folder"].astext == display_name)
    return [dict(m) for m in conn.execute(select(
        documents.c.id, documents.c.person_id, documents.c.household_id,
        documents.c.organization_id).where(where)).mappings()]


def _validate_entity_exists(conn, entity_type, entity_id):
    from app.db import metadata
    table = metadata.tables[_ENTITY_TABLE[entity_type]]
    if conn.execute(select(table.c.id).where(table.c.id == entity_id)).scalar_one_or_none() is None:
        raise LinkageResolutionError(f"{entity_type} id {entity_id} does not exist (invalid target).")


def _assert_owner_ok(docs, owner_col, target_id):
    """Fail closed: no folder document may be owned by a different entity/type than the requested one."""
    others = [c for c in _OWNER_COLS if c != owner_col]
    for d in docs:
        for c in others:
            if d[c] is not None:
                raise LinkageConflictError(
                    f"document {d['id']} already has {c} set — folder is owned differently; aborting.")
        val = d[owner_col]
        if val is not None and (target_id is None or val != target_id):
            raise LinkageConflictError(
                f"document {d['id']} already linked ({owner_col}={val}), conflicts with target "
                f"{target_id}; aborting.")


def _assert_all_unowned(docs):
    for d in docs:
        if any(d[c] is not None for c in _OWNER_COLS):
            raise LinkageConflictError(
                f"document {d['id']} is already owned — cannot mark the subject firm material; aborting.")


def _is_idempotent(current, action, target_id) -> bool:
    if current is None:
        return False
    d = current["decision"]
    if action in _LINK:
        return d == action and current["resulting_entity_id"] == target_id
    if action in _CREATE:
        return d == action                     # entity already created by the prior resolution
    return d == action                         # firm_material / defer


def _evidence_meta(evidence) -> dict:
    if not isinstance(evidence, dict):
        return {}
    return {"confidence": evidence.get("confidence"), "evidence_flags": evidence.get("evidence_flags"),
            "disposition": evidence.get("disposition")}


# --------------------------------------------------------------------------- write helpers (in a txn)

def _create_bare_person(conn, full_name) -> int:
    from app.matching.promote import _create_person
    first, last = (full_name.split(" ", 1) + [""])[:2]
    record = {"first_name": first or None, "middle_name": None, "last_name": last or None,
              "full_name": full_name, "email": None, "normalized_email": None, "phone": None,
              "normalized_phone": None, "address_line_1": None, "address_line_2": None,
              "city": None, "state": None, "postal_code": None}
    return _create_person(conn, record)


def _create_household(conn, households, name) -> int:
    return conn.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()


def _set_org(conn, documents, source_system, display_name, org_id) -> int:
    where = and_(documents.c.status != "deleted",
                 documents.c.tags["source_system"].astext == source_system,
                 documents.c.tags["taxdome_folder"].astext == display_name,
                 documents.c.organization_id.is_(None))
    return conn.execute(documents.update().where(where).values(organization_id=org_id)).rowcount


# --------------------------------------------------------------------------- lifecycle

def _ensure_in_progress(exception_id, principal, actor_user_id, request_id):
    from app.services.exception_engine import InvalidTransitionError, begin_work
    try:
        begin_work(exception_id, principal=principal, actor_user_id=actor_user_id, request_id=request_id)
    except InvalidTransitionError:
        pass   # already in_progress (or terminal) — nothing to do


def _reopen_if_resolved(exception_id, exc_status, idempotent, principal, actor_user_id, request_id):
    """A supersede correction on an already-resolved exception reopens it so the lifecycle re-runs."""
    if exc_status == "resolved" and not idempotent:
        from app.services.exception_engine import InvalidTransitionError, reopen
        try:
            reopen(exception_id, principal=principal, actor_user_id=actor_user_id, request_id=request_id)
        except InvalidTransitionError:
            pass


# --------------------------------------------------------------------------- adapter

def resolve_linkage_exception(exception_id, action, *, principal, actor_user_id=None,
                              target_entity_id=None, source_contact_id=None, name=None,
                              notes=None, supersede=False, request_id=None) -> dict:
    """Apply ``action`` to linkage exception ``exception_id``. Returns a summary dict. Fail-closed and
    idempotent; the canonical + document + ledger writes are one atomic transaction."""
    if action not in _ALL:
        raise LinkageResolutionError(f"unknown action {action!r}; expected one of {sorted(_ALL)}.")
    if principal is None or not principal.can("exception.write"):
        raise LinkageResolutionError("a principal with exception.write is required.")

    from app.db import documents, engine, households, metadata
    exceptions = metadata.tables["exceptions"]
    exception_events = metadata.tables["exception_events"]

    # ---- preflight (read-only): validate subject, target, ownership conflict, idempotency ----
    with engine.connect() as conn:
        exc = conn.execute(select(exceptions.c.domain, exceptions.c.status)
                           .where(exceptions.c.id == exception_id)).mappings().one_or_none()
        if exc is None:
            raise LinkageResolutionError(f"exception {exception_id} not found.")
        if exc["domain"] != "linkage":
            raise LinkageResolutionError(f"exception {exception_id} is not a linkage exception.")
        exc_status = exc["status"]
        if exc_status == "cancelled":
            raise LinkageResolutionError(f"exception {exception_id} is cancelled.")
        meta = conn.execute(select(exception_events.c["metadata"]).where(and_(
            exception_events.c.exception_id == exception_id,
            exception_events.c.event_type == "opened")).order_by(exception_events.c.id)
        ).scalars().first()
        subject = meta.get("subject") if isinstance(meta, dict) else None
        evidence = meta.get("evidence") if isinstance(meta, dict) else None
        if not subject or not subject.get("display_name"):
            raise LinkageResolutionError(f"exception {exception_id} has no subject metadata.")
        system = subject["source_system"]
        stype = subject["subject_type"]
        skey = subject["subject_key"]
        disp = subject["display_name"]

        if action in _LINK:
            if not isinstance(target_entity_id, int) or target_entity_id <= 0:
                raise LinkageResolutionError(f"{action} requires a positive target_entity_id.")
            _validate_entity_exists(conn, _ENTITY_TYPE[action], target_entity_id)

        docs = _folder_docs(conn, documents, system, disp)
        current = get_current_decision(system, stype, skey, conn=conn)
        idempotent = _is_idempotent(current, action, target_entity_id)

        # Fail-closed gates apply only to a genuinely NEW resolution (an idempotent repeat is a no-op).
        # The durable-resolution / status guards precede the owner-conflict check so an existing active
        # resolution reports "supersede required" rather than the downstream owner conflict it implies.
        if not idempotent:
            if current is not None and not supersede:
                raise ResolutionConflictError(
                    f"an active durable resolution ({current['decision']}) already exists for this "
                    "subject; pass supersede=True to correct it (history is retained).")
            if exc_status == "resolved" and not supersede:
                raise LinkageResolutionError(
                    f"exception {exception_id} is already resolved; pass supersede=True to correct it.")
            if action in _OWNER_COL:
                _assert_owner_ok(docs, _OWNER_COL[action], target_entity_id if action in _LINK else None)
            elif action == _FIRM:
                _assert_all_unowned(docs)

    resulting_type = _ENTITY_TYPE.get(action)      # None for firm/defer
    resulting_id = target_entity_id if action in _LINK else None

    # ---- defer: waiting + non-reusable ledger (no canonical/document writes) ----
    if action == _DEFER:
        if not idempotent:
            with engine.begin() as conn:
                record_decision(subject_system=system, subject_type=stype, subject_key=skey,
                                display_name=disp, decision="defer", evidence_snapshot=evidence or {},
                                match_reason=(notes or (evidence or {}).get("held_reason")),
                                evidence_metadata=_evidence_meta(evidence),
                                reviewed_by=principal.display_name, exception_id=exception_id,
                                supersede=supersede, conn=conn)
        _reopen_if_resolved(exception_id, exc_status, idempotent, principal, actor_user_id, request_id)
        _ensure_in_progress(exception_id, principal, actor_user_id, request_id)
        from app.services.exception_engine import InvalidTransitionError, place_waiting
        try:
            place_waiting(exception_id, principal=principal, actor_user_id=actor_user_id,
                          reason=(notes or "deferred for review"), request_id=request_id)
        except InvalidTransitionError:
            pass
        return {"action": _DEFER, "exception_id": exception_id, "idempotent": idempotent,
                "documents_in_subject": len(docs), "resulting_entity_type": None,
                "resulting_entity_id": None}

    # ---- positive / firm ----
    linked_rows = 0
    if not idempotent:
        from app.matching.promote import resolve_create_person, resolve_link_to_person
        from app.services.relationships import create_named_entity
        with engine.begin() as conn:
            docs = _folder_docs(conn, documents, system, disp)          # re-check inside the txn (TOCTOU)
            if action in _OWNER_COL:
                _assert_owner_ok(docs, _OWNER_COL[action], target_entity_id if action in _LINK else None)
            elif action == _FIRM:
                _assert_all_unowned(docs)

            if action == "create_person":
                resulting_id = (resolve_create_person(source_contact_id, conn=conn) if source_contact_id
                                else _create_bare_person(conn, name or disp))
            elif action == "create_household":
                resulting_id = _create_household(conn, households, name or disp)
            elif action == "create_business":
                resulting_id = create_named_entity(conn, "business", name or disp,
                                                   {"origin": "linkage_resolution",
                                                    "exception_id": exception_id, "subject": subject})

            from app.importers.taxdome_drive import _apply_folder_link
            if action in _PERSON:
                linked_rows = _apply_folder_link(conn, documents, disp, None, resulting_id)
                if source_contact_id:      # canonical provenance where applicable
                    resolve_link_to_person(source_contact_id, resulting_id, conn=conn)
            elif action in _HOUSEHOLD:
                linked_rows = _apply_folder_link(conn, documents, disp, resulting_id, None)
            elif action in _BUSINESS:
                linked_rows = _set_org(conn, documents, system, disp, resulting_id)
            # firm_material: NO owner writes — documents are preserved for later Firm handling.

            record_decision(subject_system=system, subject_type=stype, subject_key=skey,
                            display_name=disp, decision=action, resulting_entity_type=resulting_type,
                            resulting_entity_id=resulting_id, evidence_snapshot=evidence or {},
                            match_reason=(notes or (evidence or {}).get("match_reason")),
                            evidence_metadata=_evidence_meta(evidence),
                            reviewed_by=principal.display_name, exception_id=exception_id,
                            supersede=supersede, conn=conn)
    else:
        # idempotent repeat: reuse the prior resulting entity; re-apply NULL-only owner links (no-op if
        # already linked); write NO new ledger row and create NO new entity.
        resulting_type = current["resulting_entity_type"]
        resulting_id = current["resulting_entity_id"]
        if action in _OWNER_COL and resulting_id is not None:
            from app.importers.taxdome_drive import _apply_folder_link
            with engine.begin() as conn:
                if action in _PERSON:
                    linked_rows = _apply_folder_link(conn, documents, disp, None, resulting_id)
                elif action in _HOUSEHOLD:
                    linked_rows = _apply_folder_link(conn, documents, disp, resulting_id, None)
                elif action in _BUSINESS:
                    linked_rows = _set_org(conn, documents, system, disp, resulting_id)

    # ---- exception lifecycle: resolve through the engine ----
    _reopen_if_resolved(exception_id, exc_status, idempotent, principal, actor_user_id, request_id)
    _ensure_in_progress(exception_id, principal, actor_user_id, request_id)
    from app.services.exception_engine import InvalidTransitionError, resolve
    try:
        resolve(exception_id, action, principal=principal, actor_user_id=actor_user_id,
                notes=notes, request_id=request_id)
    except InvalidTransitionError:
        pass   # already resolved (idempotent completion)

    return {"action": action, "exception_id": exception_id, "idempotent": idempotent,
            "resulting_entity_type": resulting_type, "resulting_entity_id": resulting_id,
            "documents_linked": linked_rows, "documents_in_subject": len(docs)}
