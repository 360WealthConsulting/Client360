"""Wealthbox contacts/households migration CLI (Phase 1: inventory + preview only).

Writes manifest.json + reconciliation.csv + exceptions.csv + summary.txt. PREVIEW makes NO database
writes and moves no files. APPLY is intentionally not enabled in Phase 1 (raises) — it is unlocked only
after the preview is reviewed and approved.

Usage::
    python -m scripts.migration.wealthbox --inventory
    python -m scripts.migration.wealthbox --preview
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.wealthbox import WealthboxContactsMigration


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.wealthbox",
                                description="Wealthbox contacts/households migration (Phase 1).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--inventory", action="store_true", help="Read-only source inventory.")
    mode.add_argument("--preview", action="store_true", help="Read-only preview of what apply would create.")
    mode.add_argument("--apply", action="store_true",
                      help="DISABLED in Phase 1 — exits without creating an import_jobs row or any change.")
    args = p.parse_args(argv)

    job = WealthboxContactsMigration()
    selected = (Mode.APPLY if args.apply else Mode.INVENTORY if args.inventory else Mode.PREVIEW)
    print(f"Wealthbox migration — mode: {selected.value}")
    try:
        result = job.run(selected)
    except ModeNotSupported as exc:
        print(f"  DISABLED: {exc}")
        print("  (no import_jobs row created; no database changes were made)")
        return 2
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  exceptions: {len(result.exceptions)}")
    print(f"  artifacts:  {result.run_dir}")
    for n in result.notes:
        print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
