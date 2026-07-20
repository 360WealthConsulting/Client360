"""Unit tests for get_household_portfolio (Phase A, PR-2).

The service aggregates a household's portfolio across member accounts, reusing
the same aggregate_portfolio()/calculate_allocation() path as get_person_portfolio
(no duplicated aggregation logic). These tests insert an isolated household with
known accounts/holdings/members, assert every returned facet, verify numeric
parity with aggregate_portfolio, and cover the empty-household case.
"""
import uuid
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import (
    account_holdings,
    accounts,
    engine,
    household_relationships,
    households,
    people,
    securities,
)
from app.portfolio.calculations import aggregate_portfolio
from app.services.portfolio import get_household_portfolio


def _sym(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def _make_household(*, with_accounts=True):
    """Create an isolated household. Returns a dict of ids for assertions/teardown."""
    tag = uuid.uuid4().hex[:8]
    ids = {"security_ids": [], "account_ids": [], "person_ids": []}
    with engine.begin() as conn:
        hid = conn.execute(households.insert().values(name=f"Test HH {tag}").returning(households.c.id)).scalar_one()
        ids["household_id"] = hid
        # Two members (primary + spouse).
        for i, (name, primary) in enumerate((("Zoe Primary", True), ("Alex Spouse", False))):
            pid = conn.execute(
                people.insert().values(
                    household_id=hid, full_name=name,
                    primary_email=f"{tag}-{i}@example.test",
                    normalized_email=f"{tag}-{i}@example.test", active=True,
                ).returning(people.c.id)
            ).scalar_one()
            ids["person_ids"].append(pid)
            conn.execute(household_relationships.insert().values(
                household_id=hid, person_id=pid,
                relationship_type="spouse", is_primary=primary,
            ))
        if with_accounts:
            equity, bond = _sym("EQ"), _sym("BD")
            eq_id = conn.execute(securities.insert().values(symbol=equity, name="Equity Fund", asset_class="Equity").returning(securities.c.id)).scalar_one()
            bd_id = conn.execute(securities.insert().values(symbol=bond, name="Bond Fund", asset_class="Bond").returning(securities.c.id)).scalar_one()
            ids["security_ids"] = [eq_id, bd_id]
            # Account A: 300k / 30k cash; Account B: 100k / 10k cash -> AUM 400k, cash 40k.
            a = conn.execute(accounts.insert().values(
                household_id=hid, person_id=ids["person_ids"][0], custodian="Schwab",
                account_number=_sym("ACCTA"), registration_type="Individual",
                status="open", total_value=Decimal("300000"), cash_value=Decimal("30000"),
            ).returning(accounts.c.id)).scalar_one()
            b = conn.execute(accounts.insert().values(
                household_id=hid, person_id=ids["person_ids"][1], custodian="Schwab",
                account_number=_sym("ACCTB"), registration_type="Individual",
                status="open", total_value=Decimal("100000"), cash_value=Decimal("10000"),
            ).returning(accounts.c.id)).scalar_one()
            ids["account_ids"] = [a, b]
            # Holdings: EQ 250k (A) + EQ 90k (B) = 340k Equity; BD 50k (A) = 50k Bond.
            for acct, sec, mv in ((a, eq_id, "250000"), (a, bd_id, "50000"), (b, eq_id, "90000")):
                conn.execute(account_holdings.insert().values(
                    account_id=acct, security_id=sec, quantity=1,
                    market_value=Decimal(mv), as_of_date="2026-01-01",
                ))
    return ids


def _teardown(ids):
    with engine.begin() as conn:
        if ids["account_ids"]:
            conn.execute(delete(account_holdings).where(account_holdings.c.account_id.in_(ids["account_ids"])))
            conn.execute(delete(accounts).where(accounts.c.id.in_(ids["account_ids"])))
        if ids["security_ids"]:
            conn.execute(delete(securities).where(securities.c.id.in_(ids["security_ids"])))
        conn.execute(delete(household_relationships).where(household_relationships.c.household_id == ids["household_id"]))
        if ids["person_ids"]:
            conn.execute(delete(people).where(people.c.id.in_(ids["person_ids"])))
        conn.execute(delete(households).where(households.c.id == ids["household_id"]))


def test_household_portfolio_aggregates_all_facets():
    ids = _make_household()
    try:
        result = get_household_portfolio(ids["household_id"])
        assert result["household_id"] == ids["household_id"]
        assert result["aum"] == Decimal("400000")
        assert result["cash"] == Decimal("40000")
        assert result["cash_percent"] == Decimal("10")
        assert len(result["accounts"]) == 2
        assert len(result["holdings"]) == 3
        # Largest position = the 250k equity holding.
        assert result["largest_positions"][0]["market_value"] == Decimal("250000")
        # Concentration: 250k / 400k AUM = 62.5%.
        assert result["concentration"]["largest_position_percent"] == Decimal("62.5")
        assert result["concentration"]["top_position"]["market_value"] == Decimal("250000")
        # Allocation aggregates across accounts: Equity 340k, Bond 50k.
        assert result["allocation"]["Equity"]["value"] == Decimal("340000")
        assert result["allocation"]["Bond"]["value"] == Decimal("50000")
        # Members roster: two, primary first.
        assert [m["full_name"] for m in result["members"]] == ["Zoe Primary", "Alex Spouse"]
    finally:
        _teardown(ids)


def test_household_portfolio_matches_aggregate_portfolio_directly():
    # Parity: the service must return the same numbers as aggregate_portfolio on
    # the same rows — proving reuse, not a divergent second implementation.
    ids = _make_household()
    try:
        result = get_household_portfolio(ids["household_id"])
        with engine.connect() as conn:
            account_rows = conn.execute(select(accounts).where(accounts.c.household_id == ids["household_id"])).mappings().all()
            acct_ids = [r["id"] for r in account_rows]
            holding_rows = conn.execute(
                select(account_holdings.c.account_id, account_holdings.c.market_value,
                       account_holdings.c.cost_basis, account_holdings.c.unrealized_gain,
                       securities.c.symbol, securities.c.name, securities.c.asset_class)
                .join(securities, securities.c.id == account_holdings.c.security_id)
                .where(account_holdings.c.account_id.in_(acct_ids))
            ).mappings().all()
        expected = aggregate_portfolio(account_rows, holding_rows)
        assert result["aum"] == expected["total_aum"]
        assert result["cash"] == expected["cash"]
        assert result["concentration"]["largest_position_percent"] == expected["largest_position_percent"]
        assert result["allocation"] == expected["asset_allocation"]
    finally:
        _teardown(ids)


def test_empty_household_returns_safe_zeros():
    ids = _make_household(with_accounts=False)
    try:
        result = get_household_portfolio(ids["household_id"])
        assert result["aum"] == Decimal("0")
        assert result["cash"] == Decimal("0")
        assert result["holdings"] == []
        assert result["allocation"] == {}
        assert result["largest_positions"] == []
        assert result["concentration"]["largest_position_percent"] == Decimal("0")
        assert result["concentration"]["top_position"] is None
        assert result["accounts"] == []
        # Members still present even with no accounts.
        assert len(result["members"]) == 2
    finally:
        _teardown(ids)
