"""Import Drake client exports — every discovered year, or only the years you name.

    python -m scripts.import_drake_all_years                    # every year found under ROOT
    python -m scripts.import_drake_all_years --year 2021
    python -m scripts.import_drake_all_years --year 2021 --year 2022

NO-ARGUMENT BEHAVIOUR IS UNCHANGED. With no ``--year`` this discovers every numeric directory under
``ROOT`` and imports them in ascending order, exactly as before, in ONE transaction.

WHY YEAR SELECTION EXISTS
-------------------------
The 2021 and 2022 exports needed re-importing on their own after the 1120S short-row fix, and this
driver could only ever run all five years at once. Re-importing 2023-2025 as a side effect of fixing
2021/2022 is a wider blast radius than the operation calls for, and an operator had no supported way
to avoid it.

FAIL CLOSED
-----------
An explicitly requested year is resolved BEFORE anything is imported: the year must be plausible, its
directory must exist, and a client export must be resolvable inside it by the same rule discovery
uses. If any requested year fails, NOTHING is imported — the driver never falls back to all years and
never substitutes a different year. Selection can only ever narrow, never widen.

TRANSACTION
-----------
One transaction per invocation, in both modes. Every selected year commits together or not at all,
which is the behaviour the all-years run has always had. A failure part-way through leaves the
database exactly as it was.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(r"C:\Client360\app\.env")

# ``app.db`` reflects the whole schema at import, so importing it opens a database connection.
# It is therefore imported inside :func:`run`, where a connection is genuinely wanted — which is what
# lets ``--help``, argument parsing and year resolution work (and be tested) without a database.
from app.importers.drake_client_csv import clean_value, read_client_rows  # noqa: E402
from app.importers.drake_returns import upsert_return_rows  # noqa: E402
from app.services.drake_identifier import hash_key as _hash_key  # noqa: E402
from app.services.drake_identifier import identifier_hash  # noqa: E402

ROOT = Path(r"C:\Client360\data\Drake")

#: A year is only plausible inside this range. It rejects a typo like ``--year 5`` or ``--year 20211``
#: with a clear message instead of a confusing "directory not found".
MIN_YEAR, MAX_YEAR = 1900, 2999

clean = clean_value

# ``_hash_key`` and ``identifier_hash`` used to be defined here. They now live in
# ``app.services.drake_identifier`` and are imported above, unchanged: human-approved entity
# adjudication needs the identical hash for an EIN this export never carried, and two copies of a
# hashing rule that drift by one character produce two identities for one taxpayer. The names stay
# bound here so this module's public surface — and ``main``'s fail-closed secret check — is the same.


def read_header(path):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        return [clean(item) for item in next(reader, [])]


def find_client_file(year_folder):
    """The largest CSV in ``year_folder`` whose header carries the taxpayer columns.

    The extension match is CASE-INSENSITIVE, and deliberately so. This used to be
    ``year_folder.glob("*.csv")``, which is case-insensitive on Windows and case-SENSITIVE
    everywhere else — while every real Drake export is uppercase (``CLIENT.CSV``, ``2023.CSV``). On
    the production host it happened to work; on a case-sensitive filesystem it matched nothing and
    every year was silently reported as "no valid client export found — skipped". The new CLI tests
    run on Linux in CI, which is what surfaced it.
    """
    candidates = []

    for path in sorted(year_folder.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".csv":
            continue

        header = read_header(path)

        if {
            "TP_Social",
            "TP_FirstName",
            "TP_LastName",
        }.issubset(set(header)):
            candidates.append(path)

    if not candidates:
        return None

    return max(candidates, key=lambda item: item.stat().st_size)


def discover_years(root: Path = ROOT) -> list[Path]:
    """Every numeric year directory under ``root``, ascending. The historic no-argument behaviour."""
    return sorted(
        (folder for folder in root.iterdir() if folder.is_dir() and folder.name.isdigit()),
        key=lambda folder: int(folder.name),
    )


def resolve_years(years, root: Path = ROOT) -> list[Path]:
    """The directories for exactly the requested years, or raise.

    Deduplicated and sorted, so ``--year 2022 --year 2021 --year 2021`` is the same request as
    ``--year 2021 --year 2022``. Raises ``ValueError`` naming the first year that cannot be resolved;
    the caller must import nothing when that happens.
    """
    folders = []

    for year in sorted(set(years)):
        if not MIN_YEAR <= year <= MAX_YEAR:
            raise ValueError(f"{year} is not a plausible tax year ({MIN_YEAR}-{MAX_YEAR}).")

        folder = root / str(year)
        if not folder.is_dir():
            raise ValueError(f"no Drake directory for {year} under {root}.")

        if find_client_file(folder) is None:
            raise ValueError(f"no client export could be resolved for {year} in {folder}.")

        folders.append(folder)

    return folders


# The positional upsert that used to live here — ON CONFLICT (tax_year, source_row_number) — has been
# retired. ``source_row_number`` is the row's POSITION in the export, so a re-export that inserted,
# deleted or re-sorted a single row made row N a different taxpayer and overwrote one client's return
# with another's. The upsert now keys on a content-derived identity and lives in
# ``app.importers.drake_returns``; see ``app.services.drake_return_identity`` for how identity is
# derived and which rows deliberately get none.
# Parsing itself now lives in ``app.importers.drake_client_csv``, which normalizes the RAW ROW before
# anything is mapped to a column name — the 2021 and 2022 exports are one field short on every 1120S
# return, and ``csv.DictReader`` mapped those rows' values to the header one position early.
def import_year(connection, tax_year, client_file):
    """Import one year by STABLE IDENTITY. Returns ``(summary, anomalies, shapes)``."""
    shapes: dict[str, int] = {}
    rows, anomalies = read_client_rows(tax_year, client_file, identifier_hash=identifier_hash,
                                       counters=shapes)
    return upsert_return_rows(connection, rows), anomalies, shapes


def run(folders, *, connection=None) -> list[dict]:
    """Import every folder in ``folders`` inside ONE transaction. Returns a result per year."""
    if connection is not None:
        return _run(folders, connection)

    from app.db import engine

    # Name the target before writing anything. ``load_dotenv`` above supplies DATABASE_URL from the
    # production env file whenever the shell does not already set one, so "I unset the variable" is
    # NOT enough to make an invocation non-production — this line is what makes the target visible.
    print(f"Target database: {engine.url.database}")

    with engine.begin() as owned:
        return _run(folders, owned)


def _run(folders, connection) -> list[dict]:
    results = []

    for folder in folders:
        tax_year = int(folder.name)
        client_file = find_client_file(folder)

        if client_file is None:
            # Only reachable in the all-years mode; an explicitly requested year was resolved first.
            print(f"{tax_year}: no valid client export found — skipped")
            continue

        summary, anomalies, shapes = import_year(connection, tax_year, client_file)
        results.append({"tax_year": tax_year, "summary": summary, "anomalies": anomalies,
                        "shapes": shapes, "filename": client_file.name,
                        "source": str(client_file)})

        print(
            f"{tax_year}: read {summary['rows_read']} rows from {client_file.name} — "
            f"{shapes.get('normalized', 0)} short row(s) normalized, "
            f"{shapes.get('unrecognised_short', 0)} not normalized, "
            f"{summary['inserted']} inserted, {summary['updated']} updated, "
            f"{summary['quarantined']} quarantined"
        )

    return results


def report(results, *, selected=None) -> None:
    scope = "All-year" if selected is None else "Year-scoped (" + ", ".join(
        str(year) for year in selected) + ")"
    print(f"\n{scope} Drake import completed.")

    total_quarantined = 0
    total_anomalies = 0

    for result in results:
        total_quarantined += result["summary"]["quarantined"]
        total_anomalies += len(result["anomalies"])
        print(f"  {result['tax_year']}: {result['summary']['identified']} returns "
              f"({result['source']})")

    # A row whose field count does not match the header, and whose shape is not the proven 2021/2022
    # 1120S single-omission shape, is imported exactly as it always was — and reported here rather
    # than realigned on a guess. Silently shifting an unknown shape is the defect this importer fixed.
    if total_anomalies:
        print(f"\n{total_anomalies} short row(s) NOT NORMALIZED — shape not recognised, "
              "needing review:")
        for result in results:
            for anomaly in result["anomalies"]:
                print(f"  {result['tax_year']} row {anomaly['source_row_number']}: "
                      f"{anomaly['detail']}")

    # Quarantined rows are reported, never guessed at. A row lands here when it carries no usable
    # taxpayer identifier, or when several rows in one export claim one identity — see
    # ``app.services.drake_return_identity``. Nothing was written for them, so no existing return was
    # overwritten; they need a human to look at the export.
    if total_quarantined:
        print(f"\n{total_quarantined} row(s) QUARANTINED — imported for no one, and needing review:")
        for result in results:
            for row in result["summary"]["quarantined_rows"]:
                print(f"  {result['tax_year']} row {row['source_row_number']} "
                      f"(type={row['return_type'] or '<blank>'}): {row['identity_status']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.import_drake_all_years",
        description="Import Drake client exports — every discovered year, or only the years named.")
    parser.add_argument(
        "--year", type=int, action="append", metavar="YYYY", dest="years",
        help="import only this tax year; repeatable. Omit to import every discovered year.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        _hash_key()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.years:
        try:
            folders = resolve_years(args.years, ROOT)
        except ValueError as exc:
            # Nothing is imported. Never fall back to all years, never substitute another year.
            print(f"error: {exc} Nothing was imported.", file=sys.stderr)
            return 2
        selected = [int(folder.name) for folder in folders]
        print(f"Importing only: {', '.join(str(year) for year in selected)}")
    else:
        folders = discover_years(ROOT)
        if not folders:
            print(f"error: no Drake year folders found under {ROOT}", file=sys.stderr)
            return 2
        selected = None
        print(f"Importing every discovered year: "
              f"{', '.join(folder.name for folder in folders)}")

    results = run(folders)
    report(results, selected=selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
