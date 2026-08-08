"""Canonical Linkage Remediation CLI (READ-ONLY preview).

Analyzes documents whose canonical link (person/household/organization) is unset and proposes an existing
canonical entity for each, matched from its source folder. Strict/exact matching only; ambiguous and
no-match folders go to Review/Unresolved. Writes manifest.json / reconciliation.csv / exceptions.csv /
summary.txt. It creates nothing, moves no bytes, and changes no rows.

Usage::
    python -m scripts.migration.remediate_linkage --preview
"""
from __future__ import annotations

import sys

from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.linkage import LinkageRemediationJob


def main(argv=None) -> int:
    import argparse

    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.remediate_linkage",
                                description="Canonical linkage remediation (read-only preview).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Read-only remediation preview (default).")
    g.add_argument("--apply", action="store_true", help="DISABLED — exits without any change.")
    args = p.parse_args(argv)

    job = LinkageRemediationJob(MigrationConfig.from_env())
    mode = Mode.APPLY if args.apply else Mode.PREVIEW
    print(f"Canonical linkage remediation — mode: {mode.value}")
    try:
        result = job.run(mode)
    except ModeNotSupported as exc:
        print(f"  DISABLED: {exc}")
        print("  (no rows changed, no entities created, no bytes moved)")
        return 2
    print("-" * 60)
    for k, v in result.counts.items():
        print(f"  {k}: {v}")
    print(f"  artifacts: {result.run_dir}")
    print("  (manifest.json / reconciliation.csv / exceptions.csv / summary.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
