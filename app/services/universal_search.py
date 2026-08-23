"""Universal Search — the single entry point into Client360.

One query resolves people, households/families, businesses, trusts, estates, documents, and tax returns
(plus email / phone / EIN / account # / policy # / tax year), and every result carries a
``workspace_url`` that opens the correct Client Workspace. Read-only; it composes the authoritative
tables and enforces record scope on every source (``accessible_person_ids``) so a user never receives a
hit outside their permissions. No new ADR; no redesign of ADR-072/073.

Truthfulness / permissions:
- SSN is **not stored** in Client360, so SSN search returns an honest "not available" note (only shown
  to a principal with a sensitive capability) — never a fabricated hit and never an SSN value.
- No result ever includes a secret, SSN, or other permission-controlled identifier value.
"""
from __future__ import annotations

import re

from sqlalchemy import Text, cast, or_, select

from app.db import (
    accounts,
    document_classifications,
    document_ocr,
    documents,
    engine,
    households,
    insurance_policies,
    people,
    relationship_entities,
    tax_engagement_returns,
    tax_engagements,
    tax_return_types,
    tax_years,
)
from app.security.authorization import accessible_person_ids

# Result-type ranking (per the spec): exact matches first, then this order.
_TYPE_ORDER = {"household": 1, "person": 2, "business": 3, "trust": 4, "estate": 5,
               "document": 6, "tax_return": 7, "account": 8, "policy": 9, "info": 99}
_ENTITY_KINDS = ("business", "trust", "estate")
_SSN_RE = re.compile(r"^\d{3}-?\d{2}-?\d{4}$")
_SENSITIVE_SSN_CAP = "record.read_all"   # SSN search is gated; there is no SSN data to return anyway.


def _rank(name: str, q: str, kind: str) -> tuple:
    n = (name or "").lower()
    ql = q.lower()
    exact = 0 if n == ql else (1 if n.startswith(ql) else 2)
    return (exact, _TYPE_ORDER.get(kind, 50), n)


