"""Business Relationship Resolution — PREVIEW-ONLY resolver (hardened).

Derives candidate business↔person / business↔household relationships from STRUCTURED evidence already in
Client360. Writes NOTHING. Applying safe proposals is a separate, approved step via
``organization_service.record_ownership`` (which stamps ``relationship_ownership.evidence_source``).

Evidence priority: A confirmed source links → B Wealthbox company_name + job_title → C Drake provenance →
D household membership → E document co-ownership (CORROBORATION ONLY, never sole proof).

Role taxonomy (a title alone never invents ownership; percentages are never inferred):
- EXPLICIT_OWNERSHIP — an explicit ownership token: owner / co-owner / business owner / sole owner /
  shareholder / stockholder / proprietor / founder; partner / general partner / managing partner;
  "member" ONLY when the matched company is an LLC; and compound titles containing an ownership token
  (e.g. "Owner-Construction-Handyman").
- OFFICER_OR_MANAGEMENT_ONLY — president / CEO / CFO / COO / treasurer / secretary / manager / director /
  administrator / VP / employee titles. Establishes officer/principal ASSOCIATION, not ownership.

Buckets: SAFE_OWNERSHIP, SAFE_ASSOCIATION_ONLY, PERSON_IDENTITY_REVIEW, DUPLICATE_BUSINESS_REVIEW,
AMBIGUOUS, UNRESOLVED.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import func, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    person_source_links,
    relationship_entities,
    source_contacts,
)
from app.services.person_names import person_display_name

_WEALTHBOX = "wealthbox"
# Explicit CURRENT-ownership tokens only. "founder" is NOT here — founding a company does not prove
# present ownership, so it is association evidence unless another explicit ownership token/source proves it.
_OWNER_TOKENS = frozenset({"owner", "coowner", "shareholder", "stockholder", "proprietor",
                           "businessowner", "soleowner"})
# A partner title counts as ownership only when the title IS a partner role (partner / general partner /
# managing partner) — never an arbitrary title that merely contains "partner" (e.g. "sales partner").
_PARTNER_OK = frozenset({"partner", "general", "managing"})
_OFFICER_TOKENS = frozenset({"president", "ceo", "cfo", "coo", "cto", "cio", "treasurer", "secretary",
                             "manager", "director", "administrator", "vp", "vicepresident", "controller",
                             "bookkeeper", "accountant", "cpa", "office", "principal", "executive"})
_BUCKETS = ("SAFE_OWNERSHIP", "SAFE_ASSOCIATION_ONLY", "PERSON_IDENTITY_REVIEW",
            "DUPLICATE_BUSINESS_REVIEW", "AMBIGUOUS", "UNRESOLVED")


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _display_name(full_name, first, last) -> str:
    """Thin alias for the canonical helper — one name resolution, not a second implementation."""
    return person_display_name(full_name, first, last)


def _classify_role(job_title: str | None, business_name: str | None) -> str:
    """EXPLICIT_OWNERSHIP / OFFICER_OR_MANAGEMENT_ONLY / NONE — token-based, not exact-title."""
    toks = set(re.sub(r"[^a-z0-9]", " ", (job_title or "").lower()).split())
    if not toks:
        return "NONE"
    if toks & _OWNER_TOKENS:
        return "EXPLICIT_OWNERSHIP"
    if "partner" in toks and toks <= _PARTNER_OK:      # partner / general partner / managing partner only
        return "EXPLICIT_OWNERSHIP"
    if "member" in toks and "llc" in _norm(business_name):
        return "EXPLICIT_OWNERSHIP"
    if toks & _OFFICER_TOKENS:
        return "OFFICER_OR_MANAGEMENT_ONLY"
    return "OFFICER_OR_MANAGEMENT_ONLY"   # any other stated title is officer/association, never owner


def _name_matches(a_first, a_last, b_first, b_last) -> bool:
    la, lb, fa, fb = _norm(a_last), _norm(b_last), _norm(a_first), _norm(b_first)
    if not la or la != lb or not fa or not fb:
        return False
    return fa == fb or fa.startswith(fb) or fb.startswith(fa)


def _person_meta(conn, person_ids):
    ids = [p for p in person_ids if p]
    if not ids:
        return {}
    meta = {}
    for r in conn.execute(select(people.c.id, people.c.full_name, people.c.first_name,
                                 people.c.last_name, people.c.active).where(people.c.id.in_(ids))).mappings():
        meta[r["id"]] = {"id": r["id"], "full_name": r["full_name"], "first": r["first_name"],
                         "last": r["last_name"], "active": r["active"], "doc_count": 0, "household_ids": []}
    for pid, n in conn.execute(select(documents.c.person_id, func.count())
                               .where(documents.c.person_id.in_(ids)).group_by(documents.c.person_id)):
        if pid in meta:
            meta[pid]["doc_count"] = n
    for pid, hh in conn.execute(select(household_relationships.c.person_id,
                                       household_relationships.c.household_id)
                                .where(household_relationships.c.person_id.in_(ids))):
        if pid in meta and hh is not None:
            meta[pid]["household_ids"].append(hh)
    return meta


def _find_canonical(conn, stale):
    if not stale["last"]:
        return None, "no_canonical_found"
    cands = conn.execute(select(people.c.id, people.c.first_name, people.c.last_name)
                         .where(people.c.active.is_(True), people.c.last_name.ilike(stale["last"]))).mappings().all()
    hits = []
    for c in cands:
        if c["id"] == stale["id"] or not _name_matches(stale["first"], stale["last"],
                                                        c["first_name"], c["last_name"]):
            continue
        m = _person_meta(conn, [c["id"]]).get(c["id"], {})
        if m.get("household_ids") or m.get("doc_count"):
            hits.append(c["id"])
    if len(hits) == 1:
        return hits[0], "unique_same_name_with_provenance"
    if len(hits) > 1:
        return None, "ambiguous_canonical"
    return None, "no_canonical_found"


def _identity(conn, pid, meta):
    pm = meta.get(pid)
    if pm is None:
        return {"status": "unlinked", "effective": None, "reconcile": None}
    if pm["doc_count"] or pm["household_ids"]:
        return {"status": "ok", "effective": pm, "reconcile": None}
    canon, why = _find_canonical(conn, pm)
    if canon:
        cm = _person_meta(conn, [canon]).get(canon)
        return {"status": "reconcile", "effective": cm,
                "reconcile": {"stale_person_id": pid, "stale_name": _display_name(pm["full_name"], pm["first"], pm["last"]),
                              "canonical_person_id": canon,
                              "canonical_name": _display_name(cm["full_name"], cm["first"], cm["last"]),
                              "reason": why}}
    return {"status": "identity_review", "effective": pm, "reconcile": None, "reason": why}


def _duplicate_business_groups(conn):
    """Active business entities whose NORMALIZED name (punctuation/hyphen/spacing-insensitive) collides."""
    groups = defaultdict(list)
    for r in conn.execute(select(relationship_entities.c.id, relationship_entities.c.name)
                          .where(relationship_entities.c.entity_type == "business",
                                 relationship_entities.c.active.is_(True))).mappings():
        groups[_norm(r["name"])].append({"id": r["id"], "name": r["name"]})
    return {k: v for k, v in groups.items() if len(v) > 1}


def resolve_business_relationships(*, business_ids=None) -> dict:
    out = {b: [] for b in _BUCKETS}
    summary = {"businesses_evaluated": 0, "exact_company_matches": 0, "explicit_owner_matches": 0,
               "officer_management_associations": 0, "household_associations": 0,
               "person_reconciliations_required": 0, "duplicate_business_reviews": 0}
    with engine.connect() as conn:
        dup_groups = _duplicate_business_groups(conn)
        dup_by_norm = {k: v for k, v in dup_groups.items()}

        biz_stmt = select(relationship_entities.c.id, relationship_entities.c.name).where(
            relationship_entities.c.entity_type == "business")
        if business_ids:
            biz_stmt = biz_stmt.where(relationship_entities.c.id.in_(list(business_ids)))
        businesses = conn.execute(biz_stmt).mappings().all()

        wb = conn.execute(
            select(source_contacts.c.id, source_contacts.c.source_system, source_contacts.c.full_name,
                   source_contacts.c.raw_data["company_name"].astext.label("company_name"),
                   source_contacts.c.raw_data["job_title"].astext.label("job_title"),
                   person_source_links.c.person_id)
            .select_from(source_contacts.outerjoin(
                person_source_links, person_source_links.c.source_contact_id == source_contacts.c.id))
            .where(source_contacts.c.source_system.ilike(_WEALTHBOX))).mappings().all()
        by_company = defaultdict(list)
        for r in wb:
            key = _norm(r["company_name"])
            if key:
                by_company[key].append(r)

        for biz in businesses:
            summary["businesses_evaluated"] += 1
            bnorm = _norm(biz["name"])
            # apply_eligible is TRUE only for SAFE_OWNERSHIP (all gates satisfied). Every other bucket —
            # SAFE_ASSOCIATION_ONLY, PERSON_IDENTITY_REVIEW, DUPLICATE_BUSINESS_REVIEW, AMBIGUOUS,
            # UNRESOLVED — is report-only and NOT writable. (Apply itself is still not implemented.)
            record = {"business_id": biz["id"], "business_name": biz["name"], "bucket": None,
                      "apply_eligible": False, "owners": [], "associations": [],
                      "household_associations": [], "reconciliations": [], "duplicate_of": [], "notes": []}

            # (C) duplicate business identity — resolve canonical entity before any ownership.
            if bnorm in dup_by_norm and len(dup_by_norm[bnorm]) > 1:
                record["duplicate_of"] = [d for d in dup_by_norm[bnorm] if d["id"] != biz["id"]]
                record["bucket"] = "DUPLICATE_BUSINESS_REVIEW"
                out["DUPLICATE_BUSINESS_REVIEW"].append(record)
                continue

            matches = by_company.get(bnorm, [])
            summary["exact_company_matches"] += len(matches)
            if not matches:
                record["bucket"] = "UNRESOLVED"
                out["UNRESOLVED"].append(record)
                continue

            meta = _person_meta(conn, [m["person_id"] for m in matches if m["person_id"]])
            owners_ok, owners_bad, off_ok, off_bad = [], [], [], []
            for m in matches:
                role = _classify_role(m["job_title"], biz["name"])
                ident = _identity(conn, m["person_id"], meta) if m["person_id"] else \
                    {"status": "unlinked", "effective": None, "reconcile": None}
                eff = ident["effective"]
                prop = {
                    "person_id": eff["id"] if eff else None,
                    "name": _display_name(eff["full_name"], eff["first"], eff["last"]) if eff
                            else (m["full_name"] or "(unlinked)"),
                    "source_contact_id": m["id"], "source_system": m["source_system"],
                    "company_name": m["company_name"], "job_title": m["job_title"],
                    "role": role, "evidence": "wealthbox.company_name+job_title",
                    "identity_status": ident["status"],
                    "requires_reconciliation": ident["status"] == "reconcile",
                    "source_person_id": m["person_id"] if ident["status"] == "reconcile" else None,
                }
                if ident["reconcile"]:
                    record["reconciliations"].append(ident["reconcile"])
                    summary["person_reconciliations_required"] += 1
                if role == "EXPLICIT_OWNERSHIP":
                    summary["explicit_owner_matches"] += 1
                    (owners_ok if ident["status"] in ("ok", "reconcile") else owners_bad).append(prop)
                else:
                    summary["officer_management_associations"] += 1
                    (off_ok if ident["status"] in ("ok", "reconcile") else off_bad).append(prop)

                # (D/E) household association for a resolved (owner or officer) person + doc corroboration.
                if eff and ident["status"] in ("ok", "reconcile"):
                    for hh in eff["household_ids"]:
                        hh_name = conn.scalar(select(households.c.name).where(households.c.id == hh))
                        corrob = conn.scalar(select(func.count()).select_from(documents).where(
                            documents.c.organization_id == biz["id"], documents.c.household_id == hh)) or 0
                        record["household_associations"].append(
                            {"household_id": hh, "household_name": hh_name, "via_person_id": eff["id"],
                             "doc_coownership_count": corrob})

            # de-dup owners/associations/households by id
            record["owners"] = list({o["person_id"]: o for o in owners_ok if o["person_id"]}.values())
            record["associations"] = list({o["person_id"]: o for o in off_ok
                                           if o["person_id"] and o["person_id"]
                                           not in {x["person_id"] for x in record["owners"]}}.values())
            record["household_associations"] = list(
                {h["household_id"]: h for h in record["household_associations"]}.values())
            summary["household_associations"] += len(record["household_associations"])

            # bucket decision
            if owners_bad:
                record["notes"].append("explicit ownership evidence with unresolved person identity")
                record["bucket"] = "PERSON_IDENTITY_REVIEW"
                record["identity_review"] = owners_bad
            elif record["owners"]:
                record["bucket"] = "SAFE_OWNERSHIP"     # one OR many owners, each independently valid
            elif off_ok:
                record["bucket"] = "SAFE_ASSOCIATION_ONLY"
            elif off_bad:
                record["notes"].append("association evidence with unresolved person identity")
                record["bucket"] = "PERSON_IDENTITY_REVIEW"
                record["identity_review"] = off_bad
            else:
                record["bucket"] = "AMBIGUOUS"
            record["apply_eligible"] = record["bucket"] == "SAFE_OWNERSHIP"
            out[record["bucket"]].append(record)

    summary["duplicate_business_reviews"] = len(out["DUPLICATE_BUSINESS_REVIEW"])
    for b in _BUCKETS:
        summary[b] = len(out[b])
    summary["apply_eligible"] = sum(1 for r in out["SAFE_OWNERSHIP"] if r["apply_eligible"])
    out["summary"] = summary
    return out
