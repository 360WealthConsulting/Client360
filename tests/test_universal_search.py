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
