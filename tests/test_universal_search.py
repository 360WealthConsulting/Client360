"""Universal Search (app.services.universal_search) — coverage.

One query resolves people/households/businesses/trusts/estates/documents/tax returns (+ account/policy
numbers, email/phone), each result opening the correct Client Workspace, with record scope enforced on
every source and SSN handled honestly (never a fabricated hit). Temp/test rows only.
"""
import uuid

import pytest
from sqlalchemy import delete, insert
from sqlalchemy import select as _select

from app.db import (
    accounts,
    documents,
    engine,
    households,
    people,
    relationship_entities,
    relationship_types,
    relationships,
)
from app.security.models import Principal
from app.services.universal_search import universal_search

_TAG = "USRCH"
_FIRM = frozenset({"client.read", "record.read_all", "documents.view", "tax.read"})


@pytest.fixture
def data():
    tag = uuid.uuid4().hex[:6]
    made = {"tag": tag, "pids": [], "hids": [], "rids": [], "aids": [], "dids": []}
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"White{_TAG}{tag} Household").returning(
            households.c.id)).scalar_one()
        made["hids"].append(hid)
        mike = c.execute(people.insert().values(
            first_name="Michael", last_name=f"White{_TAG}{tag}", full_name=f"Michael White{_TAG}{tag}",
            primary_email=f"mike{tag}@e.test", primary_phone="512-555-0100", household_id=hid,
            active=True).returning(people.c.id)).scalar_one()
        made["pids"].append(mike)
        biz = c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"White Rentals LLC {_TAG}{tag}", household_id=hid,
            details={"ein": f"12-{tag}"}, active=True).returning(relationship_entities.c.id)).scalar_one()
        made["rids"].append(biz)
        acct = c.execute(insert(accounts).values(
            person_id=mike, household_id=hid, custodian="Schwab", account_number=f"ACCT-{tag}",
            account_name="Brokerage", status="open").returning(accounts.c.id)).scalar_one()
        made["aids"].append(acct)
        did = c.execute(insert(documents).values(
            original_name=f"2024 Form 1040 {_TAG}{tag}.pdf", stored_name=f"doc-{tag}",
            storage_path="/x/1040", storage_provider="Client360 Local", size_bytes=10, sha256="a" * 64,
            household_id=hid, status="active", archived=False,
            tags={"source_system": "Drake", "tax_year": "2024"}).returning(documents.c.id)).scalar_one()
        made["dids"].append(did)
    yield made
    with engine.begin() as c:
        c.execute(delete(documents).where(documents.c.id.in_(made["dids"])))
        c.execute(delete(accounts).where(accounts.c.id.in_(made["aids"])))
        c.execute(delete(relationship_entities).where(relationship_entities.c.id.in_(made["rids"])))
        c.execute(delete(people).where(people.c.id.in_(made["pids"])))
        c.execute(delete(households).where(households.c.id.in_(made["hids"])))


def _firm():
    return Principal(0, "adv@e.test", "Adv", _FIRM)


def _find(res, kind):
    return [r for r in res["results"] if r["kind"] == kind]


# --- exact / partial person + workspace url ---------------------------------

def test_person_search_opens_workspace(data):
    res = universal_search(_firm(), f"Michael White{_TAG}{data['tag']}")
    hits = _find(res, "person")
    assert hits and hits[0]["workspace_url"] == f"/client/{data['pids'][0]}"


def test_partial_name_matches(data):
    res = universal_search(_firm(), f"White{_TAG}{data['tag']}")
    assert any(r["kind"] == "person" for r in res["results"])
    assert any(r["kind"] == "household" for r in res["results"])


def test_email_and_phone_resolve_to_person(data):
    assert _find(universal_search(_firm(), f"mike{data['tag']}@e.test"), "person")
    assert _find(universal_search(_firm(), "512-555-0100"), "person")


# --- household / business / document / tax / account -------------------------

def test_household_search(data):
    hits = _find(universal_search(_firm(), f"White{_TAG}{data['tag']} Household"), "household")
    assert hits and hits[0]["workspace_url"] == f"/client/household/{data['hids'][0]}"


