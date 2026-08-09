"""Repository Relocation CLI — PREVIEW / RECONCILE (read-only) and guarded APPLY.

PREVIEW plans the human-readable destination for every document; RECONCILE checks current placement. Both
are read-only. APPLY relocates ONLY the approved owned documents (Clients/Households/Businesses), frozen to
a relocation preview's reconciliation.csv: it copies each file to D:\\Client360\\Content, verifies SHA-256 +
size, then repoints storage_uri — never deleting the source, never touching document_sources, and never
relocating Firm/unfiled or missing_source documents. APPLY is triple-guarded (confirm + verified backup +
approved expected per-area counts) and fails closed on any drift/collision/missing source.

Usage::
    python -m scripts.migration.relocate --preview
    python -m scripts.migration.relocate --apply --approved <relocation-preview-dir> \\
        --confirm --backup D:\\Client360\\SQLBackups\\pre-relocation.dump \\
        --expect-clients 16472 --expect-households 509 --expect-businesses 63
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.relocation import (
    RepairGuardError,
    RepositoryRelocationJob,
    load_approved_relocation,
)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.relocate",
                                description="Repository Relocation: read-only preview/reconcile / guarded apply.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Read-only preview of planned destinations (default).")
    g.add_argument("--reconcile", action="store_true", help="Read-only reconciliation of current placement.")
    g.add_argument("--apply", action="store_true", help="Relocate the approved owned documents (guarded).")
    p.add_argument("--approved", default=None, help="Relocation preview dir (or reconciliation.csv) freezing scope.")
    p.add_argument("--confirm", action="store_true", help="Required for APPLY.")
    p.add_argument("--backup", default=None, help="Path to a verified non-empty pre-apply DB backup.")
    p.add_argument("--expect-clients", type=int)
    p.add_argument("--expect-households", type=int)
    p.add_argument("--expect-businesses", type=int)
    args = p.parse_args(argv)

    cfg = MigrationConfig.from_env()
    job = RepositoryRelocationJob(cfg)

    if args.apply:
        expect_vals = [args.expect_clients, args.expect_households, args.expect_businesses]
        if not args.approved or not args.confirm or not args.backup or any(v is None for v in expect_vals):
            print("APPLY refused: requires --approved <dir>, --confirm, --backup <file>, and all three "
                  "--expect-clients/--expect-households/--expect-businesses counts.")
            return 2
        expect = {"Clients": args.expect_clients, "Households": args.expect_households,
                  "Businesses": args.expect_businesses}
        approved = load_approved_relocation(args.approved)
        print(f"Repository Relocation — mode: apply  (destination: {cfg.migration_dest_root})")
        try:
            result = job.run(Mode.APPLY, approved=approved, confirm=True, backup=args.backup,
                             expect=expect, source_file=args.backup)
        except (RepairGuardError, ModeNotSupported) as exc:
            print(f"APPLY aborted (fail-closed): {exc}")
            return 2
        _print(result, cfg)
        return 0

    mode = Mode.RECONCILE if args.reconcile else Mode.PREVIEW
    print(f"Repository Relocation — mode: {mode.value}  (destination: {cfg.migration_dest_root})")
    result = job.run(mode)
    _print(result, cfg)
    return 0


def _print(result, cfg) -> None:
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")


if __name__ == "__main__":
    raise SystemExit(main())
