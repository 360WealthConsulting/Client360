import csv
import hashlib
import json
import os
import re
from collections import namedtuple
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.postgresql import insert

FOLDER = Path("01 Raw Imports/AssetMark")
CSV_FILE = FOLDER / "ClientList.csv"
SOURCE_SYSTEM = "AssetMark"

_Database = namedtuple("_Database", "engine source_contacts")


@cache
def _database():
    """Resolve the engine and tables on first use, never at import.

    Reading app/.env, creating the engine and reflecting the schema are all
    deferred: importing this module must touch neither the filesystem nor the
    database. Cached, so the cost is paid once per process, exactly as before.
    """
    load_dotenv("app/.env")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing from app/.env")

    engine = create_engine(database_url)

    metadata = MetaData()
    metadata.reflect(bind=engine)

    return _Database(engine, metadata.tables["source_contacts"])


def clean(value):
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def normalize_email(value):
    value = clean(value)
    return value.lower() if value else None


def normalize_phone(value):
    value = clean(value)
    if not value:
        return None

    digits = re.sub(r"\D", "", value)

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    return digits or None


def _row_getter(row):
    """A tolerant, case-insensitive column resolver for one CSV row.

    AssetMark's ClientList.csv column headers vary; the full raw row is always
    preserved verbatim in ``raw_data`` (never dropped), while the mapped
    source_contacts fields are resolved from whichever header variant is present.
    """
    lower = {(key or "").strip().lower(): value for key, value in row.items()}

    def pick(*names):
        for name in names:
            value = clean(lower.get(name.strip().lower()))
            if value:
                return value
        return None

    return pick


def import_csv(csv_file, conn):
    """Import one AssetMark ClientList CSV. Returns (rows_read, rows_inserted, rows_skipped).

    The full raw row is preserved as JSON in ``raw_data``. Duplicate imports are
    avoided by a per-row content hash: re-running against the same file conflicts
    on (source_system, source_hash) and every row is skipped (idempotent).
    """
    source_contacts = _database().source_contacts

    rows_read = 0
    rows_inserted = 0
    rows_skipped = 0

    print(f"Importing {csv_file.name}")

    with csv_file.open("r", encoding="utf-8-sig", newline="", errors="replace") as file_handle:
        reader = csv.DictReader(file_handle)

        for row in reader:
            rows_read += 1

            # Per-row content hash over the FULL raw row — content-addressed dedup.
            row_json = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            record_hash = hashlib.sha256(row_json.encode("utf-8")).hexdigest()

            pick = _row_getter(row)

            first_name = pick("First Name", "FirstName", "First", "Client First Name",
                              "Primary First Name")
            last_name = pick("Last Name", "LastName", "Last", "Client Last Name",
                             "Primary Last Name")
            full_name = pick("Full Name", "Name", "Client Name", "Account Name", "Client",
                             "Household Name")
            if not full_name and (first_name or last_name):
                full_name = " ".join(part for part in (first_name, last_name) if part)

            email = pick("Email", "Email Address", "Primary Email", "E-mail", "EmailAddress")
            phone = pick("Phone", "Phone Number", "Primary Phone", "Mobile", "Mobile Phone",
                         "Home Phone", "Cell Phone", "Telephone")

            source_record_id = pick("Account Number", "Account #", "Account ID", "Client ID",
                                    "AssetMark ID", "AssetMark Account", "Account", "ID")

            values = {
                "source_system": SOURCE_SYSTEM,
                "source_file": csv_file.name,
                "source_record_id": source_record_id,
                "source_hash": record_hash,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "email": email,
                "normalized_email": normalize_email(email),
                "phone": phone,
                "normalized_phone": normalize_phone(phone),
                "address_line_1": pick("Address", "Address Line 1", "Address 1", "Street",
                                       "Mailing Address", "Street Address"),
                "address_line_2": pick("Address Line 2", "Address 2", "Suite", "Unit"),
                "city": pick("City"),
                "state": pick("State", "State/Province", "ST", "Province"),
                "postal_code": pick("Zip", "Zip Code", "ZIP", "Postal Code", "Zip/Postal Code"),
                "raw_data": row,
            }

            statement = (
                insert(source_contacts)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["source_system", "source_hash"])
                .returning(source_contacts.c.id)
            )

            if conn.execute(statement).scalar_one_or_none() is None:
                rows_skipped += 1
            else:
                rows_inserted += 1

    return rows_read, rows_inserted, rows_skipped


def main(csv_file=CSV_FILE):
    """Run the full AssetMark import, then promote the freshly-imported contacts.

    Invoked explicitly, never on import. The import and the promotion run inside a
    single transaction, so promotion sees the new rows (mirrors wealthbox.py).
    """
    # Lazy import: promote pulls in app.db (schema reflection), which must not happen at
    # module-import time (this importer is import-inert).
    from app.matching.promote import promote_unlinked

    if not csv_file.exists():
        raise FileNotFoundError(f"AssetMark client list not found: {csv_file}")

    file_hash = hashlib.sha256(csv_file.read_bytes()).hexdigest()
    print(f"File: {csv_file}")
    print(f"File hash: {file_hash}")

    with _database().engine.begin() as conn:
        rows_read, rows_inserted, rows_skipped = import_csv(csv_file, conn)
        promotion = promote_unlinked(source_system=SOURCE_SYSTEM, conn=conn)

    print()
    print("AssetMark import complete.")
    print(f"Rows read: {rows_read:,}")
    print(f"Rows inserted: {rows_inserted:,}")
    print(f"Rows skipped: {rows_skipped:,}")
    print()
    print("Promotion summary:")
    print(f"  inspected: {promotion.inspected:,}")
    print(f"  created: {promotion.created:,}")
    print(f"  linked_existing: {promotion.linked_existing:,}")
    print(f"  ambiguous: {promotion.ambiguous:,}")

    return {
        "rows_read": rows_read,
        "rows_inserted": rows_inserted,
        "rows_skipped": rows_skipped,
        "promotion": {
            "inspected": promotion.inspected,
            "created": promotion.created,
            "linked_existing": promotion.linked_existing,
            "ambiguous": promotion.ambiguous,
        },
    }


if __name__ == "__main__":
    main()
