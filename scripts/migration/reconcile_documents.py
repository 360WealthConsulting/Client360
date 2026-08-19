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

Two scope modes (mutually exclusive):
  * generic ``--source-system`` — every document that carries a source_system reference (broad; for
    general reconciliation of a source);
  * baseline ``--uploaded-by`` + ``--created-from`` + ``--created-to`` — the EXACT records of one
    ingestion/recovery batch, scoped by ``documents.uploaded_by`` and a half-open ``created_at`` window
    ``[from, to)``. This does NOT join document_sources, so a broader source population can never widen it.

Usage::
    # BASELINE recovery scope (the verified 17,448 SharePoint recovery batch):
    python -m scripts.migration.reconcile_documents --expected 17448 \\
        --uploaded-by "SharePoint Sync" \\
        --created-from 2026-08-17T11:00:00-04:00 --created-to 2026-08-17T15:00:00-04:00

    # Generic source scope (broader; other reconciliation use cases):
    python -m scripts.migration.reconcile_documents --source-system SharePoint --expected 17448
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from app.services.migration.base import Mode
from app.services.migration.config import MigrationConfig
from app.services.migration.document_reconciliation import DocumentReconciliationJob

_HEADLINE = (
    "scope_mode", "baseline_uploaded_by", "baseline_created_from", "baseline_created_to",
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
    # --- baseline recovery scope (mutually exclusive with --source-system) --------------------------
    p.add_argument("--uploaded-by", default=None,
                   help="BASELINE scope: exact documents.uploaded_by (e.g. 'SharePoint Sync'). Requires "
                        "--created-from and --created-to. Not hard-coded; supply per recovery batch.")
    p.add_argument("--created-from", default=None,
                   help="BASELINE scope: inclusive lower bound for documents.created_at (ISO 8601 with "
                        "offset, e.g. 2026-08-17T11:00:00-04:00).")
    p.add_argument("--created-to", default=None,
                   help="BASELINE scope: EXCLUSIVE upper bound for documents.created_at (ISO 8601).")
    args = p.parse_args(argv)

    if any((args.uploaded_by, args.created_from, args.created_to)) and not all(
            (args.uploaded_by, args.created_from, args.created_to)):
        p.error("baseline scope requires --uploaded-by, --created-from, and --created-to together")
    created_from = datetime.fromisoformat(args.created_from) if args.created_from else None
    created_to = datetime.fromisoformat(args.created_to) if args.created_to else None

    config = MigrationConfig.from_env()
    if args.migration_root:
        import dataclasses
        from pathlib import Path
        config = dataclasses.replace(config, migration_root=Path(args.migration_root))  # frozen dataclass

    job = DocumentReconciliationJob(config)
    result = job.run(Mode.RECONCILE, sharepoint_source=args.source_system,
                     taxdome_source=args.taxdome_source, expected_population=args.expected,
                     owner_proposal_limit=args.owner_proposal_limit,
                     baseline_uploaded_by=args.uploaded_by, baseline_created_from=created_from,
                     baseline_created_to=created_to)

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
