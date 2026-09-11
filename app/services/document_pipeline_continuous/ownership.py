"""Ownership resolution: three lanes, one review queue, and no overwrite — ever.

THE LANES ARE NOT EQUAL, AND THAT IS THE POINT
----------------------------------------------
A document's provenance says how much its evidence is worth, so the lanes are consulted in order of
authority and the FIRST applicable lane decides. A Drake-sourced document is never re-litigated by
filename evidence, and a TaxDome document filed in a client's own folder is never overridden by a name
that happens to appear in its text.

* ``drake`` — AUTHORITATIVE. Drake identity attribution comes from taxpayer/spouse identifier hashes
  derived from SSNs (``drake_document_owner``). When it resolves, it resolves; when it says HOLD, the
  document goes to review and NOTHING weaker is allowed to answer the question instead.
* ``taxdome`` — AUTHORITATIVE. A TaxDome document lives inside an account folder, and that folder IS
  the client mapping (``taxdome_drive.resolve_folder``). An unresolved folder goes to review with the
  candidate people attached, never to a content guess.
* ``sharepoint`` — EVIDENCE. Path, filename, OCR text, identity, address, account and household
  evidence, scored by the existing ``document_owner_proposal`` engine. Only a HIGH proposal links;
  MEDIUM and AMBIGUOUS are exactly the "ambiguous SharePoint document" case and go to the ONE review
  queue.

NEVER OVERWRITE
---------------
Two independent guards, because one is a convention and two is a rule:

1. Every link goes through ``households.resolve_document_ownership``, whose UPDATE re-checks
   "person_id AND household_id AND organization_id are all NULL" inside the same statement. A document
   that gained an owner between the proposal and the write cannot be overwritten by a stale decision.
2. Before proposing at all, this module reads the current owner. A document that already has one is
   finished as ``already_owned`` — and when a lane proposed a DIFFERENT owner, that disagreement is
   filed as an ``ownership_conflict`` review rather than discarded, because a lane contradicting the
   record is information somebody needs.

This module never creates a person, household or organization, and never moves a file.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, text

from app.db import documents
from app.services.document_pipeline_continuous import queue
from app.services.document_pipeline_continuous.model import (
    LANE_DRAKE,
    LANE_SHAREPOINT,
    LANE_TAXDOME,
    OUTCOME_ALREADY_OWNED,
    OUTCOME_LINKED,
    OUTCOME_REVIEW,
    OUTCOME_UNRESOLVED,
)

log = logging.getLogger(__name__)

#: Source-system strings as written by the ingestion providers (``document_sources.source_system``).
DRAKE_SOURCE = "Drake"
TAXDOME_SOURCE = "TaxDome Drive"
SHAREPOINT_SOURCE = "SharePoint"

#: Only a HIGH content proposal is allowed to link itself. MEDIUM and AMBIGUOUS are review material by
#: definition — they are the confidence buckets that exist because the evidence did not settle it.
#: PUBLIC because ``scripts/plan_document_ownership.py`` predicts what this module will decide, and a
#: planner that keeps its own copy of this mapping is a planner that eventually predicts the wrong
#: thing. One definition, two readers.
LINKABLE_CONFIDENCE = ("HIGH",)
REVIEWABLE_CONFIDENCE = ("MEDIUM", "AMBIGUOUS")

_LINKABLE_CONFIDENCE = LINKABLE_CONFIDENCE
_REVIEWABLE_CONFIDENCE = REVIEWABLE_CONFIDENCE

#: Evidence and candidate lists are trimmed before they reach the review queue. The proposal engine
#: already masks SSNs and never emits raw text; this bounds the row size as well.
_MAX_EVIDENCE = 8
_MAX_CANDIDATES = 8


def detect_lane(conn, document_id: int) -> str:
    """Which ownership lane owns this document, by provenance. Authority order, first match wins."""
    systems = {row[0] for row in conn.execute(text("""
        SELECT source_system FROM document_sources WHERE document_id = :document_id
    """), {"document_id": document_id})}
    if DRAKE_SOURCE in systems:
        return LANE_DRAKE
    if TAXDOME_SOURCE in systems:
        return LANE_TAXDOME
    return LANE_SHAREPOINT


def current_owner(conn, document_id: int) -> dict | None:
    row = conn.execute(select(documents.c.id, documents.c.person_id, documents.c.household_id,
                              documents.c.organization_id, documents.c.original_name, documents.c.tags)
                       .where(documents.c.id == document_id)).mappings().first()
    return dict(row) if row else None


def _is_owned(row) -> bool:
    return any(row.get(key) is not None
               for key in ("person_id", "household_id", "organization_id"))


def _proposed_entity(proposal) -> tuple[str | None, int | None, str | None]:
    return (proposal.get("entity_type") or proposal.get("proposed_entity_type"),
            proposal.get("entity_id") or proposal.get("proposed_entity_id"),
            proposal.get("entity_name") or proposal.get("proposed_entity_name"))


def _conflicts(row, entity_type, entity_id) -> bool:
    """Does an existing owner disagree with what a lane proposed? Same owner is agreement, not
    conflict — a re-run that reaches the conclusion already recorded is the pipeline working."""
    if entity_type is None or entity_id is None:
        return False
    column = {"person": "person_id", "household": "household_id",
              "organization": "organization_id"}.get(entity_type)
    if column is None:
        return True
    return row.get(column) != entity_id


def _link(conn, document_id: int, *, entity_type: str, entity_id: int, actor_user_id=None,
          request_id: str | None = None) -> dict:
    """Write ownership through the single canonical path. Never restates the ownership rules."""
    from app.services.households import resolve_document_ownership
    kwargs = {"person": "person_id", "household": "household_id",
              "organization": "organization_id"}[entity_type]
    return resolve_document_ownership(document_id, conn=conn, actor_user_id=actor_user_id,
                                      request_id=request_id or "document-pipeline",
                                      **{kwargs: entity_id})


def _review(conn, document_id: int, *, lane: str, reason_code: str, evidence=None, candidates=None,
            request_id: str | None = None) -> dict:
    queue.record_review(conn, document_id=document_id, lane=lane, reason_code=reason_code,
                        evidence=(evidence or [])[:_MAX_EVIDENCE],
                        candidates=(candidates or [])[:_MAX_CANDIDATES], request_id=request_id)
    return {"outcome": OUTCOME_REVIEW, "lane": lane, "reason_code": reason_code}


# --- lanes ---------------------------------------------------------------------------------------

def _drake_lane(conn, document_id, row, proposal, *, actor_user_id, request_id) -> dict:
    """Drake identity attribution is authoritative: it links, holds, or nothing else answers."""
    confidence = (proposal or {}).get("confidence")
    entity_type, entity_id, entity_name = _proposed_entity(proposal or {})
    evidence = (proposal or {}).get("evidence") or []

    if confidence == "HOLD":
        return _review(conn, document_id, lane=LANE_DRAKE,
                       reason_code=(proposal or {}).get("drake_resolution") or "drake_identity_hold",
                       evidence=evidence, request_id=request_id)
    if confidence in _LINKABLE_CONFIDENCE and entity_type and entity_id:
        return _apply_link(conn, document_id, row, lane=LANE_DRAKE, entity_type=entity_type,
                           entity_id=entity_id, entity_name=entity_name, evidence=evidence,
                           actor_user_id=actor_user_id, request_id=request_id)
    if confidence in _REVIEWABLE_CONFIDENCE:
        return _review(conn, document_id, lane=LANE_DRAKE, reason_code="drake_ambiguous",
                       evidence=evidence,
                       candidates=(proposal or {}).get("best_candidates") or [],
                       request_id=request_id)
    return {"outcome": OUTCOME_UNRESOLVED, "lane": LANE_DRAKE, "reason_code": "drake_no_match"}


def _taxdome_lane(conn, document_id, row, proposal, *, actor_user_id, request_id) -> dict:
    """The TaxDome account/folder mapping is authoritative; content evidence never overrides it."""
    folder = (row.get("tags") or {}).get("taxdome_folder")
    if not folder:
        # A TaxDome-sourced document with no folder tag has lost the thing that makes the lane
        # authoritative, so it is handled as evidence rather than guessed at.
        return _sharepoint_lane(conn, document_id, row, proposal, actor_user_id=actor_user_id,
                               request_id=request_id, lane=LANE_TAXDOME)
    from app.importers import taxdome_drive

    household_id, person_id = taxdome_drive.resolve_folder(conn, folder)
    if household_id is not None:
        return _apply_link(conn, document_id, row, lane=LANE_TAXDOME, entity_type="household",
                           entity_id=household_id, entity_name=folder,
                           evidence=[f"TaxDome folder {folder!r} maps to this household"],
                           actor_user_id=actor_user_id, request_id=request_id)
    if person_id is not None:
        return _apply_link(conn, document_id, row, lane=LANE_TAXDOME, entity_type="person",
                           entity_id=person_id, entity_name=folder,
                           evidence=[f"TaxDome folder {folder!r} maps to this person"],
                           actor_user_id=actor_user_id, request_id=request_id)
    candidates = []
    try:
        candidates = [{"entity_type": "person", "entity_id": c.get("id"), "name": c.get("full_name")}
                      for c in taxdome_drive.suggest_people(conn, folder, limit=_MAX_CANDIDATES)]
    except Exception:      # noqa: BLE001 — suggestions are a convenience, never a requirement
        log.debug("taxdome suggestions unavailable for folder %r", folder)
    return _review(conn, document_id, lane=LANE_TAXDOME, reason_code="taxdome_folder_unresolved",
                   evidence=[f"TaxDome folder {folder!r} does not resolve to one client"],
                   candidates=candidates, request_id=request_id)


def _sharepoint_lane(conn, document_id, row, proposal, *, actor_user_id, request_id,
                     lane: str = LANE_SHAREPOINT) -> dict:
    """Evidence lane. HIGH links; MEDIUM/AMBIGUOUS are the ambiguous documents the review queue is for."""
    proposal = proposal or {}
    confidence = proposal.get("confidence")
    route = proposal.get("route")
    entity_type, entity_id, entity_name = _proposed_entity(proposal)
    evidence = proposal.get("evidence") or []
    candidates = proposal.get("best_candidates") or []

    if confidence in _LINKABLE_CONFIDENCE and entity_type and entity_id:
        return _apply_link(conn, document_id, row, lane=lane, entity_type=entity_type,
                           entity_id=entity_id, entity_name=entity_name, evidence=evidence,
                           actor_user_id=actor_user_id, request_id=request_id)
    if confidence in _REVIEWABLE_CONFIDENCE:
        return _review(conn, document_id, lane=lane, reason_code=str(confidence).lower(),
                       evidence=evidence, candidates=candidates, request_id=request_id)
    if confidence == "HOLD":
        return _review(conn, document_id, lane=lane, reason_code="identity_hold",
                       evidence=evidence, candidates=candidates, request_id=request_id)
    # NO_MATCH / UNSUPPORTED / ERROR / no proposal at all. Not ambiguous — there is simply no evidence
    # to be ambiguous ABOUT, so it does not belong in a queue of decisions waiting to be made. It stays
    # unowned and countable, and the existing unassigned-documents surfaces already show it.
    return {"outcome": OUTCOME_UNRESOLVED, "lane": lane,
            "reason_code": (route or "no_match").lower()}


def _apply_link(conn, document_id, row, *, lane, entity_type, entity_id, entity_name, evidence,
                actor_user_id, request_id) -> dict:
    """Attempt the authoritative link, and turn every refusal into a truthful outcome."""
    try:
        result = _link(conn, document_id, entity_type=entity_type, entity_id=int(entity_id),
                       actor_user_id=actor_user_id, request_id=request_id)
    except ValueError as exc:      # unknown document / no target — a permanent condition
        return {"outcome": OUTCOME_UNRESOLVED, "lane": lane, "reason_code": "link_refused",
                "detail": str(exc)[:500]}
    if result.get("assigned"):
        return {"outcome": OUTCOME_LINKED, "lane": lane, "entity_type": entity_type,
                "entity_id": int(entity_id), "entity_name": entity_name,
                "destination": result.get("destination")}
    reason = result.get("reason")
    if reason == "already_owned":
        # Lost a race, or the record already said something. Agreement is fine; disagreement is a
        # review, because a lane contradicting the stored owner is exactly the case a human must see.
        fresh = current_owner(conn, document_id) or row
        if _conflicts(fresh, entity_type, int(entity_id)):
            return _review(conn, document_id, lane=lane, reason_code="ownership_conflict",
                           evidence=[*evidence,
                                     f"{lane} proposed {entity_type} {entity_id} "
                                     f"but the document is already owned"],
                           request_id=request_id)
        return {"outcome": OUTCOME_ALREADY_OWNED, "lane": lane, "reason_code": "already_owned"}
    return {"outcome": OUTCOME_UNRESOLVED, "lane": lane, "reason_code": reason or "link_refused"}


_LANES = {LANE_DRAKE: _drake_lane, LANE_TAXDOME: _taxdome_lane, LANE_SHAREPOINT: _sharepoint_lane}


# --- entry point ---------------------------------------------------------------------------------

def resolve(conn, document_id: int, *, proposal=None, actor_user_id=None,
            request_id: str | None = None) -> dict:
    """Resolve ownership for ONE document and return ``{outcome, lane, ...}``.

    ``proposal`` is the sanitised owner proposal the classify stage already persisted (see
    ``document_pipeline.proposal_for_document``). Passing it in is what keeps this stage cheap: the
    text was extracted once, upstream, and is never read again here."""
    row = current_owner(conn, document_id)
    if row is None:
        return {"outcome": OUTCOME_UNRESOLVED, "lane": None, "reason_code": "document_not_found"}
    lane = detect_lane(conn, document_id)
    if _is_owned(row):
        entity_type, entity_id, _name = _proposed_entity(proposal or {})
        if entity_id is not None and _conflicts(row, entity_type, int(entity_id)):
            return _review(conn, document_id, lane=lane, reason_code="ownership_conflict",
                           evidence=(proposal or {}).get("evidence") or [],
                           request_id=request_id)
        return {"outcome": OUTCOME_ALREADY_OWNED, "lane": lane, "reason_code": "already_owned"}

    if proposal is None:
        from app.services.document_pipeline import proposal_for_document
        proposal = proposal_for_document(document_id) or {}

    return _LANES[lane](conn, document_id, row, proposal, actor_user_id=actor_user_id,
                        request_id=request_id)
