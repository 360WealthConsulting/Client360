"""Read-only inventory of every legacy source (Wealthbox / TaxDome / SharePoint / Scanner / existing docs).

Writes manifest.json + reconciliation.csv + exceptions.csv + summary.txt under the migration report root.
Makes NO Client360 writes, moves no files, and never scans C:\\From AWS Server.

Usage::
    python -m scripts.migration.inventory
    python -m scripts.migration.inventory --sources wealthbox taxdome sharepoint
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode
from app.services.migration.inventory import ALL_PROVIDERS, InventoryJob


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.inventory",
                                description="Read-only inventory of legacy migration sources.")
    p.add_argument("--sources", nargs="*", choices=sorted(ALL_PROVIDERS), default=None,
                   help="Limit to these sources (default: all).")
    args = p.parse_args(argv)

    print("Migration inventory — READ-ONLY (no writes, no file movement)")
    result = InventoryJob().run(Mode.INVENTORY, sources=args.sources)
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
