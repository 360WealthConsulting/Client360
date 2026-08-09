"""Resolution evidence assembler — strictly READ-ONLY (PR-2).

Assembles a generic ``ResolutionSubject`` evidence bundle for the review workflow: everything a human (or
a later automated reuse step) needs to resolve an unresolved ingestion subject to a canonical Client360
entity — WITHOUT writing anything. It is subject-GENERIC: a TaxDome folder today, and acquired advisor
books / acquired firms / network-drive folders / CRM imports / custodian exports / scanned-paper (OCR)
batches / email attachments later, all through the same contract.

It DUPLICATES NO matching logic. It reuses:
  * scripts.migration.plan_next_linkage_batch  — identity indexes, stable-identity resolver, stable_ids,
    alternate-name candidate finder (the deterministic source-contact resolver / suggested action);
  * scripts.migration.audit_canonical_population — disposition classifier + name indexes + term search;
  * app.services.migration.linkage.LinkageRemediationJob — the deterministic folder->existing-entity
    resolver (via the Identity Service + organization index);
  * app.importers.taxdome_drive.suggest_people — canonical person candidate search;
  * app.services.resolution_knowledge — the durable folder_resolution_decisions lookup service.

STRICTLY READ-ONLY: SELECT only. No canonical writes, no person_source_links writes, no
folder_resolution_decisions writes, no exceptions created, no document ownership changes, no file
movement, no storage_uri / document_sources changes. It never weakens the deterministic matching rules.

Uncertainty is preserved, never collapsed: every candidate carries a ``basis`` and a ``deterministic``
flag; a shared identifier is labelled ``shared_identifier`` (never promoted to certainty); conflicting
evidence is surfaced in ``evidence_flags`` and all candidates are retained; the ``held_reason`` from the
deterministic resolver stays visible; superseded / non-reusable ledger decisions are reported as history
and are never returned as the reusable resolution.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.importers.taxdome_drive import _name_key, suggest_people
from app.services.migration.config import MigrationConfig
from app.services.migration.linkage import LinkageRemediationJob
from app.services.resolution_knowledge import (
    get_current_decision,
    get_decision_history,
    get_reusable_resolution,
)
from scripts.migration.audit_canonical_population import build_name_index, classify_folder
from scripts.migration.plan_next_linkage_batch import (
    _alt_candidates,
    _identity_person,
    _shared_identity,
    _tokset,
    build_indexes,
)
from scripts.migration.plan_next_linkage_batch import _load as _plan_load
from scripts.migration.plan_next_linkage_batch import resolve_folder as plan_resolve_folder

_ACCOUNT_KEYS = ("account", "account_number", "acct", "account_no")
_MAX_CANDIDATES = 25
_MAX_DOCUMENTS = 200


@dataclass
class AssemblerContext:
    """Canonical directory + identity indexes, loaded ONCE and reused across subjects (read-only)."""
    unlinked: list
    people: list
    entities: list                       # standalone relationship_entities (business/trust/estate/...)
    idx: dict                            # plan_next_linkage_batch identity indexes
    people_key: dict
    people_tok: list
    sc_key: dict
    sc_tok: list
    households: list
    hh_by_key: dict
    hh_tok: list
    ent_by_key: dict
    ent_tok: list
    job: LinkageRemediationJob           # deterministic folder -> existing-entity resolver
    org_index: dict
    prow: dict                           # person id -> person row (from idx)
    hh_name: dict                        # household id -> name


def build_context(config: MigrationConfig | None = None) -> AssemblerContext:
    """Load the canonical directory + indexes once. Reuses the plan/audit/linkage loaders — read-only."""
    from sqlalchemy import select, text

    from app.db import engine, households
    config = config or MigrationConfig.from_env()
    unlinked, people, entities = _plan_load(engine)
    idx = build_indexes(unlinked, people)
    people_key, people_tok = build_name_index(
        [(p["id"], p["full_name"], p.get("first_name"), p.get("last_name")) for p in people])
    sc_key, sc_tok = build_name_index(
        [(s["id"], s["full_name"], s.get("first_name"), s.get("last_name")) for s in unlinked])

    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        hh_rows = [dict(m) for m in conn.execute(select(households.c.id, households.c.name)).mappings()]

    hh_by_key: dict = {}
    hh_tok: list = []
    hh_name: dict = {}
    for h in hh_rows:
        hh_name[h["id"]] = h["name"]
        k = _name_key(h["name"])
        if k:
            hh_by_key.setdefault(k, []).append(h)
        t = _tokset(h["name"])
        if t:
            hh_tok.append((t, h))

    ent_by_key: dict = {}
    ent_tok: list = []
    for e in entities:
        k = _name_key(e["name"])
        if k:
            ent_by_key.setdefault(k, []).append(e)
        t = _tokset(e["name"])
        if t:
            ent_tok.append((t, e))

    job = LinkageRemediationJob(config)
    job.identity.load()
    org_index = job._load_org_index()

    return AssemblerContext(
        unlinked=unlinked, people=people, entities=entities, idx=idx,
        people_key=people_key, people_tok=people_tok, sc_key=sc_key, sc_tok=sc_tok,
        households=hh_rows, hh_by_key=hh_by_key, hh_tok=hh_tok, ent_by_key=ent_by_key, ent_tok=ent_tok,
        job=job, org_index=org_index, prow=idx["prow"], hh_name=hh_name)


# --------------------------------------------------------------------------- candidate assembly (pure)

def _extract_accounts(raw_data) -> list[str]:
    if not isinstance(raw_data, dict):
        return []
    lower = {str(k).strip().lower(): v for k, v in raw_data.items()}
    out = []
    for k in _ACCOUNT_KEYS:
        v = lower.get(k)
        if v not in (None, "", []):
            out.append(str(v).strip())
    return out


def _source_contact_candidates(display_name, ctx) -> tuple[list, bool]:
    """Source-contact candidates: exact name-key (deterministic) + alternate-name token overlap (fuzzy).
    A candidate whose email/phone is shared with another unlinked contact is labelled shared_identifier."""
    fk = _name_key(display_name)
    seen: set = set()
    out: list = []
    shared_flag = False
    for sc in ctx.idx["sc_by_key"].get(fk, []):
        shared = _shared_identity(sc, ctx.idx)
        shared_flag = shared_flag or shared
        out.append(_sc_row(sc, "shared_identifier" if shared else "exact_name",
                           deterministic=not shared))
        seen.add(sc["id"])
    for sc in _alt_candidates(set(fk.split()), ctx.idx["sc_tokens"]):
        if sc["id"] in seen:
            continue
        out.append(_sc_row(sc, "alternate_name", deterministic=False))
        seen.add(sc["id"])
    return out[:_MAX_CANDIDATES], shared_flag


def _sc_row(sc, basis, *, deterministic) -> dict:
    return {"source_contact_id": sc["id"], "full_name": sc.get("full_name"),
            "source_system": sc.get("source_system"), "source_record_id": sc.get("source_record_id"),
            "email": sc.get("normalized_email"), "phone": sc.get("normalized_phone"),
            "basis": basis, "deterministic": deterministic}


def _person_candidates(display_name, sc_candidates, ctx, conn) -> tuple[list, bool]:
    """Person candidates from suggest_people (exact/loose) + stable email/phone identity of the matched
    source contacts. Deduped by person_id, strongest basis wins. Returns (candidates, has_shared)."""
    strength = {"exact_name": 3, "stable_identity": 2, "alternate_name_fuzzy": 1, "shared_identifier": 0}
    best: dict = {}
    shared_flag = False

    def offer(pid, full_name, basis, deterministic, via):
        cur = best.get(pid)
        if cur is None or strength.get(basis, 0) > strength.get(cur["basis"], 0):
            best[pid] = {"person_id": pid, "full_name": full_name, "basis": basis,
                         "deterministic": deterministic, "via": via}

    for s in suggest_people(conn, display_name, limit=_MAX_CANDIDATES):
        exact = s.get("confidence") == "high"
        offer(s["id"], s.get("full_name"),
              "exact_name" if exact else "alternate_name_fuzzy", exact, s.get("reason"))

    # stable email/phone identity from the folder's matched source contacts
    for cand in sc_candidates:
        sc = next((x for x in ctx.unlinked if x["id"] == cand["source_contact_id"]), None)
        if sc is None:
            continue
        shared = _shared_identity(sc, ctx.idx)
        for pid in _identity_person(sc, ctx.idx):
            person = ctx.prow.get(pid, {})
            if shared:
                shared_flag = True
                offer(pid, person.get("full_name"), "shared_identifier", False,
                      f"identifier shared with another unlinked contact (sc#{sc['id']})")
            else:
                offer(pid, person.get("full_name"), "stable_identity", True,
                      f"unique email/phone match via sc#{sc['id']}")

    return list(best.values())[:_MAX_CANDIDATES], shared_flag


def _household_candidates(display_name, ctx) -> list:
    fk = _name_key(display_name)
    seen: set = set()
    out: list = []
    for h in ctx.hh_by_key.get(fk, []):
        out.append({"household_id": h["id"], "name": h["name"], "basis": "exact_name",
                    "deterministic": True})
        seen.add(h["id"])
    for h in _alt_candidates(set(fk.split()), ctx.hh_tok):
        if h["id"] in seen:
            continue
        out.append({"household_id": h["id"], "name": h["name"], "basis": "alternate_name",
                    "deterministic": False})
        seen.add(h["id"])
    return out[:_MAX_CANDIDATES]


def _business_candidates(display_name, ctx) -> list:
    fk = _name_key(display_name)
    seen: set = set()
    out: list = []
    for e in ctx.ent_by_key.get(fk, []):
        out.append({"entity_id": e["id"], "name": e["name"], "entity_type": e["entity_type"],
                    "basis": "exact_name", "deterministic": True})
        seen.add(e["id"])
    for e in _alt_candidates(set(fk.split()), ctx.ent_tok):
        if e["id"] in seen:
            continue
        out.append({"entity_id": e["id"], "name": e["name"], "entity_type": e["entity_type"],
                    "basis": "alternate_name", "deterministic": False})
        seen.add(e["id"])
    return out[:_MAX_CANDIDATES]


def _provenance(sc_candidates, ctx) -> dict:
    """Aggregate provenance from candidate source contacts, by source system (Wealthbox/Drake/Schwab/…)."""
    prov: dict = {}
    for cand in sc_candidates:
        sc = next((x for x in ctx.unlinked if x["id"] == cand["source_contact_id"]), None)
        if sc is None:
            continue
        system = sc.get("source_system") or "(unknown)"
        entry = prov.setdefault(system, {"count": 0, "source_record_ids": [], "signals": Counter()})
        entry["count"] += 1
        if sc.get("source_record_id"):
            entry["source_record_ids"].append(sc["source_record_id"])
        raw = sc.get("raw_data") if isinstance(sc.get("raw_data"), dict) else {}
        if system == "Drake" and raw.get("return_type"):
            entry["signals"][f"return_type={raw['return_type']}"] += 1
        elif system == "Wealthbox" and (raw.get("type") or raw.get("contact_type")):
            entry["signals"][f"type={raw.get('type') or raw.get('contact_type')}"] += 1
    for entry in prov.values():
        entry["signals"] = dict(entry["signals"])
    return prov


def _identifiers(sc_candidates, ctx, extra) -> dict:
    emails: set = set()
    phones: set = set()
    accounts: set = set()
    for cand in sc_candidates:
        sc = next((x for x in ctx.unlinked if x["id"] == cand["source_contact_id"]), None)
        if sc is None:
            continue
        if sc.get("normalized_email"):
            emails.add(sc["normalized_email"])
        if sc.get("normalized_phone"):
            phones.add(sc["normalized_phone"])
        accounts.update(_extract_accounts(sc.get("raw_data")))
    if isinstance(extra, dict):
        emails.update(extra.get("emails") or [])
        phones.update(extra.get("phones") or [])
        accounts.update(extra.get("accounts") or [])
    return {"emails": sorted(emails), "phones": sorted(phones), "accounts": sorted(accounts)}


def _relationships(person_candidates, ctx) -> list:
    """Known relationships surfaced from the candidates — household membership of candidate people."""
    out: list = []
    for c in person_candidates:
        person = ctx.prow.get(c["person_id"], {})
        hid = person.get("household_id")
        if hid is not None:
            out.append({"kind": "household_membership", "person_id": c["person_id"],
                        "household_id": hid, "household_name": ctx.hh_name.get(hid)})
    return out


# --------------------------------------------------------------------------- documents (read-only)

def _folder_documents(conn, folder_display_name) -> list:
    from sqlalchemy import and_, select

    from app.db import documents
    where = and_(documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                 documents.c.organization_id.is_(None), documents.c.status != "deleted",
                 documents.c.tags["taxdome_folder"].astext == folder_display_name)
    rows = conn.execute(select(documents.c.id, documents.c.original_name, documents.c.storage_uri,
                               documents.c.tags).where(where).limit(_MAX_DOCUMENTS)).mappings()
    out = []
    for r in rows:
        tags = r["tags"] if isinstance(r["tags"], dict) else {}
        out.append({"document_id": r["id"], "original_name": r["original_name"],
                    "storage_uri": r["storage_uri"], "source_system": tags.get("source_system")})
    return out


# --------------------------------------------------------------------------- assemble one subject

def assemble_subject(*, source_system, subject_type, subject_key, display_name, context,
                     documents=None, identifiers=None, conn=None) -> dict:
    """Assemble the read-only evidence bundle for one subject. ``documents`` / ``identifiers`` may be
    supplied by a subject-type adapter (future sources); for folders they default to a DB lookup."""
    if conn is None:
        from app.db import engine
        with engine.connect() as c:
            return assemble_subject(source_system=source_system, subject_type=subject_type,
                                    subject_key=subject_key, display_name=display_name, context=context,
                                    documents=documents, identifiers=identifiers, conn=c)

    norm_key = " ".join((subject_key or "").split()).casefold()
    if documents is None and subject_type == "folder":
        documents = _folder_documents(conn, display_name)
    documents = documents or []

    # candidates (reused resolvers/indexes; uncertainty preserved via basis + deterministic flags)
    sc_candidates, sc_shared = _source_contact_candidates(display_name, context)
    person_candidates, p_shared = _person_candidates(display_name, sc_candidates, context, conn)
    household_candidates = _household_candidates(display_name, context)
    business_candidates = _business_candidates(display_name, context)

    # deterministic folder -> existing-entity resolution (Identity Service + org index)
    res = context.job._resolve_folder(display_name, context.org_index)
    deterministic_outcome = None
    held_reason = None
    if res["resolution"] in ("people", "households", "businesses"):
        etype = {"people": "person", "households": "household",
                 "businesses": "relationship_entity"}[res["resolution"]]
        deterministic_outcome = {"entity_type": etype, "entity_id": res["entity_id"],
                                 "reason": res["reason"], "resolver": "linkage_identity_service"}
    else:
        held_reason = res["reason"]

    # suggested source-contact action (promotion/link) — reported, NEVER auto-applied
    disposition = classify_folder(display_name, context.people_key, context.people_tok,
                                  context.sc_key, context.sc_tok)
    plan_row = plan_resolve_folder(display_name, disposition, context.idx)
    suggested_action = {"action": plan_row["category"], "evidence": plan_row["evidence"],
                        "proposed_target": plan_row.get("proposed_target", "")}

    # durable resolution knowledge (read-only lookups)
    current = get_current_decision(source_system, subject_type, norm_key, conn=conn)
    reusable = get_reusable_resolution(source_system, subject_type, norm_key, conn=conn)
    history = get_decision_history(source_system, subject_type, norm_key, conn=conn)

    identifiers_out = _identifiers(sc_candidates, context, identifiers)
    provenance = _provenance(sc_candidates, context)
    relationships = _relationships(person_candidates, context)

    # uncertainty / conflict flags — surfaced, not collapsed
    det_people = {c["person_id"] for c in person_candidates if c["deterministic"]}
    det_biz = {c["entity_id"] for c in business_candidates if c["deterministic"]}
    det_hh = {c["household_id"] for c in household_candidates if c["deterministic"]}
    distinct_det = len(det_people) + len(det_biz) + len(det_hh)
    any_candidate = bool(person_candidates or household_candidates or business_candidates
                         or sc_candidates)
    evidence_flags = {
        "has_shared_identifier": bool(sc_shared or p_shared),
        "has_conflicting_evidence": distinct_det > 1,
        "fuzzy_only": any_candidate and distinct_det == 0 and deterministic_outcome is None,
        "no_candidates": not any_candidate,
    }
    if deterministic_outcome is not None:
        confidence = "deterministic"
    elif distinct_det >= 1:
        confidence = "candidate_deterministic_signal"
    elif any_candidate:
        confidence = "fuzzy"
    else:
        confidence = "none"
    match_reason = (deterministic_outcome["reason"] if deterministic_outcome else held_reason) or ""

    return {
        "source_system": source_system,
        "subject_type": subject_type,
        "subject_key": norm_key,
        "display_name": display_name,
        "document_count": len(documents),
        "documents": documents,
        "identifiers": identifiers_out,
        "source_contact_candidates": sc_candidates,
        "person_candidates": person_candidates,
        "household_candidates": household_candidates,
        "business_candidates": business_candidates,
        "provenance": provenance,
        "relationships": relationships,
        "current_resolution": current,
        "reusable_resolution": reusable,
        "resolution_history_count": len(history),
        "disposition": disposition,
        "deterministic_outcome": deterministic_outcome,
        "held_reason": held_reason,
        "suggested_action": suggested_action,
        "match_reason": match_reason,
        "confidence": confidence,
        "evidence_flags": evidence_flags,
    }


def assemble_folder_subject(folder_display_name, *, source_system="TaxDome Drive", context=None,
                            conn=None) -> dict:
    """Convenience adapter for TaxDome folder subjects: subject_key = normalized folder name."""
    context = context or build_context()
    return assemble_subject(source_system=source_system, subject_type="folder",
                            subject_key=_name_key(folder_display_name), display_name=folder_display_name,
                            context=context, conn=conn)
