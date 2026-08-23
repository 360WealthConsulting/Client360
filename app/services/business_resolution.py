"""Business Relationship Resolution — PREVIEW-ONLY resolver.

The Client360 business-ownership graph (``relationships`` category ownership/org_structure +
``relationship_ownership`` detail, written by ``organization_service.record_ownership``) was never
populated: 150 business ``relationship_entities`` exist with zero ownership edges. This module derives
candidate business↔person / business↔household relationships from STRUCTURED evidence already in
Client360 and returns a review report. It writes NOTHING — no ``relationships``, no ownership, no
documents. Applying the safe proposals is a separate, explicitly-approved step that must go through
``organization_service.record_ownership`` (so provenance lands in ``relationship_ownership.evidence_source``).

Evidence priority (highest first):
  A. existing confirmed source links (person_source_links → canonical person)
  B. Wealthbox ``company_name`` + ``job_title`` (CRM role evidence)
  C. Drake business/household provenance (manual_drake_household_provenance source links)
  D. existing household membership (household_relationships)
  E. document-name / document co-ownership — CORROBORATION ONLY, never sole proof.

Rules enforced:
- Role evidence (owner) is separate from association evidence. An explicit ownership job_title makes a
  person an OWNER candidate; household membership alone makes a person ASSOCIATED, never an owner.
- A stale/duplicate Wealthbox person must be reconciled to the canonical person BEFORE its ownership is
  proposed; ownership attaches to the canonical person, and the reconciliation is flagged for review.
- No ownership percentage is ever invented.
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

# Normalized (alnum-lowercase) job titles that denote OWNERSHIP. Generic roles ("member", "employee",
# "manager", "contact", "cfo", "controller", "bookkeeper") are association-only, never owner.
_OWNER_TITLES = frozenset({
    "owner", "coowner", "principal", "president", "ceo", "managingmember", "managingpartner",
    "partner", "shareholder", "founder", "proprietor", "owneroperator", "generalpartner",
})
_WEALTHBOX = "wealthbox"


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _first_last(value: str | None):
    return _norm(value)


def _name_matches(a_first, a_last, b_first, b_last) -> bool:
    """Same last name AND first names equal or one a prefix of the other (Norm↔Norman)."""
    la, lb = _norm(a_last), _norm(b_last)
    fa, fb = _norm(a_first), _norm(b_first)
    if not la or la != lb or not fa or not fb:
        return False
    return fa == fb or fa.startswith(fb) or fb.startswith(fa)


def _person_meta(conn, person_ids):
    """Per-person {id: {full_name, first, last, active, doc_count, household_ids}} for the given ids."""
    ids = [p for p in person_ids if p]
    if not ids:
        return {}
    meta = {}
    for r in conn.execute(select(people.c.id, people.c.full_name, people.c.first_name,
                                 people.c.last_name, people.c.active).where(people.c.id.in_(ids))).mappings():
        meta[r["id"]] = {"full_name": r["full_name"], "first": r["first_name"], "last": r["last_name"],
                         "active": r["active"], "doc_count": 0, "household_ids": []}
    for pid, n in conn.execute(select(documents.c.person_id, func.count())
                               .where(documents.c.person_id.in_(ids))
                               .group_by(documents.c.person_id)):
        if pid in meta:
            meta[pid]["doc_count"] = n
    for pid, hh in conn.execute(select(household_relationships.c.person_id,
                                       household_relationships.c.household_id)
                                .where(household_relationships.c.person_id.in_(ids))):
        if pid in meta and hh is not None:
            meta[pid]["household_ids"].append(hh)
    return meta


def _find_canonical(conn, stale):
    """A canonical same-name person for a stale duplicate: same last name + first-name prefix match,
    active, and holding real provenance (household membership OR documents). Returns
    (canonical_id, confidence) or (None, reason). Ambiguous (>1) → (None, 'ambiguous')."""
    if not stale["last"]:
        return None, "no_last_name"
    candidates = conn.execute(
        select(people.c.id, people.c.first_name, people.c.last_name)
        .where(people.c.active.is_(True), people.c.last_name.ilike(stale["last"]))
    ).mappings().all()
    hits = []
    for c in candidates:
        if c["id"] == stale["id"]:
            continue
        if not _name_matches(stale["first"], stale["last"], c["first_name"], c["last_name"]):
            continue
        m = _person_meta(conn, [c["id"]]).get(c["id"], {})
        if m.get("household_ids") or m.get("doc_count"):
            hits.append(c["id"])
    if len(hits) == 1:
        return hits[0], "unique_same_name_with_provenance"
    if len(hits) > 1:
        return None, "ambiguous"
    return None, "no_canonical_found"


def resolve_business_relationships(*, business_ids=None) -> dict:
    """Read-only resolution report. Optionally restrict to ``business_ids`` (used by tests)."""
    report = {"businesses_evaluated": 0, "exact_company_matches": 0, "owner_role_matches": 0,
              "household_associations": 0, "reconciliations_required": 0,
              "safe": [], "ambiguous": [], "unresolved": [], "summary": {}}
    with engine.connect() as conn:
        biz_stmt = select(relationship_entities.c.id, relationship_entities.c.name).where(
            relationship_entities.c.entity_type == "business")
        if business_ids:
            biz_stmt = biz_stmt.where(relationship_entities.c.id.in_(list(business_ids)))
        businesses = conn.execute(biz_stmt).mappings().all()

        # (B) Wealthbox contacts carrying a company_name, joined to their canonical person (A).
        wb_rows = conn.execute(
            select(source_contacts.c.id, source_contacts.c.full_name,
                   source_contacts.c.raw_data["company_name"].astext.label("company_name"),
                   source_contacts.c.raw_data["job_title"].astext.label("job_title"),
                   person_source_links.c.person_id)
            .select_from(source_contacts.outerjoin(
                person_source_links, person_source_links.c.source_contact_id == source_contacts.c.id))
            .where(source_contacts.c.source_system.ilike(_WEALTHBOX))
        ).mappings().all()
        by_company = defaultdict(list)
        for r in wb_rows:
            key = _norm(r["company_name"])
            if key:
                by_company[key].append(r)

        for biz in businesses:
            report["businesses_evaluated"] += 1
            bnorm = _norm(biz["name"])
            matches = by_company.get(bnorm, [])
            if matches:
                report["exact_company_matches"] += len(matches)

            owners, associations, household_assocs, reconciliations, notes = [], [], [], [], []
            person_ids = [m["person_id"] for m in matches if m["person_id"]]
            meta = _person_meta(conn, person_ids)

            for m in matches:
                pid = m["person_id"]
                if pid is None:
                    notes.append(f"Wealthbox contact '{m['full_name']}' not linked to a canonical person")
                    continue
                pm = meta.get(pid, {})
                effective_id, effective_name = pid, pm.get("full_name")
                is_owner = _norm(m["job_title"]) in _OWNER_TITLES

                # Reconciliation: stale duplicate (no docs + no household) → canonical same-name person.
                stale = {"id": pid, "first": pm.get("first"), "last": pm.get("last")}
                if not pm.get("doc_count") and not pm.get("household_ids"):
                    canon_id, why = _find_canonical(conn, stale)
                    if canon_id:
                        cm = _person_meta(conn, [canon_id]).get(canon_id, {})
                        reconciliations.append({"stale_person_id": pid, "stale_name": pm.get("full_name"),
                                                "canonical_person_id": canon_id,
                                                "canonical_name": cm.get("full_name"), "reason": why})
                        report["reconciliations_required"] += 1
                        effective_id, effective_name = canon_id, cm.get("full_name")
                        pm = cm
                    else:
                        notes.append(f"person {pid} ('{pm.get('full_name')}') looks stale but "
                                     f"reconciliation is {why}")

                entry = {"person_id": effective_id, "name": effective_name,
                         "job_title": m["job_title"] or None,
                         "requires_reconciliation": effective_id != pid,
                         "source_person_id": pid if effective_id != pid else None,
                         "evidence": f"Wealthbox company_name='{m['company_name']}'"
                                     + (f", job_title='{m['job_title']}'" if m["job_title"] else "")}
                if is_owner:
                    entry["role"] = "owner"
                    owners.append(entry)
                    report["owner_role_matches"] += 1
                else:
                    entry["role"] = "associated"
                    associations.append(entry)

                # (D) household association for the (effective) person; (E) document corroboration.
                for hh in pm.get("household_ids", []):
                    hh_name = conn.scalar(select(households.c.name).where(households.c.id == hh))
                    corrob = conn.scalar(
                        select(func.count()).select_from(documents)
                        .where(documents.c.organization_id == biz["id"], documents.c.household_id == hh)) or 0
                    household_assocs.append({"household_id": hh, "household_name": hh_name,
                                             "via_person_id": effective_id,
                                             "doc_coownership_count": corrob})
                    report["household_associations"] += 1

            # de-dup owners/associations/households by id
            owners = list({o["person_id"]: o for o in owners}.values())
            associations = list({a["person_id"]: a for a in associations if a["person_id"]
                                 not in {o["person_id"] for o in owners}}.values())
            household_assocs = list({h["household_id"]: h for h in household_assocs}.values())

            record = {"business_id": biz["id"], "business_name": biz["name"],
                      "company_matches": len(matches), "owners": owners, "associations": associations,
                      "household_associations": household_assocs, "reconciliations": reconciliations,
                      "notes": notes}

            distinct_owner_ids = {o["person_id"] for o in owners}
            if not matches:
                report["unresolved"].append(record)
            elif len(distinct_owner_ids) == 1 and len({h["household_id"] for h in household_assocs}) <= 1:
                # exactly one owner and at most one household → safe (reconciliation, if any, is a prereq)
                report["safe"].append(record)
            elif distinct_owner_ids or household_assocs:
                report["ambiguous"].append(record)
            else:
                report["unresolved"].append(record)

    report["summary"] = {
        "businesses_evaluated": report["businesses_evaluated"],
        "exact_company_matches": report["exact_company_matches"],
        "owner_role_matches": report["owner_role_matches"],
        "household_associations": report["household_associations"],
        "reconciliations_required": report["reconciliations_required"],
        "safe": len(report["safe"]), "ambiguous": len(report["ambiguous"]),
        "unresolved": len(report["unresolved"]),
    }
    return report