def test_business_search_opens_business_workspace(data):
    # A business result opens its own entity workspace (owners/household reachable from there),
    # rather than silently redirecting to a linked household.
    hits = _find(universal_search(_firm(), f"White Rentals LLC {_TAG}{data['tag']}"), "business")
    assert hits and hits[0]["workspace_url"] == f"/business/{hits[0]['id']}"


def test_document_search(data):
    hits = _find(universal_search(_firm(), f"2024 Form 1040 {_TAG}{data['tag']}"), "document")
    assert hits and hits[0]["open_url"] == f"/documents/{data['dids'][0]}/download"
    assert hits[0]["source"] == "Drake" and hits[0]["tax_year"] == "2024"


def test_account_number_resolves_to_client(data):
    hits = _find(universal_search(_firm(), f"ACCT-{data['tag']}"), "account")
    assert hits and hits[0]["workspace_url"] == f"/client/household/{data['hids'][0]}"


# --- ranking + filters + empty ----------------------------------------------

def test_ranking_prefers_exact_and_household_over_person(data):
    # Query the household name exactly → household should rank at/near the top.
    res = universal_search(_firm(), f"White{_TAG}{data['tag']} Household")
    assert res["results"][0]["kind"] == "household"


def test_type_filter_restricts_results(data):
    res = universal_search(_firm(), f"White{_TAG}{data['tag']}", types=["household"])
    assert res["results"] and all(r["kind"] == "household" for r in res["results"])


def test_empty_and_short_query():
    assert universal_search(_firm(), "a")["results"] == []
    assert universal_search(_firm(), "")["count"] == 0


# --- permission / record scope ----------------------------------------------

def test_out_of_scope_user_gets_no_hits(data):
    scoped = Principal(999, "s@e.test", "S", frozenset({"client.read"}))  # no read_all, no assignments
    res = universal_search(scoped, f"White{_TAG}{data['tag']}")
    assert res["results"] == []                       # never leaks out-of-scope records


def test_ssn_search_is_honest_and_gated(data):
    # Privileged principal: an honest "not available" note, never a fabricated hit.
    res = universal_search(_firm(), "123-45-6789")
    assert not res["results"]
    assert any("SSN" in n for n in res["notes"])
    # Non-privileged principal: no note, no signal.
    scoped = Principal(1, "s@e.test", "S", frozenset({"client.read"}))
    assert universal_search(scoped, "123-45-6789")["notes"] == []


def test_search_page_renders(data):
    from starlette.requests import Request

    from app.routes.search import search_page
    scope = {"type": "http", "method": "GET", "path": "/search", "headers": [], "query_string": b"",
             "state": {}}
    req = Request(scope)
    req.state.principal = _firm()
    resp = search_page(req, q=f"White{_TAG}{data['tag']} Household")
    html = resp.body.decode()
    assert "Universal search" in html and "Open workspace" in html


# --- business results surface their CANONICAL related people/households ----------------------
#
# A business row in relationship_entities carries a single optional person_id/household_id anchor,
# which is not the ownership graph: two owners are two `relationships` EDGES. Universal Search read
# only the entity row, so a company never showed the people behind it.
#
# Everything below builds the relationship the canonical way — a typed edge in an ownership
# category — and asserts that shared surnames alone are never enough.

_OWN = frozenset({"client.read", "record.read_all"})


def _ownership_type_id(c):
    """A relationship_type in an ownership category — created once, reused."""
    row = c.execute(_select(relationship_types.c.id).where(
        relationship_types.c.category == "ownership").limit(1)).scalars().first()
    if row is not None:
        return row
    return c.execute(relationship_types.insert().values(
        code=f"owner_{uuid.uuid4().hex[:6]}", name="Owner", inverse_name="Owned by",
        category="ownership", directed=True, active=True)
        .returning(relationship_types.c.id)).scalar_one()


