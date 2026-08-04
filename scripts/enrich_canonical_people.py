"""MDM-2 — backfill canonical people identity fields from linked source_contacts.

Default is PREVIEW (no changes). ``--apply`` fills only null canonical fields from a single unambiguous
linked-source value (never overwrites; skips conflicts; idempotent). Scope with --person-id / --group.

Usage:
    python scripts/enrich_canonical_people.py --preview --person-id 5265
    python scripts/enrich_canonical_people.py --apply   --person-id 5265
    python scripts/enrich_canonical_people.py --preview --group "austin weaver"
    python scripts/enrich_canonical_people.py --apply
"""
import argparse
import sys

from app.services.mdm import profile_enrichment


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python scripts/enrich_canonical_people.py",
                                description="Backfill canonical people fields from source contacts.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Report only, no changes (default).")
    mode.add_argument("--apply", action="store_true", help="Fill null canonical fields.")
    p.add_argument("--person-id", type=int, default=None, help="Limit to one person.")
    p.add_argument("--group", default=None, help="Limit to an exact (normalized) group name.")
    p.add_argument("--report", default="reports/mdm2_enrichment_report.csv", help="CSV report path.")
    args = p.parse_args(argv)

    apply = bool(args.apply)
    scope = (f" person_id={args.person_id}" if args.person_id else "") + (
        f" group='{args.group}'" if args.group else "")
    print(f"MDM-2 profile enrichment — mode: {'APPLY' if apply else 'PREVIEW (no changes)'}{scope}")
    summary = profile_enrichment.enrich_people(
        apply=apply, person_id=args.person_id, group_name=args.group, report_path=args.report,
        progress=lambda m: print("  " + m))

    print("-" * 60)
    would = [r for r in summary["rows"] if r["status"] == "would_fill"]
    for r in (would if not apply else [x for x in summary["rows"] if x["status"] == "filled"]):
        print(f"  person {r['person_id']}: {r['field']} = {r['proposed_value']} "
              f"(from source_contact {r['source_contact_id']})")
    for k in ("people", "fields_filled", "people_enriched", "conflicts", "already_set", "no_source"):
        print(f"  {k}: {summary[k]}")
    print(f"  report: {summary.get('report_path')}")
    if apply:
        print("  (APPLY COMPLETE — database changes were made)")
    else:
        print("  (PREVIEW — no database changes were made)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
