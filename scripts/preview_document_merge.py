"""Read-only preview of canonical DOCUMENT consolidation (ADR-072). Makes NO changes.

Database-only: it reads documents, their stored SHA-256 hashes and their dependent rows. It never
touches the filesystem, a storage backend, SharePoint, TaxDome or OCR, and it never writes.

Usage:
    python scripts/preview_document_merge.py                # whole corpus
    python scripts/preview_document_merge.py --limit 50     # first 50 duplicate groups
    python scripts/preview_document_merge.py --json         # machine-readable
    python scripts/preview_document_merge.py --show 10      # per-group detail for the first 10

    python -m scripts.preview_document_merge --blocked-details
    python -m scripts.preview_document_merge --blocked-details --reason shared_content_cross_person
    python -m scripts.preview_document_merge --blocked-details --output-json /tmp/blocked.json
    python -m scripts.preview_document_merge --blocked-details --output-csv  /tmp/blocked.csv

Output files are written ONLY when a path is given, and never inside the repository by default.
All console output is plain ASCII so a CP1252 Windows console needs no PYTHONIOENCODING.
"""
import argparse
import csv
import json
import sys

from app.services.document_merge import (
    BLOCKED,
    DEFAULT_SAMPLE,
    DETAIL_REASON_CODES,
    REVIEW,
    SAFE,
    blocked_details,
    preview,
)


def _summary(r: dict) -> str:
    L = ["=" * 78,
         "CANONICAL DOCUMENT MERGE PREVIEW    READ-ONLY - NO CHANGES WERE MADE",
         "=" * 78,
         f"  survivor rule      : {r['survivor_rule']}",
         f"  eligibility        : documents.{r['eligibility']}",
         f"  dependencies known : {r['dependencies_checked']}",
         ""]
    if r["unregistered_dependencies"]:
        L.append(f"  !! UNREGISTERED DEPENDENCIES: {', '.join(r['unregistered_dependencies'])}")
        L.append("     Groups touching these are BLOCKED - add a strategy before consolidating.")
        L.append("")
    L += [f"  duplicate SHA groups          : {r['total_duplicate_groups']}",
          f"  document rows in those groups : {r['total_document_rows_in_groups']}",
          f"  excess duplicate rows         : {r['excess_duplicate_rows']}",
          "",
          f"  {SAFE:<18}: {r['safe_auto_merge_groups']}",
          f"  {REVIEW:<18}: {r['review_required_groups']}",
          f"  {BLOCKED:<18}: {r['blocked_groups']}",
          "",
          f"  proposed reassignments        : {r['total_proposed_reassignments']}",
          f"  rows eventually retired       : {r['total_rows_eventually_retired']}",
          f"  provenance rows seen          : {r['provenance_rows_seen']}",
          f"  provenance tuples preserved   : {r['provenance_tuples_preserved']}"]
    if r["reassignments_by_table"]:
        L.append("\n  Reassignments by dependent table:")
        for k, n in r["reassignments_by_table"].items():
            L.append(f"    - {k}: {n}")

    rr = r["reasons"]
    L.append("")
    L.append("  " + "-" * 74)
    L.append("  WHY THE NON-SAFE GROUPS ARE NOT SAFE")
    L.append("  " + "-" * 74)
    L.append("  'primary' is mutually exclusive (one per group, so totals reconcile);")
    L.append("  'containing' counts every group carrying the reason and therefore overlaps.")
    for label, rows in (("BLOCKERS  -> BLOCKED", rr["blockers"]),
                        ("CONFLICTS -> REVIEW_REQUIRED", rr["conflicts"]),
                        ("ADVISORIES (do not affect classification)", rr["advisories"])):
        L.append(f"\n  {label}")
        if not rows:
            L.append("    (none)")
        for row in rows:
            L.append(f"    {row['code']:<38} primary {row['groups_primary']:>6}   "
                     f"containing {row['groups_containing']:>6}")
            L.append(f"      {row['description']}")
            if row["example_document_ids"]:
                L.append(f"      example document ids: {row['example_document_ids']}")

    pt = rr["primary_totals"]
    L.append("")
    L.append(f"  primary BLOCKED total        : {pt['blocked']} (classified {r['blocked_groups']})")
    L.append(f"  primary REVIEW_REQUIRED total: {pt['review_required']} "
             f"(classified {r['review_required_groups']})")
    L.append(f"  reconciles                   : {'YES' if rr['reconciles'] else 'NO'}")
    if rr["unreported_codes"]:
        L.append(f"  !! codes with no taxonomy entry: {rr['unreported_codes']}")
    return "\n".join(L)


