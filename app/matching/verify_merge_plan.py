import csv
from pathlib import Path

REPORT_FILE = Path(
    "06 Reports/private/exact_match_merge_plan.csv"
)

if not REPORT_FILE.exists():
    raise FileNotFoundError(
        f"Merge plan not found: {REPORT_FILE}"
    )

safe_groups = []
review_groups = []

with REPORT_FILE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as file_handle:
    reader = csv.DictReader(file_handle)

    for row in reader:
        decision = row["decision"].strip()

        if decision == "SAFE_CANDIDATE":
            safe_groups.append(row)
        elif decision == "REVIEW":
            review_groups.append(row)

safe_record_ids = set()

for row in safe_groups:
    record_ids = [
        value.strip()
        for value in row["record_ids"].split("|")
        if value.strip()
    ]

    safe_record_ids.update(record_ids)

print()
print("CLIENT360 MERGE PLAN VERIFICATION")
print("=" * 50)
print(f"Safe candidate groups: {len(safe_groups):,}")
print(f"Review groups: {len(review_groups):,}")
print(f"Unique source records in safe groups: {len(safe_record_ids):,}")
print()
print("No database records were changed.")
