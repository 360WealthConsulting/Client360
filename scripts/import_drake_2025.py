from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine  # noqa: E402
from app.importers.drake_client_csv import iter_client_rows  # noqa: E402
from app.services import drake_identifier  # noqa: E402
from app.services.drake_identifier import identifier_hash  # noqa: E402

CLIENT_FILE = Path(r"C:\Client360\data\Drake\2025\2025.csv")
EFILE_FILE = Path(r"C:\Client360\data\Drake\2025\2025EF.CSV")
TAX_YEAR = 2025

# The salted identifier hash used to be defined here as well. It now comes from
# ``app.services.drake_identifier``, so this importer, the all-years driver and human-approved entity
# adjudication all derive one value from one expression: two copies that drift by a single character
# would produce two identities for one taxpayer, silently and permanently. The expression is
# unchanged, and the values are identical for every input.
#
# The IMPORT-TIME check below is deliberately kept. This module has always refused to load without
# the secret — before it opens a file or a transaction — while the canonical hash reads the secret at
# CALL time. Without this, the failure would move to the first row hashed, after work had begun. The
# exception type and message are exactly what they were.
try:
    drake_identifier.hash_key()
except drake_identifier.IdentifierHashKeyMissing as exc:
    raise RuntimeError(
        "MICROSOFT_TOKEN_KEY is required for deterministic identifier hashing") from exc


