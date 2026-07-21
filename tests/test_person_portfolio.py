"""Render tests for the Client Wealth Workspace (person profile → Portfolio tab).

This tab is the individual-client view of the meeting-prep screen. These tests
assert it is aligned with the Household Wealth Workspace: the same summary
ordering (Client AUM · Household AUM · Cash % · Beneficiary status), the
"Client wealth" section title, allocation/positions before an Accounts table,
and matched empty-state language. Reuses get_person_portfolio() — no new query.
"""
import uuid
from decimal import Decimal

from sqlalchemy import delete, insert
from starlette.requests import Request

from app.db import (
    account_beneficiaries,
    account_holdings,
    accounts,
    engine,
    households,
    people,
    securities,
)
from app.routes.people import person_profile
from app.security.models import Principal


def _sym(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def _make_client(*, with_accounts=True):
    tag = uuid.uuid4().hex[:8]
    ids = {"security_ids": [], "account_ids": [], "beneficiary_ids": []}
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"Client HH {tag}").returning(households.c.id)).scalar_one()
        ids["household_id"] = hid
        pid = c.execute(people.insert().values(
            household_id=hid, full_name="Jordan Client",
            primary_email=f"{tag}@example.test", normalized_email=f"{tag}@example.test", active=True,
        ).returning(people.c.id)).scalar_one()
        ids["person_id"] = pid
        if with_accounts:
            eq = c.execute(securities.insert().values(symbol=_sym("EQ"), name="Equity Fund", asset_class="Equity").returning(securities.c.id)).scalar_one()
            ids["security_ids"] = [eq]
            acct = c.execute(insert(accounts).values(
                household_id=hid, person_id=pid, custodian="Schwab",
                account_number=_sym("ACCT"), registration_type="Roth IRA",
                status="open", total_value=Decimal("250000"), cash_value=Decimal("25000"),
            ).returning(accounts.c.id)).scalar_one()
            ids["account_ids"] = [acct]
            c.execute(insert(account_holdings).values(
                account_id=acct, security_id=eq, quantity=1,
                market_value=Decimal("225000"), as_of_date="2026-01-01"))
            bid = c.execute(account_beneficiaries.insert().values(
                account_id=acct, beneficiary_name="Estate (test)", beneficiary_type="primary", active=True,
            ).returning(account_beneficiaries.c.id)).scalar_one()
            ids["beneficiary_ids"] = [bid]
    return ids


def _teardown(ids):
    with engine.begin() as c:
        if ids["beneficiary_ids"]:
            c.execute(delete(account_beneficiaries).where(account_beneficiaries.c.id.in_(ids["beneficiary_ids"])))
        if ids["account_ids"]:
            c.execute(delete(account_holdings).where(account_holdings.c.account_id.in_(ids["account_ids"])))
            c.execute(delete(accounts).where(accounts.c.id.in_(ids["account_ids"])))
        if ids["security_ids"]:
            c.execute(delete(securities).where(securities.c.id.in_(ids["security_ids"])))
        c.execute(delete(people).where(people.c.id == ids["person_id"]))
        c.execute(delete(households).where(households.c.id == ids["household_id"]))


def _render(person_id):
    scope = {"type": "http", "method": "GET", "path": f"/people/{person_id}",
             "headers": [], "query_string": b"tab=portfolio"}
    req = Request(scope)
    req.state.principal = Principal(1, "s@e.com", "Staff", frozenset({"record.read_all", "client.read"}))
    return person_profile(req, person_id, tab="portfolio")


def test_client_workspace_aligned_with_household():
    ids = _make_client()
    try:
        body = _render(ids["person_id"]).body.decode()
        # Aligned section title + summary ordering.
        assert "Client wealth" in body
        for label in ("Client AUM", "Household AUM", "Cash", "Beneficiaries"):
            assert label in body
        # Beneficiary status uses the existing count (1 active beneficiary).
        assert "Active beneficiaries on file" in body
        # Household terminology + hierarchy.
        assert "Asset allocation" in body
        assert "Largest positions" in body
        assert "Accounts" in body
        assert "Roth IRA" in body and "Schwab" in body and "250,000.00" in body
        # Old client-only labels are gone.
        assert "Portfolio Intelligence" not in body
        assert "Accounts &amp; Registrations" not in body
        assert "Accounts & Registrations" not in body
        assert "Largest Holdings" not in body
    finally:
        _teardown(ids)


def test_client_workspace_empty_state_language_matches_household():
    ids = _make_client(with_accounts=False)
    try:
        body = _render(ids["person_id"]).body.decode()
        assert "Client wealth" in body
        assert "No portfolio accounts found for this client." in body
        assert "No positions imported." in body
    finally:
        _teardown(ids)
