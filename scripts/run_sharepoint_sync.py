"""Manual Microsoft/SharePoint incremental sync (before enabling recurring scheduling).

Two modes:
  --manifest <path>   integrate an already-staged manifest (produced by the connector)
  --stage             stage live via the connector (Microsoft Graph, incremental) then integrate

Idempotent + incremental: unchanged items are skipped (no re-extract/OCR); new/changed documents flow
through the existing pipeline (OCR only if needed); deleted source items mark the reference unavailable
(never delete a canonical document). Records a run visible at /admin/ingestion. Never creates a client
or assigns ambiguous ownership.

Examples:
    python scripts/run_sharepoint_sync.py --manifest staged_items.json
    python scripts/run_sharepoint_sync.py --stage --site <site-id>
    python scripts/run_sharepoint_sync.py --manifest staged_items.json --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.microsoft_ingestion import resolve_sharepoint_stager, run_sharepoint_sync


def main(argv=None):
    ap = argparse.ArgumentParser(description="Manual SharePoint incremental sync.")
    ap.add_argument("--manifest", default=None, help="already-staged manifest JSON")
    ap.add_argument("--stage", action="store_true", help="stage live via the connector first")
    ap.add_argument("--site", action="append", default=None, help="site id(s) to stage (with --stage)")
    ap.add_argument("--drive-id", action="append", default=None,
                    help="override drive id(s) (else discovered from microsoft_drives)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--diagnose", action="store_true",
                    help="print READ-ONLY staging config/data diagnostics and exit (no Graph, no downloads)")
    args = ap.parse_args(argv)

    if args.diagnose:
        from app.services.microsoft_ingestion import sharepoint_staging_diagnostics
        print("SharePoint staging diagnostics (READ-ONLY, no Graph call):")
        for k, v in sharepoint_staging_diagnostics().items():
            print(f"  {k}: {v}")
        return 0

    diag = {}
    if args.manifest:
        items = json.loads(Path(args.manifest).read_text())
        summary = run_sharepoint_sync(items=items, trigger_source="manual", dry_run=args.dry_run)
    elif args.stage:
        # Adapts to the deployment connector's real staging entrypoint (no hard-coded function name).
        stager = resolve_sharepoint_stager(site_ids=args.site, drive_ids=args.drive_id,
                                           dry_run=args.dry_run, diag=diag)
        summary = run_sharepoint_sync(stager=stager, trigger_source="manual", dry_run=args.dry_run)
    else:
        ap.error("provide --manifest <path>, --stage, or --diagnose")
        return 2

    if diag:
        print("Staging diagnostics:")
        print(f"  entrypoint: {diag.get('entrypoint')}   staging_root: {diag.get('staging_root')}")
        print(f"  drives discovered: {diag.get('drive_count')}   drive_ids: {diag.get('drive_ids')}")
        print(f"  total staged items returned: {diag.get('total_items')}")
        for d in (diag.get("drives") or [])[:20]:
            print(f"    drive {d['drive_id']}: items={d['items']} source={d['source']} "
                  f"download={d['download']} manifest_exists={d['manifest_exists']} "
                  f"result_type={d['result_type']} manifest={d['manifest']}")

    print("SharePoint sync:", summary.get("status"))
    for k in ("items_examined", "canonical_created", "reused_canonical", "metadata_updated", "skipped",
              "deleted", "missing", "ocr_analyzed"):
        if k in summary:
            print(f"  {k}: {summary[k]}")
    if summary.get("errors"):
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return 1 if summary.get("status") in ("error", "completed_with_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
