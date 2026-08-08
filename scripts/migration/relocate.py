"""Repository Relocation CLI (Phase: PREVIEW + RECONCILE — read-only).

Relocates existing canonical documents' bytes into the Client360 repository. This phase is read-only: it
plans destinations and reconciles current placement, writing manifest.json / reconciliation.csv /
exceptions.csv / summary.txt. APPLY and ROLLBACK are disabled (fail-closed) until the preview is approved.

Usage::
    python -m scripts.migration.relocate --preview
    python -m scripts.migration.relocate --reconcile
"""
from __future__ import annotations

import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.relocation import RepositoryRelocationJob


def main(argv=None) -> int:
    import argparse

    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.relocate",
                                description="Repository Relocation (read-only preview/reconcile phase).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Read-only preview of planned destinations (default).")
    g.add_argument("--reconcile", action="store_true", help="Read-only reconciliation of current placement.")
    g.add_argument("--apply", action="store_true", help="DISABLED this phase — exits without any change.")
    args = p.parse_args(argv)

    cfg = MigrationConfig.from_env()
    job = RepositoryRelocationJob(cfg)
    mode = Mode.RECONCILE if args.reconcile else (Mode.APPLY if args.apply else Mode.PREVIEW)
    print(f"Repository Relocation — mode: {mode.value}  (destination: {cfg.migration_dest_root})")
    try:
        result = job.run(mode)
    except ModeNotSupported as exc:
        print(f"  DISABLED: {exc}")
        print("  (no bytes copied, no storage_uri changed, no rows modified)")
        return 2
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
