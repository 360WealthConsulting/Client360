"""Read-only preview of a canonical person merge (MDM-1). Makes NO changes.

Usage:
    python scripts/preview_person_merge.py SURVIVOR_ID DUPLICATE_ID
"""
import json
import sys

from app.services.person_merge import preview_person_merge


def _fmt(report: dict) -> str:
    lines = []
    s, d = report.get("survivor"), report.get("duplicate")
    lines.append("=" * 72)
    lines.append(f"PERSON MERGE PREVIEW  (survivor {report['survivor_person_id']} "
                 f"← duplicate {report['duplicate_person_id']})   READ-ONLY, NO CHANGES")
    lines.append("=" * 72)
    lines.append(f"  SURVIVOR : {s}" if s else "  SURVIVOR : (not found)")
    lines.append(f"  DUPLICATE: {d}" if d else "  DUPLICATE: (not found)")
    lines.append(f"\n  Source links that would move: {report['source_links_would_move']}")

    lines.append("\n  Foreign-key rows referencing the duplicate:")
    fk = report["foreign_key_row_counts"]
    if fk:
        for k in sorted(fk):
            lines.append(f"    - {k}: {fk[k]}")
    else:
        lines.append("    (none)")

    lines.append("\n  Profile fields that would be filled on the survivor (from the duplicate):")
    pf = report["profile_fields_would_fill"]
    lines.extend([f"    - {k} = {v}" for k, v in pf.items()] or ["    (none)"])

    lines.append("\n  Deduplication actions:")
    lines.extend([f"    - {a['table']}.{a['column']}: reassign {a['reassign']}, "
                  f"consolidate {a['consolidate']}" for a in report["dedup_actions"]]
                 or ["    (none)"])

    if report["conflicts"]:
        lines.append("\n  Conflict-sensitive reassignments:")
        lines.extend([f"    - {c['table']}.{c['column']}: {c['action']} ({c['rows']})"
                      for c in report["conflicts"]])

    lines.append("\n  Warnings:")
    lines.extend([f"    - {w}" for w in report["warnings"]] or ["    (none)"])

    lines.append("\n  BLOCKERS:")
    lines.extend([f"    - {b}" for b in report["blockers"]] or ["    (none)"])

    lines.append(f"\n  SAFE TO MERGE: {report['safe_to_merge']}")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("Usage: python scripts/preview_person_merge.py SURVIVOR_ID DUPLICATE_ID")
        return 2
    try:
        survivor_id, duplicate_id = int(argv[0]), int(argv[1])
    except ValueError:
        print("SURVIVOR_ID and DUPLICATE_ID must be integers.")
        return 2
    report = preview_person_merge(survivor_id, duplicate_id)
    print(_fmt(report))
    if "--json" in argv:
        print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
