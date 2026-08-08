"""Canonical-population REPAIR CLI — PREVIEW (default) and guarded APPLY.

PREVIEW is read-only. APPLY writes the deterministic set ONLY and is triple-guarded: it needs --confirm,
a verified non-empty DB backup (--backup), and the approved expected counts (--expect-*). It recomputes
the plan live and fails closed before any write if a count differs from the approved preview. Document
linkage APPLY stays disabled.

Usage::
    python -m scripts.migration.repair_canonical_population --preview  <linkage-preview-dir>
    python -m scripts.migration.repair_canonical_population --apply    <linkage-preview-dir> \\
        --confirm --backup D:\\Client360\\SQLBackups\\pre-repair.dump \\
        --expect-promotions 74 --expect-links 2 --expect-households 6 --expect-businesses 239
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.canonical_repair import (
    CanonicalRepairJob,
    RepairGuardError,
    load_approved_set,
)
from app.services.migration.config import MigrationConfig


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.repair_canonical_population",
                                description="Canonical-population repair: PREVIEW (read-only) / guarded APPLY.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Read-only preview (default).")
    g.add_argument("--apply", action="store_true", help="Apply the deterministic set (guarded).")
    p.add_argument("preview_dir", help="Linkage preview directory (with exceptions.csv) for households.")
    p.add_argument("--approved", default=None,
                   help="Approved run directory (or reconciliation.csv) that FREEZES the repair scope. "
                        "Use for post-APPLY verification and re-APPLY so newly-eligible records never "
                        "expand the approved set.")
    p.add_argument("--confirm", action="store_true", help="Required for APPLY.")
    p.add_argument("--backup", default=None, help="Path to a verified non-empty pre-apply DB backup.")
    p.add_argument("--expect-promotions", type=int)
    p.add_argument("--expect-links", type=int)
    p.add_argument("--expect-households", type=int)
    p.add_argument("--expect-businesses", type=int)
    args = p.parse_args(argv)

    approved = load_approved_set(args.approved) if args.approved else None
    job = CanonicalRepairJob(MigrationConfig.from_env())
    if not args.apply:
        result = job.run(Mode.PREVIEW, preview_dir=args.preview_dir, approved=approved)
        _print(result)
        return 0

    expect_vals = [args.expect_promotions, args.expect_links, args.expect_households, args.expect_businesses]
    if not args.confirm or not args.backup or any(v is None for v in expect_vals):
        print("APPLY refused: requires --confirm, --backup <file>, and all four --expect-* counts.")
        return 2
    expect = {"promotions": args.expect_promotions, "links": args.expect_links,
              "households": args.expect_households, "businesses": args.expect_businesses}
    try:
        result = job.run(Mode.APPLY, preview_dir=args.preview_dir, confirm=True, backup=args.backup,
                         expect=expect, approved=approved, source_file=args.backup)
    except (RepairGuardError, ModeNotSupported) as exc:
        print(f"APPLY aborted (fail-closed): {exc}")
        return 2
    _print(result)
    return 0


def _print(result) -> None:
    print(f"Canonical repair — {result.mode} — {result.status}")
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")


if __name__ == "__main__":
    raise SystemExit(main())
