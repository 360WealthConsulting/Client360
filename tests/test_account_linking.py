"""Account → person linking + Client 360 account aggregation / Financial-tab listing.

The Schwab importer loads `accounts` and `source_contacts` separately; identity matching links
source_contacts → people in `person_source_links`, but nothing projected that onto
`accounts.person_id` — the column `get_person_portfolio` aggregates on — so a linked client read
$0 on the Client 360 tiles and Financial tab. These tests cover the backfill service
(`app.services.account_linking`) that closes the gap, the 4-link repair, and the resulting UI:
client aggregation, the Financial-tab account listing, zero-account behavior, multiple accounts for
one person, all Schwab-Profile source contacts linked, and Summary/Financial reading one source.
"""
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func, insert, select
from starlette.requests import Request

from app.db import (
    accounts,
    engine,
    households,
    people,
    person_source_links,
    source_contacts,
)
from app.security.models import Principal
from app.services import account_linking as al
from app.services.client360 import get_workspace
from app.services.portfolio import get_person_portfolio

FIRM = Principal(1, "m@e.test", "M", frozenset({"client.read", "record.read_all"}))


def _num():
    """A dashed Schwab-style account number; its normalized form is the profile source_record_id."""
    digits = uuid.uuid4().int % 100000000
    return f"{digits:08d}"[:4] + "-" + f"{digits:08d}"[4:]


def _scenario(*, accounts_spec, ambiguous=False, confirmed=True, link=True):
    """Build a household + person + N (account, Schwab-Profile source_contact[, person_source_link]).

    `accounts_spec` is a list of dicts with total/cash (and optional as_of). Returns an ids bag.
    """
    tag = uuid.uuid4().hex[:8]
    ids = {"account_ids": [], "source_contact_ids": [], "psl_ids": [], "extra_person_ids": []}
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"AL HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(
            household_id=hid, full_name=f"AL Client {tag}",
            primary_email=f"{tag}@example.test", normalized_email=f"{tag}@example.test", active=True,
        ).returning(people.c.id)).scalar_one()
        ids["household_id"], ids["person_id"] = hid, pid

        if ambiguous:  # a second person the same profile also links to → unresolvable
            other = c.execute(insert(people).values(
                full_name=f"AL Other {tag}", primary_email=f"o{tag}@example.test",
                normalized_email=f"o{tag}@example.test", active=True).returning(people.c.id)).scalar_one()
            ids["extra_person_ids"].append(other)

        for spec in accounts_spec:
            number = _num()
            acct = c.execute(insert(accounts).values(
                custodian="Schwab", account_number=number, account_name=spec.get("name", "TEST ACCOUNT"),
                registration_type=spec.get("type", "Individual"), status="open",
                total_value=spec["total"], cash_value=spec["cash"],
                last_imported_at=spec.get("as_of"),
            ).returning(accounts.c.id)).scalar_one()
            ids["account_ids"].append(acct)

            sc = c.execute(insert(source_contacts).values(
                source_system="Schwab Profile", source_file="Profile_Firm_TEST.csv",
                source_record_id=al._norm(number), source_hash=uuid.uuid4().hex,
                full_name=spec.get("name", "TEST ACCOUNT"), raw_data=json.dumps({"Account#": number}),
            ).returning(source_contacts.c.id)).scalar_one()
            ids["source_contact_ids"].append(sc)

            if link:
                psl = c.execute(insert(person_source_links).values(
                    person_id=pid, source_contact_id=sc, match_method="test",
                    match_score=100, confirmed=confirmed).returning(person_source_links.c.id)).scalar_one()
                ids["psl_ids"].append(psl)
                if ambiguous:
                    psl2 = c.execute(insert(person_source_links).values(
                        person_id=other, source_contact_id=sc, match_method="test",
                        match_score=100, confirmed=confirmed).returning(person_source_links.c.id)).scalar_one()
                    ids["psl_ids"].append(psl2)
    return ids


def _teardown(ids):
    with engine.begin() as c:
        if ids["psl_ids"]:
            c.execute(delete(person_source_links).where(person_source_links.c.id.in_(ids["psl_ids"])))
        if ids["account_ids"]:
            c.execute(delete(accounts).where(accounts.c.id.in_(ids["account_ids"])))
        if ids["source_contact_ids"]:
            c.execute(delete(source_contacts).where(source_contacts.c.id.in_(ids["source_contact_ids"])))
        for pid in ids["extra_person_ids"] + [ids["person_id"]]:
            c.execute(delete(people).where(people.c.id == pid))
        c.execute(delete(households).where(households.c.id == ids["household_id"]))


