import csv
import hashlib
import io
import json
import os
import re
import zipfile
from collections import namedtuple
from datetime import datetime
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.dialects.postgresql import insert

FOLDER = Path("01 Raw Imports/Wealthbox")

_Database = namedtuple("_Database", "engine source_contacts import_jobs")


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

    return _Database(
        engine,
        metadata.tables["source_contacts"],
        metadata.tables["import_jobs"],
    )


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


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def start_import_job(conn, source_file, source_hash):
    """Record the start of a Wealthbox import run (mirrors the Schwab importer)."""
    import_jobs = _database().import_jobs
    statement = (
        insert(import_jobs)
        .values(
            source_system="Wealthbox",
            source_file=source_file,
            file_hash=source_hash,
            status="started",
        )
        .returning(import_jobs.c.id)
    )
    return conn.execute(statement).scalar_one()


def finish_import_job(conn, job_id, rows_read, rows_inserted, rows_updated=0, rows_skipped=0):
    """Mark a Wealthbox import run complete with its row counts."""
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


def validation_report(conn):
    """Content-free post-import validation of the Wealthbox source data (counts only —
    no names, emails, or phone numbers). Surfaces data-quality and match-review posture so
    staff can trust the import before working the records."""
    def scalar(sql):
        return conn.execute(text(sql)).scalar() or 0

    wb = "source_contacts.source_system = 'Wealthbox'"
    total = scalar(f"SELECT count(*) FROM source_contacts WHERE {wb}")
    with_email = scalar(f"SELECT count(*) FROM source_contacts WHERE {wb} AND normalized_email IS NOT NULL")
    with_phone = scalar(f"SELECT count(*) FROM source_contacts WHERE {wb} AND normalized_phone IS NOT NULL")
    dup_email = scalar(
        f"SELECT count(*) FROM (SELECT normalized_email FROM source_contacts WHERE {wb} "
        "AND normalized_email IS NOT NULL GROUP BY normalized_email HAVING count(*) > 1) d")
    dup_phone = scalar(
        f"SELECT count(*) FROM (SELECT normalized_phone FROM source_contacts WHERE {wb} "
        "AND normalized_phone IS NOT NULL GROUP BY normalized_phone HAVING count(*) > 1) d")
    linked = scalar(
        "SELECT count(DISTINCT psl.source_contact_id) FROM person_source_links psl "
        "JOIN source_contacts sc ON sc.id = psl.source_contact_id "
        "WHERE sc.source_system = 'Wealthbox'")
    pending_review = scalar(
        "SELECT count(*) FROM match_queue mq JOIN source_contacts sc ON sc.id = mq.source_contact_id "
        "WHERE sc.source_system = 'Wealthbox' AND mq.status = 'pending'")
    return {
        "wealthbox_source_contacts": total,
        "with_email": with_email,
        "missing_email": total - with_email,
        "with_phone": with_phone,
        "missing_phone": total - with_phone,
        "duplicate_email_groups": dup_email,
        "duplicate_phone_groups": dup_phone,
        "linked_to_person": linked,
        "unlinked": total - linked,
        "pending_match_review": pending_review,
    }


def print_validation_report(report):
    print()
    print("Wealthbox import validation report")
    for key, value in report.items():
        print(f"  {key.replace('_', ' ')}: {value:,}")


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
    # Lazy import: promote pulls in app.db (schema reflection), which must not happen at
    # module-import time (this importer is import-inert).
    from app.matching.promote import promote_unlinked

    zip_files = find_contact_zips(folder)

    rows_read = 0
    rows_inserted = 0

    with _database().engine.begin() as conn:
        for zip_path in zip_files:
            job_id = start_import_job(conn, zip_path.name, file_hash(zip_path))
            read, inserted = import_contacts_zip(zip_path, conn)
            finish_import_job(conn, job_id, rows_read=read, rows_inserted=inserted)
            rows_read += read
            rows_inserted += inserted
        # Promote the freshly-imported single-source contacts to canonical people (same
        # transaction, so it sees the new rows). Conservative: unique contacts become people,
        # exact email/phone matches link to an existing person, ambiguous cases are left for
        # Match Review. Runs after every import so single-source contacts are never stranded.
        promotion = promote_unlinked(source_system="Wealthbox", conn=conn)
        report = validation_report(conn)

    print()
    print("Wealthbox contact import complete.")
    print(f"Rows read: {rows_read:,}")
    print(f"Rows processed: {rows_inserted:,}")
    print("Sensitive identity and medical fields were excluded.")
    print(
        f"Promotion — created {promotion.created}, "
        f"linked {promotion.linked_existing}, ambiguous {promotion.ambiguous} "
        f"(of {promotion.inspected} unlinked)."
    )
    print_validation_report(report)

    return {"rows_read": rows_read, "rows_inserted": rows_inserted}


if __name__ == "__main__":
    main()
