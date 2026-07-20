"""Render tests for the Household Wealth Workspace (Phase A, PR-3).

The workspace lives on the existing /households/{id} page and is fed by
get_household_portfolio(). These tests render the real route and assert the
Overview / Accounts / Holdings sections and the summary appear, that the
existing Members table is preserved, and that an empty household degrades
gracefully.
"""
import uuid
from decimal import Decimal

from sqlalchemy import delete, insert
from starlette.requests import Request

from app.db import (
    account_holdings,
    accounts,
    engine,
    household_relationships,
    households,
    people,
    securities,
)
from app.routes.households import household_profile
from app.security.models import Principal


def _sym(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def _make_household(*, with_accounts=True):
    tag = uuid.uuid4().hex[:8]
    ids = {"security_ids": [], "account_ids": [], "person_ids": []}
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"Workspace HH {tag}").returning(households.c.id)).scalar_one()
        ids["household_id"] = hid
        for i, (name, primary) in enumerate((("Dana Primary", True), ("Reese Spouse", False))):
            pid = c.execute(people.insert().values(
                household_id=hid, full_name=name, last_name=name.split()[1],
                first_name=name.split()[0], primary_email=f"{tag}-{i}@example.test",
                normalized_email=f"{tag}-{i}@example.test", active=True,
            ).returning(people.c.id)).scalar_one()
            ids["person_ids"].append(pid)
            c.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="spouse", is_primary=primary))
        if with_accounts:
            eq = c.execute(securities.insert().values(symbol=_sym("EQ"), name="Equity Fund", asset_class="Equity").returning(securities.c.id)).scalar_one()
            bd = c.execute(securities.insert().values(symbol=_sym("BD"), name="Bond Fund", asset_class="Bond").returning(securities.c.id)).scalar_one()
            ids["security_ids"] = [eq, bd]
            acct = c.execute(insert(accounts).values(
                household_id=hid, person_id=ids["person_ids"][0], custodian="Schwab",
                account_number=_sym("ACCT"), registration_type="Roth IRA",
                status="open", total_value=Decimal("200000"), cash_value=Decimal("20000"),
            ).returning(accounts.c.id)).scalar_one()
            ids["account_ids"] = [acct]
            for sec, mv in ((eq, "150000"), (bd, "30000")):
                c.execute(insert(account_holdings).values(
                    account_id=acct, security_id=sec, quantity=1,
                    market_value=Decimal(mv), as_of_date="2026-01-01"))
    return ids


def _teardown(ids):
    with engine.begin() as c:
        if ids["account_ids"]:
            c.execute(delete(account_holdings).where(account_holdings.c.account_id.in_(ids["account_ids"])))
            c.execute(delete(accounts).where(accounts.c.id.in_(ids["account_ids"])))
        if ids["security_ids"]:
            c.execute(delete(securities).where(securities.c.id.in_(ids["security_ids"])))
        c.execute(delete(household_relationships).where(household_relationships.c.household_id == ids["household_id"]))
        if ids["person_ids"]:
            c.execute(delete(people).where(people.c.id.in_(ids["person_ids"])))
        c.execute(delete(households).where(households.c.id == ids["household_id"]))


def _render(household_id):
    scope = {"type": "http", "method": "GET", "path": f"/households/{household_id}",
             "headers": [], "query_string": b""}
    req = Request(scope)
    req.state.principal = Principal(1, "s@e.com", "Staff", frozenset({"record.read_all", "client.read"}))
    return household_profile(req, household_id)


def test_workspace_renders_all_sections():
    ids = _make_household()
    try:
        body = _render(ids["household_id"]).body.decode()
        # Workspace + component stylesheet.
        assert 'href="/static/css/workspace.css"' in body
        assert "Household wealth" in body
        # Overview.
        assert "Concentration" in body
        assert "Beneficiaries" in body
        assert "Asset allocation" in body
        assert "Largest positions" in body
        assert "Equity" in body and "Bond" in body
        # Accounts section: registration + custodian + value.
        assert "Accounts" in body
        assert "Roth IRA" in body
        assert "Schwab" in body
        assert "200,000.00" in body
        # Holdings: full table collapsed in a <details> (on-page, not navigated).
        assert "<details" in body
        assert "All holdings" in body
        assert "150,000.00" in body  # holding value still in the DOM
        # Open tasks reframed as the Meeting Agenda (count only), not a bare metric.
        assert "Meeting agenda" in body
        assert "Open Tasks" not in body
        # Admin form tucked behind a collapsible "Manage members".
        assert "Manage members" in body
        # "Last import" ops stat removed from the overview.
        assert "Last import" not in body
        # Roster is visible; the collapsed manage-members admin block sits below it.
        assert "Household members" in body
        assert "Dana Primary" in body
        assert body.index("Household members") < body.index("Manage members")
    finally:
        _teardown(ids)


def test_workspace_empty_household_degrades_gracefully():
    ids = _make_household(with_accounts=False)
    try:
        body = _render(ids["household_id"]).body.decode()
        assert "Household wealth" in body
        assert "No portfolio accounts found for this household." in body
        assert "No holdings imported for this household." in body
        assert "No positions imported." in body
        # Members still render.
        assert "Dana Primary" in body
    finally:
        _teardown(ids)
