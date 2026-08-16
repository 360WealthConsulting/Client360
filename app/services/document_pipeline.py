"""Document ingestion/extraction pipeline (owner-proposal edition).

The single, source-agnostic path a document travels once it is a canonical Client360 record:

    DOCUMENT (canonical) -> extract text (PDF native / Excel / image OCR fallback, all reused)
        -> classify (doc type + year, reusing document_classification)
        -> extract identity evidence + match canonical owner (reusing document_owner_proposal)
        -> deterministic confidence -> ROUTE (HIGH / MEDIUM / AMBIGUOUS / NO_MATCH / UNSUPPORTED / ERROR)
        -> (ingestion) persist a NON-authoritative proposal as a versioned document_facts row
        -> (legacy) READ-ONLY batch over unassigned documents for a review worklist

This module ORCHESTRATES existing services only — it never re-implements matching or scoring, and it
NEVER writes ownership. Owner assignment stays the existing per-document explicit Confirm path. Proposal
state is persisted with the SAME mechanism the Knowledge pipeline already uses (document_classifications +
versioned document_facts, is_current/version), so NO migration is required. All-NULL ownership recheck,
already-owned protection, and the six permanent rejects are enforced by document_owner_proposal and are
never bypassed here.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

from sqlalchemy import and_, select

from app.db import document_classifications, document_facts, documents, engine
from app.services.document_classification import CLASSIFIER_VERSION, classify_document
from app.services.document_owner_proposal import (
    PERMANENT_REJECT_DOCUMENT_IDS,
    build_match_indexes,
    propose_document_owner,
)

# Every new canonical document is analyzed after ingestion. Analysis is fully guarded — a failure never
# blocks ingestion (see analyze_and_persist / the hook in document_sources). Toggle for future tuning.
AUTO_ANALYZE_NEW_DOCUMENTS = True

# DESIGN ONLY — DO NOT ENABLE without explicit direction. When True, a document carrying strong identity
# evidence (email / phone) that resolves to NO existing canonical record routes to NEW_CLIENT_CANDIDATE
# for human review. It NEVER creates or assigns a client; enabling it only changes the routing LABEL.
EMIT_NEW_CLIENT_CANDIDATE = False

PROPOSAL_FACT_TYPE = "owner_proposal"
PIPELINE_VERSION = "pipeline-v1"
ROUTES = ("HIGH", "MEDIUM", "AMBIGUOUS", "NEW_CLIENT_CANDIDATE", "NO_MATCH", "UNSUPPORTED", "ERROR")
_ROUTE_SCORE = {"HIGH": 0.9, "MEDIUM": 0.6, "AMBIGUOUS": 0.3, "NEW_CLIENT_CANDIDATE": 0.0,
                "NO_MATCH": 0.0, "UNSUPPORTED": 0.0, "ERROR": 0.0}
# extraction methods that mean "no usable text came out" (as opposed to text with no identity in it).
_NO_TEXT_METHODS = {"unsupported", "image_no_text", "pdf_no_text", "none"}
_OCR_METHODS = {"ocr", "ocr_cache"}
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")   # 4-digit year, not part of a longer digit run


def _detect_year(text, filename):
    """Best-effort tax/document year from the filename first (e.g. 'Form1095a_2021.pdf'), then content.
    Bounded to a plausible range; returns a string or None."""
    hi = date.today().year + 1
    for src in (filename or "", text or ""):
        yrs = [int(m.group(0)) for m in _YEAR_RE.finditer(src) if 1990 <= int(m.group(0)) <= hi]
        if yrs:
            return str(max(yrs))
    return None


def _has_strong_identity(proposal):
    """Document content carries a strong personal identifier (email or phone) — used only to distinguish a
    potential NEW_CLIENT_CANDIDATE from a true NO_MATCH. Never triggers any client creation."""
    ex = proposal.get("extracted") or {}
    return bool(ex.get("emails") or ex.get("phones"))


def _route(proposal, text):
    conf = proposal.get("confidence")
    if conf in ("HIGH", "MEDIUM", "AMBIGUOUS"):
        return conf
    if not (text or "").strip() and proposal.get("extraction_method") in _NO_TEXT_METHODS:
        return "UNSUPPORTED"          # could not extract usable content (vs. content had no identity)
    if EMIT_NEW_CLIENT_CANDIDATE and _has_strong_identity(proposal):
        return "NEW_CLIENT_CANDIDATE"  # strong identity, no existing match — for human approval only
    return "NO_MATCH"


def analyze_document(document_id, *, conn=None, idx=None, ocr=False):
    """Run the full pipeline for ONE document and return a unified, READ-ONLY result dict:
    {document_id, filename, source_folder, eligible, reason?, doc_type, doc_type_confidence, year,
     proposed_entity_type/id/name, confidence, route, evidence, competing, best_candidates, extracted,
     extraction_method}. Writes nothing. Ineligible (already-owned / permanent-reject / missing)
     documents come back with route 'SKIPPED' and no proposed owner."""
    own = conn if conn is not None else engine.connect()
    try:
        proposal = propose_document_owner(document_id, conn=own, idx=idx, with_text=True, ocr=ocr)
        text = proposal.pop("text", "") if isinstance(proposal, dict) else ""
        if not proposal.get("eligible"):
            return {"document_id": document_id, "eligible": False,
                    "reason": proposal.get("reason"), "route": "SKIPPED",
                    "doc_type": None, "doc_type_confidence": None, "year": None,
                    "proposed_entity_type": None, "proposed_entity_id": None,
                    "proposed_entity_name": None, "confidence": None, "evidence": [],
                    "competing": [], "best_candidates": []}
        doc_type, cls_conf = classify_document(proposal.get("filename"), text)
        proposal["doc_type"] = doc_type
        proposal["doc_type_confidence"] = cls_conf
        proposal["year"] = _detect_year(text, proposal.get("filename"))
        proposal["route"] = _route(proposal, text)
        return proposal
    finally:
        if conn is None:
            own.close()


# --- persistence (versioned document_facts + document_classifications; NEVER ownership) ------------

def _upsert_classification(conn, doc_id, doc_type, confidence, now):
    existing = conn.execute(select(document_classifications.c.id).where(
        document_classifications.c.document_id == doc_id)).scalar()
    values = {"doc_type": doc_type, "confidence": confidence,
              "classifier_version": CLASSIFIER_VERSION, "classified_at": now, "updated_at": now}
    if existing is None:
        conn.execute(document_classifications.insert().values(document_id=doc_id, **values))
    else:
        conn.execute(document_classifications.update().where(
            document_classifications.c.id == existing).values(**values))


def _current_proposal_version(conn, doc_id):
    return conn.execute(select(document_facts.c.version).where(and_(
        document_facts.c.document_id == doc_id,
        document_facts.c.fact_type == PROPOSAL_FACT_TYPE,
        document_facts.c.is_current.is_(True))).order_by(document_facts.c.version.desc())
        .limit(1)).scalar() or 0


def _write_proposal_fact(conn, doc_id, payload, score, now):
    """Supersede the prior current owner_proposal fact and write the new one. The value is a compact,
    SANITIZED JSON blob (route + proposed owner + doc type/year + already-masked evidence) — it never
    contains raw document text or a full SSN/TIN, and it is NOT ownership."""
    prev = _current_proposal_version(conn, doc_id)
    conn.execute(document_facts.update().where(and_(
        document_facts.c.document_id == doc_id,
        document_facts.c.fact_type == PROPOSAL_FACT_TYPE,
        document_facts.c.is_current.is_(True))).values(is_current=False))
    conn.execute(document_facts.insert().values(
        document_id=doc_id, fact_type=PROPOSAL_FACT_TYPE, fact_value=json.dumps(payload),
        confidence=score, extraction_engine="owner_proposal", extractor_version=PIPELINE_VERSION,
        extracted_at=now, version=prev + 1, is_current=True))


def persist_proposal(conn, result):
    """Persist a pipeline result as NON-authoritative state: the doc type (document_classifications) and a
    versioned owner_proposal fact (document_facts). Writes NO ownership. Ineligible/missing documents are
    a no-op. `conn` must be a writable connection/transaction."""
    doc_id = result.get("document_id")
    if not result.get("eligible") or doc_id is None:
        return
    now = datetime.now(UTC)
    _upsert_classification(conn, doc_id, result.get("doc_type") or "unknown",
                           result.get("doc_type_confidence") or 0.0, now)
    payload = {"route": result.get("route"), "confidence": result.get("confidence"),
               "entity_type": result.get("proposed_entity_type"),
               "entity_id": result.get("proposed_entity_id"),
               "entity_name": result.get("proposed_entity_name"),
               "doc_type": result.get("doc_type"), "year": result.get("year"),
               "evidence": (result.get("evidence") or [])[:6],
               "best_candidates": result.get("best_candidates") or []}
    _write_proposal_fact(conn, doc_id, payload, _ROUTE_SCORE.get(result.get("route"), 0.0), now)


def _persist_failure(conn, doc_id):
    now = datetime.now(UTC)
    _write_proposal_fact(conn, doc_id, {"route": "ERROR", "confidence": None, "entity_type": None,
                                        "entity_id": None, "entity_name": None,
                                        "error": "analysis_failed"}, 0.0, now)


def analyze_and_persist(document_id, *, conn, idx=None, ocr=False):
    """Analyze one document and persist its proposal state, SAVEPOINT-isolated so any failure rolls back
    only this analysis (never the surrounding ingestion transaction). Returns the result dict or None on
    failure. `conn` must be inside a transaction. Never writes ownership; never raises. ``ocr`` is left
    off on the ingestion path so heavy OCR never slows a bulk sync — the legacy batch and a future async
    OCR worker fill in OCR text; the native proposal is generated immediately either way."""
    sp = conn.begin_nested()
    try:
        result = analyze_document(document_id, conn=conn, idx=idx, ocr=ocr)
        persist_proposal(conn, result)
        sp.commit()
        return result
    except Exception:                          # noqa: BLE001 — analysis must never break ingestion
        sp.rollback()
        try:
            sp2 = conn.begin_nested()
            _persist_failure(conn, document_id)
            sp2.commit()
        except Exception:                      # noqa: BLE001
            try:
                sp2.rollback()
            except Exception:                  # noqa: BLE001
                pass
        return None


# --- read helper ----------------------------------------------------------------------------------

def proposal_for_document(document_id):
    """Latest persisted owner-proposal payload for a document, or None if never analyzed."""
    with engine.connect() as conn:
        v = conn.execute(select(document_facts.c.fact_value).where(and_(
            document_facts.c.document_id == document_id,
            document_facts.c.fact_type == PROPOSAL_FACT_TYPE,
            document_facts.c.is_current.is_(True))).limit(1)).scalar()
    if not v:
        return None
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return None


# --- legacy read-only batch -----------------------------------------------------------------------

def _unassigned_ids(conn, limit=None):
    stmt = (select(documents.c.id)
            .where(documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                   documents.c.organization_id.is_(None), documents.c.status != "deleted")
            .order_by(documents.c.id))
    if limit:
        stmt = stmt.limit(limit)
    return [r[0] for r in conn.execute(stmt) if r[0] not in PERMANENT_REJECT_DOCUMENT_IDS]


def run_batch(*, limit=None, include_details=True, ocr=False):
    """Analysis of every genuinely-unassigned document (person/household/organization all NULL), excluding
    the six permanent rejects from assignability. Assigns NO ownership and never modifies documents. With
    ``ocr=True`` it invokes the existing OCR backend for image-only/scanned documents (this populates the
    document_ocr TEXT cache — a benign, idempotent side effect — but writes no ownership/document state).
    Returns {total, counts:{route->n}, stats:{...}, details:[...]}; per-document failures bucket to ERROR."""
    counts = dict.fromkeys(ROUTES, 0)
    stats = {"ocr_extracted": 0, "ocr_with_identity": 0, "unsupported_remaining": 0}
    details = []
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        idx = build_match_indexes(conn)          # build once; reused across all documents
        for did in ids:
            try:
                r = analyze_document(did, conn=conn, idx=idx, ocr=ocr)
                route = r.get("route") or "NO_MATCH"
                if route not in counts:          # 'SKIPPED' shouldn't occur (rejects pre-excluded)
                    route = "NO_MATCH"
            except Exception as exc:             # noqa: BLE001
                route = "ERROR"
                r = {"document_id": did, "route": "ERROR", "error": str(exc)[:200]}
            counts[route] += 1
            if r.get("extraction_method") in _OCR_METHODS:
                stats["ocr_extracted"] += 1
                if route in ("HIGH", "MEDIUM", "AMBIGUOUS", "NEW_CLIENT_CANDIDATE"):
                    stats["ocr_with_identity"] += 1
            if route == "UNSUPPORTED":
                stats["unsupported_remaining"] += 1
            if include_details:
                details.append(_detail(r))
    return {"total": len(ids), "counts": counts, "stats": stats, "details": details}


def _detail(r):
    """A compact, sanitized per-document row for the batch report (no raw text, no full SSN)."""
    return {"document_id": r.get("document_id"), "filename": r.get("filename"),
            "source_folder": r.get("source_folder"), "doc_type": r.get("doc_type"),
            "year": r.get("year"), "route": r.get("route"), "confidence": r.get("confidence"),
            "proposed_entity_type": r.get("proposed_entity_type"),
            "proposed_entity_id": r.get("proposed_entity_id"),
            "proposed_entity_name": r.get("proposed_entity_name"),
            "evidence": (r.get("evidence") or [])[:6],
            "competing": r.get("competing") or [], "best_candidates": r.get("best_candidates") or [],
            "extraction_method": r.get("extraction_method"), "error": r.get("error")}
