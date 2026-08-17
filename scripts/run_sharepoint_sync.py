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

from app.services.microsoft_ingestion import resolve_sharepoint_stager, run_sharepoint_sync


def main(argv=None):
    ap = argparse.ArgumentParser(description="Manual SharePoint incremental sync.")
    ap.add_argument("--manifest", default=None, help="already-staged manifest JSON")
    ap.add_argument("--stage", action="store_true", help="stage live via the connector first")
    ap.add_argument("--site", action="append", default=None, help="site id(s) to stage (with --stage)")
    ap.add_argument("--drive-id", action="append", default=None,
                    help="override drive id(s) (else discovered from microsoft_drives)")
    ap.add_argument("--limit", type=int, default=None, help="cap items the connector processes (fast test)")
    ap.add_argument("--top", type=int, default=None,
                    help="initial /root/delta $top page size (small first page, e.g. 10)")
    ap.add_argument("--timeout", type=int, default=None,
                    help="per-drive wall-clock timeout in seconds (surface a slow initial delta cleanly)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--diagnose", action="store_true",
                    help="print READ-ONLY staging config/data diagnostics and exit (no Graph, no downloads)")
    ap.add_argument("--inspect-manifest", default=None, metavar="PATH",
                    help="print READ-ONLY manifest diagnostics (counts, failed, duplicates, path fields)")
    ap.add_argument("--authoritative", action="store_true",
                    help="the staged input is a COMPLETE drive snapshot; only then reconcile missing/"
                         "deleted refs. NEVER use with --manifest, --limit, or a partial batch.")
    ap.add_argument("--resume-ocr", action="store_true",
                    help="with --manifest: reconcile (no download) then run OCR/analysis on the already-"
                         "imported documents. Cache-aware; bounded per page/document; safe to re-run.")
    args = ap.parse_args(argv)

    def _progress(ev):
        import time
        print(f"    [{time.strftime('%H:%M:%S')}] {ev}", flush=True)

    if args.inspect_manifest:
        from app.services.microsoft_ingestion import (
            analyze_manifest,
            manifest_ocr_status,
            manifest_path_records,
        )
        print(f"SharePoint manifest diagnostics (READ-ONLY, no Graph, no import): {args.inspect_manifest}")
        for k, v in analyze_manifest(args.inspect_manifest).items():
            print(f"  {k}: {v}")
        print("  import/OCR status (already-imported refs + OCR state):")
        for k, v in manifest_ocr_status(args.inspect_manifest).items():
            if k not in ("exists", "path"):
                print(f"    {k}: {v}")
        rows = manifest_path_records(args.inspect_manifest)
        print(f"  records ({len(rows)} shown, path fields only — no contents):")
        for r in rows:
            print(f"    - name={r['name']} status={r['status']} failed={r['failed']} "
                  f"file_exists={r['file_exists']} drive_id={r['drive_id']} item_id={r['item_id']}")
            print(f"        target={r['target']}")
            print(f"        local_path={r['local_path']}")
        return 0

    if args.diagnose:
        from app.services.microsoft_ingestion import sharepoint_staging_diagnostics
        print("SharePoint staging diagnostics (READ-ONLY, no Graph call):")
        for k, v in sharepoint_staging_diagnostics().items():
            print(f"  {k}: {v}")
        return 0

    diag = {}
    if args.manifest:
        # Robust parse (array / JSONL / append-only) + drop failed + dedupe by SharePoint identity, so an
        # already-downloaded run can be reconciled/imported WITHOUT re-downloading.
        from app.services.microsoft_ingestion import load_manifest_items, resume_ocr_for_items
        items = load_manifest_items(args.manifest)
        print(f"Loaded {len(items)} usable staged item(s) from manifest: {args.manifest}")
        # A manifest is a PARTIAL/manual batch -> never authoritative (never reconciles missing/deleted).
        # With --resume-ocr, reconcile WITHOUT OCR first, then OCR the already-imported docs (bounded).
        summary = run_sharepoint_sync(items=items, trigger_source="manual", dry_run=args.dry_run,
                                      authoritative=False, ocr=not args.resume_ocr,
                                      ocr_progress=_progress)
        if args.resume_ocr and not args.dry_run:
            print("Resuming OCR/analysis on already-imported documents (no download):")
            oc = resume_ocr_for_items(items, ocr_progress=_progress)
            print(f"OCR resume: {oc}")
            summary.update({k: oc[k] for k in ("ocr_analyzed", "ocr_failed", "ocr_timed_out")})
    elif args.stage:
        # Authoritative (reconcile missing/deleted) ONLY for a complete, non-limited, non-dry-run snapshot.
        authoritative = bool(args.authoritative) and not args.limit and not args.dry_run
        if args.authoritative and not authoritative:
            print("NOTE: --authoritative ignored because --limit/--dry-run makes this a partial batch; "
                  "missing/deleted reconciliation is skipped.")
        # Adapts to the deployment connector's real staging entrypoint (no hard-coded function name).
        stager = resolve_sharepoint_stager(site_ids=args.site, drive_ids=args.drive_id,
                                           dry_run=args.dry_run, diag=diag, limit=args.limit,
                                           top=args.top, timeout=args.timeout, progress=_progress)
        summary = run_sharepoint_sync(stager=stager, trigger_source="manual", dry_run=args.dry_run,
                                      authoritative=authoritative, ocr_progress=_progress)
    else:
        ap.error("provide --manifest <path>, --stage, or --diagnose")
        return 2

    if diag:
        print("Staging diagnostics:")
        print(f"  entrypoint: {diag.get('entrypoint')}   params: {diag.get('entrypoint_params')}")
        print(f"  connector callables: {diag.get('connector_callables')}")
        print(f"  staging_root: {diag.get('staging_root')}")
        print(f"  drives discovered: {diag.get('drive_count')}   drive_ids: {diag.get('drive_ids')}")
        print(f"  total staged items returned: {diag.get('total_items')}")
        if diag.get("enumerator"):
            print(f"  dry-run enumerator: {diag.get('enumerator')}")
        if diag.get("enum_errors"):
            print(f"  enum_errors: {diag.get('enum_errors')}")
        for d in (diag.get("drives") or [])[:20]:
            print(f"    drive {d['drive_id']}: items={d['items']} source={d['source']} "
                  f"download={d['download']} manifest_exists={d.get('manifest_exists')} "
                  f"result_type={d.get('result_type')} result_keys={d.get('result_keys')} "
                  f"result_counts={d.get('result_counts')} manifest={d.get('manifest')}")

    print("SharePoint sync:", summary.get("status"))
    for k in ("items_examined", "canonical_created", "reused_canonical", "metadata_updated", "skipped",
              "deleted", "missing", "missing_reconciliation_skipped", "ocr_analyzed", "ocr_failed",
              "ocr_timed_out"):
        if k in summary:
            print(f"  {k}: {summary[k]}")
    if summary.get("errors"):
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:20]:
            print(f"    - {e}")
    return 1 if summary.get("status") in ("error", "completed_with_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