@pytest.fixture
def company():
    """A company owned by two people in one household, wired through the canonical edge table."""
    tag = uuid.uuid4().hex[:6]
    made = {"tag": tag, "pids": [], "hids": [], "rids": [], "eids": []}
    with engine.begin() as c:
        hid = c.execute(households.insert().values(
            name=f"Rowe{_TAG}{tag} Household").returning(households.c.id)).scalar_one()
        made["hids"].append(hid)
        type_id = _ownership_type_id(c)
        biz = c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"Rowe Builders Inc {_TAG}{tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        made["rids"].append(biz)
        made["business_id"] = biz
        for first in ("Alan", "Bettina"):
            pid = c.execute(people.insert().values(
                first_name=first, last_name=f"Rowe{_TAG}{tag}",
                full_name=f"{first} Rowe{_TAG}{tag}", household_id=hid, active=True)
                .returning(people.c.id)).scalar_one()
            made["pids"].append(pid)
            # household_id is NOT set here: relationship_entities.household_id is UNIQUE and
            # identifies THE household entity, not the household a person belongs to. The person's
            # household is people.household_id, which is where the service reads it from.
            ent = c.execute(relationship_entities.insert().values(
                entity_type="person", person_id=pid,
                name=f"{first} Rowe{_TAG}{tag}", active=True)
                .returning(relationship_entities.c.id)).scalar_one()
            made["rids"].append(ent)
            eid = c.execute(relationships.insert().values(
                from_entity_id=ent, to_entity_id=biz, relationship_type_id=type_id,
                active=True).returning(relationships.c.id)).scalar_one()
            made["eids"].append(eid)
    yield made
    with engine.begin() as c:
        c.execute(delete(relationships).where(relationships.c.id.in_(made["eids"])))
        c.execute(delete(relationship_entities).where(
            relationship_entities.c.id.in_(made["rids"])))
        c.execute(delete(people).where(people.c.id.in_(made["pids"])))
        c.execute(delete(households).where(households.c.id.in_(made["hids"])))


def _business(res, made):
    return [r for r in res["results"] if r["id"] == made["business_id"]][0]


def _row(res, kind, ident):
    hits = [r for r in res["results"] if r["kind"] == kind and r["id"] == ident]
    return hits[0] if hits else None


def test_a_related_person_is_a_first_class_result_not_a_sub_row(company):
    """The core fix: an owner reached through a canonical edge joins the MAIN result set."""
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")

    people_rows = [r for r in res["results"] if r["kind"] == "person"]
    assert sorted(p["name"] for p in people_rows) == [
        f"Alan Rowe{_TAG}{company['tag']}", f"Bettina Rowe{_TAG}{company['tag']}"]
    for row in people_rows:
        assert row["workspace_url"] == f"/client/{row['id']}"
    # Nothing is nested any more.
    assert all("related_people" not in r for r in res["results"])


def test_the_related_household_is_a_first_class_result(company):
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    hh = _row(res, "household", company["hids"][0])

    assert hh is not None, "the related household did not enter the result set"
    assert hh["workspace_url"] == f"/client/household/{company['hids'][0]}"
    assert any("Members:" in c for c in hh["relationship_context"])


def test_relationship_context_appears_on_the_normal_rows(company):
    tag = company["tag"]
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")

    biz = _row(res, "business", company["business_id"])
    assert any(c.startswith("Owners:") for c in biz["relationship_context"])
    assert f"Alan Rowe{_TAG}{tag}" in " ".join(biz["relationship_context"])

    alan = [r for r in res["results"]
            if r["kind"] == "person" and r["name"].startswith("Alan")][0]
    assert any(f"Rowe Builders Inc {_TAG}{tag}" in c for c in alan["relationship_context"])


def test_the_business_workspace_link_is_unchanged(company):
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    assert _row(res, "business", company["business_id"])["workspace_url"] == \
        f"/business/{company['business_id']}"


def test_no_entity_is_ever_duplicated_in_the_result_set(company):
    """Dedup invariant: one row per canonical entity, whether it matched, was promoted, or both."""
    tag = company["tag"]
    for query in (f"Rowe Builders Inc {_TAG}{tag}",   # business matches; owners promoted
                  f"Rowe{_TAG}{tag}"):                # people + household match directly
        res = universal_search(_firm(), query)
        keys = [(r["kind"], r["id"]) for r in res["results"]]
        assert len(keys) == len(set(keys)), f"duplicate entity rows for {query!r}: {keys}"


def test_context_is_merged_onto_a_row_that_also_matched_directly(company):
    """Alan matches by name AND owns the business — one row carrying the relationship context."""
    tag = company["tag"]
    res = universal_search(_firm(), f"Rowe{_TAG}{tag}")

    alan = [r for r in res["results"]
            if r["kind"] == "person" and r["name"].startswith("Alan")]
    assert len(alan) == 1
    assert any(f"Rowe Builders Inc {_TAG}{tag}" in c
               for c in alan[0].get("relationship_context", [])), \
        "the directly-matched row did not receive its relationship context"


