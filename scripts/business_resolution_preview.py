"""PREVIEW-ONLY report of derivable business relationships (READ-ONLY; writes nothing).

    python scripts/business_resolution_preview.py            # full preview
    python scripts/business_resolution_preview.py --business 135   # one business (e.g. Pullen)

Apply is intentionally NOT implemented here — safe proposals are applied separately via
``organization_service.record_ownership`` after human review (duplicate businesses resolved and stale
person identities reconciled first).
"""
from __future__ import annotations

import argparse

from app.services.business_resolution import resolve_business_relationships

_BUCKETS = ("SAFE_OWNERSHIP", "SAFE_ASSOCIATION_ONLY", "PERSON_IDENTITY_REVIEW",
            "DUPLICATE_BUSINESS_REVIEW", "AMBIGUOUS", "UNRESOLVED")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Business relationship resolution PREVIEW (read-only).")
    ap.add_argument("--business", action="append", type=int, default=None,
                    help="restrict to specific business relationship_entities id(s)")
    ap.add_argument("--show", type=int, default=1000, help="max detail rows per bucket to print")
    args = ap.parse_args(argv)

    rep = resolve_business_relationships(business_ids=args.business)
    s = rep["summary"]
    print("BUSINESS RELATIONSHIP RESOLUTION — PREVIEW (read-only, nothing written)")
    for k in ("businesses_evaluated", "exact_company_matches", "explicit_owner_matches",
              "officer_management_associations", "household_associations",
              "person_reconciliations_required", "duplicate_business_reviews", "apply_eligible"):
        print(f"  {k:<32} {s[k]}")
    print("  " + "-" * 40)
    for b in _BUCKETS:
        print(f"  {b:<32} {s[b]}")

    def _owner_line(o):
        rec = (f"  [RECONCILE src {o['source_person_id']}→{o['person_id']}]"
               if o["requires_reconciliation"] else "")
        return (f"    {o['role']:<24} person {o['person_id']} {o['name']}  "
                f"sc={o['source_contact_id']} sys={o['source_system']} "
                f"company='{o['company_name']}' title='{o['job_title']}' id_status={o['identity_status']}{rec}")

    def dump(bucket):
        rows = rep[bucket]
        print(f"\n=== {bucket} ({len(rows)}) ===")
        for rec in rows[:args.show]:
            print(f"- business {rec['business_id']}: {rec['business_name']}  "
                  f"apply_eligible={rec['apply_eligible']}")
            if rec["duplicate_of"]:
                print("    DUPLICATE OF: " + ", ".join(f"{d['id']}:{d['name']}" for d in rec["duplicate_of"]))
            for o in rec["owners"]:
                print(_owner_line(o))
            for a in rec["associations"]:
                print(_owner_line(a))
            for h in rec["household_associations"]:
                print(f"    HOUSEHOLD {h['household_id']} {h['household_name']} "
                      f"(doc_coownership={h['doc_coownership_count']}, via person {h['via_person_id']})")
            for rc in rec["reconciliations"]:
                print(f"    RECONCILE stale {rc['stale_person_id']} ({rc['stale_name']}) → "
                      f"canonical {rc['canonical_person_id']} ({rc['canonical_name']}) [{rc['reason']}]")
            for o in rec.get("identity_review", []):
                print(f"    IDENTITY_REVIEW {o['role']} person {o['person_id']} {o['name']} "
                      f"(status={o['identity_status']})")
            for n in rec["notes"]:
                print(f"    NOTE {n}")

    for b in _BUCKETS:
        dump(b)
    print("\nPREVIEW ONLY — no relationships/ownership written. Apply safe proposals separately via "
          "organization_service.record_ownership after review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
