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
    relationship_types,
    relationships,
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


#: An ENTITY is a client record a staff member navigates to; CONTENT is something filed against
#: one. Ranking by this class first is what stops a page of "<surname> ….pdf" filenames burying the
#: people of that surname: a prefix match on a filename used to outrank a person outright, because
#: match quality was the primary sort key and record type was only a tiebreaker.
_KIND_CLASS = {"household": 0, "person": 0, "business": 0, "trust": 0, "estate": 0,
               "document": 1, "tax_return": 1, "account": 1, "policy": 1, "info": 2}


def _rank(name: str, q: str, kind: str) -> tuple:
    """Sort key: exact hit, then entity-before-content, then match quality, then type, then name.

    An EXACT name match still wins outright regardless of type — typing a full filename must still
    put that document first. Below that, entities come before content, so a partial query returns
    the people, households and businesses it matched before the documents filed against them."""
    n = (name or "").lower()
    ql = q.lower()
    exact = 0 if n == ql else (1 if n.startswith(ql) else 2)
    return (0 if exact == 0 else 1, _KIND_CLASS.get(kind, 1), exact,
            _TYPE_ORDER.get(kind, 50), n)


#: Characters people type around a phone number. A query made only of these plus digits is
#: treated as a phone; anything else (an address, a name containing a number) is not, so no new
#: noise is introduced into name/email search.
_PHONE_PUNCTUATION = frozenset(" ()-.+")
#: Below this many digits a "phone" fragment matches almost every number, so it is ignored.
_MIN_PHONE_DIGITS = 3


def _phone_query_digits(query: str) -> str | None:
    """The digits of ``query`` when it is written like a phone number, else ``None``.

    Reuses ``app.services.people._normalize_phone`` — the SAME function that writes
    ``people.normalized_phone`` — rather than introducing a second phone model."""
    from app.services.people import _normalize_phone

    q = (query or "").strip()
    if not q or any(ch not in _PHONE_PUNCTUATION and not ch.isdigit() for ch in q):
        return None
    digits = _normalize_phone(q)
    return digits if digits and len(digits) >= _MIN_PHONE_DIGITS else None


def _phone_query_variants(query: str) -> tuple[str, ...]:
    """The digit forms of a phone-shaped ``query`` to match against ``normalized_phone``.

    Always includes the query's own normalized digits. When those are exactly 11 digits beginning
    with "1" — a US number typed with its country code, e.g. "+1 (540) 555-1212" or
    "1-540-555-1212" — the 10-digit form is added as well, because stored numbers are normalized
    without a country code.

    SEARCH TOLERANCE ONLY. Nothing here rewrites a person record, touches ``primary_phone``, or
    changes what the import pipelines store; there is no second persistent normalization scheme.
    The original 11-digit form is always searched too, so a number genuinely stored with a leading
    1 is never excluded. An 11-digit query that does not begin with "1" is never truncated — it is
    a real number, not a country code."""
    digits = _phone_query_digits(query)
    if not digits:
        return ()
    if len(digits) == 11 and digits.startswith("1"):
        return (digits, digits[1:])
    return (digits,)


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
            # A phone typed WITH punctuation must find the same person as bare digits.
            # ``normalized_phone`` holds digits only, so only the raw query was ever compared
            # against it: "540-555-1212" and "540 555 1212" matched nothing, while the same
            # number as "5405551212" matched. Normalising the QUERY the way the column is
            # written closes that gap, and a US country code typed by the user ("+1 540...") is
            # tolerated by also trying the 10-digit form. See _phone_query_variants.
            for phone_digits in _phone_query_variants(q):
                conds.append(people.c.normalized_phone.ilike(f"%{phone_digits}%"))
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

    # BEFORE the sort and slice: a related person must be able to compete for a place in the
    # results, not be attached to whatever survived a page of document matches.
    _enrich_and_promote(rows, acc, acc_households, firm_wide)
    rows.sort(key=lambda r: _rank(r["name"], q, r["kind"]))
    result["results"] = rows[:limit]
    result["count"] = len(result["results"])
    return result


#: Edge categories that express "who is behind this business". Deliberately narrow: an ownership or
#: org-structure edge is a deliberate, stored statement about control. Nothing here ever infers a
#: relationship from a shared surname, a shared document or a shared tax return.
_RELATION_CATEGORIES = ("ownership", "org_structure")
#: Related people shown under one business before the list is truncated. Keeps a widely-held entity
#: from dominating the results page.
_MAX_RELATED = 6
#: Hard ceiling on the ONE edge query, so a densely connected entity cannot unbound the search.
_MAX_EDGES = 400