def _person_id(account_id):
    with engine.connect() as c:
        return c.scalar(select(accounts.c.person_id).where(accounts.c.id == account_id))


# --- the linking service -----------------------------------------------------

def test_link_projects_source_link_onto_account():
    ids = _scenario(accounts_spec=[{"total": Decimal("250000"), "cash": Decimal("25000")}])
    try:
        assert _person_id(ids["account_ids"][0]) is None          # importer left it NULL
        summary = al.link_accounts_to_people()
        assert summary["linked"] >= 1
        assert _person_id(ids["account_ids"][0]) == ids["person_id"]
        with engine.connect() as c:
            hh = c.scalar(select(accounts.c.household_id).where(accounts.c.id == ids["account_ids"][0]))
        assert hh == ids["household_id"]                           # household backfilled from the person
    finally:
        _teardown(ids)


def test_link_is_idempotent():
    ids = _scenario(accounts_spec=[{"total": Decimal("100"), "cash": Decimal("10")}])
    try:
        al.link_accounts_to_people()
        second = al.link_accounts_to_people()
        assert second["linked"] == 0                              # nothing to change on a rescan
        assert _person_id(ids["account_ids"][0]) == ids["person_id"]
    finally:
        _teardown(ids)


def test_ambiguous_profile_is_not_linked():
    ids = _scenario(accounts_spec=[{"total": Decimal("500"), "cash": Decimal("0")}], ambiguous=True)
    try:
        al.link_accounts_to_people()
        assert _person_id(ids["account_ids"][0]) is None          # two candidate people → never guessed
    finally:
        _teardown(ids)


def test_repair_source_links_creates_missing_link_without_duplicates():
    # A profile with NO person_source_link (the consistency gap), plus the canonical person.
    ids = _scenario(accounts_spec=[{"total": Decimal("441857.25"), "cash": Decimal("210641.74")}], link=False)
    try:
        with engine.connect() as c:
            number = c.scalar(select(accounts.c.account_number).where(accounts.c.id == ids["account_ids"][0]))
        pairs = ((number, ids["person_id"]),)
        first = al.repair_source_links(pairs)
        assert first["created"] == 1
        second = al.repair_source_links(pairs)                    # idempotent — unique constraint respected
        assert second["created"] == 0 and second["already_present"] == 1
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(person_source_links)
                         .where(person_source_links.c.source_contact_id.in_(ids["source_contact_ids"])))
        assert n == 1                                             # no duplicate link
        # and the account now links after propagation
        al.link_accounts_to_people()
        assert _person_id(ids["account_ids"][0]) == ids["person_id"]
    finally:
        with engine.begin() as c:
            c.execute(delete(person_source_links).where(
                person_source_links.c.source_contact_id.in_(ids["source_contact_ids"])))
        _teardown(ids)


# --- dry-run preview (no writes) ---------------------------------------------

def test_preview_reports_planned_changes_without_writing():
    # Profile with NO person_source_link (a consistency gap) + the canonical person.
    ids = _scenario(accounts_spec=[{"total": Decimal("441857.25"), "cash": Decimal("210641.74")}], link=False)
    try:
        with engine.connect() as c:
            number = c.scalar(select(accounts.c.account_number).where(accounts.c.id == ids["account_ids"][0]))
        plan = al.preview(((number, ids["person_id"]),))
        # It reports the source link to be created and the account that would receive a person_id.
        assert any(sl["source_contact_id"] == ids["source_contact_ids"][0]
                   and sl["person_id"] == ids["person_id"] for sl in plan["source_links_to_create"])
        change = next(ch for ch in plan["account_changes"] if ch["account_id"] == ids["account_ids"][0])
        assert change["current_person_id"] is None and change["new_person_id"] == ids["person_id"]
        assert change["account_number"] == number and change["client_name"]
        # ...but NOTHING was written.
        assert _person_id(ids["account_ids"][0]) is None
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(person_source_links)
                         .where(person_source_links.c.source_contact_id.in_(ids["source_contact_ids"])))
        assert n == 0
    finally:
        _teardown(ids)


# --- client aggregation ------------------------------------------------------

