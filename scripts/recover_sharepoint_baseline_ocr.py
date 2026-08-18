"""Scoped recovery for the interrupted SharePoint baseline OCR.

Selects EXACTLY the verified baseline-created canonical population and runs the existing, already-hardened
baseline OCR path over it — the same ``microsoft_ingestion._ocr_documents`` loop that built the original
queue, so it is cache-aware (already-completed OCR is reused), subprocess-isolated (a hanging document is
killed by the hard wall-clock timeout and the loop continues), and status-tracked (the baseline
OcrRunTracker, monitor with ``python -m app.jobs.ocr_status``).

It does NOT call Microsoft Graph, download SharePoint files, import anything, create/reconcile canonical
documents, or use the generic firm-wide ocr_runner candidate query. It only SELECTs a fixed, verified set
of document ids and feeds them to the baseline OCR loop.

The population (verified read-only in production):

    documents.uploaded_by = 'SharePoint Sync'
    created_at >= 2026-08-17T11:00:00-04:00
    created_at <  2026-08-17T15:00:00-04:00
    count = 17448   (ids 39370..56817)

Usage::

    python scripts/recover_sharepoint_baseline_ocr.py --check
    python scripts/recover_sharepoint_baseline_ocr.py --run --expect-count 17448
"""
from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import func, select

from app.db import documents, engine

# --- the verified baseline-created population (exact scope) ------------------
UPLOADED_BY = "SharePoint Sync"
CREATED_FROM = datetime.fromisoformat("2026-08-17T11:00:00-04:00")
CREATED_TO = datetime.fromisoformat("2026-08-17T15:00:00-04:00")
EXPECTED_COUNT = 17448


def _selection_where():
    """The EXACT predicate that defines the verified baseline population. Deliberately does not add any
    other filter, so the count matches the read-only verification precisely (the count gate enforces it)."""
    return (documents.c.uploaded_by == UPLOADED_BY,
            documents.c.created_at >= CREATED_FROM,
            documents.c.created_at < CREATED_TO)


def scope_stats():
    """Read-only aggregate over the scoped population: count + id/created_at bounds."""
    with engine.connect() as conn:
        row = conn.execute(select(
            func.count().label("count"),
            func.min(documents.c.id).label("min_id"),
            func.max(documents.c.id).label("max_id"),
            func.min(documents.c.created_at).label("min_created_at"),
            func.max(documents.c.created_at).label("max_created_at"),
        ).where(*_selection_where())).mappings().one()
    return dict(row)


def scope_ids():
    """The scoped document ids, ordered by ``documents.id`` (stable, deterministic)."""
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(
            select(documents.c.id).where(*_selection_where()).order_by(documents.c.id))]


def _print_stats(st):
    print("SharePoint baseline OCR — scoped population (READ-ONLY):")
    print(f"  filter        uploaded_by='{UPLOADED_BY}'  "
          f"{CREATED_FROM.isoformat()} <= created_at < {CREATED_TO.isoformat()}")
    print(f"  count         {st['count']}   (expected {EXPECTED_COUNT})")
    print(f"  id range      {st['min_id']} .. {st['max_id']}")
    print(f"  created_at    {st['min_created_at']}  ..  {st['max_created_at']}")


def _banner(n):
    print("=" * 64)
    print(f"  SharePoint baseline recovery: {n} docs")
    print("  path: microsoft_ingestion._ocr_documents  (cache-aware, subprocess-isolated, tracked)")
    print("  no Graph · no downloads · no imports · no canonical creation")
    print("  monitor:  python -m app.jobs.ocr_status")
    print("=" * 64)


def _progress(ev):
    import time
    print(f"    [{time.strftime('%H:%M:%S')}] {ev}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python scripts/recover_sharepoint_baseline_ocr.py",
        description="Scoped OCR recovery for the interrupted SharePoint baseline (verified population only).")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="READ-ONLY: print the scoped count, id range, and created_at range, then exit.")
    mode.add_argument("--run", action="store_true",
                      help="Execute OCR recovery over the scoped population (requires --expect-count).")
    ap.add_argument("--expect-count", type=int, default=None,
                    help=f"Required confirmation for --run: must equal {EXPECTED_COUNT}.")
    args = ap.parse_args(argv)

    st = scope_stats()
    if args.check:
        _print_stats(st)
        return 0

    # --run: two independent guards must BOTH hold before any OCR is started.
    if args.expect_count != EXPECTED_COUNT:
        print(f"REFUSING to run: --run requires --expect-count {EXPECTED_COUNT} "
              f"(got {args.expect_count}). No OCR started.")
        return 2
    if st["count"] != EXPECTED_COUNT:
        print(f"REFUSING to run: the scoped population is {st['count']}, not exactly {EXPECTED_COUNT}. "
              f"No OCR started.")
        _print_stats(st)
        return 2

    ids = scope_ids()
    if len(ids) != EXPECTED_COUNT:          # defensive: selection must agree with the aggregate count
        print(f"REFUSING to run: selected {len(ids)} ids, not exactly {EXPECTED_COUNT}. No OCR started.")
        return 2

    _banner(len(ids))
    from app.services.microsoft_ingestion import _ocr_documents
    counts = _ocr_documents(ids, progress=_progress)
    print(f"OCR recovery complete over {len(ids)} scoped documents: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
