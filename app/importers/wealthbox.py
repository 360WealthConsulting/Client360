import csv
import hashlib
import io
import json
import os
import re
import zipfile
from collections import namedtuple
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.postgresql import insert

FOLDER = Path("01 Raw Imports/Wealthbox")

_Database = namedtuple("_Database", "engine source_contacts")


@lru_cache(maxsize=None)
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


def find_contact_zips(folder=FOLDER):
    """Locate the Wealthbox contact exports, raising if none are present.

    Import-time discovery would make merely importing this module depend on a
    client-data folder that is gitignored and absent from any clean checkout.
    """
    zip_files = sorted(folder.glob("*contacts*.zip"))

    if not zip_files:
        raise FileNotFoundError(
            "No Wealthbox contacts ZIP found in 01 Raw Imports/Wealthbox"
        )

    return zip_files

# These fields will not be copied into source_contacts.raw_data.
sensitive_fields = {
    "ssn",
    "passport_number",
    "green_card_number",
    "drivers_license_number",
    "drivers_license_state",
    "drivers_license_issued_date",
    "drivers_license_expires_date",
    "medical_conditions",
    "taxpayer_id",
}


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


def sanitized_row(row):
    return {
        key: value
        for key, value in row.items()
        if key.lower().strip() not in sensitive_fields
    }


def import_contacts_zip(zip_path, conn):
    """Import one Wealthbox contacts ZIP. Returns (rows_read, rows_inserted)."""
    source_contacts = _database().source_contacts

    rows_read = 0
    rows_inserted = 0

    print(f"Opening {zip_path.name}")

    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
        ]

        if not csv_names:
            raise RuntimeError(
                f"No CSV found inside {zip_path.name}"
            )

        for csv_name in csv_names:
            print(f"Importing {csv_name}")

            with archive.open(csv_name) as binary_file:
                text_file = io.TextIOWrapper(
                    binary_file,
                    encoding="utf-8-sig",
                    errors="replace",
                    newline="",
                )

                reader = csv.DictReader(text_file)

                for row in reader:
                    rows_read += 1

                    source_record_id = (
                        clean(row.get("external_unique_id"))
                        or clean(row.get("id"))
                    )

                    safe_raw_data = sanitized_row(row)

                    if source_record_id:
                        hash_basis = f"wealthbox:{source_record_id}"
                    else:
                        hash_basis = json.dumps(
                            safe_raw_data,
                            sort_keys=True,
                            ensure_ascii=False,
                            default=str,
                        )

                    source_hash = hashlib.sha256(
                        hash_basis.encode("utf-8")
                    ).hexdigest()

                    email = clean(row.get("primary_email"))
                    phone = clean(row.get("primary_phone"))

                    values = {
                        "source_system": "Wealthbox",
                        "source_file": zip_path.name,
                        "source_record_id": source_record_id,
                        "source_hash": source_hash,
                        "first_name": clean(row.get("first_name")),
                        "middle_name": clean(row.get("middle_name")),
                        "last_name": clean(row.get("last_name")),
                        "full_name": clean(row.get("name")),
                        "email": email,
                        "normalized_email": normalize_email(email),
                        "phone": phone,
                        "normalized_phone": normalize_phone(phone),
                        "address_line_1": clean(
                            row.get("mailing_address_street")
                        ),
                        "address_line_2": clean(
                            row.get("mailing_address_street_2")
                        ),
                        "city": clean(
                            row.get("mailing_address_city")
                        ),
                        "state": clean(
                            row.get("mailing_address_state")
                        ),
                        "postal_code": clean(
                            row.get("mailing_address_zip_code")
                        ),
                        "raw_data": safe_raw_data,
                    }

                    statement = (
                        insert(source_contacts)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=[
                                "source_system",
                                "source_hash",
                            ],
                            set_={
                                key: value
                                for key, value in values.items()
                                if key not in {
                                    "source_system",
                                    "source_hash",
                                }
                            },
                        )
                        .returning(
                            source_contacts.c.id,
                            source_contacts.c.imported_at,
                        )
                    )

                    result = conn.execute(statement).first()

                    if result is not None:
                        rows_inserted += 1

    return rows_read, rows_inserted


def main(folder=FOLDER):
    """Run the full Wealthbox import. Invoked explicitly, never on import.

    All ZIPs are imported inside a single transaction, as before.
    """
    zip_files = find_contact_zips(folder)

    rows_read = 0
    rows_inserted = 0

    with _database().engine.begin() as conn:
        for zip_path in zip_files:
            read, inserted = import_contacts_zip(zip_path, conn)
            rows_read += read
            rows_inserted += inserted

    print()
    print("Wealthbox contact import complete.")
    print(f"Rows read: {rows_read:,}")
    print(f"Rows processed: {rows_inserted:,}")
    print("Sensitive identity and medical fields were excluded.")

    return {"rows_read": rows_read, "rows_inserted": rows_inserted}


if __name__ == "__main__":
    main()