def test_the_canonical_person_name_is_used_never_person_id(company):
    """relationship_entities.name is a stale snapshot that falls back to "Person {id}"."""
    from sqlalchemy import update as _update

    tag = company["tag"]
    with engine.begin() as c:                    # simulate the stale snapshot production had
        c.execute(_update(relationship_entities)
                  .where(relationship_entities.c.person_id == company["pids"][0])
                  .values(name=f"Person {company['pids'][0]}"))

    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
    names = [r["name"] for r in res["results"] if r["kind"] == "person"]

    assert f"Alan Rowe{_TAG}{tag}" in names, "the canonical people record was not used"
    assert not any(n.startswith("Person ") for n in names), "an internal id reached the UI"
    assert str(company["pids"][0]) not in " ".join(names)


def test_a_nameless_person_gets_a_neutral_label_not_an_id():
    from app.services.universal_search import _display_name

    assert _display_name({"full_name": None, "first_name": None, "last_name": None}) == \
        "Unnamed person"
    assert _display_name(None) == "Unnamed person"
    assert _display_name({"full_name": "  ", "first_name": "Ann", "last_name": "Lee"}) == "Ann Lee"


def test_entities_outrank_documents_for_the_same_query(data):
    """A page of filename prefix matches must not bury the people."""
    tag = data["tag"]
    res = universal_search(_firm(), f"White{_TAG}{tag}")
    kinds = [r["kind"] for r in res["results"]]

    if "document" in kinds and "person" in kinds:
        assert kinds.index("person") < kinds.index("document"), \
            "a document outranked a person for the same query"


def test_an_exact_name_match_still_wins_outright(data):
    """Typing a full document name must still put that document first."""
    res = universal_search(_firm(), f"2024 Form 1040 {_TAG}{data['tag']}.pdf")
    assert res["results"], "the exact document match vanished"
    assert res["results"][0]["kind"] == "document"


def test_a_same_surname_person_with_no_edge_is_not_promoted(company):
    tag = company["tag"]
    with engine.begin() as c:
        stranger = c.execute(people.insert().values(
            first_name="Clive", last_name=f"Unrelated{_TAG}{tag}",
            full_name=f"Clive Unrelated{_TAG}{tag}", active=True)
            .returning(people.c.id)).scalar_one()
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
        assert f"Clive Unrelated{_TAG}{tag}" not in [r["name"] for r in res["results"]]
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == stranger))


def test_an_inactive_edge_does_not_promote(company):
    from sqlalchemy import update as _update

    with engine.begin() as c:
        c.execute(_update(relationships)
                  .where(relationships.c.id == company["eids"][0]).values(active=False))

    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    assert len([r for r in res["results"] if r["kind"] == "person"]) == 1


def test_only_ownership_category_edges_promote(company):
    tag = company["tag"]
    with engine.begin() as c:
        other_type = c.execute(relationship_types.insert().values(
            code=f"advises_{uuid.uuid4().hex[:6]}", name="Advises", inverse_name="Advised by",
            category="advisory", directed=True, active=True)
            .returning(relationship_types.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            first_name="Vera", last_name=f"Advisor{_TAG}{tag}",
            full_name=f"Vera Advisor{_TAG}{tag}", active=True)
            .returning(people.c.id)).scalar_one()
        ent = c.execute(relationship_entities.insert().values(
            entity_type="person", person_id=pid, name=f"Vera Advisor{_TAG}{tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        edge = c.execute(relationships.insert().values(
            from_entity_id=ent, to_entity_id=company["business_id"],
            relationship_type_id=other_type, active=True)
            .returning(relationships.c.id)).scalar_one()
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
        assert f"Vera Advisor{_TAG}{tag}" not in [r["name"] for r in res["results"]]
    finally:
        with engine.begin() as c:
            c.execute(delete(relationships).where(relationships.c.id == edge))
            c.execute(delete(relationship_entities).where(relationship_entities.c.id == ent))
            c.execute(delete(people).where(people.c.id == pid))
            c.execute(delete(relationship_types).where(relationship_types.c.id == other_type))


