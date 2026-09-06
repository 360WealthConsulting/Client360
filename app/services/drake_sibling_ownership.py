"""Drake client-id carry-over ownership — READ-ONLY eligibility. Never writes.

WHY THIS EXISTS SEPARATELY FROM THE GENERIC OWNER MANIFEST PATH
    ``scripts/apply_owner_manifest.py`` requires the deployed proposal engine to independently
    propose the SAME owner at HIGH confidence. Measured against the 33 documents the first repaired
    Drake sync created, that gate passes 0 of 23 candidates: 21 return HOLD because the engine's
    proposal generator needs a filename or SharePoint folder name, and a Drake DDM document has
    neither — its filenames are ``1.PDF``, ``F.PDF``, ``TaxFormSelection__03212022_104540.pdf``.
    Requiring positive agreement therefore makes the population permanently zero.

    The evidence Drake actually has is the NATIVE CLIENT ID: 8 hex characters extracted structurally
    from the DDM directory layout (``<root>\\<bucket>\\<CLIENT_ID>\\Documents\\<file>``), which exists
    before Client360 sees the file and is not derived from any name. When every already-owned Drake
    document under one client id agrees on one owner, assigning a new document under that same id
    carries forward a decision a human already made. That is a different evidence class, not a
    weaker one — and it resolves exactly the content-free documents name matching cannot.

    So the policy is NON_CONTRADICTION_REQUIRED, never IGNORED: the engine does not have to agree,
    but it may not disagree, and the document's own content may not name a competing party. Ignoring
    the engine entirely would have assigned document 121833 to a person while the engine held HIGH
    evidence for a household.

FIRM PREPARER SIGNALS ARE NOISE, NOT CONFLICT
    Every tax return names its preparer and the preparing firm. Counting those as evidence AGAINST
    the client is the same failure the codebase already fixed once, when the firm's own switchboard
    number put 2,157 documents on one person. Verified firm staff and firm/self entities are
    therefore ignored FOR CONTRADICTION PURPOSES ONLY.

    They never become positive evidence, and they never become owners: rule 12 rejects a proposed
    owner that is firm staff or a firm/self entity outright. The exclusion set is not a name list
    and not a frequency heuristic — it is whatever ``build_match_indexes`` derived from the firm's
    narrowly-held mail domain and the SharePoint library roots.

RETROSPECTIVE MODE
    ``evaluate(..., retrospective=True)`` scores a document that ALREADY has an owner, so the rule
    can be measured against decisions humans already made. It skips only the all-NULL requirement;
    every other check runs unchanged. The target is always excluded from its own sibling set, so its
    own ownership can never manufacture its own proposal.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db import documents, metadata
from app.services import document_high_validation as hv
from app.services.document_owner_proposal import (
    PERMANENT_REJECT_DOCUMENT_IDS,
    extract_document_text,
)
from app.services.drake_document_owner import FROZEN_DRAKE_DOCUMENT_IDS

#: Resolved through metadata (as document_owner_proposal does) so a deployment without the table
#: still imports this module.
document_sources = metadata.tables["document_sources"]

SOURCE_SYSTEM = "Drake"

#: The DDM client id: exactly 8 hex characters, uppercase or lowercase.
CLIENT_ID_RE = re.compile(r"\A[0-9A-Fa-f]{8}\Z")

#: Contradiction classes that block a carry-over. Every one names a COMPETING party; none of them
#: is a quality signal about the document itself.
BLOCKING_CONTRADICTIONS = (
    "foreign_strong_identifier",
    "multiple_strong_identities",
    "multiple_named_identities",
    "household_person_conflict",
    "organization_person_conflict",
    "folder_identity_conflict",
    "placeholder_candidate",
    "engine_proposes_different_owner",
)

#: Verdict reasons, one per failing rule. Stable strings — the preview and the apply both key on them.
R_NO_CLIENT_ID = "no_valid_client_id"
R_NO_SOURCE = "no_available_drake_source"
R_NO_SIBLING = "no_owned_sibling"
R_MULTI_SIBLING = "sibling_owners_disagree"
R_ALREADY_OWNED = "already_owned"
R_NOT_ACTIVE = "not_active"
R_CONTRADICTED = "contradicted"
R_OWNER_INELIGIBLE = "owner_not_eligible"
R_OWNER_IS_FIRM = "owner_is_firm_staff_or_entity"
R_REJECTED = "permanent_reject_or_frozen"


def firm_identities(idx):
    """The verified firm staff / firm-self sets, straight from the deployed index. No name list."""
    return set(idx.get("staff") or ()), set(idx.get("firm_entities") or ())


def _document_row(conn, document_id):
    return conn.execute(
        select(documents.c.id, documents.c.original_name, documents.c.person_id,
               documents.c.household_id, documents.c.organization_id, documents.c.status,
               documents.c.archived, documents.c.deleted_at, documents.c.storage_uri,
               documents.c.storage_path, documents.c.tags, documents.c.category,
               documents.c.classification, documents.c.subcategory)
        .where(documents.c.id == document_id)).mappings().first()


def drake_source(conn, document_id):
    """The document's Drake source reference, or None. Availability is the caller's check."""
    return conn.execute(
        select(document_sources.c.id, document_sources.c.source_external_id,
               document_sources.c.available, document_sources.c.source_uri)
        .where(document_sources.c.document_id == document_id,
               document_sources.c.source_system == SOURCE_SYSTEM)
        .order_by(document_sources.c.id.desc()).limit(1)).mappings().first()


def sibling_owners(conn, client_id, *, exclude_document_id):
    """Owner tuples held by OTHER live Drake documents under the same client id.

    ``exclude_document_id`` is what keeps a retrospective honest: a document must never contribute
    its own ownership to the evidence that is supposed to predict it."""
    rows = conn.execute(
        select(documents.c.id, documents.c.person_id, documents.c.household_id,
               documents.c.organization_id)
        .select_from(documents.join(document_sources,
                                    document_sources.c.document_id == documents.c.id))
        .where(document_sources.c.source_system == SOURCE_SYSTEM,
               document_sources.c.source_external_id == client_id,
               documents.c.status != "deleted",
               documents.c.archived.is_(False),
               documents.c.id != exclude_document_id)).mappings().all()
    owned = [r for r in rows
             if r["person_id"] or r["household_id"] or r["organization_id"]]
    tuples = {(r["person_id"], r["household_id"], r["organization_id"]) for r in owned}
    return tuples, sorted(r["id"] for r in owned)


def owner_of(tup):
    """(person, household, organization) -> ("person"|"household"|"organization", id)."""
    p, h, o = tup
    if p:
        return ("person", p)
    if h:
        return ("household", h)
    return ("organization", o)


def document_signals(conn, row, idx, *, ocr=False):
    """Extract the document's identity signals with the SAME primitive the engine uses.

    Deliberately calls ``extract_document_text`` directly rather than ``propose_document_owner``:
    that function refuses an already-owned document before it reaches extraction, which silently
    produced empty text — and therefore a vacuous contradiction check — for every historical
    document in the first retrospective attempt."""
    from pathlib import Path
    path = None
    if row["storage_uri"] and Path(row["storage_uri"]).is_absolute():
        path = Path(row["storage_uri"])
    elif row["storage_path"]:
        path = Path(row["storage_path"])
    text, method = extract_document_text(conn, row, path, ocr=ocr)
    sig, households, orgs = hv._doc_signals(text, idx)
    folder = (row["tags"] or {}).get("taxdome_folder")
    return {"sig": sig, "households": households, "orgs": orgs, "folder": folder,
            "method": method, "text_len": len(text or "")}


def contradictions(candidate, signals, idx, *, exclude_firm):
    """Competing-party classes for ``candidate``. Mirrors ``document_high_validation._contradictions``
    with two changes: the proposal comes from sibling evidence rather than the engine, and verified
    firm identities may be dropped from the signal set first."""
    staff, firm = firm_identities(idx)
    sig, households, orgs = signals["sig"], set(signals["households"]), set(signals["orgs"])
    excluded = {"staff": sorted(p for p in sig if p in staff),
                "entities": sorted(o for o in orgs if o in firm)}
    if exclude_firm:
        sig = {p: s for p, s in sig.items() if p not in staff}
        orgs = orgs - firm
    ptype, oid = candidate
    strong = {p for p, s in sig.items() if s & {"email", "phone"}}
    named = {p for p, s in sig.items() if "name" in s}
    out = []
    if ptype == "person":
        info = idx["pid"].get(oid, {})
        if hv._placeholder_name(info.get("name")):
            out.append("placeholder_candidate")
        household = info.get("household_id")
        co_members = idx["members"].get(household, set()) if household else set()
        if strong - {oid}:
            out.append("foreign_strong_identifier")
        if len(strong) >= 2:
            out.append("multiple_strong_identities")
        if (named - {oid}) - co_members:
            out.append("multiple_named_identities")
        if households:
            out.append("household_person_conflict")
        if orgs:
            out.append("organization_person_conflict")
        folder_pids = hv._folder_pids(signals["folder"], idx)
        if folder_pids and oid not in folder_pids:
            out.append("folder_identity_conflict")
    elif ptype == "household":
        members = idx["members"].get(oid, set())
        if strong - members:
            out.append("household_person_conflict")
        if orgs:
            out.append("organization_person_conflict")
    else:
        if strong or households:
            out.append("organization_person_conflict")
    return sorted(set(out)), excluded


def owner_is_eligible(candidate, idx):
    """Rule 11 + 12. A firm identity is never an owner, however strong the sibling evidence is."""
    ptype, oid = candidate
    staff, firm = firm_identities(idx)
    if ptype == "person":
        if oid in staff:
            return False, R_OWNER_IS_FIRM
        return (oid in (idx.get("owner_eligible") or ())), R_OWNER_INELIGIBLE
    if ptype == "organization":
        if oid in firm:
            return False, R_OWNER_IS_FIRM
        return (oid in (idx.get("org_eligible") or ())), R_OWNER_INELIGIBLE
    members = idx["members"].get(oid, set())
    return bool(members & (idx.get("owner_eligible") or set())), R_OWNER_INELIGIBLE


def engine_proposal(conn, document_id, idx):
    """The deployed engine's own answer, or (None, None, None) when it declines/refuses.

    Only a HIGH proposal naming a DIFFERENT owner blocks. A HOLD is silence, not disagreement."""
    from app.services.document_owner_proposal import propose_document_owner
    try:
        p = propose_document_owner(document_id, conn=conn, idx=idx, with_text=False, ocr=False)
    except Exception:  # noqa: BLE001 — the engine must never break eligibility scoring
        return (None, None, None)
    if not p.get("eligible"):
        return (None, None, p.get("reason"))
    return (p.get("proposed_entity_type"), p.get("proposed_entity_id"), p.get("confidence"))


def evaluate(conn, document_id, idx, *, exclude_firm=True, retrospective=False, ocr=False):
    """Score one document. Returns a verdict dict; ``eligible`` is True only when every rule passes.

    ``retrospective=True`` skips ONLY the all-NULL requirement so an already-owned historical
    document can be scored against the decision a human already made."""
    v = {"document_id": document_id, "eligible": False, "reasons": [], "candidate": None,
         "client_id": None, "siblings": [], "sibling_tuples": [], "engine": None,
         "contradictions_strict": [], "contradictions_tuned": [], "firm_excluded": None,
         "prior": None, "text_len": None, "extract_method": None}

    row = _document_row(conn, document_id)
    if row is None:
        v["reasons"].append("not_found")
        return v
    v["prior"] = {"person_id": row["person_id"], "household_id": row["household_id"],
                  "organization_id": row["organization_id"]}

    if document_id in PERMANENT_REJECT_DOCUMENT_IDS or document_id in FROZEN_DRAKE_DOCUMENT_IDS:
        v["reasons"].append(R_REJECTED)
    if row["status"] == "deleted" or row["archived"] or row["deleted_at"] is not None:
        v["reasons"].append(R_NOT_ACTIVE)
    owned = any(v["prior"][k] for k in ("person_id", "household_id", "organization_id"))
    if owned and not retrospective:
        v["reasons"].append(R_ALREADY_OWNED)

    src = drake_source(conn, document_id)
    if src is None or not src["available"] or not CLIENT_ID_RE.match(str(src["source_external_id"] or "")):
        v["reasons"].append(R_NO_SOURCE if src is None or not src["available"] else R_NO_CLIENT_ID)
        return v
    v["client_id"] = src["source_external_id"]

    tuples, sibs = sibling_owners(conn, v["client_id"], exclude_document_id=document_id)
    # Owner tuples mix ints and None, so sort on a None-safe key rather than the tuple itself.
    v["siblings"] = sibs
    v["sibling_tuples"] = sorted(tuples, key=lambda t: tuple((x is None, x or 0) for x in t))
    if not tuples:
        v["reasons"].append(R_NO_SIBLING)
        return v
    if len(tuples) > 1:
        v["reasons"].append(R_MULTI_SIBLING)
        return v

    candidate = owner_of(next(iter(tuples)))
    v["candidate"] = candidate
    ok, why = owner_is_eligible(candidate, idx)
    if not ok:
        v["reasons"].append(why)

    # A deleted or unreadable document can still reach here (reasons accumulate rather than return
    # early, so the preview can explain a rejection fully). Extraction must never be the thing that
    # breaks scoring: on failure the document simply has no signals, and the reasons already
    # collected stand.
    try:
        signals = document_signals(conn, row, idx, ocr=ocr)
    except Exception:  # noqa: BLE001 — an unreadable file is a fact about the file, not an error here
        signals = {"sig": {}, "households": set(), "orgs": set(), "folder": None,
                   "method": "unreadable", "text_len": 0}
    v["text_len"], v["extract_method"] = signals["text_len"], signals["method"]
    strict, excluded = contradictions(candidate, signals, idx, exclude_firm=False)
    tuned, _ = contradictions(candidate, signals, idx, exclude_firm=True)
    v["firm_excluded"] = excluded

    etype, eid, econf = engine_proposal(conn, document_id, idx)
    v["engine"] = {"type": etype, "id": eid, "confidence": econf}
    if econf == "HIGH" and (etype, eid) != candidate:
        strict = sorted({*strict, "engine_proposes_different_owner"})
        tuned = sorted({*tuned, "engine_proposes_different_owner"})
    v["contradictions_strict"], v["contradictions_tuned"] = strict, tuned

    active = tuned if exclude_firm else strict
    if active:
        v["reasons"].append(R_CONTRADICTED)
    v["eligible"] = not v["reasons"]
    return v
