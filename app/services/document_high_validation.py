"""Phase 1 — READ-ONLY validation of HIGH owner proposals for the legacy document backlog.

For every genuinely-unassigned document whose proposal is HIGH, this produces an auditable report row
(id, filename, source system, source folder/path, extraction method, proposed owner, confidence, the
evidence classes used, and whether identity came from document CONTENT, folder/source CONTEXT, or BOTH)
and runs conservative contradiction checks. ANY contradiction removes the document from bulk eligibility.

It WRITES NOTHING and assigns NO ownership — it only reports which HIGH proposals are clean enough that a
human could later confirm them in bulk, and which must go to manual review. It reuses the existing
document_owner_proposal engine (extraction + matching) and its indexes; no matching/scoring logic is
re-implemented and no scoring is changed.

Contradiction classes (each excludes the document from bulk eligibility):
  placeholder_candidate        proposed person's canonical name is placeholder-quality
  foreign_strong_identifier    the document also carries a DIFFERENT person's email/phone
  multiple_strong_identities   two or more distinct people are matched by a strong identifier
  multiple_named_identities    a second, non-co-household person is named in the content
  household_person_conflict    a person is proposed but a household (2+ co-members) is also present (or vice-versa)
  organization_person_conflict a person and a canonical business both appear (or vice-versa)
  folder_identity_conflict     the source folder confidently names a DIFFERENT canonical person
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from app.db import documents, engine, metadata, people, relationship_entities
from app.services.document_eligibility import is_intelligence_eligible
from app.services.document_owner_proposal import (
    _EMAIL_RE,
    _PHONE_RE,
    PERMANENT_REJECT_DOCUMENT_IDS,
    _content_name_candidates,
    _phone10,
    _placeholder_name,
    _valid_nanp,
    build_match_indexes,
    propose_document_owner,
)

CONTRADICTION_CLASSES = (
    "placeholder_candidate", "foreign_strong_identifier", "multiple_strong_identities",
    "multiple_named_identities", "household_person_conflict", "organization_person_conflict",
    "folder_identity_conflict",
)


def _unassigned_ids(conn, limit=None):
    """Documents eligible for OWNERSHIP ANALYSIS / REVIEW. Nothing else may use this selector.

    Program/runtime artifacts (``.dll``, ``.000``, ``.exe``, Drake data blobs …) were ingested by
    importers that apply no type filter, and became owner-proposal candidates. They are filtered out
    HERE, at the analysis boundary — the rows themselves are untouched, keep their provenance and
    stay fully queryable; they simply do not enter document intelligence.

    AUTHORITY BOUNDARY — do not cross it:
      * This is a DOCUMENT OWNERSHIP/REVIEW selector (proposal, review queue, confirm, new-entity
        proposal, NO_MATCH context). It is NOT an input to MDM.
      * Drake is a PRIMARY AUTHORITY for identity and duplicate resolution, and that authority runs
        entirely through ``source_contacts`` (``source_system='Drake'`` →
        ``raw_data.identifier_hash`` → ``drake_identity`` → ``drake_identity_match_candidates``).
        It never reads the ``documents`` corpus.
      * Document eligibility must therefore NEVER control MDM, canonical person/household/business
        identity, duplicate resolution, Drake authority, provenance, or evidence retention. A file
        that is not worth analysing can still be authoritative evidence and must remain so.
      * A Drake-native file may simultaneously be authoritative for identity, preserved as
        provenance, and neither OCR- nor classification-eligible. Excluding it here says nothing
        about its evidential standing.
      * ``document_pipeline._unassigned_ids`` is a SEPARATE selector and is deliberately NOT gated:
        it drives fact/OCR production, so gating it would change what evidence EXISTS rather than
        what is offered for review.
    """
    stmt = (select(documents.c.id, documents.c.original_name, documents.c.content_type)
            .where(documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                   documents.c.organization_id.is_(None), documents.c.status != "deleted")
            .order_by(documents.c.id))
    if limit:
        stmt = stmt.limit(limit)
    return [r[0] for r in conn.execute(stmt)
            if r[0] not in PERMANENT_REJECT_DOCUMENT_IDS
            and is_intelligence_eligible(r[1], r[2])]


def _doc_meta(conn, did, folder):
    row = conn.execute(select(documents.c.tags, documents.c.uploaded_by)
                       .where(documents.c.id == did)).mappings().first() or {}
    tags = row.get("tags") or {}
    source_system = tags.get("source_system")
    source_path = tags.get("taxdome_folder") or folder
    ds = metadata.tables.get("document_sources")
    if ds is not None:
        src = conn.execute(select(ds.c.source_system, ds.c.source_path, ds.c.source_uri)
                           .where(ds.c.document_id == did).limit(1)).mappings().first()
        if src:
            source_system = source_system or src["source_system"]
            source_path = source_path or src["source_path"] or src["source_uri"]
    if not source_system and row.get("uploaded_by"):
        source_system = str(row["uploaded_by"]).replace(" Sync", "").strip() or None
    return source_system, source_path


def _doc_signals(text, idx):
    """Recompute (read-only) every canonical candidate the document content matches, by signal class —
    the full picture the contradiction checks need. Reuses the engine's own regexes/index/primitives."""
    emails = {m.group(0).lower() for m in _EMAIL_RE.finditer(text)}
    phones = {p for p in (_phone10(m.group(0)) for m in _PHONE_RE.finditer(text)) if p and _valid_nanp(p)}
    full_names, first_last, _labeled = _content_name_candidates(text)
    sig: dict[int, set] = {}

    def _add(pid, cls):
        sig.setdefault(pid, set()).add(cls)

    for e in emails:
        for pid in idx["email"].get(e, ()):
            _add(pid, "email")
    for ph in phones:
        for pid in idx["phone"].get(ph, ()):
            _add(pid, "phone")
    for nm in full_names:
        if nm in idx["inst"]:
            continue
        for pid in idx["name"].get(nm, ()):
            _add(pid, "name")
    for pair in first_last:
        if " ".join(pair) in idx["inst"]:
            continue
        for pid in idx["first_last"].get(pair, ()):
            _add(pid, "name")
    named = {p for p, s in sig.items() if "name" in s}
    households = {hh for hh, mem in idx["members"].items() if len(mem & named) >= 2}
    orgs = {idx["biz"][nm][0] for nm in full_names if nm in idx["biz"]}
    return sig, households, orgs