def test_out_of_scope_related_people_are_not_promoted(company):
    """The business is visible to a narrow principal; its owners must still not be."""
    from sqlalchemy import update as _update

    from app.db import record_assignments
    from tests._portal_util import seed_staff_user

    tag = company["tag"]
    staff_user_id = seed_staff_user()
    with engine.begin() as c:
        visible = c.execute(people.insert().values(
            first_name="Dee", last_name=f"Visible{_TAG}{tag}",
            full_name=f"Dee Visible{_TAG}{tag}", active=True)
            .returning(people.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            entity_type="person", entity_id=visible, user_id=staff_user_id,
            assignment_type="primary"))
        c.execute(_update(relationship_entities)
                  .where(relationship_entities.c.id == company["business_id"])
                  .values(person_id=visible))
    narrow = Principal(staff_user_id, "narrow@e.test", "Narrow", frozenset({"client.read"}))
    try:
        res = universal_search(narrow, f"Rowe Builders Inc {_TAG}{tag}")
        assert _row(res, "business", company["business_id"]) is not None, \
            "the business was filtered out — this test would prove nothing"
        names = [r["name"] for r in res["results"]]
        assert f"Alan Rowe{_TAG}{tag}" not in names, "an out-of-scope owner was promoted"
        assert f"Rowe{_TAG}{tag} Household" not in names
    finally:
        with engine.begin() as c:
            c.execute(delete(record_assignments).where(
                record_assignments.c.entity_id == visible,
                record_assignments.c.entity_type == "person"))
            c.execute(_update(relationship_entities)
                      .where(relationship_entities.c.id == company["business_id"])
                      .values(person_id=None))
            c.execute(delete(people).where(people.c.id == visible))


def test_expansion_is_one_hop_and_query_count_is_bounded(company):
    """Promoted rows are never expanded again, and the edge query runs once."""
    from sqlalchemy import event

    statements = []

    def _before(conn, cursor, statement, *a, **kw):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        universal_search(_firm(), f"Rowe{_TAG}{company['tag']}")
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    edge_queries = [s for s in statements if "relationship_types" in s]
    assert len(edge_queries) == 1, f"the edge query ran {len(edge_queries)} times (recursion/N+1)"
    assert len(statements) < 25, f"the search issued {len(statements)} queries"


def test_the_related_list_is_capped(company):
    from app.services.universal_search import _MAX_EDGES, _MAX_RELATED
    assert _MAX_RELATED == 6 and _MAX_EDGES == 400


def test_prefix_matching_documents_cannot_crowd_out_the_people(company):
    """The production failure, reproduced.

    Documents whose FILENAME starts with the query used to outrank people outright (match quality
    was the primary sort key), and with enough of them the people fell past the result limit and
    vanished. This creates that exact condition: many prefix-matching documents, a small limit."""
    from sqlalchemy import insert as _insert

    tag = company["tag"]
    prefix = f"Rowe{_TAG}{tag}"
    doc_ids = []
    with engine.begin() as c:
        for i in range(12):
            doc_ids.append(c.execute(_insert(documents).values(
                original_name=f"{prefix} statement {i}.pdf", stored_name=f"d-{tag}-{i}",
                storage_path=f"/x/{tag}/{i}", storage_provider="Client360 Local",
                size_bytes=10, sha256=f"{i:064d}", household_id=company["hids"][0],
                status="active", archived=False).returning(documents.c.id)).scalar_one())
    try:
        res = universal_search(_firm(), prefix, limit=10)
        kinds = [r["kind"] for r in res["results"]]

        assert "person" in kinds, "people were crowded out by prefix-matching documents"
        assert kinds.index("person") < (kinds.index("document") if "document" in kinds else 99), \
            "a document outranked a person for the same query"
        names = [r["name"] for r in res["results"] if r["kind"] == "person"]
        assert f"Alan Rowe{_TAG}{tag}" in names and f"Bettina Rowe{_TAG}{tag}" in names
    finally:
        with engine.begin() as c:
            c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))


