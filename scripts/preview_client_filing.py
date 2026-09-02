"""READ-ONLY filing preview for one client. Reports what a backfill WOULD propose; writes nothing.

    python scripts/preview_client_filing.py --person 3824 --show-rows

For every active document owned by the client (person + their household) it prints the current and
proposed category, the current and proposed tax year, the evidence and confidence behind each, any
person attribution inside the household, and any reason the row needs a human.

Every judgement comes from the existing engines — ``document_classification`` for category,
``document_tax_year`` for the year, ``document_filing_evidence`` to compose them. This script opens a
read-only view of the data and contains no INSERT, UPDATE or DELETE.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy import or_, select

from app.db import documents, engine, people
from app.services.document_filing_evidence import filing_evidence

#: Initial tokens the firm uses inside shared filenames, supplied per client. DLW is deliberately
#: absent — see ``document_filing_evidence.UNRESOLVED_PERSON_INITIALS``.
MEMBER_INITIALS = {
    3824: {"MBW": "Michael Blaine White", "DGW": "Debra Gregory White",
           "HGW": "Hudson G White", "EWW": "Emerson W White"},
}


def _rows(person_id=None, household_id=None, limit=5000):
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
            select(documents).where(*conditions).order_by(documents.c.id).limit(limit)).mappings()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--person", type=int)
    parser.add_argument("--household", type=int)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--show-rows", action="store_true")
    args = parser.parse_args(argv)

    initials = MEMBER_INITIALS.get(args.person, {})
    rows = _rows(args.person, args.household, args.limit)
    preview = [filing_evidence(r, member_initials=initials) for r in rows]

    category_new = [p for p in preview if p["proposed_category"] and not p["current_category"]]
    category_any = [p for p in preview if p["proposed_category"]]
    year_ok = [p for p in preview if p["proposed_tax_year"]]
    year_conflict = [p for p in preview if p["tax_year_conflict"]]
    person_ok = [p for p in preview if p["proposed_person"]]
    review = [p for p in preview if p["requires_review"]]

    print(f"documents examined            : {len(preview)}")
    print(f"CATEGORY_AUTOFILABLE (now null): {len(category_new)}"
          f"   (any proposal: {len(category_any)})")
    print(f"TAX_YEAR_AUTOFILABLE          : {len(year_ok)}")
    print(f"PERSON_ATTRIBUTABLE           : {len(person_ok)}")
    print(f"HUMAN_REVIEW_REQUIRED         : {len(review)}"
          f"   (of which tax-year conflicts: {len(year_conflict)})")
    print("\nby tax-year confidence        :",
          dict(Counter(p["tax_year_confidence"] for p in preview)))
    print("proposed categories           :",
          dict(Counter(p["proposed_category"] for p in category_any).most_common()))
    print("person attribution            :",
          dict(Counter(p["person_initials"] for p in preview if p["person_initials"])))

    if args.show_rows:
        print(f"\n{'id':>7} {'owner':<13} {'cur cat':<14} {'prop cat':<11} {'cur yr':<7}"
              f" {'prop yr':<8} {'conf':<9} document / evidence")
        for p in preview:
            print(f"{p['document_id']:>7} {str(p['current_owner'] or '-'):<13} "
                  f"{str(p['current_category'] or '-'):<14} {str(p['proposed_category'] or '-'):<11} "
                  f"{str(p['current_tax_year'] or '-'):<7} {str(p['proposed_tax_year'] or '-'):<8} "
                  f"{p['tax_year_confidence']:<9} {(p['original_name'] or '')[:46]}")
            detail = "; ".join(p["tax_year_evidence"] + p["category_evidence"] + p["person_evidence"])
            if detail:
                print(f"{'':>7} └─ {detail[:150]}")
            if p["requires_review"]:
                print(f"{'':>7} ⚠ REVIEW: {'; '.join(p['review_reasons'])[:140]}")

    print("\nNOTHING WAS WRITTEN. Category, tax year, owner and person attribution are all proposals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