def _folder_pids(folder, idx):
    if not folder:
        return set()
    full, first_last, _ = _content_name_candidates(folder)
    pids: set[int] = set()
    for nm in full:
        pids |= set(idx["name"].get(nm, []))
    for pair in first_last:
        pids |= set(idx["first_last"].get(pair, []))
    return pids


def _evidence_classes(evidence):
    classes = set()
    for e in evidence:
        el = e.lower()
        if "name '" in el or "exact name" in el:
            classes.add("name")
        if "email" in el and "matched" in el:
            classes.add("email")
        if "phone" in el:
            classes.add("phone")
        if "address" in el or "zip" in el:
            classes.add("address")
        if "household members" in el:
            classes.add("household_members")
        if "business legal name" in el:
            classes.add("business_name")
        if "label" in el:
            classes.add("label")
    return sorted(classes)


def _contradictions(proposal, text, folder, idx):
    ptype, pid = proposal.get("proposed_entity_type"), proposal.get("proposed_entity_id")
    sig, households, orgs = _doc_signals(text, idx)
    strong_pids = {p for p, s in sig.items() if s & {"email", "phone"}}
    named_pids = {p for p, s in sig.items() if "name" in s}
    out = []

    if ptype == "person":
        info = idx["pid"].get(pid, {})
        if _placeholder_name(info.get("name")):
            out.append("placeholder_candidate")
        if strong_pids - {pid}:
            out.append("foreign_strong_identifier")
        if len(strong_pids) >= 2:
            out.append("multiple_strong_identities")
        hh = info.get("household_id")
        co_members = idx["members"].get(hh, set()) if hh else set()
        if (named_pids - {pid}) - co_members:
            out.append("multiple_named_identities")
        if households:
            out.append("household_person_conflict")
        if orgs:
            out.append("organization_person_conflict")
        folder_pids = _folder_pids(folder, idx)
        if folder_pids and pid not in folder_pids:
            out.append("folder_identity_conflict")
    elif ptype == "household":
        members = idx["members"].get(pid, set())
        if strong_pids - members:
            out.append("household_person_conflict")
        if orgs:
            out.append("organization_person_conflict")
    elif ptype == "organization":
        if strong_pids or households:
            out.append("organization_person_conflict")
    return sorted(set(out)), (sig, households, orgs)


def _provenance(proposal, folder, idx):
    ptype, pid = proposal.get("proposed_entity_type"), proposal.get("proposed_entity_id")
    folder_pids = _folder_pids(folder, idx)
    if ptype == "person":
        supports = pid in folder_pids
    elif ptype == "household":
        supports = bool(idx["members"].get(pid, set()) & folder_pids)
    else:
        supports = False
    return "both" if supports else "content"   # HIGH is always content-driven; folder can corroborate


