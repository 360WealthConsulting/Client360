"""Stable source identity: durable owner mappings, and one review per source client.

THE IDEA
--------
An authoritative source does not identify a client by their name. Drake identifies them by a client
id; TaxDome by an account. Once a human — or an authoritative lane — has established that
"TaxDome account X is Client360 household 252", that fact should be recorded against X and never
re-derived. Re-deriving it means a rename, a typo or an OCR result can silently move a client's
documents, which is precisely the failure the authoritative lanes exist to prevent.

So this module does two things:

1. **Names the stable key** for each authoritative source, and keeps it separate from the display
   name. ``subject_key`` is what identity means; ``display_name`` is what a human reads.
2. **Persists and reuses the mapping** through the existing ``folder_resolution_decisions`` ledger.

WHY THAT LEDGER AND NOT A NEW TABLE
------------------------------------
``folder_resolution_decisions`` already is this, exactly: a subject-generic
``(subject_system, subject_type, subject_key)`` identity with a separate ``display_name``; a PARTIAL
UNIQUE index admitting one ACTIVE decision per subject; append-only supersession
(``active``/``superseded_at``/``superseded_by``) so a correction is versioned rather than an
overwrite; a fail-closed CHECK tying each decision to a matching entity type; and a service
(``resolution_knowledge``) that refuses to change an existing active decision unless the caller
passes ``supersede=True`` and that validates the target entity EXISTS before recording anything.

Every requirement a durable authoritative mapping has, that ledger already satisfies. Adding a second
table would have meant two places that answer "who owns this source identity", and the interesting
question would eventually be which one is right.

WHAT THIS MODULE WILL NOT DO
-----------------------------
* It records only ``link_*`` decisions. The ledger's vocabulary also includes ``create_person`` and
  friends, which are decisions a HUMAN records after deciding a client should exist. The pipeline
  never creates an entity, so it never records a create.
* It never maps a blank or unnamed subject. An empty key is not an identity.
* It never supersedes an existing mapping. A change of mapping is an explicit human act through
  ``resolution_knowledge.record_decision(supersede=True)``; an automated writer that could supersede
  is an automated writer that can silently move a client's documents.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select, text

from app.db import documents, metadata
from app.services.document_pipeline_continuous.model import (
    LANE_DRAKE,
    LANE_TAXDOME,
)

log = logging.getLogger(__name__)

DRAKE_SOURCE = "Drake"
TAXDOME_SOURCE = "TaxDome Drive"
SHAREPOINT_SOURCE = "SharePoint"

#: Subject types. Distinct per source so two systems cannot collide on the same key string.
SUBJECT_DRAKE_CLIENT = "drake_client"
SUBJECT_TAXDOME_ACCOUNT = "taxdome_account"
SUBJECT_TAXDOME_FOLDER = "taxdome_folder"

#: Ledger decisions this module is allowed to write. Creates are deliberately absent.
_LINK_DECISION = {"person": "link_person", "household": "link_household",
                  "organization": "link_business"}

#: The ledger's entity vocabulary, and the pipeline's. ``relationship_entity`` IS an organization.
_LEDGER_TO_PIPELINE = {"person": "person", "household": "household",
                       "relationship_entity": "organization"}

_NON_KEY = re.compile(r"[^a-z0-9]+")


class SourceIdentity:
    """A stable source identity, plus the display name a human reads. Never the same string."""

    __slots__ = ("system", "subject_type", "key", "display", "lane")

    def __init__(self, system, subject_type, key, display, lane):
        self.system, self.subject_type = system, subject_type
        self.key, self.display, self.lane = key, display, lane

    @property
    def triple(self):
        return (self.system, self.subject_type, self.key)

    def __repr__(self):      # display name deliberately omitted — it is a client name
        return f"SourceIdentity({self.system!r}, {self.subject_type!r}, key=<{len(self.key)} chars>)"

    def __eq__(self, other):
        return isinstance(other, SourceIdentity) and self.triple == other.triple

    def __hash__(self):
        return hash(self.triple)


def normalise_key(value) -> str:
    """A stable key from a folder or account string: case, spacing and punctuation removed.

    Normalising is what makes ``WHITE, MICHAEL AND DEBRA`` and ``White, Michael & Debra`` ONE identity
    instead of two — the same account written twice by two people. ``&`` is folded to ``and`` because
    that single substitution is the difference in practice, and it is mechanical rather than a
    judgement about names.

    It is NOT a name match. Every character that distinguishes two families survives, so two
    different clients can never normalise together — which is the property that lets this key be
    trusted as an identity rather than a guess."""
    text_value = str(value or "").strip().lower().replace("&", " and ")
    return _NON_KEY.sub("-", text_value).strip("-")


def _sources(conn, document_id):
    ds = metadata.tables.get("document_sources")
    if ds is None:
        return []
    return conn.execute(
        select(ds.c.source_system, ds.c.source_external_id, ds.c.source_path, ds.c.source_uri)
        .where(ds.c.document_id == document_id)).mappings().all()


def source_identity(conn, document_id) -> SourceIdentity | None:
    """The stable authoritative identity for a document, or None when no authoritative source applies.

    Authority order matches ``ownership.detect_lane``: Drake, then TaxDome. SharePoint has no stable
    per-client key in the general case and deliberately returns None — evidence stays evidence."""
    rows = _sources(conn, document_id)
    by_system = {r["source_system"]: r for r in rows}

    drake = by_system.get(DRAKE_SOURCE)
    if drake is not None:
        key = normalise_key(drake["source_external_id"])
        if key:
            return SourceIdentity(DRAKE_SOURCE, SUBJECT_DRAKE_CLIENT, key,
                                  str(drake["source_external_id"]), LANE_DRAKE)
        # A Drake document with no external id has lost the thing that makes it authoritative.
        return None

    taxdome = by_system.get(TAXDOME_SOURCE)
    if taxdome is not None:
        # Prefer the account id: it survives a folder rename, which a folder name does not.
        account = normalise_key(taxdome["source_external_id"])
        if account:
            return SourceIdentity(TAXDOME_SOURCE, SUBJECT_TAXDOME_ACCOUNT, account,
                                  str(taxdome["source_external_id"]), LANE_TAXDOME)
        folder = conn.execute(select(documents.c.tags).where(
            documents.c.id == document_id)).scalar()
        folder_name = (folder or {}).get("taxdome_folder")
        key = normalise_key(folder_name)
        if key:
            return SourceIdentity(TAXDOME_SOURCE, SUBJECT_TAXDOME_FOLDER, key,
                                  str(folder_name), LANE_TAXDOME)
    return None


# --- durable mapping ------------------------------------------------------------------------------

def lookup_mapping(conn, identity: SourceIdentity):
    """The persisted owner for this source identity, or None.

    Returns only an APPROVED POSITIVE resolution — ``get_reusable_resolution`` filters out reject,
    defer, ambiguous and every superseded row. A document whose identity resolves here must never be
    put through name, filename or OCR inference again: the answer is already known and was verified.
    """
    from app.services.resolution_knowledge import get_reusable_resolution

    row = get_reusable_resolution(*identity.triple, conn=conn)
    if not row:
        return None
    entity_type = _LEDGER_TO_PIPELINE.get(row.get("resulting_entity_type"))
    entity_id = row.get("resulting_entity_id")
    if not entity_type or entity_id is None:
        return None      # firm_material and anything else carrying no canonical entity
    return {"entity_type": entity_type, "entity_id": int(entity_id),
            "decision_id": row["id"], "match_reason": row.get("match_reason"),
            "confidence": row.get("confidence")}


def persist_mapping(conn, identity: SourceIdentity, *, entity_type, entity_id, evidence=None,
                    match_reason=None, confidence=None, actor=None) -> int | None:
    """Record that this source identity resolves to this Client360 entity. Returns the decision id.

    Fail-closed and non-destructive:

    * refuses to record anything but a ``link_*`` decision — the pipeline never creates an entity;
    * never passes ``supersede=True``, so it can only ever ADD the first mapping for an identity.
      If one already exists, ``record_decision`` raises and this returns None. Changing an
      established mapping is a human act, because an automated writer that can supersede is an
      automated writer that can silently move a client's documents;
    * ``record_decision`` validates the target entity EXISTS, so a mapping can never point at
      nothing.
    """
    from app.services.resolution_knowledge import (
        ResolutionConflictError,
        ResolutionKnowledgeError,
        record_decision,
    )

    decision = _LINK_DECISION.get(entity_type)
    if decision is None:
        log.warning("refusing to persist a mapping for unknown entity type %r", entity_type)
        return None
    if not identity.key or not identity.display:
        return None

    ledger_type = {"person": "person", "household": "household",
                   "organization": "relationship_entity"}[entity_type]
    try:
        return record_decision(
            subject_system=identity.system, subject_type=identity.subject_type,
            subject_key=identity.key, display_name=identity.display, decision=decision,
            resulting_entity_type=ledger_type, resulting_entity_id=int(entity_id),
            evidence_snapshot={"evidence": list(evidence or [])[:8]},
            match_reason=match_reason, confidence=confidence,
            reviewed_by=actor or "document-pipeline", conn=conn)
    except ResolutionConflictError:
        # Another mapping is already active for this identity. Leaving it alone is the whole point.
        log.info("a mapping already exists for %r; leaving it unchanged", identity)
        return None
    except ResolutionKnowledgeError as exc:
        log.warning("could not persist mapping for %r: %s", identity, exc)
        return None


# --- source-level review --------------------------------------------------------------------------

def _reviews():
    from app.services.document_pipeline_continuous.model import _bind

    reviews = _bind("document_pipeline_source_reviews")
    members = _bind("document_pipeline_source_review_documents")
    if reviews is None or members is None:
        from app.services.document_pipeline_continuous.model import PipelineNotInstalled
        raise PipelineNotInstalled(
            "source-level ownership review is not installed in this database: apply the docpipe02 "
            "migration (`alembic upgrade head`).")
    return reviews, members


def open_source_review(conn, identity: SourceIdentity, *, document_id, reason_code,
                       evidence=None, candidates=None, actor_user_id=None,
                       request_id=None) -> int:
    """Attach one document to THE open review for this source identity, opening it if needed.

    Returns the review id. The second document through here joins the first one's review rather than
    opening another — which is the difference between 18 reviewer decisions and 474."""
    reviews, members = _reviews()
    row = conn.execute(
        select(reviews.c.id).where(
            reviews.c.subject_system == identity.system,
            reviews.c.subject_type == identity.subject_type,
            reviews.c.subject_key == identity.key,
            reviews.c.status == "open")).scalar()

    if row is None:
        row = conn.execute(text(f"""
            INSERT INTO {reviews.name}
                (subject_system, subject_type, subject_key, display_name, lane, reason_code,
                 evidence, candidates, status)
            VALUES (:system, :stype, :key, :display, :lane, :reason,
                    CAST(:evidence AS jsonb), CAST(:candidates AS jsonb), 'open')
            ON CONFLICT (subject_system, subject_type, subject_key)
                WHERE status = 'open' DO NOTHING
            RETURNING id
        """), {"system": identity.system, "stype": identity.subject_type, "key": identity.key,
               "display": identity.display, "lane": identity.lane, "reason": reason_code,
               "evidence": _json(evidence), "candidates": _json(candidates)}).scalar()
        if row is None:      # lost a race; the other writer's review is the one to join
            row = conn.execute(
                select(reviews.c.id).where(
                    reviews.c.subject_system == identity.system,
                    reviews.c.subject_type == identity.subject_type,
                    reviews.c.subject_key == identity.key,
                    reviews.c.status == "open")).scalar()
        _audit(conn, "document_pipeline.source_review_opened", identity, row,
               actor_user_id=actor_user_id, request_id=request_id, reason_code=reason_code)

    conn.execute(text(f"""
        INSERT INTO {members.name} (review_id, document_id) VALUES (:review, :document)
        ON CONFLICT ON CONSTRAINT uq_document_pipeline_source_review_document DO NOTHING
    """), {"review": row, "document": int(document_id)})
    conn.execute(text(f"UPDATE {reviews.name} SET updated_at = now() WHERE id = :id"), {"id": row})
    return int(row)


# --- establishing a mapping from agreement that already exists ------------------------------------

def documents_for_identity(conn, identity: SourceIdentity, *, live_only: bool = True) -> list[dict]:
    """Every document carrying this source identity, with its stored owner.

    The account id is preferred over the folder name for the same reason ``source_identity`` prefers
    it: a folder can be renamed and an account cannot.
    """
    ds = metadata.tables.get("document_sources")
    if ds is None:
        return []
    if identity.subject_type == SUBJECT_TAXDOME_FOLDER:
        key_sql = "d.tags->>'taxdome_folder'"
        system = TAXDOME_SOURCE
    elif identity.subject_type == SUBJECT_TAXDOME_ACCOUNT:
        key_sql = "s.source_external_id"
        system = TAXDOME_SOURCE
    elif identity.subject_type == SUBJECT_DRAKE_CLIENT:
        key_sql = "s.source_external_id"
        system = DRAKE_SOURCE
    else:
        return []

    live = ("AND d.status = 'active' AND d.deleted_at IS NULL"
            " AND d.archived = false AND d.archived_at IS NULL") if live_only else ""
    rows = conn.execute(text(f"""
        SELECT DISTINCT d.id, d.person_id, d.household_id, d.organization_id, {key_sql} AS raw_key
          FROM documents d
          JOIN {ds.name} s ON s.document_id = d.id
         WHERE s.source_system = :system {live}
    """), {"system": system}).mappings().all()
    # Normalising in Python rather than SQL keeps ONE definition of what a key is — the same function
    # that produced identity.key in the first place. A second normalisation written in SQL is a
    # second answer waiting to disagree with the first.
    return [dict(r) for r in rows if normalise_key(r["raw_key"]) == identity.key]


def unique_compatible_owner(rows) -> tuple[str, int] | None:
    """The one owner every already-owned document here agrees on, or None.

    Compatibility is not equality. A document filed to a PERSON and another filed to that person's
    HOUSEHOLD are the same client, recorded at two levels, and the established rule — the one
    ``taxdome_drive.resolve_folder`` already follows and ``conflicts_with_stored_owner`` already
    honours — is that the household is the answer when there is one. So:

    * one distinct household, and every person present belongs to it  -> that household;
    * no household at all and exactly one distinct person             -> that person;
    * no household or person and exactly one organization             -> that organization;
    * anything else                                                   -> None, meaning ambiguous.

    Returning None is the safe direction: it leaves the identity unmapped and answerable by a human,
    rather than guessing which of two clients a folder belongs to.
    """
    owned = [r for r in rows if r.get("person_id") or r.get("household_id")
             or r.get("organization_id")]
    if not owned:
        return None
    households = {r["household_id"] for r in owned if r.get("household_id")}
    persons = {r["person_id"] for r in owned if r.get("person_id")}
    orgs = {r["organization_id"] for r in owned if r.get("organization_id")}

    if len(households) == 1 and not orgs:
        return ("household", int(next(iter(households))))
    if not households and not orgs and len(persons) == 1:
        return ("person", int(next(iter(persons))))
    if not households and not persons and len(orgs) == 1:
        return ("organization", int(next(iter(orgs))))
    return None


def establish_mapping_from_existing_owners(conn, identity: SourceIdentity, *, actor=None,
                                           request_id=None) -> dict:
    """Record the mapping that this source identity's OWN documents already agree on.

    The pipeline learned mappings only as a side effect of writing ownership, so an identity whose
    documents were all filed correctly years ago — and which therefore needs no write at all — stayed
    unmapped, and the next document arriving under it went back through inference. That is exactly
    backwards: the best-established clients were the ones getting the least benefit.

    This records nothing new about the world. It reads agreement that already exists and writes it
    down once, so it stops being re-derived. It never assigns ownership, never creates an entity, and
    never supersedes an existing mapping.
    """
    existing = lookup_mapping(conn, identity)
    if existing is not None:
        return {"status": "already_mapped", "entity_type": existing["entity_type"],
                "entity_id": existing["entity_id"], "documents": 0}

    rows = documents_for_identity(conn, identity)
    owned = [r for r in rows if r.get("person_id") or r.get("household_id")
             or r.get("organization_id")]
    if not owned:
        return {"status": "no_owned_documents", "documents": len(rows)}

    answer = unique_compatible_owner(owned)
    if answer is None:
        return {"status": "ambiguous", "documents": len(rows), "owned": len(owned)}

    entity_type, entity_id = answer
    persons = {r["person_id"] for r in owned if r.get("person_id")}
    if entity_type == "household" and persons:
        # A folder owned partly by a person and partly by a household is the one case structure
        # cannot settle: usually one client at two levels, occasionally two clients sharing a
        # folder. Phase 1 does not guess — it requires Drake to confirm the couple files jointly,
        # and sends everything else to one folder-level review.
        from app.services.document_pipeline_continuous import household_policy

        verdict = household_policy.household_mapping_allowed(
            conn,
            person_ids=persons,
            household_ids={r["household_id"] for r in owned if r.get("household_id")},
            organization_ids={r["organization_id"] for r in owned if r.get("organization_id")})
        if not verdict.mapped:
            return {"status": "ambiguous", "reason": verdict.condition,
                    "policy_detail": verdict.detail,
                    "documents": len(rows), "owned": len(owned)}
        entity_type, entity_id = verdict.entity_type, verdict.entity_id

    decision_id = persist_mapping(
        conn, identity, entity_type=entity_type, entity_id=entity_id,
        evidence=[f"{len(owned)} already-owned document(s) under this identity agree"],
        match_reason="established from existing agreement", actor=actor)
    if decision_id is None:
        return {"status": "refused", "entity_type": entity_type, "entity_id": entity_id,
                "documents": len(rows), "owned": len(owned)}
    return {"status": "established", "entity_type": entity_type, "entity_id": entity_id,
            "mapping_id": decision_id, "documents": len(rows), "owned": len(owned)}


def source_review_documents(conn, review_id) -> list[int]:
    _reviews_t, members = _reviews()
    return [int(r[0]) for r in conn.execute(
        select(members.c.document_id).where(members.c.review_id == review_id)
        .order_by(members.c.document_id))]


def open_source_reviews(conn, *, lane=None, limit=100, offset=0) -> list[dict]:
    """Open source-level reviews with their document counts. One row per client decision."""
    reviews, members = _reviews()
    clause = "AND r.lane = :lane" if lane else ""
    rows = conn.execute(text(f"""
        SELECT r.id, r.subject_system, r.subject_type, r.subject_key, r.display_name, r.lane,
               r.reason_code, r.evidence, r.candidates, r.opened_at, r.updated_at,
               (SELECT count(*) FROM {members.name} m WHERE m.review_id = r.id) AS document_count
          FROM {reviews.name} r
         WHERE r.status = 'open' {clause}
         ORDER BY document_count DESC, r.id
         LIMIT :limit OFFSET :offset
    """), {"lane": lane, "limit": int(limit), "offset": int(offset)}).mappings().all()
    return [dict(r) for r in rows]


def resolve_source_review(conn, review_id, *, entity_type, entity_id, actor_user_id=None,
                          note=None, request_id=None, persist_mapping_too=True) -> dict:
    """Record ONE decision for a source identity, then apply it to its still-eligible documents.

    Three things happen, in this order, and each is guarded:

    1. the decision is recorded on the review — once, for the client, not once per document;
    2. it is persisted as a durable mapping so FUTURE documents inherit it without review;
    3. it is applied to the review's documents that are still genuinely unowned. A document that
       gained an owner since the review opened is left exactly as it is: the canonical write refuses
       to overwrite, and this never asks it to.
    """
    from app.services.households import resolve_document_ownership

    reviews, _members = _reviews()
    review = conn.execute(select(reviews).where(reviews.c.id == review_id)).mappings().first()
    if review is None:
        raise ValueError(f"no source review {review_id}")
    if review["status"] != "open":
        return {"review_id": review_id, "applied": 0, "skipped": 0, "already_owned": 0,
                "reason": "review is not open"}

    ledger_type = {"person": "person", "household": "household",
                   "organization": "relationship_entity"}.get(entity_type)
    if ledger_type is None:
        raise ValueError(f"unknown entity type {entity_type!r}")

    identity = SourceIdentity(review["subject_system"], review["subject_type"],
                              review["subject_key"], review["display_name"] or review["subject_key"],
                              review["lane"])

    mapping_id = None
    if persist_mapping_too:
        mapping_id = persist_mapping(conn, identity, entity_type=entity_type, entity_id=entity_id,
                                     match_reason=f"source review {review_id}", actor=note or None)

    applied = already_owned = skipped = 0
    column = {"person": "person_id", "household": "household_id",
              "organization": "organization_id"}[entity_type]
    for document_id in source_review_documents(conn, review_id):
        result = resolve_document_ownership(document_id, conn=conn, actor_user_id=actor_user_id,
                                            request_id=request_id or "source-review",
                                            **{column: int(entity_id)})
        if result.get("assigned"):
            applied += 1
        elif result.get("reason") == "already_owned":
            already_owned += 1
        else:
            skipped += 1

    conn.execute(text(f"""
        UPDATE {reviews.name}
           SET status = 'resolved', resolution_entity_type = :etype, resolution_entity_id = :eid,
               resolution_note = :note, resolved_by_user_id = :actor,
               resolved_at = now(), updated_at = now()
         WHERE id = :id
    """), {"id": review_id, "etype": ledger_type, "eid": int(entity_id), "note": note,
           "actor": actor_user_id})
    _audit(conn, "document_pipeline.source_review_resolved", identity, review_id,
           actor_user_id=actor_user_id, request_id=request_id,
           reason_code=review["reason_code"], applied=applied, already_owned=already_owned)
    return {"review_id": review_id, "mapping_id": mapping_id, "applied": applied,
            "already_owned": already_owned, "skipped": skipped}


def _json(value):
    import json
    return json.dumps(list(value or [])[:8])


def _audit(conn, action, identity: SourceIdentity, review_id, *, actor_user_id=None,
           request_id=None, **extra):
    """Audit the source-level decision. Never raises; never records the display name."""
    try:
        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type="document_source_review",
                          entity_id=review_id, actor_user_id=actor_user_id,
                          request_id=request_id or "document-pipeline",
                          metadata={"subject_system": identity.system,
                                    "subject_type": identity.subject_type, **extra},
                          conn=conn)
    except Exception:      # noqa: BLE001 — an audit failure must not roll back the decision
        log.exception("source review audit failed for %s", action)
