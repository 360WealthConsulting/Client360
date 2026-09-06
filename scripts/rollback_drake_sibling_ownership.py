"""Rollback for a committed Drake carry-over ownership apply. ALL-OR-NOTHING.

Restores exactly the ownership fields the apply changed, for exactly the documents named in that
apply's rollback snapshot, using the values recorded in that snapshot. It restores nothing else:
status, deleted_at, storage, proposals, source availability, OCR and review_status are never touched,
and no file is moved.

DRIFT IS FATAL, NOT SOMETHING TO WORK AROUND
    The snapshot records what each document's ownership was BEFORE the apply (all NULL) and what the
    apply set. Before restoring, every document must currently hold EXACTLY the owner the apply
    assigned. If a document has since been re-owned, cleared, deleted or reassigned by a human, this
    aborts the whole run rather than overwrite that decision. A rollback that silently discards a
    later human judgement is worse than no rollback.

USAGE
    python scripts/rollback_drake_sibling_ownership.py --snapshot <dir-or-csv> --dry-run
    python scripts/rollback_drake_sibling_ownership.py --snapshot <dir-or-csv> --apply \\
        --confirm ROLLBACK-<BATCH-ID>-<ROWS>
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.db import documents, engine  # noqa: E402

SNAPSHOT_NAME = "rollback_snapshot_drake_sibling_ownership.csv"
OWNER_COLUMN = {"person": "person_id", "household": "household_id",
                "organization": "organization_id"}


def _resolve(path):
    return os.path.join(path, SNAPSHOT_NAME) if os.path.isdir(path) else path


def load(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("ABORT: snapshot is empty")
    return rows


def run(snapshot_path, *, apply_changes=False, confirm=None, batch_id=None, log=print):
    path = _resolve(snapshot_path)
    rows = load(path)
    want = f"ROLLBACK-{batch_id}-{len(rows)}" if batch_id else None
    if apply_changes and want and confirm != want:
        raise SystemExit(f"ABORT: --apply requires --confirm {want}")

    report = {"snapshot": path, "rows": len(rows), "restored": 0, "failures": [],
              "committed": False}
    with engine.begin() as conn:
        trans = conn.begin_nested()
        ids = [int(r["document_id"]) for r in rows]
        current = {r["id"]: r for r in conn.execute(
            select(documents.c.id, documents.c.person_id, documents.c.household_id,
                   documents.c.organization_id, documents.c.status)
            .where(documents.c.id.in_(ids)).with_for_update()).mappings()}

        for r in rows:
            did = int(r["document_id"])
            cur = current.get(did)
            if cur is None:
                report["failures"].append({"document_id": did, "why": "document missing"})
                continue
            if cur["status"] == "deleted":
                report["failures"].append({"document_id": did, "why": "document deleted since apply"})
                continue
            col = OWNER_COLUMN[r["destination_owner_type"]]
            if cur[col] != int(r["destination_owner_id"]):
                report["failures"].append(
                    {"document_id": did,
                     "why": f"drift: {col} is {cur[col]}, apply set {r['destination_owner_id']}"})
                continue
            for other in set(OWNER_COLUMN.values()) - {col}:
                if cur[other] is not None:
                    report["failures"].append(
                        {"document_id": did, "why": f"drift: {other} was set after the apply"})
                    break

        if report["failures"]:
            trans.rollback()
            log(f"  ABORT: {len(report['failures'])} row(s) drifted; nothing restored")
            for f in report["failures"][:20]:
                log(f"    doc={f['document_id']} {f['why']}")
            return report

        for r in rows:
            did = int(r["document_id"])
            values = {c: (int(r[f"prior_{c}"]) if (r.get(f"prior_{c}") or "").strip() else None)
                      for c in OWNER_COLUMN.values()}
            conn.execute(documents.update().where(documents.c.id == did).values(**values))
            report["restored"] += 1

        if not apply_changes:
            trans.rollback()
            log(f"  DRY RUN — would restore {report['restored']} document(s). Nothing committed.")
            report["restored"] = 0
            return report
        trans.commit()
        report["committed"] = True
    log(f"  COMMITTED rollback of {report['restored']} document(s)")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rollback_drake_sibling_ownership")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    a = ap.parse_args(argv)
    if a.apply and a.dry_run:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    r = run(a.snapshot, apply_changes=a.apply, confirm=a.confirm, batch_id=a.batch_id)
    print(json.dumps(r, indent=2, default=str))
    return 1 if r["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