def _owner_exists(conn, etype, eid):
    """The proposed owner record must still exist (it could have been deleted after the batch)."""
    if eid is None:
        return False
    table = {"person": people, "household": _hh(), "organization": relationship_entities}.get(etype)
    if table is None:
        return False
    return conn.execute(select(table.c.id).where(table.c.id == eid).limit(1)).scalar() is not None


def _hh():
    from app.db import households
    return households


def evaluate_high(conn, did, idx, *, ocr=False):
    """Single source of truth — is document `did` a CLEAN, currently-HIGH proposal RIGHT NOW? READ-ONLY.

    Returns {status, reason, proposal, contradictions}. status:
      'ineligible' — not all-NULL / permanent-reject / missing (reason = why)
      'not_high'   — proposal is not HIGH (reason = the actual confidence)
      'excluded'   — HIGH but a contradiction fired (reason/contradictions = the classes)
      'eligible'   — HIGH and contradiction-free and the owner record still exists
    Used by the batch report, the bulk-confirm preview, AND the final pre-write recheck, so all three
    agree by construction."""
    proposal = propose_document_owner(did, conn=conn, idx=idx, with_text=True, ocr=ocr)
    if not proposal.get("eligible"):
        return {"status": "ineligible", "reason": proposal.get("reason"), "proposal": proposal,
                "contradictions": []}
    text = proposal.pop("text", "")
    if proposal.get("confidence") != "HIGH":
        return {"status": "not_high", "reason": proposal.get("confidence"), "proposal": proposal,
                "contradictions": []}
    folder = proposal.get("source_folder")
    contradictions, _sig = _contradictions(proposal, text, folder, idx)
    if not _owner_exists(conn, proposal.get("proposed_entity_type"), proposal.get("proposed_entity_id")):
        contradictions = sorted(set(contradictions) | {"owner_missing"})
    if contradictions:
        return {"status": "excluded", "reason": contradictions, "proposal": proposal,
                "contradictions": contradictions}
    return {"status": "eligible", "reason": None, "proposal": proposal, "contradictions": []}


def build_report_row(conn, did, proposal, contradictions, idx):
    """A sanitized per-document report row (no raw text, no full SSN) shared by the report + preview."""
    folder = proposal.get("source_folder")
    source_system, source_path = _doc_meta(conn, did, folder)
    method = proposal.get("extraction_method")
    return {
        "document_id": did,
        "filename": proposal.get("filename"),
        "source_system": source_system,
        "source_path": source_path,
        "extraction_method": method,
        "extraction_class": "ocr" if method in ("ocr", "ocr_cache") else "native",
        "proposed_entity_type": proposal.get("proposed_entity_type"),
        "proposed_entity_id": proposal.get("proposed_entity_id"),
        "proposed_entity_name": proposal.get("proposed_entity_name"),
        "confidence": proposal.get("confidence"),
        "evidence_classes": _evidence_classes(proposal.get("evidence") or []),
        "evidence": (proposal.get("evidence") or [])[:6],
        "identity_provenance": _provenance(proposal, folder, idx),
        "contradictions": contradictions,
        "eligible": not contradictions,
    }


def validate_high_proposals(*, limit=None, ocr=False):
    """READ-ONLY. Returns {high_total, eligible, excluded, native_high, ocr_high, reason_counts, rows}.
    Each row is a sanitized per-document record (no raw text, no full SSN). Writes nothing."""
    rows = []
    reasons: Counter = Counter()
    native_high = ocr_high = 0
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        idx = build_match_indexes(conn)
        for did in ids:
            ev = evaluate_high(conn, did, idx, ocr=ocr)
            if ev["status"] not in ("eligible", "excluded"):
                continue                                       # only HIGH proposals are reported
            row = build_report_row(conn, did, ev["proposal"], ev["contradictions"], idx)
            if row["extraction_class"] == "ocr":
                ocr_high += 1
            else:
                native_high += 1
            for c in row["contradictions"]:
                reasons[c] += 1
            rows.append(row)
    eligible = [r for r in rows if r["eligible"]]
    excluded = [r for r in rows if not r["eligible"]]
    return {
        "high_total": len(rows),
        "eligible": len(eligible),
        "excluded": len(excluded),
        "native_high": native_high,
        "ocr_high": ocr_high,
        "reason_counts": dict(reasons),
        "rows": rows,
    }
