from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine  # noqa: E402
from app.importers.drake_returns import upsert_return_rows  # noqa: E402

ROOT = Path(r"C:\Client360\data\Drake")
HASH_KEY = os.getenv("MICROSOFT_TOKEN_KEY", "")

if not HASH_KEY:
    raise RuntimeError("MICROSOFT_TOKEN_KEY is required.")


def clean(value):
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def identifier_hash(value):
    digits = "".join(ch for ch in clean(value) if ch.isdigit())
    if not digits:
        return None
    return hashlib.sha256(
        f"{HASH_KEY}:{digits}".encode()
    ).hexdigest()


def normalized_name(first, last):
    return " ".join(
        part.lower()
        for part in (clean(first), clean(last))
        if part
    )


def parse_date(value):
    value = clean(value)
    if not value:
        return None

    for fmt in ("%m%d%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def decimal_value(value):
    value = clean(value).replace(",", "").replace("$", "")
    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


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
def parse_client_row(row, *, tax_year, source_row_number, source_updated_at):
    """One Drake ``CLIENT.CSV`` record -> the column values for ``drake_client_returns``.

    Pure, so the identity rules can be exercised against real Drake column names without a database.
    ``source_row_number`` is carried purely as provenance now; it no longer keys anything.
    """
    return {
        "tax_year": tax_year,
        "source_row_number": source_row_number,
        "taxpayer_identifier_hash": identifier_hash(row.get("TP_Social")),
        "spouse_identifier_hash": identifier_hash(row.get("SP_Social")),
        "taxpayer_first_name": row.get("TP_FirstName") or None,
        "taxpayer_last_name": row.get("TP_LastName") or None,
        "taxpayer_normalized_name": normalized_name(
            row.get("TP_FirstName"),
            row.get("TP_LastName"),
        ),
        "taxpayer_dob": parse_date(row.get("TP_DoB")),
        "spouse_first_name": row.get("SP_FirstName") or None,
        "spouse_last_name": row.get("SP_LastName") or None,
        "spouse_normalized_name": normalized_name(
            row.get("SP_FirstName"),
            row.get("SP_LastName"),
        ),
        "spouse_dob": parse_date(row.get("SP_DoB")),
        "filing_status": row.get("FS") or None,
        "return_type": row.get("Type") or None,
        "preparer_code": row.get("Prep") or None,
        "agi": decimal_value(row.get("AGI")),
        "preparer_fee": decimal_value(row.get("Prep_Fee")),
        "prepare_date": parse_date(row.get("Prepare - Date")),
        "review_date": parse_date(row.get("Review - Date")),
        "approved_date": parse_date(row.get("Approved - Date")),
        "complete_date": parse_date(row.get("Complete - Date")),
        "federal_product": row.get("e-File Product #1") or None,
        "federal_ack_date": parse_date(row.get("e-File ACK Date #1")),
        "federal_ack_code": row.get("e-File ACK Code #1") or None,
        "state_product": row.get("e-File Product #2") or None,
        "state_ack_date": parse_date(row.get("e-File ACK Date #2")),
        "state_ack_code": row.get("e-File ACK Code #2") or None,
        "source_updated_at": source_updated_at,
        "raw_data": json.dumps(row, ensure_ascii=False),
    }


def read_client_rows(tax_year, client_file):
    """Parse one year's export. The WHOLE file is materialized on purpose.

    Identity collisions are only visible with the complete export in hand, so the batch — not the
    row — is the unit of import.
    """
    source_time = datetime.fromtimestamp(client_file.stat().st_mtime, tz=UTC)

    with client_file.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return [
            parse_client_row(
                {clean(key): clean(value)
                 for key, value in original_row.items() if key is not None},
                tax_year=tax_year,
                source_row_number=row_number,
                source_updated_at=source_time,
            )
            for row_number, original_row in enumerate(csv.DictReader(handle), start=1)
        ]


def import_year(connection, tax_year, client_file):
    """Import one year by STABLE IDENTITY. Returns the upsert summary."""
    return upsert_return_rows(connection, read_client_rows(tax_year, client_file))


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

        summary = import_year(connection, tax_year, client_file)
        results.append((tax_year, summary, client_file.name))

        print(
            f"{tax_year}: read {summary['rows_read']} rows from {client_file.name} — "
            f"{summary['inserted']} inserted, {summary['updated']} updated, "
            f"{summary['quarantined']} quarantined"
        )

print("\nAll-year Drake import completed.")

total_quarantined = 0

for tax_year, summary, filename in results:
    total_quarantined += summary["quarantined"]
    print(f"  {tax_year}: {summary['identified']} returns ({filename})")

# Quarantined rows are reported, never guessed at. A row lands here when it carries no usable taxpayer
# identifier, or when several rows in one export claim one identity — see
# ``app.services.drake_return_identity``. Nothing was written for them, so no existing return was
# overwritten; they need a human to look at the export.
if total_quarantined:
    print(f"\n{total_quarantined} row(s) QUARANTINED — imported for no one, and needing review:")
    for tax_year, summary, _filename in results:
        for row in summary["quarantined_rows"]:
            print(f"  {tax_year} row {row['source_row_number']} "
                  f"(type={row['return_type'] or '<blank>'}): {row['identity_status']}")