def _group(g: dict) -> str:
    L = ["", "-" * 78,
         f"  [{g['classification']}]  survivor {g['proposed_survivor']}  "
         f"<- duplicates {g['duplicate_document_ids']}",
         f"    rows {g['row_count']}  excess {g['excess_rows']}  "
         f"reassignments {g['total_reassignments']}",
         f"    provenance: {g['provenance']['rows']} rows -> "
         f"{g['provenance']['preserved_after_merge']} distinct tuples "
         f"({', '.join(g['provenance']['source_systems']) or 'none'})"]
    for b in g["blockers"]:
        L.append(f"    BLOCKER  {b['code']}: {b.get('detail', b['description'])}")
    for c in g["conflicts"]:
        L.append(f"    CONFLICT {c['code']}: {c.get('detail', c['description'])}")
    if g["reassignments_required"]:
        L.append("    would reassign:")
        for k, info in sorted(g["reassignments_required"].items()):
            L.append(f"      - {k}: {info['rows']} ({info['strategy']}, ON DELETE {info['delete_rule']})")
    return "\n".join(L)


def _owner_label(m) -> str:
    parts = []
    if m["person_id"]:
        parts.append(f"person {m['person_id']} ({m['person_name']})")
    if m["household_id"]:
        parts.append(f"household {m['household_id']} ({m['household_name']})")
    if m["organization_id"]:
        parts.append(f"organization {m['organization_id']} ({m['organization_name']})")
    return "; ".join(parts) or "(no owner)"


def _blocked_text(report, sample) -> str:
    su = report["summary"]
    L = ["=" * 78,
         "NON-MERGEABLE DETAIL      READ-ONLY - NO CHANGES WERE MADE",
         "=" * 78,
         f"  blocked groups (all)        : {su['blocked_groups_total']}",
         f"  shared-content groups (all) : {su['shared_content_groups_total']}",
         f"  groups in this view         : {su['blocked_groups_in_this_report']}",
         f"  rows preserved cross-owner  : {su['rows_preserved_cross_owner']}",
         f"  reasons included            : {', '.join(report['filter_reasons'])}",
         "",
         "  By group shape:"]
    for code, n in su["by_shape"].items():
        L.append(f"    {code:<40} {n:>6}")
    L += ["", "  By reason / shape:"]
    for code, b in su["by_reason"].items():
        L.append(f"    {code:<34} groups {b['groups']:>5}  rows {b['document_rows']:>6}  "
                 f"excess {b['excess_rows']:>6}")
    L += ["",
          f"  groups with >2 members      : {su['groups_with_more_than_2_members']}",
          f"  groups with >10 members     : {su['groups_with_more_than_10_members']}",
          f"  groups with >100 members    : {su['groups_with_more_than_100_members']}",
          f"  distinct source systems     : {', '.join(su['distinct_source_systems']) or '(none)'}",
          "",
          "  Distinct owners per group (owners -> group count):"]
    for owners, n in su["distinct_owners_per_group"].items():
        L.append(f"    {owners:>4} -> {n}")
    L += ["", "  Largest 20 groups by member count:"]
    for g in su["largest_20_groups"]:
        L.append(f"    {g['sha256'][:16]}...  members {g['member_count']:>5}  "
                 f"owners {g['distinct_owners']:>5}  {g['primary_reason']}")

    L += ["", "=" * 78, "PER-GROUP DETAIL", "=" * 78]
    for g in report["groups"]:
        shape = g["ownership_shape"]
        L += ["", "-" * 78,
              f"  sha256 {g['sha256']}",
              f"  {g['classification']}  shape {g['shape']}",
              f"  reason {g['primary_reason'] or '(none - no merge proposed)'}   "
              f"members {g['member_count']}   "
              f"excess {g['excess_rows']}   source rows {g['source_record_count']}",
              f"  merge partitions {g['merge_partition_count']}   "
              f"preserved rows {g['rows_preserved_cross_owner']}",
              f"  survivor {g['proposed_survivor'] if g['proposed_survivor'] is not None else '-'}"
              f"   "
              f"conflicting dimensions: {', '.join(g['conflicting_dimensions']) or '(none)'}",
              f"  distinct owners {shape['distinct_owners']}   "
              f"max members per owner {shape['max_members_per_owner']}   "
              f"unowned {shape['unowned_members']}   "
              f"distinct source uris {shape['distinct_source_uris']}"]
        for note in shape["evidence_notes"]:
            L.append(f"    evidence: {note}")
        shown = g["members"][:sample]
        L.append(f"  members (showing {len(shown)} of {g['member_count']}):")
        for m in shown:
            flag = ("SURVIVOR" if m["is_survivor"]
                    else ("duplicate" if m["proposed_for_retirement"] else "PRESERVED"))
            L.append(f"    [{flag}] doc {m['document_id']}  {m['original_name'] or '(no name)'}")
            L.append(f"        category={m['category'] or '-'}  "
                     f"classification={m['classification'] or '-'}")
            L.append(f"        owner: {_owner_label(m)}")
            for src in m["sources"][:sample]:
                L.append(f"        source: {src['source_system']} :: "
                         f"{src['source_uri'] or src['source_path'] or '(no locator)'}")
            if len(m["sources"]) > sample:
                L.append(f"        ... {len(m['sources']) - sample} more source row(s) "
                         f"(use --output-json for all)")
        if g["member_count"] > len(shown):
            L.append(f"    ... {g['member_count'] - len(shown)} more member(s) not shown "
                     f"(use --output-json / --output-csv for the complete set)")
    return "\n".join(L)


