"""Read-only trace of how one account number was imported + valued (Schwab).

Proves, with row data, *where* an account's valuation is — or why it is zero. For a given
account number it reports every ``accounts`` row that matches (exact AND normalized, so a
dash / no-dash duplicate is caught), the ``import_jobs`` run that last wrote each row, the
Schwab-Profile ``source_contact`` (which carries NO valuation — the account importer supplies
value from ``AccountsList_*.csv``), and the RAW ``AccountsList`` cells (`Total Value` /
`Cash Available`) the importer read — with `parse_money` applied exactly as the importer does.

The valuation columns ``total_value`` / ``cash_value`` are written ONLY by
``app.importers.schwab.import_accounts_file`` from the AccountsList CSV; the Profile import
writes only ``source_contacts``. This tool never writes. Run:
``python -m app.services.account_import_trace <account_number>``
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from sqlalchemy import func, select

from app.db import accounts, engine, metadata, source_contacts

import_jobs = metadata.tables["import_jobs"]

VALUATION_COLUMNS = ("Total Value", "Cash Available")   # the AccountsList cells the importer reads


def _norm(number: str | None) -> str:
    if not number:
        return ""
    return re.sub(r"[^0-9a-z]", "", str(number).lower())


def _normalized_col():
    # SQL: strip non-alphanumerics + lowercase account_number, to match dash/no-dash variants.
    return func.regexp_replace(func.lower(accounts.c.account_number), "[^0-9a-z]", "", "g")


def _sql(stmt) -> str:
    return str(stmt.compile(engine, compile_kwargs={"literal_binds": True})).replace("\n", " ")


def _db_rows(account_number):
    target = _norm(account_number)
    stmt = (select(accounts.c.id, accounts.c.person_id, accounts.c.household_id, accounts.c.custodian,
                   accounts.c.account_number, accounts.c.account_name, accounts.c.registration_type,
                   accounts.c.status, accounts.c.total_value, accounts.c.cash_value,
                   accounts.c.source_file, accounts.c.last_imported_at, accounts.c.created_at)
            .where(_normalized_col() == target)
            .order_by(accounts.c.total_value.desc().nullslast()))
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt).mappings()]
        source_files = {r["source_file"] for r in rows if r["source_file"]}
        jobs = []
        if source_files:
            jobs = [dict(r) for r in conn.execute(
                select(import_jobs.c.id, import_jobs.c.source_system, import_jobs.c.source_file,
                       import_jobs.c.status, import_jobs.c.rows_read, import_jobs.c.rows_inserted,
                       import_jobs.c.completed_at)
                .where(import_jobs.c.source_file.in_(source_files))
                .order_by(import_jobs.c.completed_at.desc().nullslast())).mappings()]
        contacts = [dict(r) for r in conn.execute(
            select(source_contacts.c.id, source_contacts.c.source_system, source_contacts.c.source_record_id,
                   source_contacts.c.full_name, source_contacts.c.source_file, source_contacts.c.raw_data)
            .where(func.regexp_replace(func.lower(source_contacts.c.source_record_id),
                                       "[^0-9a-z]", "", "g") == target)).mappings()]
    return {"sql": _sql(stmt), "rows": rows, "import_jobs": jobs, "source_contacts": contacts}


def _raw_accountslist(account_number, folder=None):
    """The RAW AccountsList rows for this account — the authoritative valuation source. Applies
    `parse_money` exactly as the importer does, so the parsed value equals what would be stored."""
    from app.importers import schwab
    folder = Path(folder or schwab.FOLDER)
    target = _norm(account_number)
    files = sorted(folder.glob("AccountsList_*.csv")) if folder.exists() else []
    matches = []
    for path in files:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw_number = schwab.clean(row.get("Account Number"))
                if raw_number and _norm(raw_number) == target:
                    matches.append({
                        "file": path.name, "raw_account_number": raw_number,
                        "raw_total_value": row.get("Total Value"),
                        "raw_cash_available": row.get("Cash Available"),
                        "parsed_total_value": schwab.parse_money(row.get("Total Value")),
                        "parsed_cash_value": schwab.parse_money(row.get("Cash Available")),
                        "status": row.get("Status"),
                        "has_valuation_columns": all(col in row for col in VALUATION_COLUMNS),
                        "header": list(row.keys()),
                    })
    return {"folder": str(folder), "folder_exists": folder.exists(),
            "files_scanned": [p.name for p in files], "matches": matches}


def trace(account_number, *, folder=None) -> dict:
    db = _db_rows(account_number)
    raw = _raw_accountslist(account_number, folder=folder)

    rows = db["rows"]
    nonzero_db = [r for r in rows if (r["total_value"] or 0) or (r["cash_value"] or 0)]
    nonzero_raw = [m for m in raw["matches"] if (m["parsed_total_value"] or 0) or (m["parsed_cash_value"] or 0)]

    if len(rows) > 1:
        verdict = (f"{len(rows)} account rows match this number (differently formatted account_number "
                   f"strings are distinct rows under the unique key). "
                   + ("A DIFFERENT row holds the non-zero valuation — the linked row is not the valued row."
                      if nonzero_db else "None of them carry a non-zero valuation."))
    elif not rows:
        verdict = "no accounts row matches this number at all."
    elif nonzero_db:
        verdict = "the single account row already carries a non-zero valuation (zero is elsewhere)."
    elif not raw["folder_exists"]:
        verdict = ("the account row's total_value/cash_value are zero; the raw AccountsList folder is not "
                   "present on THIS host — run on the server to read the source cells.")
    elif not raw["matches"]:
        verdict = ("the account row is zero AND the raw AccountsList CSV contains NO row for this account "
                   "number — the valuation was never imported for this account.")
    elif nonzero_raw:
        verdict = ("the raw AccountsList CSV HAS a non-zero valuation for this account, but the stored row "
                   "is zero — the value did not reach the accounts row (number-format mismatch on the "
                   "upsert key, or a later import overwrote it with a zero row).")
    else:
        parsed = [str(m["parsed_total_value"]) for m in raw["matches"]]
        verdict = (f"the raw AccountsList 'Total Value' cell(s) parse to {parsed} — the source itself is "
                   f"zero/blank for this account, so total_value was correctly stored as that value.")

    return {"account_number": account_number, **db, "raw_accountslist": raw,
            "db_row_count": len(rows), "verdict": verdict}


def _print(d: dict) -> None:
    print("=" * 80)
    print(f"ACCOUNT IMPORT TRACE — account_number={d['account_number']}")
    print("=" * 80)
    print("[1] SQL (accounts, normalized match):")
    print(f"    {d['sql']}")
    print()
    print(f"[1/2] accounts rows matching (exact + normalized): {d['db_row_count']}")
    for r in d["rows"]:
        print(f"    id={r['id']}  number={r['account_number']!r}  person_id={r['person_id']}  "
              f"total_value={r['total_value']}  cash_value={r['cash_value']}  status={r['status']}")
        print(f"        source_file={r['source_file']!r}  last_imported_at={r['last_imported_at']}  "
              f"created_at={r['created_at']}")
    print()
    print("[3] import_jobs that wrote those source files (most recent first):")
    for j in d["import_jobs"]:
        print(f"    job {j['id']}  system={j['source_system']!r}  file={j['source_file']!r}  "
              f"status={j['status']}  rows_read={j['rows_read']}  completed={j['completed_at']}")
    if not d["import_jobs"]:
        print("    (none)")
    print()
    print("[4] Schwab-Profile source_contact (no valuation — identity only):")
    for sc in d["source_contacts"]:
        raw = sc.get("raw_data")
        hh_balance = None
        if isinstance(raw, dict):
            hh_balance = raw.get("HH Group Balance")
        print(f"    id={sc['id']}  record_id={sc['source_record_id']!r}  name={sc['full_name']!r}  "
              f"file={sc['source_file']!r}  HH Group Balance={hh_balance!r}")
    if not d["source_contacts"]:
        print("    (none)")
    print()
    raw = d["raw_accountslist"]
    print(f"[4/5] RAW AccountsList cells (valuation source) — folder={raw['folder']} "
          f"exists={raw['folder_exists']} files={raw['files_scanned']}:")
    for m in raw["matches"]:
        print(f"    {m['file']}  number={m['raw_account_number']!r}  "
              f"'Total Value'={m['raw_total_value']!r} → parsed {m['parsed_total_value']}  "
              f"'Cash Available'={m['raw_cash_available']!r} → parsed {m['parsed_cash_value']}")
        if not m["has_valuation_columns"]:
            print(f"        !! this CSV has no {VALUATION_COLUMNS} columns; header={m['header']}")
    if raw["folder_exists"] and not raw["matches"]:
        print("    (no AccountsList row matches this account number)")
    print()
    print(">>> VERDICT:")
    print(f"    {d['verdict']}")
    print("=" * 80)


def main(argv=None) -> int:
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: python -m app.services.account_import_trace <account_number>")
        return 2
    _print(trace(args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
