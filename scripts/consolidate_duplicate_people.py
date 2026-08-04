"""MDM-2 — consolidate duplicate canonical people via the MDM-1 merge engine.

Default is PREVIEW (no database changes). ``--apply`` performs merges through merge_people() for every
safe, unambiguous group; it is resumable (already-merged / vanished duplicates are skipped). Ambiguous
and blocked groups are always skipped. A CSV report is written for both modes.

Usage:
    python scripts/consolidate_duplicate_people.py                 # preview (default)
    python scripts/consolidate_duplicate_people.py --preview
    python scripts/consolidate_duplicate_people.py --apply
    python scripts/consolidate_duplicate_people.py --apply --report reports/mdm2_merge_report.csv
"""
import argparse
import sys

from app.services.mdm import consolidator


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python scripts/consolidate_duplicate_people.py",
                                description="Consolidate duplicate canonical people (MDM-2).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Report only, no changes (default).")
    mode.add_argument("--apply", action="store_true", help="Merge every safe, unambiguous group.")
    mode.add_argument("--apply-clear-only", dest="apply_clear_only", action="store_true",
                      help="Strict first pass: merge only clear groups (materially-stronger survivor, "
                           "all duplicates empty shells, no warnings/blockers).")
    mode.add_argument("--preview-approved", dest="preview_approved", action="store_true",
                      help="Preview a human-approved merge of one group (needs --group + "
                           "--survivor-person-id). No changes.")
    mode.add_argument("--apply-approved", dest="apply_approved", action="store_true",
                      help="Apply a human-approved merge of one manually-reviewed group (needs --group "
                           "+ --survivor-person-id). Runs the engine per pair; refuses on any "
                           "blocker/warning/conflict/unexpected evidence.")
    p.add_argument("--group", default=None, help="Limit to one exact (normalized) group name.")
    p.add_argument("--person-id", type=int, default=None, help="Limit to the group containing this id.")
    p.add_argument("--survivor-person-id", dest="survivor_person_id", type=int, default=None,
                   help="Explicit survivor for approved mode.")
    p.add_argument("--report", default="reports/mdm2_merge_report.csv", help="Pair-level CSV path.")
    p.add_argument("--group-summary", default="reports/mdm2_group_summary.csv",
                   help="Group-level CSV path.")
    args = p.parse_args(argv)

    apply = bool(args.apply)
    clear_only = bool(args.apply_clear_only)
    approved = bool(args.apply_approved or args.preview_approved)
    approved_apply = bool(args.apply_approved)
    scope = (f" group='{args.group}'" if args.group else "") + (
        f" person_id={args.person_id}" if args.person_id else "") + (
        f" survivor={args.survivor_person_id}" if args.survivor_person_id else "")

    if approved:
        label = "APPLY (human-approved)" if approved_apply else "PREVIEW (human-approved)"
        print(f"MDM-2 consolidation — mode: {label}{scope}")
        try:
            summary = consolidator.approved_merge(
                group_name=args.group, survivor_person_id=args.survivor_person_id,
                apply=approved_apply, report_path=args.report, group_summary_path=args.group_summary,
                progress=lambda m: print("  " + m))
        except consolidator.MergeBlocked as exc:
            print(f"  REFUSED: {exc}")
            print("  (APPLY COMPLETE — no database changes were made)")
            return 2
    else:
        label = ("APPLY (clear-only)" if clear_only else "APPLY" if apply else "PREVIEW (no changes)")
        print(f"MDM-2 consolidation — mode: {label}{scope}")
        summary = consolidator.consolidate(
            apply=apply, apply_clear_only=clear_only, group_name=args.group, person_id=args.person_id,
            report_path=args.report, group_summary_path=args.group_summary,
            progress=lambda m: print("  " + m))

    print("-" * 60)
    for k in ("groups", "merged", "skipped", "ambiguous", "blocked", "failed"):
        print(f"  {k}: {summary.get(k, 0)}")
    if not approved:
        print(f"  clear_only_qualified: {summary.get('clear_only_qualified', 0)}")
    print(f"  pair report:  {summary.get('report_path')}")
    print(f"  group report: {summary.get('group_summary_path')}")
    print(f"  automatic-survivor rules: {consolidator.AUTOMATIC_SURVIVOR_RULE_COUNT}")
    # Footer reflects ACTUAL writes: an apply that merged nothing made no changes.
    did_apply = apply or clear_only or approved_apply
    if not did_apply:
        print("  (PREVIEW — no database changes were made)")
    elif summary.get("merged", 0) > 0:
        print("  (APPLY COMPLETE — database changes were made)")
    else:
        print("  (APPLY COMPLETE — no database changes were made)")
    return 1 if summary.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