def test_promotion_happens_before_the_limit_is_applied(company):
    """A promoted person must compete for a place, not be appended after the page is cut."""
    from sqlalchemy import insert as _insert

    tag = company["tag"]
    business_name = f"Rowe Builders Inc {_TAG}{tag}"
    doc_ids = []
    with engine.begin() as c:
        for i in range(12):
            doc_ids.append(c.execute(_insert(documents).values(
                original_name=f"{business_name} filing {i}.pdf", stored_name=f"b-{tag}-{i}",
                storage_path=f"/y/{tag}/{i}", storage_provider="Client360 Local",
                size_bytes=10, sha256=f"{i + 100:064d}", household_id=company["hids"][0],
                status="active", archived=False).returning(documents.c.id)).scalar_one())
    try:
        res = universal_search(_firm(), business_name, limit=5)

        assert len(res["results"]) <= 5, "the limit was not applied to the final set"
        kinds = [r["kind"] for r in res["results"]]
        assert "person" in kinds, "promoted people did not compete for a place in the page"
    finally:
        with engine.begin() as c:
            c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))


# --- regression: a NULL display name crashed the whole search ---------------------------------
#
# people.full_name is NULLABLE and the person branch also matches on first/last/email/phone, so a
# person with no full_name entered the results with name=None. _promote_person returns that
# EXISTING row unchanged, so the None reached the owners list and
# `", ".join(...)` raised TypeError — a 500 for the entire query, not a missing badge.

def test_joined_discards_nulls_and_blanks():
    from app.services.universal_search import _joined

    assert _joined([None, "Ada"]) == "Ada"
    assert _joined([None, None]) == ""
    assert _joined(["  ", "", None]) == ""
    assert _joined(["B", "A", "A", None]) == "A, B"          # deduped and sorted
    assert _joined([]) == ""


def test_joined_counts_more_from_valid_names_only():
    from app.services.universal_search import _MAX_RELATED, _joined

    names = [f"Name {i:02d}" for i in range(_MAX_RELATED + 3)] + [None, "  "]
    out = _joined(names)
    assert out.endswith("+3 more"), out                       # nulls do not inflate the count
    assert "None" not in out


def test_context_never_renders_of_none():
    from app.services.universal_search import _context

    assert _context(None, None) == ""
    assert _context("Owns", None) == ""
    assert _context(None, "Acme") == "Related to Acme"
    assert _context("  ", "Acme") == "Related to Acme"
    assert _context("Owns", "Acme") == "Owns of Acme"


def _nameless_owner(company, *, full_name=None, first=None, last=None):
    """Add an owner whose display-name sources are null/blank, wired the canonical way."""
    with engine.begin() as c:
        type_id = _ownership_type_id(c)
        pid = c.execute(people.insert().values(
            full_name=full_name, first_name=first, last_name=last,
            household_id=company["hids"][0], active=True).returning(people.c.id)).scalar_one()
        ent = c.execute(relationship_entities.insert().values(
            entity_type="person", person_id=pid, name=f"Person {pid}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        edge = c.execute(relationships.insert().values(
            from_entity_id=ent, to_entity_id=company["business_id"],
            relationship_type_id=type_id, active=True)
            .returning(relationships.c.id)).scalar_one()
    return {"pid": pid, "ent": ent, "edge": edge}


def _cleanup_owner(made):
    with engine.begin() as c:
        c.execute(delete(relationships).where(relationships.c.id == made["edge"]))
        c.execute(delete(relationship_entities).where(
            relationship_entities.c.id == made["ent"]))
        c.execute(delete(people).where(people.c.id == made["pid"]))


def test_an_owner_with_a_null_name_does_not_crash_the_search(company):
    """The exact production condition: full_name NULL on a related person."""
    tag = company["tag"]
    made = _nameless_owner(company, full_name=None, first=None, last=f"Rowe{_TAG}{tag}")
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")   # must not raise

        biz = _row(res, "business", company["business_id"])
        owners = " ".join(c for c in biz["relationship_context"] if c.startswith("Owners:"))
        assert "None" not in owners
        # The named owners still render.
        assert f"Alan Rowe{_TAG}{tag}" in owners and f"Bettina Rowe{_TAG}{tag}" in owners
    finally:
        _cleanup_owner(made)


def test_a_whitespace_only_name_is_omitted_not_rendered(company):
    tag = company["tag"]
    made = _nameless_owner(company, full_name="   ", first="  ", last="  ")
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
        biz = _row(res, "business", company["business_id"])
        owners = " ".join(c for c in biz["relationship_context"] if c.startswith("Owners:"))

        assert "None" not in owners
        assert ",  ," not in owners and not owners.endswith(", ")
        assert f"Alan Rowe{_TAG}{tag}" in owners
    finally:
        _cleanup_owner(made)


