"""READ-ONLY diagnosis of relocation count drift.

When the guarded relocation APPLY aborts on count drift, this explains exactly which frozen approved
documents no longer match the live plan and why. It reuses the SAME guard logic (``_plan_apply``) as the
APPLY, so its verdict is identical to what aborted the run — it never guesses.

For each drifted document it reports: document_id, original_name, frozen source path, current storage_uri,
frozen proposed destination, current proposed destination, person_id/household_id/organization_id, whether
the current source file exists, and the exact drift reason mapped to a plain cause:

    destination_drift  -> destination changed (e.g. owner renamed / category or year changed)
    source_drift       -> source path changed (storage_uri differs from the frozen source)
    owner_removed      -> owner/link changed (person/household/organization link cleared -> now Firm)
    missing_document   -> document row deleted

STRICTLY READ-ONLY: SELECT + filesystem stat only. No writes, no file movement, no storage_uri or
document_sources changes.

Usage::
    python -m scripts.migration.diagnose_relocation_drift <frozen-approved-preview-dir>
"""
from __future__ import annotations

import argparse
import os
import sys

from app.services.migration.config import MigrationConfig
from app.services.migration.relocation import RepositoryRelocationJob, load_approved_relocation

_REASON_PLAIN = {
    "destination_drift": "destination changed",
    "source_drift": "source path changed",
    "owner_removed": "owner/link changed (now Firm)",
    "missing_document": "document row deleted",
}


def diagnose(frozen_dir, cfg=None):
    cfg = cfg or MigrationConfig.from_env()
    approved = load_approved_relocation(frozen_dir)
    job = RepositoryRelocationJob(cfg)
    docs, people_map, hh_map, org_map = job._load()
    pending, applied, applied_by_area, drift = job._plan_apply(docs, people_map, hh_map, org_map, approved)
    by_id = {d["id"]: d for d in docs}
    dest_root = cfg.migration_dest_root

    drifted = []
    for did, reason in drift:
        farea, fsrc, fdest = approved.get(did, ("", "", ""))
        d = by_id.get(did)
        if d is None:
            drifted.append({"document_id": did, "original_name": "(missing)", "frozen_area": farea,
                            "frozen_source": fsrc, "current_storage_uri": "", "frozen_destination": fdest,
                            "current_destination": "", "person_id": None, "household_id": None,
                            "organization_id": None, "current_source_exists": False,
                            "drift_reason": reason, "cause": _REASON_PLAIN.get(reason, reason)})
            continue
        placed = job.naming.plan(d, people=people_map, households=hh_map, organizations=org_map)
        src = d.get("storage_uri") or ""
        drifted.append({
            "document_id": did, "original_name": d.get("original_name") or "", "frozen_area": farea,
            "frozen_source": fsrc, "current_storage_uri": src,
            "frozen_destination": fdest, "current_destination": placed.full(dest_root),
            "person_id": d.get("person_id"), "household_id": d.get("household_id"),
            "organization_id": d.get("organization_id"),
            "current_source_exists": bool(src) and job.storage.stat(src).exists,
            "drift_reason": reason, "cause": _REASON_PLAIN.get(reason, reason),
        })
    return {"frozen_approved_total": len(approved), "live_pending": len(pending),
            "live_applied": sum(applied_by_area.values()), "drift_count": len(drift),
            "applied_by_area": dict(applied_by_area), "drifted": drifted}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.diagnose_relocation_drift",
                                description="Read-only diagnosis of relocation count drift.")
    p.add_argument("frozen_dir", help="Frozen approved relocation preview directory (with reconciliation.csv).")
    args = p.parse_args(argv)
    if not os.path.isfile(os.path.join(args.frozen_dir, "reconciliation.csv")):
        print(f"ERROR: no reconciliation.csv in {args.frozen_dir}")
        return 2
    res = diagnose(args.frozen_dir)
    print(f"frozen_approved_total: {res['frozen_approved_total']}   live_pending: {res['live_pending']}   "
          f"live_applied: {res['live_applied']}   drift_count: {res['drift_count']}")
    print(f"applied_by_area: {res['applied_by_area']}")
    print("\n=== drifted documents ===")
    for r in res["drifted"]:
        print(f"\n  document_id: {r['document_id']}   [{r['drift_reason']} -> {r['cause']}]")
        print(f"    original_name        : {r['original_name']}")
        print(f"    person/household/org : {r['person_id']} / {r['household_id']} / {r['organization_id']}")
        print(f"    frozen_source        : {r['frozen_source']}")
        print(f"    current_storage_uri  : {r['current_storage_uri']}")
        print(f"    current_source_exists: {r['current_source_exists']}")
        print(f"    frozen_destination   : {r['frozen_destination']}")
        print(f"    current_destination  : {r['current_destination']}")
    print("\nDiagnosis complete (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
