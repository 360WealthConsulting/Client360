"""Rebuild ``drake_identity`` from Drake source contacts, without destroying linkage.

This script used to ``DELETE FROM drake_identity`` and re-insert, which silently discarded
``primary_person_id`` on every identity -- 931 person links at the time this was fixed, including
human-adjudicated and individually authorised manual repairs. The rebuild semantics now live in
``app.services.drake_identity_rebuild``, which refreshes only the fields Drake actually derives and
fails closed rather than dropping state. This file is the command-line entry point.

The table's schema is owned by the ``drake01`` Alembic migration, so the old ``CREATE TABLE IF NOT
EXISTS`` block is gone with it -- a script is no longer a second definition of the schema.

    python scripts/build_drake_identity.py
"""
from dotenv import load_dotenv

load_dotenv(r"C:\Client360\app\.env")

from app.services.drake_identity_rebuild import RebuildRefused  # noqa: E402
from app.services.drake_identity_rebuild import main as rebuild  # noqa: E402

print("=" * 70)
print("BUILDING DRAKE IDENTITIES")
print("=" * 70)
print()

try:
    report = rebuild()
except RebuildRefused as refusal:
    print("REFUSED - nothing was written:")
    print(f"  {refusal}")
    raise SystemExit(1) from refusal

for line in report.lines():
    print(line)

print()
print("Finished.")
