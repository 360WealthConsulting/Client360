import csv
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.postgresql import insert

load_dotenv("app/.env")

database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(database_url)

metadata = MetaData()
metadata.reflect(bind=engine)

source_contacts = metadata.tables["source_contacts"]
folder = Path("01 Raw Imports/Dave Ramsey")

seen_files = {}
rows_read = 0
rows_inserted = 0
rows_skipped = 0

with engine.begin() as conn:
    for csv_file in sorted(folder.glob("*.csv")):
        file_hash = hashlib.sha256(csv_file.read_bytes()).hexdigest()

        if file_hash in seen_files:
            print(f"Skipping duplicate export: {csv_file.name}")
            continue

        seen_files[file_hash] = csv_file.name
        print(f"Importing {csv_file.name}")

        with csv_file.open(
            "r",
            encoding="utf-8-sig",
            newline="",
            errors="replace",
        ) as file_handle:
            reader = csv.DictReader(file_handle)

            for row in reader:
                rows_read += 1

                row_json = json.dumps(
                    row,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )

                record_hash = hashlib.sha256(
                    row_json.encode("utf-8")
                ).hexdigest()

                statement = (
                    insert(source_contacts)
                    .values(
                        source_system="Dave Ramsey",
                        source_file=csv_file.name,
                        source_hash=record_hash,
                        first_name=row.get("First Name"),
                        last_name=row.get("Last Name"),
                        full_name=row.get("Full Name"),
                        email=row.get("Email"),
                        phone=row.get("Phone"),
                        address_line_1=row.get("Address Line 1"),
                        address_line_2=row.get("Address Line 2"),
                        city=row.get("City"),
                        state=row.get("State"),
                        postal_code=row.get("Postal Code"),
                        territory=row.get("Territory"),
                        raw_data=row,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["source_system", "source_hash"]
                    )
                    .returning(source_contacts.c.id)
                )

                result = conn.execute(statement)

                if result.scalar_one_or_none() is None:
                    rows_skipped += 1
                else:
                    rows_inserted += 1

print()
print("Dave Ramsey import complete.")
print(f"Rows read: {rows_read:,}")
print(f"Rows inserted: {rows_inserted:,}")
print(f"Duplicate records skipped: {rows_skipped:,}")