def universal_search(principal, query: str, *, types=None, active_only: bool = False,
                     include_archived: bool = False, limit: int = 50) -> dict:
    q = (query or "").strip()
    result = {"query": q, "results": [], "count": 0, "notes": []}
    if len(q) < 2:
        return result
    like = f"%{q}%"
    want = set(types) if types else None

    def _wanted(kind):
        return want is None or kind in want

    with engine.connect() as conn:
        accessible = accessible_person_ids(conn, principal)      # None = firm-wide
        firm_wide = accessible is None
        acc = None if firm_wide else set(accessible)
        # Households in scope = households of accessible people (firm-wide → all).
        acc_households = None
        if not firm_wide:
            acc_households = set(conn.scalars(
                select(people.c.household_id).where(
                    people.c.id.in_(acc or {-1}), people.c.household_id.isnot(None)))) if acc else set()

        rows = []

        def _person_in_scope(col):
            return None if firm_wide else col.in_(acc or {-1})

        def _household_in_scope(col):
            return None if firm_wide else col.in_(acc_households or {-1})

        # --- People (name / email / phone) ---
        if _wanted("person"):
            conds = [people.c.full_name.ilike(like), people.c.first_name.ilike(like),
                     people.c.last_name.ilike(like), people.c.primary_email.ilike(like),
                     people.c.normalized_email.ilike(like), people.c.primary_phone.ilike(like),
                     people.c.normalized_phone.ilike(like)]
            stmt = select(people.c.id, people.c.full_name, people.c.primary_email,
                          people.c.primary_phone, people.c.household_id, people.c.active).where(or_(*conds))
            if not firm_wide:
                stmt = stmt.where(_person_in_scope(people.c.id))
            # Inactive people (e.g. a deactivated company-as-person row) are noise in normal search;
            # only surface them when the caller explicitly asks to include archived/inactive records.
            if active_only or not include_archived:
                stmt = stmt.where(people.c.active.is_(True))
            for r in conn.execute(stmt.limit(limit)).mappings():
                rows.append({"kind": "person", "id": r["id"], "name": r["full_name"],
                             "entity_type": "person", "household_id": r["household_id"],
                             "subtitle": r["primary_email"] or r["primary_phone"] or "",
                             "quick_status": "active" if r["active"] else "inactive",
                             "workspace_url": f"/client/{r['id']}"})

        # --- Households / families (name / city) ---
        if _wanted("household"):
            stmt = select(households.c.id, households.c.name, households.c.city).where(
                or_(households.c.name.ilike(like), households.c.city.ilike(like)))
            if not firm_wide:
                stmt = stmt.where(_household_in_scope(households.c.id))
            for r in conn.execute(stmt.limit(limit)).mappings():
                rows.append({"kind": "household", "id": r["id"], "name": r["name"],
                             "entity_type": "household", "household_id": r["id"],
                             "subtitle": r["city"] or "", "quick_status": "",
                             "workspace_url": f"/client/household/{r['id']}"})

        # --- Business / Trust / Estate (relationship_entities); EIN via details ---
        if want is None or want & set(_ENTITY_KINDS):
            ein = q if q.isdigit() or "-" in q else None
            conds = [relationship_entities.c.name.ilike(like)]
            if ein:
                conds.append(relationship_entities.c.details["ein"].astext.ilike(f"%{q}%"))
            stmt = select(relationship_entities.c.id, relationship_entities.c.name,
                          relationship_entities.c.entity_type, relationship_entities.c.person_id,
                          relationship_entities.c.household_id, relationship_entities.c.active).where(
                relationship_entities.c.entity_type.in_(list(_ENTITY_KINDS)), or_(*conds))
            if active_only:
                stmt = stmt.where(relationship_entities.c.active.is_(True))
            for r in conn.execute(stmt.limit(limit)).mappings():
                if not firm_wide and not (
                        (r["person_id"] in (acc or set())) or (r["household_id"] in (acc_households or set()))):
                    continue
                if want is not None and r["entity_type"] not in want:
                    continue
                # A business/trust/estate opens its own entity workspace; the linked person/household
                # (if any) is reachable from there. This avoids a dead result when the entity has no
                # person_id/household_id, and keeps navigation to related people one hop away.
                url = f"/business/{r['id']}"
                rows.append({"kind": r["entity_type"], "id": r["id"], "name": r["name"],
                             "entity_type": r["entity_type"], "household_id": r["household_id"],
                             "subtitle": r["entity_type"].title(),
                             "quick_status": "active" if r["active"] else "inactive",
                             "workspace_url": url})

        # --- Documents (name / tax year) ---
        if _wanted("document"):
            year = q if re.fullmatch(r"\d{4}", q) else None
            # Match filename, OCR text (extracted document contents), classified doc type, and
            # metadata (tags).
            ocr_hits = select(document_ocr.c.document_id).where(
                document_ocr.c.status == "completed", document_ocr.c.text.ilike(like))
            class_hits = select(document_classifications.c.document_id).where(
                document_classifications.c.doc_type.ilike(like))
            conds = [documents.c.original_name.ilike(like),
                     documents.c.id.in_(ocr_hits),
                     documents.c.id.in_(class_hits),
                     cast(documents.c.tags, Text).ilike(like)]
            if year:
                conds.append(documents.c.tags["tax_year"].astext == year)
            cls = document_classifications
            stmt = (select(documents.c.id, documents.c.original_name, documents.c.person_id,
                           documents.c.household_id, documents.c.storage_provider,
                           documents.c.tags["tax_year"].astext.label("tax_year"),
                           documents.c.tags["source_system"].astext.label("source_system"),
                           documents.c.archived, cls.c.doc_type.label("doc_type"))
                    .select_from(documents.outerjoin(cls, cls.c.document_id == documents.c.id))
                    .where(or_(*conds)))
            if not include_archived:
                stmt = stmt.where(documents.c.archived.is_(False))
            for r in conn.execute(stmt.limit(limit)).mappings():
                if not firm_wide and not (
                        (r["person_id"] in (acc or set())) or (r["household_id"] in (acc_households or set()))):
                    continue
                owner_url = (f"/client/household/{r['household_id']}" if r["household_id"]
                             else (f"/client/{r['person_id']}" if r["person_id"] else None))
                dt = (r["doc_type"] or "").replace("_", " ") if r["doc_type"] else ""
                subtitle = " · ".join(x for x in (dt, r["tax_year"] or "") if x)
                rows.append({"kind": "document", "id": r["id"], "name": r["original_name"],
                             "entity_type": "document", "household_id": r["household_id"],
                             "subtitle": subtitle,
                             "source": _doc_source(r), "quick_status": "archived" if r["archived"] else "",
                             "open_url": f"/documents/{r['id']}/download", "workspace_url": owner_url,
                             "tax_year": r["tax_year"], "doc_type": r["doc_type"]})

        # --- Tax returns (by year, return type, or client name) ---
        if _wanted("tax_return"):
            rows.extend(_tax_return_rows(conn, q, like, acc, acc_households, firm_wide, limit))

        # --- Account # / Policy # (resolve to the owning client workspace) ---
        if _wanted("account"):
            rows.extend(_number_rows(conn, accounts, accounts.c.account_number, "account",
                                     like, acc, acc_households, firm_wide, limit))
        if _wanted("policy"):
            rows.extend(_number_rows(conn, insurance_policies, insurance_policies.c.policy_number,
                                     "policy", like, acc, acc_households, firm_wide, limit))

    # SSN: honest, permission-gated, never a fabricated hit (SSN is not stored).
    if _SSN_RE.match(q):
        if principal.can(_SENSITIVE_SSN_CAP):
            result["notes"].append("SSN search is not available — SSNs are not stored in Client360.")
        # Non-privileged users get nothing (no signal that an SSN exists).

    rows.sort(key=lambda r: _rank(r["name"], q, r["kind"]))
    result["results"] = rows[:limit]
    result["count"] = len(result["results"])
    return result


