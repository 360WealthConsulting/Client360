from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine  # noqa: E402
from app.importers.drake_client_csv import clean_value, read_client_rows  # noqa: E402
from app.importers.drake_returns import upsert_return_rows  # noqa: E402

ROOT = Path(r"C:\Client360\data\Drake")
HASH_KEY = os.getenv("MICROSOFT_TOKEN_KEY", "")

if not HASH_KEY:
    raise RuntimeError("MICROSOFT_TOKEN_KEY is required.")


clean = clean_value


def identifier_hash(value):
    """The salted SSN/EIN hash. The secret stays here, in the script that loads the environment."""
    digits = "".join(ch for ch in clean(value) if ch.isdigit())
    if not digits:
        return None
    return hashlib.sha256(
        f"{HASH_KEY}:{digits}".encode()
    ).hexdigest()


def read_header(path):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        return [clean(item) for item in next(reader, [])]


def find_client_file(year_folder):
    candidates = []

    for path in year_folder.glob("*.csv"):
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


# The positional upsert that used to live here — ON CONFLICT (tax_year, source_row_number) — has been
# retired. ``source_row_number`` is the row's POSITION in the export, so a re-export that inserted,
# deleted or re-sorted a single row made row N a different taxpayer and overwrote one client's return
# with another's. The upsert now keys on a content-derived identity and lives in
# ``app.importers.drake_returns``; see ``app.services.drake_return_identity`` for how identity is
# derived and which rows deliberately get none.
# Parsing itself now lives in ``app.importers.drake_client_csv``, which normalizes the RAW ROW before
# anything is mapped to a column name — the 2021 and 2022 exports are one field short on every 1120S
# return, and ``csv.DictReader`` mapped those rows' values to the header one position early. It also
# makes the parse importable and therefore testable, which this script never was: it connects to the
# database and runs the import at module scope.
def import_year(connection, tax_year, client_file):
    """Import one year by STABLE IDENTITY. Returns ``(summary, anomalies)``."""
    rows, anomalies = read_client_rows(tax_year, client_file, identifier_hash=identifier_hash)
    return upsert_return_rows(connection, rows), anomalies


folders = sorted(
    (
        folder
        for folder in ROOT.iterdir()
        if folder.is_dir() and folder.name.isdigit()
    ),
    key=lambda folder: int(folder.name),
)

if not folders:
    raise RuntimeError(f"No Drake year folders found under {ROOT}")

results = []

with engine.begin() as connection:
    for folder in folders:
        tax_year = int(folder.name)
        client_file = find_client_file(folder)

        if client_file is None:
            print(f"{tax_year}: no valid client export found — skipped")
            continue

        summary, anomalies = import_year(connection, tax_year, client_file)
        results.append((tax_year, summary, client_file.name, anomalies))

        print(
            f"{tax_year}: read {summary['rows_read']} rows from {client_file.name} — "
            f"{summary['inserted']} inserted, {summary['updated']} updated, "
            f"{summary['quarantined']} quarantined"
        )

print("\nAll-year Drake import completed.")

total_quarantined = 0
total_anomalies = 0

for tax_year, summary, filename, anomalies in results:
    total_quarantined += summary["quarantined"]
    total_anomalies += len(anomalies)
    print(f"  {tax_year}: {summary['identified']} returns ({filename})")

# A row whose field count does not match the header, and whose shape is not the proven 2021/2022
# 1120S single-omission shape, is imported exactly as it always was — and reported here rather than
# realigned on a guess. Silently shifting an unknown shape is the defect this importer just fixed.
if total_anomalies:
    print(f"\n{total_anomalies} short row(s) NOT NORMALIZED — shape not recognised, needing review:")
    for tax_year, _summary, _filename, anomalies in results:
        for anomaly in anomalies:
            print(f"  {tax_year} row {anomaly['source_row_number']}: {anomaly['detail']}")

# Quarantined rows are reported, never guessed at. A row lands here when it carries no usable taxpayer
# identifier, or when several rows in one export claim one identity — see
# ``app.services.drake_return_identity``. Nothing was written for them, so no existing return was
# overwritten; they need a human to look at the export.
if total_quarantined:
    print(f"\n{total_quarantined} row(s) QUARANTINED — imported for no one, and needing review:")
    for tax_year, summary, _filename, _anomalies in results:
        for row in summary["quarantined_rows"]:
            print(f"  {tax_year} row {row['source_row_number']} "
                  f"(type={row['return_type'] or '<blank>'}): {row['identity_status']}")
