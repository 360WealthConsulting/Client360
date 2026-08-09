"""READ-ONLY reconciliation of a relocation preview's by_area totals vs the frozen approved set.

The preview ``by_area`` counts every document routed to an area REGARDLESS of state (needs_relocation /
already_in_repository / missing_source). ``load_approved_relocation`` (and the guarded APPLY) freeze scope
to ``state == needs_relocation`` only. This tool proves, straight from the preview's reconciliation.csv,
which owned documents are counted in by_area but excluded from the approved set, and why — so the correct
guarded ``--expect-*`` counts are the approved (needs_relocation) counts, not the broad by_area totals.

For each excluded owned document it reports: document_id, original_name, area, state, current_storage_uri,
proposed_destination, and current source existence.

STRICTLY READ-ONLY: reads the CSV, SELECTs original_name for the excluded ids, and stats their source
path. No writes, no file movement, no storage_uri/document_sources changes, no guard/count changes.

Usage::
    python -m scripts.migration.diagnose_area_state <relocation-preview-dir>
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

from app.services.migration.config import MigrationConfig
from app.services.migration.relocation import _OWNED_AREAS

_APPROVED_STATE = "needs_relocation"


def read_rows(preview_dir):
    csvpath = preview_dir if str(preview_dir).endswith(".csv") else os.path.join(preview_dir, "reconciliation.csv")
    with open(csvpath, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def area_state_breakdown(rows):
    """Pure: per-area state counts + the owned rows excluded from the approved (needs_relocation) set."""
    area_state: dict[str, Counter] = defaultdict(Counter)
    excluded: list[dict] = []
    for r in rows:
        area, state = r.get("area") or "", r.get("state") or ""
        area_state[area][state] += 1
        if area in _OWNED_AREAS and state != _APPROVED_STATE:
            excluded.append(r)
    return area_state, excluded


def analyze(preview_dir, cfg=None, enrich=True):
    rows = read_rows(preview_dir)
    area_state, excluded = area_state_breakdown(rows)
    if enrich and excluded:
        cfg = cfg or MigrationConfig.from_env()
        from app.services.migration.storage import LocalFilesystemStorage
        storage = LocalFilesystemStorage()
        names = {}
        ids = [int(r["document_id"]) for r in excluded if (r.get("document_id") or "").strip().isdigit()]
        if ids:
            from sqlalchemy import select

            from app.db import engine, metadata
            documents = metadata.tables["documents"]
            with engine.connect() as conn:
                names = {m["id"]: m["original_name"] for m in conn.execute(
                    select(documents.c.id, documents.c.original_name).where(documents.c.id.in_(ids))).mappings()}
        for r in excluded:
            did = int(r["document_id"]) if (r.get("document_id") or "").strip().isdigit() else None
            r["original_name"] = names.get(did, "(not found)")
            src = r.get("current_storage_uri") or ""
            r["source_exists"] = bool(src) and storage.stat(src).exists
    return {"area_state": {a: dict(c) for a, c in area_state.items()}, "excluded": excluded,
            "owned_areas": _OWNED_AREAS}


def _print(res) -> None:
    print("=== per-area state breakdown (by_area counts ALL states) ===")
    for area, states in res["area_state"].items():
        total = sum(states.values())
        approved = states.get(_APPROVED_STATE, 0)
        tag = "  [OWNED]" if area in res["owned_areas"] else ""
        print(f"  {area}: total={total}  needs_relocation(approved)={approved}  "
              f"other={ {s: n for s, n in states.items() if s != _APPROVED_STATE} }{tag}")
    print("\n=== owned documents counted in by_area but EXCLUDED from the approved set ===")
    for r in res["excluded"]:
        print(f"\n  document_id: {r.get('document_id')}   area: {r.get('area')}   state: {r.get('state')}")
        print(f"    original_name       : {r.get('original_name', '(not enriched)')}")
        print(f"    current_storage_uri : {r.get('current_storage_uri')}")
        print(f"    proposed_destination: {r.get('proposed_destination')}")
        print(f"    source_exists       : {r.get('source_exists', '(not checked)')}")
    print("\n=== reconciliation (owned areas) ===")
    for area in res["owned_areas"]:
        states = res["area_state"].get(area, {})
        total = sum(states.values())
        approved = states.get(_APPROVED_STATE, 0)
        excl = total - approved
        detail = "; ".join(f"{s}: {n}" for s, n in states.items() if s != _APPROVED_STATE) or "none"
        print(f"  {area}: {total} by_area = {approved} approved(needs_relocation) + {excl} excluded ({detail})")
    print("\nDiagnosis complete (read-only). Correct guarded --expect-* = the approved(needs_relocation) counts.")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.diagnose_area_state",
                                description="Read-only reconciliation of relocation by_area vs approved set.")
    p.add_argument("preview_dir", help="Relocation preview directory (with reconciliation.csv).")
    args = p.parse_args(argv)
    if not os.path.isfile(os.path.join(args.preview_dir, "reconciliation.csv")):
        print(f"ERROR: no reconciliation.csv in {args.preview_dir}")
        return 2
    _print(analyze(args.preview_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
