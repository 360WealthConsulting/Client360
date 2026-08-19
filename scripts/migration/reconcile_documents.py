"""Post-OCR document reconciliation CLI — READ-ONLY (TaxDome exit).

Runs :class:`app.services.migration.document_reconciliation.DocumentReconciliationJob` in RECONCILE mode:
it only SELECTs and writes the standard four artifacts (manifest.json / reconciliation.csv /
exceptions.csv / summary.txt) under the migration report directory. It NEVER mutates documents,
document_ocr, document_sources, ownership, source_contacts, or migration state — and never runs the OCR
job.

The expected population is EXPLICIT (``--expected``), never a hard-coded constant. For the current
recovery pass it will be 17448 once the SharePoint baseline OCR run completes. If the scoped population
does not equal ``--expected`` (or the OCR buckets do not reconcile to the scoped population), the run
prints FAILED and exits non-zero — it never reports a silent success.

Usage::
    # After the baseline OCR finishes (staging restore, never live production):
    python -m scripts.migration.reconcile_documents --source-system SharePoint --expected 17448

    # Optional bounded ownership deep-pass (reads file bytes; keep small while OCR/disk is busy):
    python -m scripts.migration.reconcile_documents --expected 17448 --owner-proposal-limit 500
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.base import Mode
from app.services.migration.config import MigrationConfig
from app.services.migration.document_reconciliation import DocumentReconciliationJob

_HEADLINE = (
    "scoped_population", "expected_population", "population_difference", "invariant_ok",
    "reconciliation_status", "ocr_completed", "ocr_failed", "ocr_timed_out", "ocr_unsupported",
    "ocr_password_required", "ocr_skipped", "ocr_pending", "ownership_exceptions",
    "duplicate_exceptions", "source_integrity_exceptions", "searchability_exceptions",
    "taxdome_exit_exceptions", "total_operator_review_exceptions",
)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(
        prog="python -m scripts.migration.reconcile_documents",
        description="Read-only post-OCR document reconciliation (SharePoint baseline / TaxDome exit).")
    p.add_argument("--source-system", default="SharePoint",
                   help="Source system that scopes the population (default: SharePoint).")
    p.add_argument("--taxdome-source", default="TaxDome",
                   help="Source system label for TaxDome-exit categories (default: TaxDome).")
    p.add_argument("--expected", type=int, default=None,
                   help="EXPLICIT expected population (e.g. 17448). If omitted, the population invariant "
                        "is reported but NOT enforced.")
    p.add_argument("--owner-proposal-limit", type=int, default=0,
                   help="Cap for the bounded ownership deep-pass (0 = skip; reuses document_owner_proposal, "
                        "reads file bytes, never triggers live OCR).")
    p.add_argument("--migration-root", default=None,
                   help="Override the migration report root (else CLIENT360_MIGRATION_ROOT / 'Migration').")
    args = p.parse_args(argv)

    config = MigrationConfig.from_env()
    if args.migration_root:
        import dataclasses
        from pathlib import Path
        config = dataclasses.replace(config, migration_root=Path(args.migration_root))  # frozen dataclass

    job = DocumentReconciliationJob(config)
    result = job.run(Mode.RECONCILE, sharepoint_source=args.source_system,
                     taxdome_source=args.taxdome_source, expected_population=args.expected,
                     owner_proposal_limit=args.owner_proposal_limit)

    c = result.counts
    print(f"reconciliation run: {result.run_dir}")
    print(f"status: {result.status}  |  invariant: {c.get('reconciliation_status')}")
    for k in _HEADLINE:
        if k in c:
            print(f"  {k}: {c[k]}")
    print(f"artifacts: manifest.json, reconciliation.csv, exceptions.csv, summary.txt in {result.run_dir}")

    # Fail closed: a population/bucket invariant failure exits non-zero so it can never read as success.
    return 0 if c.get("invariant_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
