"""READ-ONLY tax-year inference preview. Reports what a backfill WOULD write; writes nothing.

``documents`` has no ``tax_year`` column, so this proposes a year from evidence the firm already
files by — the filename and SharePoint's own year folders — and reports the evidence and confidence
behind each proposal. Run it before anyone authorises a backfill, and again to check a rule change.

    python scripts/preview_tax_years.py --person 3824
    python scripts/preview_tax_years.py --household 1 --show-rows
    python scripts/preview_tax_years.py --limit 5000            # firm-wide sample

The database connection is opened READ-ONLY and the script contains no UPDATE, INSERT or DELETE.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy import or_, select

from app.db import documents, engine, people
from app.services.document_tax_year import preview_tax_years


def _rows(person_id=None, household_id=None, limit=2000):
    """Active, non-deleted documents for a client (person + their household) or a firm-wide sample."""
    conditions = [documents.c.status != "deleted", documents.c.deleted_at.is_(None)]
    with engine.connect() as connection:
        if person_id:
            hh = connection.execute(
                select(people.c.household_id).where(people.c.id == person_id)).scalar_one_or_none()
            scope = documents.c.person_id == person_id
            if hh is not None:
                scope = or_(scope, documents.c.household_id == hh)
            conditions.append(scope)
        elif household_id:
            conditions.append(documents.c.household_id == household_id)
        return [dict(r) for r in connection.execute(
            select(documents).where(*conditions).limit(limit)).mappings()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--person", type=int, help="preview one person (and their household)")
    parser.add_argument("--household", type=int, help="preview one household")
    parser.add_argument("--limit", type=int, default=2000, help="maximum documents to read")
    parser.add_argument("--show-rows", action="store_true", help="list every proposal")
    args = parser.parse_args(argv)

    rows = _rows(args.person, args.household, args.limit)
    preview = preview_tax_years(rows)
    proposed = [p for p in preview if p["proposed_year"] is not None]
    by_confidence = Counter(p["confidence"] for p in preview)

    print(f"documents examined : {len(preview)}")
    print(f"year proposable    : {len(proposed)}"
          f" ({100.0 * len(proposed) / len(preview):.1f}%)" if preview else "")
    for confidence in ("strong", "moderate", "conflict", "none"):
        if by_confidence.get(confidence):
            print(f"  {confidence:9s} {by_confidence[confidence]:6d}")
    if proposed:
        years = Counter(p["proposed_year"] for p in proposed)
        print("  years: " + ", ".join(f"{y} ({n})" for y, n in sorted(years.items())))

    if args.show_rows:
        print("\nid       year  confidence  evidence / filename")
        for p in preview:
            print(f"{p['document_id']:<8} {str(p['proposed_year'] or '-'):<5} "
                  f"{p['confidence']:<11} {'; '.join(p['evidence']) or '—'}"
                  f"  [{(p['original_name'] or '')[:48]}]")

    print("\nNOTHING WAS WRITTEN. A backfill needs a real tax-year field and its own authorisation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