def _display_name(person_row):
    """The staff-facing name for a person, from the CANONICAL people record.

    Never ``relationship_entities.name``: that column is a snapshot written by
    ``ensure_person_entity`` at creation time, and it falls back to the literal string
    ``f"Person {person_id}"`` when the person had no full name then. Reading it put an internal id
    in front of staff and went stale whenever a name was corrected afterwards. The id is never a
    display value; a genuinely nameless person gets a neutral label instead."""
    if person_row is None:
        return "Unnamed person"
    for candidate in (person_row.get("full_name"),
                      " ".join(p for p in (person_row.get("first_name"),
                                           person_row.get("last_name")) if p)):
        if (candidate or "").strip():
            return candidate.strip()
    return "Unnamed person"


def _joined(names, limit=_MAX_RELATED):
    shown = names[:limit]
    extra = len(names) - len(shown)
    return ", ".join(shown) + (f" +{extra} more" if extra > 0 else "")


def _enrich_and_promote(rows, acc, acc_households, firm_wide):
    """Give entity rows their canonical relationship context, and PROMOTE the related records.

    Relationships enrich a normal result; they are not the only way a related person becomes
    visible. A person reached through an ownership edge is added to the SAME result set as a
    first-class row with its own workspace link, then competes for a place on merit like any other
    row — so it can be found, opened and ranked normally rather than living inside another row.

    ONE HOP ONLY. Edges are read once, from the rows the text search already produced; nothing
    promoted is expanded again, so the graph is never walked recursively.

    BOUNDED AND BATCHED: a fixed number of queries regardless of how many entities matched — one
    for the person/household entity ids, one for the edges, one for people names, one for household
    names. No per-row lookups.

    DEDUPLICATED: a record that both matched textually and was reached through a relationship
    appears once; the relationship context is merged into the existing row.

    SCOPED: a promoted person must pass the same ``accessible_person_ids`` set a directly matched
    person passes, and a promoted household the same household set. A relationship can never be a
    route around record scope, and an omission is indistinguishable from "no such edge"."""
    by_key = {(r["kind"], r["id"]): r for r in rows}
    business_ids = {r["id"] for r in rows if r["kind"] in _ENTITY_KINDS}
    person_ids = {r["id"] for r in rows if r["kind"] == "person"}
    household_ids = {r["id"] for r in rows if r["kind"] == "household"}
    if not (business_ids or person_ids or household_ids):
        return

    owner = relationship_entities.alias("owner")
    target = relationship_entities.alias("target")
    with engine.connect() as conn:
        # The entity ids standing for the people/households already in the result set, so their
        # outgoing edges can be read in the same pass as the businesses' incoming ones.
        side_ids = set(business_ids)
        if person_ids or household_ids:
            side_ids |= set(conn.scalars(select(relationship_entities.c.id).where(or_(
                relationship_entities.c.person_id.in_(person_ids or {-1}),
                relationship_entities.c.household_id.in_(household_ids or {-1})))))
        if not side_ids:
            return

        edges = conn.execute(
            select(relationships.c.from_entity_id, relationships.c.to_entity_id,
                   owner.c.person_id.label("from_person"), owner.c.household_id.label("from_household"),
                   owner.c.entity_type.label("from_type"), owner.c.name.label("from_entity_name"),
                   target.c.person_id.label("to_person"), target.c.household_id.label("to_household"),
                   target.c.entity_type.label("to_type"), target.c.name.label("to_entity_name"),
                   relationship_types.c.name.label("role"),
                   relationship_types.c.inverse_name.label("inverse_role"))
            .select_from(relationships
                .join(relationship_types,
                      relationship_types.c.id == relationships.c.relationship_type_id)
                .join(owner, owner.c.id == relationships.c.from_entity_id)
                .join(target, target.c.id == relationships.c.to_entity_id))
            .where(or_(relationships.c.to_entity_id.in_(side_ids),
                       relationships.c.from_entity_id.in_(side_ids)),
                   relationships.c.active.is_(True),
                   relationship_types.c.category.in_(_RELATION_CATEGORIES))
            .limit(_MAX_EDGES)).mappings().all()
        if not edges:
            return

        # Canonical display names, batched. people/households are the authority, not entity rows.
        want_people = {e["from_person"] for e in edges if e["from_person"]} | \
                      {e["to_person"] for e in edges if e["to_person"]}
        if not firm_wide:
            want_people &= (acc or set())
        person_rows = {r["id"]: dict(r) for r in conn.execute(
            select(people.c.id, people.c.full_name, people.c.first_name, people.c.last_name,
                   people.c.household_id, people.c.primary_email, people.c.active)
            .where(people.c.id.in_(want_people or {-1}))).mappings()} if want_people else {}

        want_households = {e["from_household"] for e in edges if e["from_household"]} | \
                          {e["to_household"] for e in edges if e["to_household"]} | \
                          {r["household_id"] for r in person_rows.values() if r["household_id"]}
        if not firm_wide:
            want_households &= (acc_households or set())
        household_rows = {r["id"]: dict(r) for r in conn.execute(
            select(households.c.id, households.c.name, households.c.city)
            .where(households.c.id.in_(want_households or {-1}))).mappings()} \
            if want_households else {}

    def _promote_person(pid, context):
        row = by_key.get(("person", pid))
        if row is None:
            src = person_rows.get(pid)
            if src is None:                      # out of scope, or no canonical record
                return None
            row = {"kind": "person", "id": pid, "name": _display_name(src),
                   "entity_type": "person", "household_id": src["household_id"],
                   "subtitle": src["primary_email"] or "",
                   "quick_status": "active" if src["active"] else "inactive",
                   "workspace_url": f"/client/{pid}", "promoted": True}
            by_key[("person", pid)] = row
            rows.append(row)
        row.setdefault("relationship_context", [])
        if context and context not in row["relationship_context"]:
            row["relationship_context"].append(context)
        return row

    def _promote_household(hid, context):
        row = by_key.get(("household", hid))
        if row is None:
            src = household_rows.get(hid)
            if src is None:
                return None
            row = {"kind": "household", "id": hid, "name": src["name"] or "Unnamed household",
                   "entity_type": "household", "household_id": hid,
                   "subtitle": src["city"] or "", "quick_status": "",
                   "workspace_url": f"/client/household/{hid}", "promoted": True}
            by_key[("household", hid)] = row
            rows.append(row)
        row.setdefault("relationship_context", [])
        if context and context not in row["relationship_context"]:
            row["relationship_context"].append(context)
        return row

    owners_of = {}
    for edge in edges:
        business = by_key.get(("business", edge["to_entity_id"])) \
            or by_key.get(("trust", edge["to_entity_id"])) \
            or by_key.get(("estate", edge["to_entity_id"]))
        role = (edge["role"] or "").strip()
        # --- business in the results: promote its owners, and name them on the business row ---
        if business is not None:
            pid, hid = edge["from_person"], edge["from_household"]
            if pid and pid in person_rows:
                promoted = _promote_person(pid, f"{role} of {business['name']}".strip())
                if promoted is not None:
                    owners_of.setdefault(business["id"], []).append(promoted["name"])
                    member_household = person_rows[pid]["household_id"]
                    if member_household in household_rows:
                        _promote_household(member_household, None)
            elif hid and hid in household_rows:
                promoted = _promote_household(hid, f"{role} of {business['name']}".strip())
                if promoted is not None:
                    owners_of.setdefault(business["id"], []).append(promoted["name"])
        # --- person/household in the results: name what they own on their own row ---
        from_person, from_household = edge["from_person"], edge["from_household"]
        subject = (by_key.get(("person", from_person)) if from_person else None) \
            or (by_key.get(("household", from_household)) if from_household else None)
        if subject is not None and edge["to_type"] in _ENTITY_KINDS:
            target_name = edge["to_entity_name"] or "an entity"
            subject.setdefault("relationship_context", [])
            context = f"{role} of {target_name}".strip()
            if context not in subject["relationship_context"]:
                subject["relationship_context"].append(context)

    for entity_id, names in owners_of.items():
        for kind in _ENTITY_KINDS:
            row = by_key.get((kind, entity_id))
            if row is not None:
                row.setdefault("relationship_context", [])
                label = f"Owners: {_joined(sorted(set(names)))}"
                if label not in row["relationship_context"]:
                    row["relationship_context"].append(label)

    # Household rows list their members — canonical people.household_id, never a name match.
    _add_household_members(rows, by_key, acc, firm_wide)


def _add_household_members(rows, by_key, acc, firm_wide):
    household_ids = [r["id"] for r in rows if r["kind"] == "household"]
    if not household_ids:
        return
    with engine.connect() as conn:
        stmt = select(people.c.id, people.c.household_id, people.c.full_name,
                      people.c.first_name, people.c.last_name).where(
            people.c.household_id.in_(household_ids[:_MAX_RELATED * 4]),
            people.c.active.is_(True))
        if not firm_wide:
            stmt = stmt.where(people.c.id.in_(acc or {-1}))
        members = {}
        for r in conn.execute(stmt).mappings():
            members.setdefault(r["household_id"], []).append(_display_name(dict(r)))
    for hid, names in members.items():
        row = by_key.get(("household", hid))
        if row is None:
            continue
        row.setdefault("relationship_context", [])
        label = f"Members: {_joined(sorted(names))}"
        if label not in row["relationship_context"]:
            row["relationship_context"].append(label)


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
