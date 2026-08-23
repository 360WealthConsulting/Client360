"""PREVIEW-ONLY report of derivable business relationships (READ-ONLY; writes nothing).

    python scripts/business_resolution_preview.py            # full preview
    python scripts/business_resolution_preview.py --business 135   # one business (e.g. Pullen)

Apply is intentionally NOT implemented here — safe proposals are applied separately via
``organization_service.record_ownership`` after human review.
"""
from __future__ import annotations

import argparse

from app.services.business_resolution import resolve_business_relationships


def main(argv=None):
    ap = argparse.ArgumentParser(description="Business relationship resolution PREVIEW (read-only).")
    ap.add_argument("--business", action="append", type=int, default=None,
                    help="restrict to specific business relationship_entities id(s)")
    ap.add_argument("--show", type=int, default=25, help="max detail rows per bucket to print")
    args = ap.parse_args(argv)

    rep = resolve_business_relationships(business_ids=args.business)
    s = rep["summary"]
    print("BUSINESS RELATIONSHIP RESOLUTION — PREVIEW (read-only, nothing written)")
    for k in ("businesses_evaluated", "exact_company_matches", "owner_role_matches",
              "household_associations", "reconciliations_required", "safe", "ambiguous", "unresolved"):
        print(f"  {k:<24} {s[k]}")

    def dump(title, bucket):
        print(f"\n=== {title} ({len(bucket)}) ===")
        for rec in bucket[:args.show]:
            print(f"- business {rec['business_id']}: {rec['business_name']}  "
                  f"(company_matches={rec['company_matches']})")
            for o in rec["owners"]:
                flag = "  [RECONCILE " + str(o["source_person_id"]) + "→" + str(o["person_id"]) + "]" \
                    if o["requires_reconciliation"] else ""
                print(f"    OWNER  person {o['person_id']} {o['name']} title={o['job_title']}{flag}")
            for a in rec["associations"]:
                print(f"    ASSOC  person {a['person_id']} {a['name']} title={a['job_title']}")
            for h in rec["household_associations"]:
                print(f"    HOUSEHOLD {h['household_id']} {h['household_name']} "
                      f"(doc_coownership={h['doc_coownership_count']})")
            for rc in rec["reconciliations"]:
                print(f"    RECONCILE stale {rc['stale_person_id']} ({rc['stale_name']}) "
                      f"→ canonical {rc['canonical_person_id']} ({rc['canonical_name']}) [{rc['reason']}]")
            for n in rec["notes"]:
                print(f"    NOTE {n}")

    dump("SAFE (reviewed apply candidates)", rep["safe"])
    dump("AMBIGUOUS (needs human review)", rep["ambiguous"])
    print(f"\nUNRESOLVED businesses: {len(rep['unresolved'])} (no structured CRM evidence)")
    print("\nPREVIEW ONLY — no relationships/ownership written. Apply via organization_service.record_ownership after review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
