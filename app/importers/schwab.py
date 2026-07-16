import csv
import hashlib
import os
import re
from collections import namedtuple
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.postgresql import insert

FOLDER = Path("01 Raw Imports/Schwab")

_Database = namedtuple("_Database", "engine accounts source_contacts import_jobs")


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

    return _Database(
        engine,
        metadata.tables["accounts"],
        metadata.tables["source_contacts"],
        metadata.tables["import_jobs"],
    )


def find_input_files(folder=FOLDER):
    """Locate the Schwab input CSVs, raising if either set is missing.

    Import-time discovery would make merely importing this module depend on a
    client-data folder that is gitignored and absent from any clean checkout.
    """
    accounts_files = sorted(folder.glob("AccountsList_*.csv"))
    profile_files = sorted(folder.glob("Profile_Firm_*.csv"))

    if not accounts_files:
        raise FileNotFoundError("No Schwab AccountsList CSV found.")

    if not profile_files:
        raise FileNotFoundError("No Schwab Profile CSV found.")

    return accounts_files, profile_files


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


def parse_money(value):
    value = clean(value)

    if not value:
        return None

    cleaned = (
        value.replace("$", "")
        .replace(",", "")
        .replace("(", "-")
        .replace(")", "")
    )

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(value):
    value = clean(value)

    if not value or value.upper() in {"N/A", "NONE", "NOT SENT"}:
        return None

    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue

    return None


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start_import_job(conn, source_file, source_hash):
    import_jobs = _database().import_jobs

    statement = (
        insert(import_jobs)
        .values(
            source_system="Schwab",
            source_file=source_file,
            file_hash=source_hash,
            status="started",
        )
        .returning(import_jobs.c.id)
    )

    return conn.execute(statement).scalar_one()


def finish_import_job(
    conn,
    job_id,
    rows_read,
    rows_inserted,
    rows_updated,
    rows_skipped,
):
    import_jobs = _database().import_jobs

    conn.execute(
        import_jobs.update()
        .where(import_jobs.c.id == job_id)
        .values(
            status="completed",
            completed_at=datetime.now(),
            rows_read=rows_read,
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
            rows_skipped=rows_skipped,
        )
    )


def fail_import_job(conn, job_id, error_message):
    import_jobs = _database().import_jobs

    conn.execute(
        import_jobs.update()
        .where(import_jobs.c.id == job_id)
        .values(
            status="failed",
            completed_at=datetime.now(),
            error_message=str(error_message)[:5000],
        )
    )


def import_accounts_file(path):
    database = _database()
    accounts = database.accounts

    rows_read = 0
    rows_inserted = 0
    rows_updated = 0
    rows_skipped = 0

    with database.engine.begin() as conn:
        job_id = start_import_job(conn, path.name, file_hash(path))

        try:
            with path.open(
                "r",
                encoding="utf-8-sig",
                errors="replace",
                newline="",
            ) as file_handle:
                reader = csv.DictReader(file_handle)

                for row in reader:
                    rows_read += 1

                    account_number = clean(row.get("Account Number"))

                    if not account_number:
                        rows_skipped += 1
                        continue

                    values = {
                        "custodian": "Schwab",
                        "account_number": account_number,
                        "account_name": clean(row.get("Name")),
                        "registration_type": clean(row.get("Registration")),
                        "status": clean(row.get("Status")),
                        "total_value": parse_money(row.get("Total Value")),
                        "cash_value": parse_money(row.get("Cash Available")),
                        "open_date": parse_date(
                            row.get("Performance Start Date")
                        ),
                        "closed_date": parse_date(row.get("Closed Date")),
                        "source_file": path.name,
                    }

                    statement = (
                        insert(accounts)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=[
                                "custodian",
                                "account_number",
                            ],
                            set_={
                                key: value
                                for key, value in values.items()
                                if key not in {
                                    "custodian",
                                    "account_number",
                                }
                            },
                        )
                        .returning(accounts.c.id)
                    )

                    conn.execute(statement)
                    rows_inserted += 1

            finish_import_job(
                conn,
                job_id,
                rows_read,
                rows_inserted,
                rows_updated,
                rows_skipped,
            )

        except Exception as exc:
            fail_import_job(conn, job_id, exc)
            raise

    print(f"Imported Schwab accounts file: {path.name}")
    print(f"Rows read: {rows_read:,}")
    print(f"Rows processed: {rows_inserted:,}")
    print(f"Rows skipped: {rows_skipped:,}")


def import_profile_file(path):
    database = _database()
    source_contacts = database.source_contacts

    rows_read = 0
    rows_inserted = 0
    rows_updated = 0
    rows_skipped = 0

    with database.engine.begin() as conn:
        job_id = start_import_job(conn, path.name, file_hash(path))

        try:
            with path.open(
                "r",
                encoding="utf-8-sig",
                errors="replace",
                newline="",
            ) as file_handle:
                physical_rows = list(csv.reader(file_handle))

            if len(physical_rows) < 5:
                raise RuntimeError(
                    f"Unexpected Schwab profile format in {path.name}"
                )

            header = physical_rows[3]

            for values_row in physical_rows[4:]:
                if not values_row:
                    continue

                if len(values_row) != len(header):
                    rows_skipped += 1
                    continue

                row = dict(zip(header, values_row))
                rows_read += 1

                account_number = clean(row.get("Account#"))

                if not account_number:
                    rows_skipped += 1
                    continue

                primary_holder = clean(
                    row.get("Primary Account Holder")
                )

                email = (
                    clean(row.get("Primary Email Address"))
                    or clean(row.get("Account Email Address"))
                )

                phone = (
                    clean(row.get("Cell Phone"))
                    or clean(row.get("Home Phone"))
                    or clean(row.get("Business Phone"))
                )

                safe_raw_data = {
                    key: value
                    for key, value in row.items()
                    if key != "Taxpayer ID"
                }

                source_hash = hashlib.sha256(
                    f"schwab-profile:{account_number}".encode("utf-8")
                ).hexdigest()

                values = {
                    "source_system": "Schwab Profile",
                    "source_file": path.name,
                    "source_record_id": account_number,
                    "source_hash": source_hash,
                    "full_name": primary_holder,
                    "email": email,
                    "normalized_email": normalize_email(email),
                    "phone": phone,
                    "normalized_phone": normalize_phone(phone),
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
                    .returning(source_contacts.c.id)
                )

                conn.execute(statement)
                rows_inserted += 1

            finish_import_job(
                conn,
                job_id,
                rows_read,
                rows_inserted,
                rows_updated,
                rows_skipped,
            )

        except Exception as exc:
            fail_import_job(conn, job_id, exc)
            raise

    print()
    print(f"Imported Schwab profile file: {path.name}")
    print(f"Rows read: {rows_read:,}")
    print(f"Rows processed: {rows_inserted:,}")
    print(f"Rows skipped: {rows_skipped:,}")
    print("Taxpayer IDs were excluded.")


def main(folder=FOLDER):
    """Run the full Schwab import. Invoked explicitly, never on import."""
    accounts_files, profile_files = find_input_files(folder)

    for accounts_path in accounts_files:
        import_accounts_file(accounts_path)

    for profile_path in profile_files:
        import_profile_file(profile_path)

    print()
    print("Schwab import complete.")


if __name__ == "__main__":
    main()