def test_a_nameless_owner_is_labelled_consistently_never_blank_or_none(company):
    """An owner with no name IS still surfaced as a result row, so the label names it the same way.

    Suppressing the label here would hide the existence of an owner that is listed below it. What
    must never appear is "None", a dangling "Owners: ", or an empty badge."""
    tag = company["tag"]
    with engine.begin() as c:
        from sqlalchemy import update as _update
        c.execute(_update(relationships)
                  .where(relationships.c.id.in_(company["eids"])).values(active=False))
    made = _nameless_owner(company, full_name=None, first=None, last=None)
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
        biz = _row(res, "business", company["business_id"])
        labels = biz.get("relationship_context", [])

        owners = [c for c in labels if c.startswith("Owners:")]
        assert owners == ["Owners: Unnamed person"], owners
        assert "None" not in " ".join(labels)
        assert not any(c.strip() in ("Owners:", "Members:") for c in labels)
        assert all(c.strip() for c in labels), "an empty context badge was emitted"
        # ...and that name matches the row actually shown for that person.
        promoted = [r for r in res["results"]
                    if r["kind"] == "person" and r["id"] == made["pid"]]
        assert promoted and promoted[0]["name"] == "Unnamed person"
    finally:
        _cleanup_owner(made)


def test_no_owners_label_at_all_when_no_owner_is_visible(company):
    """The genuinely-empty path: every owner filtered out by scope leaves no label behind."""
    from sqlalchemy import update as _update

    from app.db import record_assignments
    from tests._portal_util import seed_staff_user

    tag = company["tag"]
    staff_user_id = seed_staff_user()
    with engine.begin() as c:
        visible = c.execute(people.insert().values(
            first_name="Dee", last_name=f"Anchor{_TAG}{tag}",
            full_name=f"Dee Anchor{_TAG}{tag}", active=True)
            .returning(people.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            entity_type="person", entity_id=visible, user_id=staff_user_id,
            assignment_type="primary"))
        c.execute(_update(relationship_entities)
                  .where(relationship_entities.c.id == company["business_id"])
                  .values(person_id=visible))
    narrow = Principal(staff_user_id, "narrow@e.test", "Narrow", frozenset({"client.read"}))
    try:
        res = universal_search(narrow, f"Rowe Builders Inc {_TAG}{tag}")
        biz = _row(res, "business", company["business_id"])
        assert biz is not None, "the business was filtered out — this proves nothing"
        labels = biz.get("relationship_context", [])

        assert not any(c.startswith("Owners:") for c in labels), labels
        assert all(c.strip() for c in labels)
    finally:
        with engine.begin() as c:
            c.execute(delete(record_assignments).where(
                record_assignments.c.entity_id == visible,
                record_assignments.c.entity_type == "person"))
            c.execute(_update(relationship_entities)
                      .where(relationship_entities.c.id == company["business_id"])
                      .values(person_id=None))
            c.execute(delete(people).where(people.c.id == visible))


def test_a_nameless_person_surfaced_as_a_result_reads_unnamed_person(company):
    """Requirement: never "Person <id>"; a neutral label only where the person IS a result."""
    tag = company["tag"]
    made = _nameless_owner(company, full_name=None, first=None, last=f"Rowe{_TAG}{tag}")
    try:
        res = universal_search(_firm(), f"Rowe{_TAG}{tag}")
        names = [r["name"] for r in res["results"] if r["kind"] == "person"]

        assert not any((n or "").startswith("Person ") for n in names), names
        assert None not in names, "a NULL display name still reaches the result set"
        assert str(made["pid"]) not in " ".join(n for n in names if n)
    finally:
        _cleanup_owner(made)


def test_promotion_and_ranking_still_work_with_a_null_named_owner(company):
    tag = company["tag"]
    made = _nameless_owner(company, full_name=None, first=None, last=f"Rowe{_TAG}{tag}")
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
        kinds = [r["kind"] for r in res["results"]]

        assert "business" in kinds and "person" in kinds
        assert _row(res, "household", company["hids"][0]) is not None
        keys = [(r["kind"], r["id"]) for r in res["results"]]
        assert len(keys) == len(set(keys))
    finally:
        _cleanup_owner(made)
