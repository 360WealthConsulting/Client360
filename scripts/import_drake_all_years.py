from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine  # noqa: E402

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


upsert = text("""
INSERT INTO drake_client_returns (
    tax_year,
    source_row_number,
    taxpayer_identifier_hash,
    spouse_identifier_hash,
    taxpayer_first_name,
    taxpayer_last_name,
    taxpayer_normalized_name,
    taxpayer_dob,
    spouse_first_name,
    spouse_last_name,
    spouse_normalized_name,
    spouse_dob,
    filing_status,
    return_type,
    preparer_code,
    agi,
    preparer_fee,
    prepare_date,
    review_date,
    approved_date,
    complete_date,
    federal_product,
    federal_ack_date,
    federal_ack_code,
    state_product,
    state_ack_date,
    state_ack_code,
    source_updated_at,
    raw_data
)
VALUES (
    :tax_year,
    :source_row_number,
    :taxpayer_identifier_hash,
    :spouse_identifier_hash,
    :taxpayer_first_name,
    :taxpayer_last_name,
    :taxpayer_normalized_name,
    :taxpayer_dob,
    :spouse_first_name,
    :spouse_last_name,
    :spouse_normalized_name,
    :spouse_dob,
    :filing_status,
    :return_type,
    :preparer_code,
    :agi,
    :preparer_fee,
    :prepare_date,
    :review_date,
    :approved_date,
    :complete_date,
    :federal_product,
    :federal_ack_date,
    :federal_ack_code,
    :state_product,
    :state_ack_date,
    :state_ack_code,
    :source_updated_at,
    CAST(:raw_data AS JSONB)
)
ON CONFLICT (tax_year, source_row_number)
DO UPDATE SET
    taxpayer_identifier_hash = EXCLUDED.taxpayer_identifier_hash,
    spouse_identifier_hash = EXCLUDED.spouse_identifier_hash,
    taxpayer_first_name = EXCLUDED.taxpayer_first_name,
    taxpayer_last_name = EXCLUDED.taxpayer_last_name,
    taxpayer_normalized_name = EXCLUDED.taxpayer_normalized_name,
    taxpayer_dob = EXCLUDED.taxpayer_dob,
    spouse_first_name = EXCLUDED.spouse_first_name,
    spouse_last_name = EXCLUDED.spouse_last_name,
    spouse_normalized_name = EXCLUDED.spouse_normalized_name,
    spouse_dob = EXCLUDED.spouse_dob,
    filing_status = EXCLUDED.filing_status,
    return_type = EXCLUDED.return_type,
    preparer_code = EXCLUDED.preparer_code,
    agi = EXCLUDED.agi,
    preparer_fee = EXCLUDED.preparer_fee,
    prepare_date = EXCLUDED.prepare_date,
    review_date = EXCLUDED.review_date,
    approved_date = EXCLUDED.approved_date,
    complete_date = EXCLUDED.complete_date,
    federal_product = EXCLUDED.federal_product,
    federal_ack_date = EXCLUDED.federal_ack_date,
    federal_ack_code = EXCLUDED.federal_ack_code,
    state_product = EXCLUDED.state_product,
    state_ack_date = EXCLUDED.state_ack_date,
    state_ack_code = EXCLUDED.state_ack_code,
    source_updated_at = EXCLUDED.source_updated_at,
    raw_data = EXCLUDED.raw_data
""")


def import_year(connection, tax_year, client_file):
    source_time = datetime.fromtimestamp(
        client_file.stat().st_mtime,
        tz=UTC,
    )

    count = 0

    with client_file.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)

        for row_number, original_row in enumerate(reader, start=1):
            row = {
                clean(key): clean(value)
                for key, value in original_row.items()
                if key is not None
            }

            connection.execute(
                upsert,
                {
                    "tax_year": tax_year,
                    "source_row_number": row_number,
                    "taxpayer_identifier_hash": identifier_hash(
                        row.get("TP_Social")
                    ),
                    "spouse_identifier_hash": identifier_hash(
                        row.get("SP_Social")
                    ),
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
                    "federal_ack_date": parse_date(
                        row.get("e-File ACK Date #1")
                    ),
                    "federal_ack_code": row.get("e-File ACK Code #1") or None,
                    "state_product": row.get("e-File Product #2") or None,
                    "state_ack_date": parse_date(
                        row.get("e-File ACK Date #2")
                    ),
                    "state_ack_code": row.get("e-File ACK Code #2") or None,
                    "source_updated_at": source_time,
                    "raw_data": json.dumps(row, ensure_ascii=False),
                },
            )

            count += 1

    return count


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

        count = import_year(connection, tax_year, client_file)
        results.append((tax_year, count, client_file.name))

        print(
            f"{tax_year}: imported {count} rows "
            f"from {client_file.name}"
        )

print("\nAll-year Drake import completed.")

for tax_year, count, filename in results:
    print(f"  {tax_year}: {count} returns ({filename})")