def clean(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("\x00", "").strip()


def normalized_name(first: str | None, last: str | None) -> str:
    return " ".join(
        part.lower()
        for part in (clean(first), clean(last))
        if part
    )


def parse_date(value: str | None) -> str | None:
    value = clean(value)
    if not value:
        return None

    for fmt in ("%m%d%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass

    return None


def decimal_text(value: str | None) -> str | None:
    value = clean(value).replace(",", "").replace("$", "")
    return value or None


schema_sql = """
CREATE TABLE IF NOT EXISTS drake_client_returns (
    id BIGSERIAL PRIMARY KEY,
    tax_year INTEGER NOT NULL,
    source_row_number INTEGER NOT NULL,

    taxpayer_identifier_hash VARCHAR(64),
    spouse_identifier_hash VARCHAR(64),

    taxpayer_first_name TEXT,
    taxpayer_last_name TEXT,
    taxpayer_normalized_name TEXT,
    taxpayer_dob DATE,

    spouse_first_name TEXT,
    spouse_last_name TEXT,
    spouse_normalized_name TEXT,
    spouse_dob DATE,

    filing_status TEXT,
    return_type TEXT,
    preparer_code TEXT,

    agi NUMERIC,
    preparer_fee NUMERIC,

    prepare_date DATE,
    review_date DATE,
    approved_date DATE,
    complete_date DATE,

    federal_product TEXT,
    federal_ack_date DATE,
    federal_ack_code TEXT,

    state_product TEXT,
    state_ack_date DATE,
    state_ack_code TEXT,

    source_updated_at TIMESTAMPTZ NOT NULL,
    raw_data JSONB NOT NULL,

    UNIQUE (tax_year, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_drake_client_returns_taxpayer_hash
    ON drake_client_returns (taxpayer_identifier_hash);

CREATE INDEX IF NOT EXISTS ix_drake_client_returns_taxpayer_name
    ON drake_client_returns (taxpayer_normalized_name);

CREATE TABLE IF NOT EXISTS drake_efile_records (
    id BIGSERIAL PRIMARY KEY,
    tax_year INTEGER NOT NULL,
    source_row_number INTEGER NOT NULL,

    taxpayer_identifier_hash VARCHAR(64),
    spouse_identifier_hash VARCHAR(64),

    taxpayer_name TEXT,
    return_type TEXT,
    preparer_code TEXT,

    agi NUMERIC,
    refund_amount NUMERIC,
    balance_due NUMERIC,

    transmission_date DATE,
    acknowledgement_date DATE,
    acknowledgement_code TEXT,
    submission_id TEXT,

    source_updated_at TIMESTAMPTZ NOT NULL,
    raw_data JSONB NOT NULL,

    UNIQUE (tax_year, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_drake_efile_records_taxpayer_hash
    ON drake_efile_records (taxpayer_identifier_hash);
"""

client_upsert = text("""
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

efile_upsert = text("""
INSERT INTO drake_efile_records (
    tax_year,
    source_row_number,
    taxpayer_identifier_hash,
    spouse_identifier_hash,
    taxpayer_name,
    return_type,
    preparer_code,
    agi,
    refund_amount,
    balance_due,
    transmission_date,
    acknowledgement_date,
    acknowledgement_code,
    submission_id,
    source_updated_at,
    raw_data
)
VALUES (
    :tax_year,
    :source_row_number,
    :taxpayer_identifier_hash,
    :spouse_identifier_hash,
    :taxpayer_name,
    :return_type,
    :preparer_code,
    :agi,
    :refund_amount,
    :balance_due,
    :transmission_date,
    :acknowledgement_date,
    :acknowledgement_code,
    :submission_id,
    :source_updated_at,
    CAST(:raw_data AS JSONB)
)
ON CONFLICT (tax_year, source_row_number)
DO UPDATE SET
    taxpayer_identifier_hash = EXCLUDED.taxpayer_identifier_hash,
    spouse_identifier_hash = EXCLUDED.spouse_identifier_hash,
    taxpayer_name = EXCLUDED.taxpayer_name,
    return_type = EXCLUDED.return_type,
    preparer_code = EXCLUDED.preparer_code,
    agi = EXCLUDED.agi,
    refund_amount = EXCLUDED.refund_amount,
    balance_due = EXCLUDED.balance_due,
    transmission_date = EXCLUDED.transmission_date,
    acknowledgement_date = EXCLUDED.acknowledgement_date,
    acknowledgement_code = EXCLUDED.acknowledgement_code,
    submission_id = EXCLUDED.submission_id,
    source_updated_at = EXCLUDED.source_updated_at,
    raw_data = EXCLUDED.raw_data
""")


def import_clients(connection) -> int:
    """Import the 2025 client export.

    Rows pass through ``app.importers.drake_client_csv`` first. The 2025 export carries no malformed
    rows, but the same reader is used here so that a future export with the 2021/2022 short-row 1120S
    shape cannot silently import its values one column early — and so that an unrecognised short row
    is reported rather than realigned on a guess.
    """
    count = 0
    unrecognised = 0
    source_time = datetime.fromtimestamp(
        CLIENT_FILE.stat().st_mtime,
        tz=UTC,
    )

    with CLIENT_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for client_row in iter_client_rows(handle):
            row_number = client_row.row_number
            row = {key: value for key, value in client_row.mapping.items() if key is not None}

            if client_row.shape.needs_review:
                unrecognised += 1
                print(f"  short row NOT NORMALIZED at row {row_number}: {client_row.shape.detail}")

            connection.execute(
                client_upsert,
                {
                    "tax_year": TAX_YEAR,
                    "source_row_number": row_number,
                    "taxpayer_identifier_hash": identifier_hash(row.get("TP_Social")),
                    "spouse_identifier_hash": identifier_hash(row.get("SP_Social")),
                    "taxpayer_first_name": clean(row.get("TP_FirstName")) or None,
                    "taxpayer_last_name": clean(row.get("TP_LastName")) or None,
                    "taxpayer_normalized_name": normalized_name(
                        row.get("TP_FirstName"),
                        row.get("TP_LastName"),
                    ),
                    "taxpayer_dob": parse_date(row.get("TP_DoB")),
                    "spouse_first_name": clean(row.get("SP_FirstName")) or None,
                    "spouse_last_name": clean(row.get("SP_LastName")) or None,
                    "spouse_normalized_name": normalized_name(
                        row.get("SP_FirstName"),
                        row.get("SP_LastName"),
                    ),
                    "spouse_dob": parse_date(row.get("SP_DoB")),
                    "filing_status": clean(row.get("FS")) or None,
                    "return_type": clean(row.get("Type")) or None,
                    "preparer_code": clean(row.get("Prep")) or None,
                    "agi": decimal_text(row.get("AGI")),
                    "preparer_fee": decimal_text(row.get("Prep_Fee")),
                    "prepare_date": parse_date(row.get("Prepare - Date")),
                    "review_date": parse_date(row.get("Review - Date")),
                    "approved_date": parse_date(row.get("Approved - Date")),
                    "complete_date": parse_date(row.get("Complete - Date")),
                    "federal_product": clean(row.get("e-File Product #1")) or None,
                    "federal_ack_date": parse_date(row.get("e-File ACK Date #1")),
                    "federal_ack_code": clean(row.get("e-File ACK Code #1")) or None,
                    "state_product": clean(row.get("e-File Product #2")) or None,
                    "state_ack_date": parse_date(row.get("e-File ACK Date #2")),
                    "state_ack_code": clean(row.get("e-File ACK Code #2")) or None,
                    "source_updated_at": source_time,
                    "raw_data": json.dumps({k: clean(v) for k, v in row.items()}, ensure_ascii=False),
                },
            )
            count += 1

    if unrecognised:
        print(f"  {unrecognised} short row(s) NOT NORMALIZED — shape not recognised, needing review")

    return count


def import_efile(connection) -> int:
    count = 0
    source_time = datetime.fromtimestamp(
        EFILE_FILE.stat().st_mtime,
        tz=UTC,
    )

    with EFILE_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)

        for row_number, row in enumerate(reader, start=1):
            connection.execute(
                efile_upsert,
                {
                    "tax_year": TAX_YEAR,
                    "source_row_number": row_number,
                    "taxpayer_identifier_hash": identifier_hash(row.get("SSN")),
                    "spouse_identifier_hash": identifier_hash(row.get("SSSN")),
                    "taxpayer_name": clean(row.get("TPName") or row.get("TName")) or None,
                    "return_type": clean(row.get("rettype")) or None,
                    "preparer_code": clean(row.get("Preparer")) or None,
                    "agi": decimal_text(row.get("AGI")),
                    "refund_amount": decimal_text(row.get("RefAmt")),
                    "balance_due": decimal_text(row.get("BalDue")),
                    "transmission_date": parse_date(row.get("TRNDate")),
                    "acknowledgement_date": parse_date(row.get("AckDate")),
                    "acknowledgement_code": clean(row.get("AckCode")) or None,
                    "submission_id": clean(row.get("MeF Submission ID")) or None,
                    "source_updated_at": source_time,
                    "raw_data": json.dumps({k: clean(v) for k, v in row.items()}, ensure_ascii=False),
                },
            )
            count += 1

    return count


if not CLIENT_FILE.exists():
    raise FileNotFoundError(CLIENT_FILE)

if not EFILE_FILE.exists():
    raise FileNotFoundError(EFILE_FILE)

with engine.begin() as connection:
    for statement in schema_sql.split(";"):
        if statement.strip():
            connection.execute(text(statement))

    client_count = import_clients(connection)
    efile_count = import_efile(connection)

print(f"Imported {client_count} Drake client rows.")
print(f"Imported {efile_count} Drake e-file rows.")
print("Drake 2025 read-only import completed.")
