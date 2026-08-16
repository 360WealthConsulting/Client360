"""Phase 4 — READ-ONLY context analysis of NO_MATCH documents.

For every genuinely-unassigned document whose owner proposal is NO_MATCH, decide whether SOURCE CONTEXT
(not another OCR pass) can defensibly associate it with an existing person / household / organization, and
classify it into exactly ONE bucket:

  A CONTEXT_HIGH        folder is uniquely & reliably mapped to one existing owner (a durable folder
                        decision, or resolved-neighbour documents that unanimously point to one owner),
                        and the document does not contradict it.
  B CONTEXT_LIKELY      the document lacks its own identity but sits in a strongly-mapped folder (a
                        household inferred from multiple resolved members, or a folder name that uniquely
                        maps to one canonical entity) — a good candidate for human approval.
  C CONFLICT            context points at more than one owner (folder holds documents for unrelated
                        parties), or the document names a different party than the folder owner.
  D GENERAL_OR_UNRESOLVED  no defensible owner from document OR context (likely firm/general material).
  E POSSIBLE_NEW_ENTITY the document carries a strong coherent identity with no existing canonical match
                        and no folder mapping (analysis only — no entity is created).

It WRITES NOTHING, assigns NO ownership, creates/merges NOTHING, and does not re-run OCR (it reuses the
OCR text already cached). Permanent rejects and already-owned documents are excluded. Folder-name
similarity ALONE is never CONTEXT_HIGH — A requires real ownership evidence (a durable decision or
resolved neighbours). Where a folder holds documents for multiple household members, it maps to the
HOUSEHOLD (bucket B), never to one guessed person.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import or_, select

from app.db import documents, engine, households, metadata, people, relationship_entities
from app.services import document_entity_proposal as ep
from app.services.document_high_validation import _doc_meta, _unassigned_ids
from app.services.document_owner_proposal import (
    _content_name_candidates,
    _norm,
    build_match_indexes,
    propose_document_owner,
)

BUCKETS = ("CONTEXT_HIGH", "CONTEXT_LIKELY", "CONFLICT", "GENERAL_OR_UNRESOLVED", "POSSIBLE_NEW_ENTITY")
_BUCKET_LETTER = {"CONTEXT_HIGH": "A", "CONTEXT_LIKELY": "B", "CONFLICT": "C",
                  "GENERAL_OR_UNRESOLVED": "D", "POSSIBLE_NEW_ENTITY": "E"}


def _entity_name(conn, etype, eid):
    table = {"person": people, "household": households, "organization": relationship_entities}.get(etype)
    if table is None or eid is None:
        return None
    col = table.c.full_name if etype == "person" else table.c.name
    return conn.execute(select(col).where(table.c.id == eid)).scalar()


def _folder_resolved_owners(conn):
    """folder(normalised) -> list of {doc_id, type, id} for every ALREADY-OWNED document in that folder.
    This is real ownership evidence (resolved neighbours), the basis for a reliable folder mapping."""
    out: dict[str, list] = {}
    rows = conn.execute(select(documents.c.id, documents.c.tags, documents.c.person_id,
                               documents.c.household_id, documents.c.organization_id)
                        .where(or_(documents.c.person_id.isnot(None),
                                   documents.c.household_id.isnot(None),
                                   documents.c.organization_id.isnot(None)),
                               documents.c.status != "deleted")).mappings()
    for r in rows:
        folder = (r["tags"] or {}).get("taxdome_folder")
        if not folder:
            continue
        key = _norm(folder)
        for etype, eid in (("person", r["person_id"]), ("household", r["household_id"]),
                           ("organization", r["organization_id"])):
            if eid is not None:
                out.setdefault(key, []).append({"doc_id": r["id"], "type": etype, "id": eid})
    return out


def _folder_decision_map(conn):
    """folder(normalised) -> (type, id) from durable folder-resolution decisions, when that table exists."""
    frd = metadata.tables.get("folder_resolution_decisions")
    if frd is None:
        return {}
    out = {}
    try:
        rows = conn.execute(select(frd.c.subject_key, frd.c.display_name, frd.c.resulting_entity_type,
                                   frd.c.resulting_entity_id, frd.c.active)
                            .where(frd.c.active.is_(True),
                                   frd.c.resulting_entity_id.isnot(None))).mappings()
    except Exception:  # noqa: BLE001 — schema variance; treat as no decisions
        return {}
    for r in rows:
        et = (r["resulting_entity_type"] or "").lower()
        if et not in ("person", "household", "organization"):
            continue
        for name in (r["subject_key"], r["display_name"]):
            if name:
                out[_norm(name)] = (et, r["resulting_entity_id"])
    return out


def _resolve_folder_owner(conn, folder, folder_resolved, folder_decisions, idx):
    """Establish the folder's owner from real evidence. Returns a dict with `kind`:
    decision | unique | household | conflict | none, plus owner/owners/source/support doc ids."""
    if not folder:
        return {"kind": "none"}
    key = _norm(folder)
    dec = folder_decisions.get(key)
    if dec:
        return {"kind": "decision", "owner": (dec[0], dec[1], _entity_name(conn, dec[0], dec[1])),
                "source": "durable folder-resolution decision", "support": []}
    entries = folder_resolved.get(key, [])
    if not entries:
        return {"kind": "none"}
    owners = {(e["type"], e["id"]) for e in entries}
    if len(owners) == 1:
        t, i = next(iter(owners))
        support = [e["doc_id"] for e in entries]
        return {"kind": "unique", "owner": (t, i, _entity_name(conn, t, i)),
                "source": "resolved neighbour documents", "support": support}
    persons = [i for (t, i) in owners if t == "person"]
    houses = [i for (t, i) in owners if t == "household"]
    orgs = [i for (t, i) in owners if t == "organization"]
    hh_of_persons = {idx["pid"].get(i, {}).get("household_id") for i in persons}
    hh_of_persons = {h for h in hh_of_persons if h}
    # all resolved persons share ONE household and no org/other household -> the household owns the folder
    if persons and not orgs and not houses and len(hh_of_persons) == 1:
        hid = next(iter(hh_of_persons))
        return {"kind": "household", "owner": ("household", hid, _entity_name(conn, "household", hid)),
                "source": "multiple household members resolved in folder",
                "support": [e["doc_id"] for e in entries]}
    return {"kind": "conflict", "owners": sorted(owners), "support": [e["doc_id"] for e in entries]}


def _folder_name_entity(folder, idx):
    """Weak signal: does the folder NAME uniquely map to one canonical entity? Returns (type,id,name),
    'ambiguous', or None. Never sufficient for CONTEXT_HIGH on its own."""
    if not folder:
        return None
    full, first_last, _ = _content_name_candidates(folder)
    matches = set()
    for nm in full:
        for pid in idx["name"].get(nm, []):
            matches.add(("person", pid))
        if nm in idx["biz"]:
            matches.add(("organization", idx["biz"][nm][0]))
    for pair in first_last:
        for pid in idx["first_last"].get(pair, []):
            matches.add(("person", pid))
    for hid, hname in idx["hh_name"].items():
        if _norm(hname) and _norm(hname) in _norm(folder):
            matches.add(("household", hid))
    if not matches:
        return None
    if len(matches) > 1:
        return "ambiguous"
    t, i = next(iter(matches))
    return (t, i)


def _doc_strong_identity(text, idx):
    """A coherent, corroborated identity in the document itself (mirrors the new-entity detector's rule),
    or None. Reuses the entity-proposal extractor; no re-proposal."""
    ident = ep._extract_identity(text)
    businesses = [b for b in ident["businesses"] if not ep._is_institution(b, idx)]
    names = ident["names"]
    has_addr = bool(ident["zips"] or ident["streets"])
    if len(names) >= 2 and len({_norm(n).split()[-1] for n in names}) == 1:
        return ("household", names[0], ident)
    if businesses and not names:
        return ("organization", businesses[0], ident)
    if names and (ident["emails"] or ident["phones"] or has_addr):
        return ("person", names[0], ident)
    return None


def _classify(conn, did, proposal, text, folder, idx, folder_resolved, folder_decisions):
    fo = _resolve_folder_owner(conn, folder, folder_resolved, folder_decisions, idx)
    strong = _doc_strong_identity(text, idx)
    source_system, source_path = _doc_meta(conn, did, folder)
    base = {"document_id": did, "filename": proposal.get("filename"), "source_system": source_system,
            "source_path": source_path, "extraction_method": proposal.get("extraction_method"),
            "proposed_owner_type": None, "proposed_owner_id": None, "proposed_owner_name": None,
            "contextual_confidence": None, "evidence": None, "supporting_documents": fo.get("support", []),
            "folder_mapping": None, "contradicting_evidence": None}

    def _contradicts(owner):
        # the document itself strongly names a DIFFERENT person than a PERSON-owned folder
        if not strong or strong[0] != "person" or owner[0] != "person":
            return None
        doc_sur = _norm(strong[1]).split()[-1]
        own_sur = _norm(idx["pid"].get(owner[1], {}).get("name") or "").split()
        own_sur = own_sur[-1] if own_sur else ""
        if doc_sur and own_sur and doc_sur != own_sur:
            return f"document names '{strong[1]}' but folder owner is '{owner[2]}'"
        return None

    if fo["kind"] == "conflict":
        base.update({"bucket": "CONFLICT", "folder_mapping": "ambiguous (multiple owners resolved)",
                     "evidence": "folder holds resolved documents for unrelated owners: "
                                 + ", ".join(f"{t} #{i}" for t, i in fo["owners"])})
        return base
    if fo["kind"] in ("unique", "decision", "household"):
        owner = fo["owner"]
        contra = _contradicts(owner)
        if contra:
            base.update({"bucket": "CONFLICT", "folder_mapping": "unique", "contradicting_evidence": contra,
                         "evidence": f"folder maps to {owner[0]} #{owner[1]} via {fo['source']}"})
            return base
        base.update({"proposed_owner_type": owner[0], "proposed_owner_id": owner[1],
                     "proposed_owner_name": owner[2], "evidence": f"folder maps to this owner via {fo['source']}"})
        if fo["kind"] == "household":
            base.update({"bucket": "CONTEXT_LIKELY", "contextual_confidence": "likely",
                         "folder_mapping": "household (multiple members resolved)"})
        else:
            base.update({"bucket": "CONTEXT_HIGH", "contextual_confidence": "high", "folder_mapping": "unique"})
        return base

    # no resolved/decision mapping -> try folder-NAME mapping (weaker), then doc identity
    fn = _folder_name_entity(folder, idx)
    if fn == "ambiguous":
        base.update({"bucket": "CONFLICT", "folder_mapping": "ambiguous (folder name matches several entities)",
                     "evidence": "folder name maps to more than one canonical entity"})
        return base
    if fn:
        base.update({"bucket": "CONTEXT_LIKELY", "contextual_confidence": "likely", "folder_mapping": "name-only unique",
                     "proposed_owner_type": fn[0], "proposed_owner_id": fn[1],
                     "proposed_owner_name": _entity_name(conn, fn[0], fn[1]),
                     "evidence": "folder name uniquely maps to a canonical entity (no resolved neighbours)"})
        return base
    if strong:
        base.update({"bucket": "POSSIBLE_NEW_ENTITY", "folder_mapping": "none",
                     "evidence": f"document carries a strong {strong[0]} identity ('{strong[1]}') with no "
                                 "existing canonical match and no folder mapping"})
        return base
    base.update({"bucket": "GENERAL_OR_UNRESOLVED", "folder_mapping": "none",
                 "evidence": "no defensible owner from document or folder context"})
    return base


def analyze_nomatch(*, limit=None, ocr=False):
    """READ-ONLY. Classify every current NO_MATCH document by source context. Returns
    {total, counts{bucket->n}, rows[], folder_stats{}, top_folders[], reasons{}}. Writes nothing."""
    rows = []
    counts: Counter = Counter()
    folder_nomatch: Counter = Counter()
    reasons: Counter = Counter()
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        idx = build_match_indexes(conn)
        folder_resolved = _folder_resolved_owners(conn)
        folder_decisions = _folder_decision_map(conn)
        for did in ids:
            proposal = propose_document_owner(did, conn=conn, idx=idx, with_text=True, ocr=ocr)
            if not proposal.get("eligible") or proposal.get("confidence") != "NO_MATCH":
                continue
            text = proposal.pop("text", "")
            folder = proposal.get("source_folder")
            row = _classify(conn, did, proposal, text, folder, idx, folder_resolved, folder_decisions)
            counts[row["bucket"]] += 1
            reasons[row["evidence"]] += 1
            if folder:
                folder_nomatch[folder] += 1
            rows.append(row)

        # folder statistics
        folders = {r["source_path"] for r in rows if r["source_path"]}
        unique_mapped = sum(1 for f in folders
                            if _resolve_folder_owner(conn, f, folder_resolved, folder_decisions, idx)["kind"]
                            in ("unique", "decision"))
        mixed = sum(1 for f in folders
                    if _resolve_folder_owner(conn, f, folder_resolved, folder_decisions, idx)["kind"]
                    in ("conflict", "household"))
    top_folders = [{"folder": f, "nomatch_docs": n} for f, n in folder_nomatch.most_common(20)]
    top_reasons = [{"reason": r, "count": n} for r, n in reasons.most_common(10)]
    return {
        "total": len(rows),
        "counts": {b: counts.get(b, 0) for b in BUCKETS},
        "letters": {_BUCKET_LETTER[b]: counts.get(b, 0) for b in BUCKETS},
        "rows": rows,
        "folder_stats": {"unique_folders": len(folders), "unique_mapped": unique_mapped,
                         "mixed_or_ambiguous": mixed},
        "top_folders": top_folders,
        "reasons": top_reasons,
    }
