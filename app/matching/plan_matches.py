import csv
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, select

load_dotenv("app/.env")

database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(database_url)

metadata = MetaData()
metadata.reflect(bind=engine)

source_contacts = metadata.tables["source_contacts"]

REPORT_FOLDER = Path("06 Reports/private")
REPORT_FOLDER.mkdir(parents=True, exist_ok=True)

REPORT_FILE = REPORT_FOLDER / "exact_match_merge_plan.csv"


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

    return digits if len(digits) >= 7 else None


def normalize_name(value):
    value = clean(value)

    if not value:
        return None

    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()

    # Schwab often stores names as "LAST, FIRST".
    value = value.replace(",", " ")

    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value or None


def record_name(record):
    full_name = clean(record["full_name"])

    if full_name:
        return full_name

    parts = [
        clean(record["first_name"]),
        clean(record["last_name"]),
    ]

    return " ".join(part for part in parts if part) or None


def name_tokens(value):
    normalized = normalize_name(value)

    if not normalized:
        return set()

    ignored = {
        "jr",
        "sr",
        "ii",
        "iii",
        "iv",
        "mr",
        "mrs",
        "ms",
        "dr",
    }

    return {
        token
        for token in normalized.split()
        if token not in ignored
    }


def names_compatible(names):
    token_sets = [
        name_tokens(name)
        for name in names
        if name_tokens(name)
    ]

    if len(token_sets) < 2:
        return False

    common_tokens = set.intersection(*token_sets)

    # At least one meaningful name token must agree across all records.
    if not common_tokens:
        return False

    # Reject groups whose names appear substantially unrelated.
    for left_index, left_tokens in enumerate(token_sets):
        for right_tokens in token_sets[left_index + 1:]:
            if not left_tokens & right_tokens:
                return False

    return True


with engine.connect() as connection:
    records = connection.execute(
        select(
            source_contacts.c.id,
            source_contacts.c.source_system,
            source_contacts.c.full_name,
            source_contacts.c.first_name,
            source_contacts.c.last_name,
            source_contacts.c.email,
            source_contacts.c.normalized_email,
            source_contacts.c.phone,
            source_contacts.c.normalized_phone,
        )
    ).mappings().all()


groups = defaultdict(list)

for record in records:
    email = (
        clean(record["normalized_email"])
        or normalize_email(record["email"])
    )

    phone = (
        clean(record["normalized_phone"])
        or normalize_phone(record["phone"])
    )

    if email and phone:
        groups[(email, phone)].append(record)


planned_rows = []
safe_groups = 0
review_groups = 0

for (email, phone), members in sorted(groups.items()):
    systems = sorted({
        member["source_system"]
        for member in members
    })

    if len(systems) < 2:
        continue

    names = [
        record_name(member)
        for member in members
        if record_name(member)
    ]

    duplicate_source_records = len(members) != len(systems)
    compatible_names = names_compatible(names)

    reasons = []

    if duplicate_source_records:
        reasons.append("multiple records from same source")

    if not compatible_names:
        reasons.append("names require review")

    if len(members) > 5:
        reasons.append("large shared-contact group")

    if not reasons:
        decision = "SAFE_CANDIDATE"
        safe_groups += 1
    else:
        decision = "REVIEW"
        review_groups += 1

    planned_rows.append({
        "decision": decision,
        "email": email,
        "phone": phone,
        "source_systems": " | ".join(systems),
        "record_count": len(members),
        "record_ids": " | ".join(
            str(member["id"])
            for member in members
        ),
        "names": " | ".join(
            record_name(member) or "(no name)"
            for member in members
        ),
        "review_reason": "; ".join(reasons),
    })


with REPORT_FILE.open(
    "w",
    encoding="utf-8-sig",
    newline="",
) as file_handle:
    writer = csv.DictWriter(
        file_handle,
        fieldnames=[
            "decision",
            "email",
            "phone",
            "source_systems",
            "record_count",
            "record_ids",
            "names",
            "review_reason",
        ],
    )

    writer.writeheader()
    writer.writerows(planned_rows)


print()
print("CLIENT360 DRY-RUN MERGE PLAN")
print("=" * 50)
print(f"Source records reviewed: {len(records):,}")
print(f"Cross-source email-and-phone groups: {len(planned_rows):,}")
print(f"Safe candidate groups: {safe_groups:,}")
print(f"Groups requiring review: {review_groups:,}")
print()
print(f"Report created: {REPORT_FILE}")
print("No database records were changed.")