def test_client_account_aggregation_matches_account_totals():
    ids = _scenario(accounts_spec=[{"total": Decimal("250000"), "cash": Decimal("25000")}])
    try:
        al.link_accounts_to_people()
        portfolio = get_person_portfolio(ids["person_id"])
        assert "aum" not in portfolio          # AUM is exposed to nobody
        assert len(portfolio["accounts"]) >= 1  # account linkage itself still works
        assert portfolio["cash"] == Decimal("25000")
        assert len(portfolio["accounts"]) == 1
    finally:
        _teardown(ids)


def test_multiple_accounts_for_one_person_are_summed():
    ids = _scenario(accounts_spec=[
        {"total": Decimal("441857.25"), "cash": Decimal("210641.74")},
        {"total": Decimal("58142.75"), "cash": Decimal("9358.26")},
    ])
    try:
        al.link_accounts_to_people()
        portfolio = get_person_portfolio(ids["person_id"])
        assert "aum" not in portfolio
        assert len(portfolio["accounts"]) == 2  # both accounts still linked to the person
        assert portfolio["cash"] == Decimal("220000.00")
        assert len(portfolio["accounts"]) == 2
    finally:
        _teardown(ids)


def test_zero_account_client_reads_zero_and_renders():
    ids = _scenario(accounts_spec=[])   # person, no accounts
    try:
        portfolio = get_person_portfolio(ids["person_id"])
        assert "aum" not in portfolio and (portfolio.get("cash") or 0) == 0
        body = _render_financial(ids["person_id"])
        assert "No linked accounts" in body                       # empty state, no crash
    finally:
        _teardown(ids)


# --- Summary / Financial share one source ------------------------------------

def test_summary_tiles_and_financial_tab_use_one_source():
    ids = _scenario(accounts_spec=[{"total": Decimal("250000"), "cash": Decimal("25000")}])
    try:
        al.link_accounts_to_people()
        ws = get_workspace(FIRM, person_id=ids["person_id"])
        # Summary snapshot tiles and the Financial section derive from the SAME portfolio read.
        # One source, and it carries no AUM on either surface.
        assert "aum" not in ws["snapshot"]["assets"]
        assert "aum" not in ws["sections"]["financial"]
        assert ws["snapshot"]["assets"]["cash"] == ws["sections"]["financial"]["cash"] == Decimal("25000")
    finally:
        _teardown(ids)


# --- Financial tab account listing -------------------------------------------

def _render_financial(person_id):
    from app.routes.client360 import client_workspace
    req = Request({"type": "http", "method": "GET", "path": f"/client/{person_id}",
                   "headers": [], "query_string": b"tab=financial"})
    return client_workspace(req, person_id, tab="financial", principal=FIRM).body.decode()


def test_financial_tab_lists_linked_accounts_with_fields():
    as_of = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)   # noon UTC → same calendar day in US tz
    ids = _scenario(accounts_spec=[
        {"total": Decimal("441857.25"), "cash": Decimal("210641.74"),
         "name": "LANCASTER, KENDELL Q", "type": "Dsg Ben In", "as_of": as_of}])
    try:
        al.link_accounts_to_people()
        body = _render_financial(ids["person_id"])
        assert "Accounts" in body
        with engine.connect() as c:
            number = c.scalar(select(accounts.c.account_number).where(accounts.c.id == ids["account_ids"][0]))
        assert number in body                                     # account number
        assert "Schwab" in body                                   # custodian
        assert "LANCASTER, KENDELL Q" in body                     # account name
        assert "Dsg Ben In" in body                               # account type
        assert "441,857.25" in body                               # total value
        assert "210,641.74" in body                               # cash value
        assert "2026-03-13" in body                               # as-of date
    finally:
        _teardown(ids)


# --- all Schwab-Profile source contacts linked -------------------------------

def test_unlinked_profile_count_reflects_linkage():
    before = al.unlinked_profile_count()
    ids = _scenario(accounts_spec=[{"total": Decimal("1"), "cash": Decimal("0")}], link=False)
    try:
        assert al.unlinked_profile_count() == before + 1          # the new profile is unlinked
        with engine.connect() as c:
            number = c.scalar(select(accounts.c.account_number).where(accounts.c.id == ids["account_ids"][0]))
        al.repair_source_links(((number, ids["person_id"]),))
        assert al.unlinked_profile_count() == before              # every profile linked again
    finally:
        with engine.begin() as c:
            c.execute(delete(person_source_links).where(
                person_source_links.c.source_contact_id.in_(ids["source_contact_ids"])))
        _teardown(ids)
