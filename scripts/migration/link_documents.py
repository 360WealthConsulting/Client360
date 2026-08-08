"""Document canonical-link CLI — PREVIEW (default) and guarded APPLY.

Sets documents.person_id / household_id / organization_id for the APPROVED deterministic linkage set only,
frozen to a linkage preview's reconciliation.csv. It never moves bytes, changes storage_uri, or touches
document_sources; ambiguous/unmatched documents are never touched. Document linkage APPLY is triple-guarded
(confirm + verified backup + approved expected counts) and fails closed on any drift.

Usage::
    python -m scripts.migration.link_documents --preview --approved <linkage-preview-dir>
    python -m scripts.migration.link_documents --apply   --approved <linkage-preview-dir> \\
        --confirm --backup D:\\Client360\\SQLBackups\\pre-doclink.dump \\
        --expect-people 204 --expect-households 319 --expect-businesses 63
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.document_link import (
    DocumentLinkJob,
    RepairGuardError,
    load_approved_doc_links,
)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.link_documents",
                                description="Document canonical-link: PREVIEW (read-only) / guarded APPLY.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Read-only preview (default).")
    g.add_argument("--apply", action="store_true", help="Apply the frozen deterministic link set (guarded).")
    p.add_argument("--approved", required=True,
                   help="Linkage preview directory (or reconciliation.csv) that FREEZES the link scope.")
    p.add_argument("--confirm", action="store_true", help="Required for APPLY.")
    p.add_argument("--backup", default=None, help="Path to a verified non-empty pre-apply DB backup.")
    p.add_argument("--expect-people", type=int)
    p.add_argument("--expect-households", type=int)
    p.add_argument("--expect-businesses", type=int)
    args = p.parse_args(argv)

    approved = load_approved_doc_links(args.approved)
    job = DocumentLinkJob(MigrationConfig.from_env())
    if not args.apply:
        _print(job.run(Mode.PREVIEW, approved=approved))
        return 0

    expect_vals = [args.expect_people, args.expect_households, args.expect_businesses]
    if not args.confirm or not args.backup or any(v is None for v in expect_vals):
        print("APPLY refused: requires --confirm, --backup <file>, and all three --expect-* counts.")
        return 2
    expect = {"people": args.expect_people, "households": args.expect_households,
              "businesses": args.expect_businesses}
    try:
        result = job.run(Mode.APPLY, approved=approved, confirm=True, backup=args.backup,
                         expect=expect, source_file=args.backup)
    except (RepairGuardError, ModeNotSupported) as exc:
        print(f"APPLY aborted (fail-closed): {exc}")
        return 2
    _print(result)
    return 0


def _print(result) -> None:
    print(f"Document link — {result.mode} — {result.status}")
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")


if __name__ == "__main__":
    raise SystemExit(main())
