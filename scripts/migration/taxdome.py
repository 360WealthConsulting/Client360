"""TaxDome Document Migration CLI (Phase 2: PREVIEW only — the one-time TaxDome retirement pipeline).

PREVIEW is read-only: no database rows, no file copy/move, no changes. It writes migration_preview.csv,
migration_summary.txt, migration_manifest.json (plus the framework artifacts). APPLY is not built yet and
is refused until the preview is approved.

Usage::
    python -m scripts.migration.taxdome --preview
    python -m scripts.migration.taxdome --preview --source "C:\\...\\Documents\\TaxDome"
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.taxdome import TaxDomeDocumentMigration


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.taxdome",
                                description="TaxDome document migration (Phase 2: preview only).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Read-only preview (default).")
    mode.add_argument("--apply", action="store_true",
                      help="DISABLED until preview approval — exits without any change.")
    p.add_argument("--source", default=None, help="Override the TaxDome migration source root.")
    args = p.parse_args(argv)

    cfg = MigrationConfig.from_env()
    if args.source:
        cfg = dataclasses.replace(cfg, taxdome_migration_root=Path(args.source))
    job = TaxDomeDocumentMigration(cfg)
    selected = Mode.APPLY if args.apply else Mode.PREVIEW
    print(f"TaxDome document migration — mode: {selected.value}  (source: {cfg.taxdome_migration_root})")
    try:
        result = job.run(selected)
    except ModeNotSupported as exc:
        print(f"  DISABLED: {exc}")
        print("  (no database rows, no files copied/moved, no changes)")
        return 2
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (migration_preview.csv / migration_summary.txt / migration_manifest.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
