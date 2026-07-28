"""Instrumented trace of the Client 360 wealth read for a single person (read-only).

Answers, for one ``person_id``, exactly *where* the AUM/Cash figures become zero by
replaying the real production path — ``get_person_portfolio`` → snapshot → the Summary
tiles — and dumping every intermediate value:

  1. the SQL ``get_person_portfolio`` executes (person-accounts + household lookup);
  2. the number of account rows returned;
  3. each row's ``total_value`` / ``cash_value`` (+ person_id / household_id);
  4. the snapshot object the Summary tiles read;
  5. the final AUM / Cash rendered into the tiles and the Financial tab.

It writes nothing. Run:  ``python -m app.services.portfolio_diagnostics <person_id>``
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, select

from app.db import accounts, engine, people
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.portfolio import get_person_portfolio

# A full-scope reader so the workspace boundary lets the trace through for any person.
_TRACE_PRINCIPAL = Principal(0, "diagnostics@local", "Diagnostics",
                             frozenset({"client.read", "record.read_all"}))


def _sql(stmt) -> str:
    return str(stmt.compile(engine, compile_kwargs={"literal_binds": True})).replace("\n", " ")


def _dec(value) -> Decimal:
    return Decimal(value or 0)


def diagnose(person_id: int) -> dict:
    person_accounts_stmt = (
        select(accounts).where(accounts.c.person_id == person_id)
        .order_by(accounts.c.total_value.desc().nullslast()))
    household_stmt = (
        select(accounts.c.household_id)
        .where(and_(accounts.c.person_id == person_id, accounts.c.household_id.is_not(None))).limit(1))

    with engine.connect() as conn:
        person = conn.execute(
            select(people.c.id, people.c.full_name, people.c.household_id).where(people.c.id == person_id)
        ).mappings().first()

        person_rows = conn.execute(person_accounts_stmt).mappings().all()
        resolved_household_id = conn.scalar(household_stmt)

        household_id = person["household_id"] if person else None
        # Where does the money actually sit? Every account in this person's household,
        # regardless of which person_id it is attributed to.
        household_rows = []
        if household_id is not None:
            household_rows = conn.execute(
                select(accounts.c.id, accounts.c.account_number, accounts.c.account_name,
                       accounts.c.person_id, accounts.c.household_id,
                       accounts.c.total_value, accounts.c.cash_value)
                .where(accounts.c.household_id == household_id)
                .order_by(accounts.c.total_value.desc().nullslast())).mappings().all()

    portfolio = get_person_portfolio(person_id)
    workspace = get_workspace(_TRACE_PRINCIPAL, person_id=person_id)
    tiles = (workspace or {}).get("snapshot", {}).get("assets", {})
    financial = (workspace or {}).get("sections", {}).get("financial", {})

    person_total = sum((_dec(r["total_value"]) for r in person_rows), Decimal(0))
    person_cash = sum((_dec(r["cash_value"]) for r in person_rows), Decimal(0))
    tiles_zero = _dec(tiles.get("aum")) == 0 and _dec(tiles.get("cash")) == 0

    # First point where the figure is zero.
    if person is None:
        first_zero = f"person_id {person_id} does not exist in people"
    elif len(person_rows) == 0:
        household_note = (" — but the household holds account(s) attributed to a different person_id "
                          "(see 'where the money sits' below)") if household_rows else ""
        first_zero = (f"the person-accounts SQL returned 0 rows — no account has person_id = {person_id}"
                      f"{household_note}")
    elif person_total == 0 and person_cash == 0:
        first_zero = "account rows were found, but their total_value AND cash_value are 0/NULL in the row data"
    elif tiles_zero:
        first_zero = ("account rows with non-zero values were found, yet the tiles read 0 — the zero is "
                      "introduced AFTER aggregate_portfolio (inspect the snapshot / tile mapping)")
    else:
        first_zero = (f"no zero — the portfolio is non-zero (tile AUM={tiles.get('aum')}, "
                      f"Cash={tiles.get('cash')})")

    return {
        "requested_person_id": person_id,
        "person": dict(person) if person else None,
        "sql": {"person_accounts": _sql(person_accounts_stmt), "household_lookup": _sql(household_stmt)},
        "person_rows": [dict(r) for r in person_rows],
        "person_row_count": len(person_rows),
        "resolved_household_id": resolved_household_id,
        "household_rows": [dict(r) for r in household_rows],
        "portfolio": {"aum": portfolio.get("aum"), "cash": portfolio.get("cash"),
                      "account_count": len(portfolio.get("accounts") or []),
                      "household_aum": (portfolio.get("household") or {}).get("aum")},
        "snapshot_assets": dict(tiles),
        "financial_section": {"aum": financial.get("aum"), "cash": financial.get("cash"),
                              "account_count": len(financial.get("accounts") or [])},
        "first_zero_point": first_zero,
    }


def _print(d: dict) -> None:
    print("=" * 78)
    print(f"PORTFOLIO TRACE — person_id={d['requested_person_id']}")
    print("=" * 78)
    print(f"[person]   {d['person']}")
    print()
    print("[1] SQL executed by get_person_portfolio():")
    print(f"    person-accounts : {d['sql']['person_accounts']}")
    print(f"    household lookup: {d['sql']['household_lookup']}")
    print()
    print(f"[2] rows returned by the person-accounts query: {d['person_row_count']}")
    print("[3] total_value / cash_value for those rows:")
    if d["person_rows"]:
        for r in d["person_rows"]:
            print(f"      account {r['id']}  {r.get('account_number')}  person_id={r.get('person_id')}  "
                  f"household_id={r.get('household_id')}  total={r.get('total_value')}  cash={r.get('cash_value')}")
    else:
        print("      (none)")
    print()
    print(f"    get_person_portfolio() → aum={d['portfolio']['aum']}  cash={d['portfolio']['cash']}  "
          f"accounts={d['portfolio']['account_count']}  household_aum={d['portfolio']['household_aum']}")
    print()
    print("[4] snapshot object read by the Summary tiles (assets):")
    print(f"      {d['snapshot_assets']}")
    print()
    print("[5] final values rendered:")
    print(f"      Summary tiles  → AUM={d['snapshot_assets'].get('aum')}  Cash={d['snapshot_assets'].get('cash')}")
    print(f"      Financial tab  → AUM={d['financial_section']['aum']}  Cash={d['financial_section']['cash']}  "
          f"accounts listed={d['financial_section']['account_count']}")
    print()
    if d["household_rows"]:
        print("[where the money sits] every account in this person's household:")
        for r in d["household_rows"]:
            print(f"      account {r['id']}  {r['account_number']}  {r['account_name']}  "
                  f"person_id={r['person_id']}  total={r['total_value']}  cash={r['cash_value']}")
        print()
    print(">>> FIRST ZERO POINT:")
    print(f"    {d['first_zero_point']}")
    print("=" * 78)


def main(argv=None) -> int:
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not args or not args[0].lstrip("-").isdigit():
        print("Usage: python -m app.services.portfolio_diagnostics <person_id>")
        return 2
    _print(diagnose(int(args[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
