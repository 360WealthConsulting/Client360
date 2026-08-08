"""Canonical-population remediation CLI (READ-ONLY preview).

Classifies every unlinked source_contact into a deterministic proposed action and projects how many
linkage-exception folders would become resolvable after the repair. Writes manifest.json /
reconciliation.csv / exceptions.csv / summary.txt. It creates nothing, links nothing, moves no bytes, and
changes no rows.

Usage::
    python -m scripts.migration.preview_canonical_population [<linkage-preview-directory>]

Passing the linkage preview directory (the one containing exceptions.csv) enables the household-candidate
and folder-resolvability projections.
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.canonical_population import CanonicalPopulationPreviewJob
from app.services.migration.config import MigrationConfig


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.preview_canonical_population",
                                description="Read-only canonical-population remediation preview.")
    p.add_argument("preview_dir", nargs="?", default=None,
                   help="Linkage preview directory (with exceptions.csv) for folder/household projection.")
    args = p.parse_args(argv)
    job = CanonicalPopulationPreviewJob(MigrationConfig.from_env())
    print("Canonical-population remediation — mode: preview")
    try:
        result = job.run(Mode.PREVIEW, preview_dir=args.preview_dir)
    except ModeNotSupported as exc:
        print(f"  DISABLED: {exc}")
        return 2
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
