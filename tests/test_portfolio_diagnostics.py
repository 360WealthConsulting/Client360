"""Read-only portfolio trace (`app.services.portfolio_diagnostics`).

Verifies the instrumented trace replays the real get_person_portfolio → snapshot → tile path
and correctly identifies the first point where AUM/Cash becomes zero, without writing anything.
"""
import uuid
from decimal import Decimal

from sqlalchemy import delete, insert, select

from app.db import accounts, engine, households, people
from app.services import portfolio_diagnostics as pdx


def _seed(*, own_account, total=Decimal("512345.67"), cash=Decimal("78901.23")):
    tag = uuid.uuid4().hex[:8]
    ids = {"people": [], "accounts": [], "hh": None}
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"DX HH {tag}").returning(households.c.id)).scalar_one()
        ids["hh"] = hid
        holder = c.execute(insert(people).values(
            household_id=hid, full_name=f"Holder {tag}", active=True).returning(people.c.id)).scalar_one()
        viewed = c.execute(insert(people).values(
            household_id=hid, full_name=f"Viewed {tag}", active=True).returning(people.c.id)).scalar_one()
        ids["people"] = [holder, viewed]
        ids["viewed"] = viewed
        owner = viewed if own_account else holder
        aid = c.execute(insert(accounts).values(
            custodian="Schwab", account_number=f"7997-{tag[:4]}", account_name="LLOYD, DENISE",
            registration_type="Joint", status="open", person_id=owner, household_id=hid,
            total_value=total, cash_value=cash).returning(accounts.c.id)).scalar_one()
        ids["accounts"] = [aid]
    return ids


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(accounts).where(accounts.c.id.in_(ids["accounts"])))
        c.execute(delete(people).where(people.c.id.in_(ids["people"])))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def test_trace_flags_zero_when_account_attributed_to_other_household_member():
    ids = _seed(own_account=False)
    try:
        d = pdx.diagnose(ids["viewed"])
        assert d["person_row_count"] == 0
        assert d["portfolio"]["aum"] == 0 and d["snapshot_assets"]["aum"] == 0
        assert d["financial_section"]["aum"] == 0
        assert "0 rows" in d["first_zero_point"] and "different person_id" in d["first_zero_point"]
        # The trace still surfaces where the money sits (under the other member).
        assert d["household_rows"] and d["household_rows"][0]["total_value"] == Decimal("512345.67")
        # SQL is captured verbatim.
        assert "accounts.person_id =" in d["sql"]["person_accounts"]
    finally:
        _teardown(ids)


def test_trace_reports_nonzero_for_person_with_own_account():
    ids = _seed(own_account=True)
    try:
        d = pdx.diagnose(ids["viewed"])
        assert d["person_row_count"] == 1
        assert d["portfolio"]["aum"] == Decimal("512345.67")
        assert d["snapshot_assets"]["aum"] == Decimal("512345.67")   # tiles read the same figure
        assert d["financial_section"]["aum"] == Decimal("512345.67")
        assert "no zero" in d["first_zero_point"]
    finally:
        _teardown(ids)


def test_trace_writes_nothing():
    ids = _seed(own_account=False)
    try:
        before = _account_snapshot(ids["accounts"][0])
        pdx.diagnose(ids["viewed"])
        assert _account_snapshot(ids["accounts"][0]) == before
    finally:
        _teardown(ids)


def test_usage_when_no_person_id():
    assert pdx.main([]) == 2


def _account_snapshot(account_id):
    with engine.connect() as c:
        r = c.execute(select(accounts.c.person_id, accounts.c.total_value, accounts.c.cash_value)
                      .where(accounts.c.id == account_id)).mappings().first()
    return dict(r)
