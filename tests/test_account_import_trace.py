"""Read-only Schwab account import trace (`app.services.account_import_trace`).

Proves the tool reports every accounts row for a number (exact + normalized), the raw
AccountsList valuation cells, and the correct verdict for each root cause — without writing.
"""
import uuid
from decimal import Decimal

from sqlalchemy import delete, insert, select

from app.db import accounts, engine
from app.services import account_import_trace as ait

_HEADER = "Account Number,Name,Registration,Status,Total Value,Cash Available\n"


def _account(number, *, total, cash, person_id=None, household_id=None, source_file="AccountsList_X.csv"):
    with engine.begin() as c:
        return c.execute(insert(accounts).values(
            custodian="Schwab", account_number=number, account_name="LLOYD, DENISE",
            status="open", person_id=person_id, household_id=household_id,
            total_value=total, cash_value=cash, source_file=source_file,
        ).returning(accounts.c.id)).scalar_one()


def _cleanup(account_ids):
    with engine.begin() as c:
        c.execute(delete(accounts).where(accounts.c.id.in_(account_ids)))


def _write_csv(folder, number, total_cell, cash_cell):
    (folder / "AccountsList_2026-03-13.csv").write_text(
        _HEADER + f'{number},"LLOYD, DENISE",Individual,Open,"{total_cell}","{cash_cell}"\n')


def test_trace_flags_duplicate_rows_valued_under_other_format(tmp_path):
    num = f"5391-{uuid.uuid4().hex[:4]}"
    zero = _account(num, total=Decimal("0.00"), cash=Decimal("0.00"), person_id=None)
    valued = _account(num.replace("-", ""), total=Decimal("318204.55"), cash=Decimal("41022.19"))
    try:
        d = ait.trace(num, folder=tmp_path)          # empty folder → DB-only evidence
        assert d["db_row_count"] == 2
        assert "regexp_replace" in d["sql"]           # normalized match SQL shown
        assert any((r["total_value"] or 0) > 0 for r in d["rows"])
        assert "DIFFERENT row holds the non-zero valuation" in d["verdict"]
    finally:
        _cleanup([zero, valued])


def test_trace_source_genuinely_zero(tmp_path):
    num = f"5391-{uuid.uuid4().hex[:4]}"
    aid = _account(num, total=Decimal("0.00"), cash=Decimal("0.00"), source_file="AccountsList_2026-03-13.csv")
    _write_csv(tmp_path, num, "$0.00", "$0.00")
    try:
        d = ait.trace(num, folder=tmp_path)
        assert d["db_row_count"] == 1
        assert d["raw_accountslist"]["matches"][0]["parsed_total_value"] == Decimal("0")
        assert "source itself is zero" in d["verdict"]
    finally:
        _cleanup([aid])


def test_trace_raw_has_value_but_db_zero(tmp_path):
    num = f"5391-{uuid.uuid4().hex[:4]}"
    aid = _account(num, total=Decimal("0.00"), cash=Decimal("0.00"), source_file="AccountsList_2026-03-13.csv")
    _write_csv(tmp_path, num, "$318,204.55", "$41,022.19")   # source HAS a value, DB is zero
    try:
        d = ait.trace(num, folder=tmp_path)
        assert d["raw_accountslist"]["matches"][0]["parsed_total_value"] == Decimal("318204.55")
        assert "did not reach the accounts row" in d["verdict"]
    finally:
        _cleanup([aid])


def test_trace_writes_nothing(tmp_path):
    num = f"5391-{uuid.uuid4().hex[:4]}"
    aid = _account(num, total=Decimal("0.00"), cash=Decimal("0.00"))
    try:
        before = _snapshot(aid)
        ait.trace(num, folder=tmp_path)
        assert _snapshot(aid) == before
    finally:
        _cleanup([aid])


def test_usage_when_no_argument():
    assert ait.main([]) == 2


def _snapshot(account_id):
    with engine.connect() as c:
        r = c.execute(select(accounts.c.total_value, accounts.c.cash_value, accounts.c.person_id)
                      .where(accounts.c.id == account_id)).mappings().first()
    return dict(r)
