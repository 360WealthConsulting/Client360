"""Targeted SharePoint re-stage recovery for baseline OCR failures with a MISSING local source file.

Sibling to ``scripts/recover_sharepoint_baseline_ocr.py`` but deliberately NARROWER: that script
re-runs the OCR loop over the whole verified baseline; THIS one does not run OCR at all. It selects
ONLY the subset of the baseline whose OCR failed because the local source file went missing
(``document_ocr.status='failed'`` with a source-file-not-found ``last_error``), re-stages each file
from SharePoint via the existing hardened connector, verifies it against ``source_hash``, and hands
it to the existing SHA-verified ``backfill_local_source`` — which updates ONLY
``documents.storage_uri`` / ``storage_path`` (the fields OCR resolves through). It NEVER touches
ownership, NEVER writes ``document_sources``, NEVER re-runs OCR, and NEVER overwrites a good file.

Preview is the default and has ZERO side effects (no network, no writes). ``--apply`` is guarded by a
required ``--expect-candidates N`` that must equal the scoped count before anything is downloaded.

Usage::

    # PREVIEW (zero side effects) — classify what apply would do, write a manifest:
    python scripts/restage_missing_source_ocr.py --manifest /tmp/restage_preview.json

    # APPLY — re-stage + verify + backfill (requires the count gate to match):
    python scripts/restage_missing_source_ocr.py --apply --expect-candidates 3108 \\
        --manifest /tmp/restage_apply.json

The expected next step is a SCOPED OCR retry over ONLY the recovered document ids — this tool never
triggers OCR itself.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from app.services.sharepoint_restage import (
    SOURCE_NOT_FOUND_MARKER,
    run_restage,
    select_candidates,
)

# The verified baseline scope (same window as scripts/recover_sharepoint_baseline_ocr.py). These are
# defaults for convenience, NOT hard-coded constants baked into the engine — every value is overridable
# and the effective scope is printed on every run.
DEFAULT_UPLOADED_BY = "SharePoint Sync"
DEFAULT_CREATED_FROM = "2026-08-17T11:00:00-04:00"
DEFAULT_CREATED_TO = "2026-08-17T15:00:00-04:00"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python scripts/restage_missing_source_ocr.py",
        description="Re-stage missing SharePoint source files for baseline OCR failures (preview by default).")
    ap.add_argument("--uploaded-by", default=DEFAULT_UPLOADED_BY,
                    help=f"Baseline documents.uploaded_by (default: '{DEFAULT_UPLOADED_BY}').")
    ap.add_argument("--created-from", default=DEFAULT_CREATED_FROM,
                    help="Inclusive lower bound for documents.created_at (ISO 8601 with offset).")
    ap.add_argument("--created-to", default=DEFAULT_CREATED_TO,
                    help="EXCLUSIVE upper bound for documents.created_at (ISO 8601 with offset).")
    ap.add_argument("--marker", default=SOURCE_NOT_FOUND_MARKER,
                    help="Substring that scopes source-file-not-found failures in document_ocr.last_error.")
    ap.add_argument("--apply", action="store_true",
                    help="Perform the recovery (download + verify + backfill). Requires --expect-candidates.")
    ap.add_argument("--expect-candidates", type=int, default=None,
                    help="Required confirmation for --apply: must equal the scoped candidate count.")
    ap.add_argument("--staging-root", default=None,
                    help="Staging root (else CLIENT360_SHAREPOINT_STAGING_ROOT; fail-closed if unset).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process at most N candidates (diagnostics; the count gate still checks the full scope).")
    ap.add_argument("--manifest", default=None,
                    help="Write the full per-document manifest JSON to this path.")
    args = ap.parse_args(argv)

    created_from = datetime.fromisoformat(args.created_from)
    created_to = datetime.fromisoformat(args.created_to)

    print("SharePoint re-stage recovery — scope (READ-ONLY selection):")
    print(f"  uploaded_by   '{args.uploaded_by}'")
    print(f"  created_at    {created_from.isoformat()} <= created_at < {created_to.isoformat()}")
    print(f"  marker        last_error contains '{args.marker}'")

    # Count gate: independently determine the scoped candidate count before any apply.
    from app.db import engine
    with engine.connect() as conn:
        scoped = len(select_candidates(conn, uploaded_by=args.uploaded_by, created_from=created_from,
                                       created_to=created_to, marker=args.marker))
    print(f"  candidates    {scoped}")

    if args.apply:
        if args.expect_candidates is None or args.expect_candidates != scoped:
            print(f"REFUSING to apply: --apply requires --expect-candidates {scoped} "
                  f"(got {args.expect_candidates}). Nothing downloaded, nothing changed.")
            return 2

    connector = None  # built lazily inside run_restage only when apply actually needs the network

    report = run_restage(uploaded_by=args.uploaded_by, created_from=created_from, created_to=created_to,
                         marker=args.marker, apply=args.apply, staging_root=args.staging_root,
                         connector=connector, limit=args.limit)

    print()
    print(f"result: {report.summary_line()}")
    if report.staging_root:
        print(f"staging_root: {report.staging_root}")
    if args.manifest:
        path = report.write_manifest(args.manifest)
        print(f"manifest: {path}")
    if not args.apply:
        planned = report.counts.get("planned", 0)
        print(f"\npreview only — no network, no writes. {planned} document(s) would be re-staged on --apply.")
        print("next: re-run with --apply --expect-candidates <count>, then a SCOPED OCR retry over the "
              "recovered ids (this tool never runs OCR).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