def _doc_source(r):
    ss = str(r.get("source_system") or "").lower()
    for key, badge in (("taxdome", "TaxDome"), ("drake", "Drake"), ("sharepoint", "SharePoint"),
                       ("schwab", "Schwab"), ("assetmark", "AssetMark")):
        if key in ss:
            return badge
    return r.get("storage_provider") or "Upload"


def _number_rows(conn, table, number_col, kind, like, acc, acc_households, firm_wide, limit):
    stmt = select(table.c.id, number_col.label("number"), table.c.person_id,
                  table.c.household_id).where(number_col.ilike(like))
    out = []
    for r in conn.execute(stmt.limit(limit)).mappings():
        if not firm_wide and not (
                (r["person_id"] in (acc or set())) or (r["household_id"] in (acc_households or set()))):
            continue
        url = (f"/client/household/{r['household_id']}" if r["household_id"]
               else (f"/client/{r['person_id']}" if r["person_id"] else None))
        out.append({"kind": kind, "id": r["id"], "name": r["number"],
                    "entity_type": kind, "household_id": r["household_id"],
                    "subtitle": f"{kind} number", "quick_status": "", "workspace_url": url})
    return out


def _tax_return_rows(conn, q, like, acc, acc_households, firm_wide, limit):
    year = int(q) if re.fullmatch(r"\d{4}", q) else None
    stmt = (select(tax_engagements.c.person_id, tax_engagements.c.household_id,
                   tax_years.c.year, tax_return_types.c.code.label("return_type"),
                   tax_engagement_returns.c.id.label("return_id"),
                   tax_engagement_returns.c.status)
            .select_from(tax_engagements
                .join(tax_engagement_returns,
                      tax_engagement_returns.c.tax_engagement_id == tax_engagements.c.id)
                .join(tax_return_types, tax_return_types.c.id == tax_engagement_returns.c.return_type_id)
                .join(tax_years, tax_years.c.id == tax_engagements.c.tax_year_id)))
    conds = [tax_return_types.c.code.ilike(like), tax_return_types.c.name.ilike(like)]
    if year:
        conds.append(tax_years.c.year == year)
    stmt = stmt.where(or_(*conds))
    out = []
    for r in conn.execute(stmt.limit(limit)).mappings():
        if not firm_wide and not (
                (r["person_id"] in (acc or set())) or (r["household_id"] in (acc_households or set()))):
            continue
        url = (f"/client/household/{r['household_id']}?tab=tax" if r["household_id"]
               else (f"/client/{r['person_id']}?tab=tax" if r["person_id"] else None))
        if not url:
            continue
        out.append({"kind": "tax_return", "id": r["return_id"],
                    "name": f"{r['year']} {r['return_type']}", "entity_type": "tax_return",
                    "household_id": r["household_id"], "subtitle": f"Tax return · {r['status']}",
                    "quick_status": r["status"], "workspace_url": url, "tax_year": str(r["year"])})
    return out
