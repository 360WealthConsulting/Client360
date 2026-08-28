"""Universal Search (app.services.universal_search) — coverage.

One query resolves people/households/businesses/trusts/estates/documents/tax returns (+ account/policy
numbers, email/phone), each result opening the correct Client Workspace, with record scope enforced on
every source and SSN handled honestly (never a fabricated hit). Temp/test rows only.
"""
import uuid

import pytest
from sqlalchemy import delete, insert

from app.db import (
    accounts,
    documents,
    engine,
    households,
    people,
    relationship_entities,
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

from sqlalchemy import select as _select

from app.db import relationship_types, relationships

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


def test_a_business_result_surfaces_its_canonical_owners(company):
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    biz = _business(res, company)

    names = sorted(p["name"] for p in biz["related_people"])
    assert names == [f"Alan Rowe{_TAG}{company['tag']}", f"Bettina Rowe{_TAG}{company['tag']}"]
    assert all(p["role"] for p in biz["related_people"]), "the stored role is not surfaced"


def test_related_people_link_to_the_personal_profile(company):
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    biz = _business(res, company)

    for person in biz["related_people"]:
        assert person["url"] == f"/client/{person['person_id']}"
        assert person["person_id"] in company["pids"]


def test_the_related_household_is_surfaced_and_links_to_household_360(company):
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    biz = _business(res, company)

    assert [h["name"] for h in biz["related_households"]] == [
        f"Rowe{_TAG}{company['tag']} Household"]
    assert biz["related_households"][0]["url"] == \
        f"/client/household/{company['hids'][0]}"


def test_the_business_workspace_link_is_unchanged(company):
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    biz = _business(res, company)
    assert biz["workspace_url"] == f"/business/{company['business_id']}"


def test_a_same_surname_person_with_no_edge_is_never_shown(company):
    """Relationships come from the stored graph, never from a matching name."""
    tag = company["tag"]
    with engine.begin() as c:
        stranger = c.execute(people.insert().values(
            first_name="Clive", last_name=f"Rowe{_TAG}{tag}",
            full_name=f"Clive Rowe{_TAG}{tag}", active=True)
            .returning(people.c.id)).scalar_one()
    try:
        res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}")
        biz = _business(res, company)
        assert f"Clive Rowe{_TAG}{tag}" not in [p["name"] for p in biz["related_people"]]
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == stranger))


def test_an_inactive_edge_is_not_surfaced(company):
    from sqlalchemy import update as _update

    with engine.begin() as c:
        c.execute(_update(relationships)
                  .where(relationships.c.id == company["eids"][0]).values(active=False))

    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    biz = _business(res, company)
    assert len(biz["related_people"]) == 1, "an ended relationship was still shown"


def test_out_of_scope_related_people_are_omitted_while_the_business_still_shows(company):
    """The load-bearing case: the business IS visible to a narrow principal, but its owners are not.

    Constructed so the business cannot simply be filtered out — it is anchored to a person the
    principal is assigned to — which is what makes this prove the related-list scope filter rather
    than the pre-existing entity filter."""
    from sqlalchemy import update as _update

    from app.db import record_assignments

    from tests._portal_util import seed_staff_user

    tag = company["tag"]
    staff_user_id = seed_staff_user()           # a real user row; no record.read_all capability
    with engine.begin() as c:
        visible = c.execute(people.insert().values(
            first_name="Dee", last_name=f"Visible{_TAG}{tag}",
            full_name=f"Dee Visible{_TAG}{tag}", active=True)
            .returning(people.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            entity_type="person", entity_id=visible, user_id=staff_user_id,
            assignment_type="primary"))
        # Anchor the business to the person this principal CAN see.
        c.execute(_update(relationship_entities)
                  .where(relationship_entities.c.id == company["business_id"])
                  .values(person_id=visible))
    narrow = Principal(staff_user_id, "narrow@e.test", "Narrow", frozenset({"client.read"}))
    try:
        rows = universal_search(narrow, f"Rowe Builders Inc {_TAG}{tag}")["results"]
        match = [r for r in rows if r["id"] == company["business_id"]]
        assert match, "the business itself was filtered out — this test would prove nothing"
        biz = match[0]
        assert biz["related_people"] == [], "an out-of-scope owner was disclosed"
        assert biz["related_households"] == [], "an out-of-scope household was disclosed"
        # The firm-wide principal still sees them, so the data really is there.
        firm_biz = _business(
            universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}"), company)
        assert len(firm_biz["related_people"]) == 2
    finally:
        with engine.begin() as c:
            c.execute(delete(record_assignments).where(
                record_assignments.c.entity_id == visible,
                record_assignments.c.entity_type == "person"))
            c.execute(_update(relationship_entities)
                      .where(relationship_entities.c.id == company["business_id"])
                      .values(person_id=None))
            c.execute(delete(people).where(people.c.id == visible))


def test_only_ownership_category_edges_are_surfaced(company):
    """A non-ownership edge (e.g. an advisory or referral link) is not "who owns this business"."""
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
        biz = _business(universal_search(_firm(), f"Rowe Builders Inc {_TAG}{tag}"), company)
        assert f"Vera Advisor{_TAG}{tag}" not in [p["name"] for p in biz["related_people"]], \
            "a non-ownership edge was surfaced as a related client"
        assert len(biz["related_people"]) == 2
    finally:
        with engine.begin() as c:
            c.execute(delete(relationships).where(relationships.c.id == edge))
            c.execute(delete(relationship_entities).where(relationship_entities.c.id == ent))
            c.execute(delete(people).where(people.c.id == pid))
            c.execute(delete(relationship_types).where(relationship_types.c.id == other_type))


def test_non_entity_results_carry_no_related_block(company):
    res = universal_search(_firm(), f"Alan Rowe{_TAG}{company['tag']}")
    for row in res["results"]:
        if row["kind"] == "person":
            assert "related_people" not in row


def test_relations_are_attached_with_a_bounded_number_of_queries(company):
    """One extra round trip for the whole page, not one per business (no N+1)."""
    from sqlalchemy import event

    statements = []
    engine_ = engine

    def _before(conn, cursor, statement, *a, **kw):
        if "relationships" in statement or "relationship_types" in statement:
            statements.append(statement)

    event.listen(engine_, "before_cursor_execute", _before)
    try:
        universal_search(_firm(), f"Rowe{_TAG}{company['tag']}")
    finally:
        event.remove(engine_, "before_cursor_execute", _before)

    edge_queries = [s for s in statements if "relationship_types" in s]
    assert len(edge_queries) <= 1, f"the edge lookup ran {len(edge_queries)} times (N+1)"


def test_the_related_list_is_capped(company):
    from app.services.universal_search import _MAX_RELATED
    assert _MAX_RELATED == 6
    res = universal_search(_firm(), f"Rowe Builders Inc {_TAG}{company['tag']}")
    biz = _business(res, company)
    assert len(biz["related_people"]) <= _MAX_RELATED
