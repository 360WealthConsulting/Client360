"""Contract tests for the normalized Wealth service vocabulary (Phase C.1 PR-2).

get_person_portfolio() and get_household_portfolio() both build on the shared
_portfolio() helper, which produces one canonical contract and then adds
temporary legacy aliases. These tests pin: (1) both services expose the full
canonical vocabulary with identical values for the same underlying accounts;
(2) the legacy aliases mirror the canonical values byte-for-byte; (3) the
household result stays clean (no aliases re-exposed).
"""
import uuid
from decimal import Decimal

from sqlalchemy import delete, insert

from app.db import (
    account_beneficiaries,
    account_holdings,
    accounts,
    engine,
    households,
    people,
    securities,
)
from app.services.portfolio import (
    _CANONICAL_KEYS,
    get_household_portfolio,
    get_person_portfolio,
)

_LEGACY_ALIASES = {
    "total_aum": "aum",
    "asset_allocation": "allocation",
    "largest_holdings": "largest_positions",
}


def _sym(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def _make_sole_member_household():
    """One person who owns every account in the household -> the person and the
    household portfolios must produce identical canonical values."""
    tag = uuid.uuid4().hex[:8]
    ids = {"security_ids": [], "account_ids": [], "beneficiary_ids": []}
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"Contract HH {tag}").returning(households.c.id)).scalar_one()
        ids["household_id"] = hid
        pid = c.execute(people.insert().values(
            household_id=hid, full_name="Sole Owner",
            primary_email=f"{tag}@example.test", normalized_email=f"{tag}@example.test", active=True,
        ).returning(people.c.id)).scalar_one()
        ids["person_id"] = pid
        eq = c.execute(securities.insert().values(symbol=_sym("EQ"), name="Equity Fund", asset_class="Equity").returning(securities.c.id)).scalar_one()
        bd = c.execute(securities.insert().values(symbol=_sym("BD"), name="Bond Fund", asset_class="Bond").returning(securities.c.id)).scalar_one()
        ids["security_ids"] = [eq, bd]
        a = c.execute(insert(accounts).values(
            household_id=hid, person_id=pid, custodian="Schwab", account_number=_sym("A"),
            registration_type="Roth IRA", status="open", total_value=Decimal("300000"), cash_value=Decimal("30000"),
        ).returning(accounts.c.id)).scalar_one()
        b = c.execute(insert(accounts).values(
            household_id=hid, person_id=pid, custodian="Schwab", account_number=_sym("B"),
            registration_type="Individual", status="open", total_value=Decimal("100000"), cash_value=Decimal("10000"),
        ).returning(accounts.c.id)).scalar_one()
        ids["account_ids"] = [a, b]
        for acct, sec, mv in ((a, eq, "250000"), (a, bd, "50000"), (b, eq, "90000")):
            c.execute(insert(account_holdings).values(account_id=acct, security_id=sec, quantity=1, market_value=Decimal(mv), as_of_date="2026-01-01"))
        bid = c.execute(account_beneficiaries.insert().values(
            account_id=a, beneficiary_name="Estate (test)", beneficiary_type="primary", active=True,
        ).returning(account_beneficiaries.c.id)).scalar_one()
        ids["beneficiary_ids"] = [bid]
    return ids


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(account_beneficiaries).where(account_beneficiaries.c.id.in_(ids["beneficiary_ids"])))
        c.execute(delete(account_holdings).where(account_holdings.c.account_id.in_(ids["account_ids"])))
        c.execute(delete(accounts).where(accounts.c.id.in_(ids["account_ids"])))
        c.execute(delete(securities).where(securities.c.id.in_(ids["security_ids"])))
        c.execute(delete(people).where(people.c.id == ids["person_id"]))
        c.execute(delete(households).where(households.c.id == ids["household_id"]))


def test_both_services_expose_identical_canonical_values():
    ids = _make_sole_member_household()
    try:
        person = get_person_portfolio(ids["person_id"])
        household = get_household_portfolio(ids["household_id"])
        # Both expose the full canonical vocabulary.
        for key in _CANONICAL_KEYS:
            assert key in person, f"person missing canonical key {key}"
            assert key in household, f"household missing canonical key {key}"
        # Scalar/dict canonical values match (person owns every household account).
        for key in ("aum", "cash", "cash_percent", "allocation", "beneficiary_count"):
            assert person[key] == household[key], f"canonical {key} diverges"
        assert person["concentration"]["largest_position_percent"] == household["concentration"]["largest_position_percent"]
        for key in ("holdings", "largest_positions", "accounts"):
            assert len(person[key]) == len(household[key])
        # Sanity on the actual numbers.
        assert person["aum"] == Decimal("400000")
        assert person["concentration"]["largest_position_percent"] == Decimal("62.5")
    finally:
        _teardown(ids)


def test_legacy_aliases_mirror_canonical_on_person_service():
    ids = _make_sole_member_household()
    try:
        person = get_person_portfolio(ids["person_id"])
        for legacy, canonical in _LEGACY_ALIASES.items():
            assert person[legacy] == person[canonical], f"alias {legacy} != {canonical}"
        assert person["largest_position_percent"] == person["concentration"]["largest_position_percent"]
    finally:
        _teardown(ids)


def test_household_result_exposes_no_legacy_aliases():
    ids = _make_sole_member_household()
    try:
        household = get_household_portfolio(ids["household_id"])
        for legacy in (*_LEGACY_ALIASES.keys(), "largest_position_percent"):
            assert legacy not in household, f"household should not re-expose {legacy}"
    finally:
        _teardown(ids)