CSV_COLUMNS = (
    "sha256", "group_classification", "group_shape", "primary_reason",
    "conflicting_dimensions", "member_count", "excess_rows",
    "group_merge_partitions", "group_rows_preserved",
    "proposed_survivor", "document_id", "is_survivor", "proposed_for_retirement", "preserved",
    "original_name", "category",
    "classification", "subcategory", "status",
    "person_id", "person_name", "household_id", "household_name",
    "organization_id", "organization_name",
    "source_systems", "source_uris", "source_record_count",
    "group_distinct_owners", "group_max_members_per_owner", "group_unowned_members",
    "group_distinct_source_uris", "group_evidence_notes",
)


def _csv_rows(report):
    """One row per MEMBER document, carrying its group's context - enough to classify later."""
    for g in report["groups"]:
        shape = g["ownership_shape"]
        for m in g["members"]:
            yield {
                "sha256": g["sha256"], "group_classification": g["classification"],
                "group_shape": g["shape"], "primary_reason": g["primary_reason"],
                "conflicting_dimensions": "|".join(g["conflicting_dimensions"]),
                "member_count": g["member_count"], "excess_rows": g["excess_rows"],
                "group_merge_partitions": g["merge_partition_count"],
                "group_rows_preserved": g["rows_preserved_cross_owner"],
                "proposed_survivor": g["proposed_survivor"],
                "document_id": m["document_id"], "is_survivor": m["is_survivor"],
                "proposed_for_retirement": m["proposed_for_retirement"],
                "preserved": m["preserved"],
                "original_name": m["original_name"], "category": m["category"],
                "classification": m["classification"], "subcategory": m["subcategory"],
                "status": m["status"],
                "person_id": m["person_id"], "person_name": m["person_name"],
                "household_id": m["household_id"], "household_name": m["household_name"],
                "organization_id": m["organization_id"],
                "organization_name": m["organization_name"],
                "source_systems": "|".join(s["source_system"] for s in m["sources"]),
                "source_uris": "|".join(s["source_uri"] or s["source_path"] or ""
                                        for s in m["sources"]),
                "source_record_count": len(m["sources"]),
                "group_distinct_owners": shape["distinct_owners"],
                "group_max_members_per_owner": shape["max_members_per_owner"],
                "group_unowned_members": shape["unowned_members"],
                "group_distinct_source_uris": shape["distinct_source_uris"],
                "group_evidence_notes": " | ".join(shape["evidence_notes"]),
            }


def _write_csv(path, report) -> int:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        n = 0
        for row in _csv_rows(report):
            writer.writerow(row)
            n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only canonical document merge preview.")
    ap.add_argument("--limit", type=int, default=None, help="only the first N duplicate groups")
    ap.add_argument("--show", type=int, default=0, help="print per-group detail for the first N")
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    ap.add_argument("--blocked-details", action="store_true",
                    help="per-group detail for every BLOCKED and SHARED_CONTENT group "
                         "(read-only)")
    ap.add_argument("--reason", action="append", choices=sorted(DETAIL_REASON_CODES),
                    help="restrict --blocked-details to groups whose primary reason OR shape "
                         "is this code (repeatable)")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help="members/sources shown per group in text output")
    ap.add_argument("--output-json", metavar="PATH",
                    help="write the complete report to PATH (nothing is written without this)")
    ap.add_argument("--output-csv", metavar="PATH",
                    help="write one row per member document to PATH")
    args = ap.parse_args(argv)

    if args.blocked_details:
        report = blocked_details(reasons=args.reason, limit=args.limit)
        if args.output_json:
            with open(args.output_json, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, default=str)
            print(f"wrote JSON report: {args.output_json}")
        if args.output_csv:
            n = _write_csv(args.output_csv, report)
            print(f"wrote CSV report: {args.output_csv} ({n} member rows)")
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        elif not (args.output_json or args.output_csv):
            print(_blocked_text(report, args.sample))
        print("\n  (read-only - no document, row, file or migration was changed)")
        return 0

    report = preview(limit=args.limit)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    print(_summary(report))
    for g in report["groups"][:args.show]:
        print(_group(g))
    print("\n  (preview only - no document, row, file or migration was changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
