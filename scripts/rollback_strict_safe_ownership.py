#!/usr/bin/env python3
"""Strict-safe ownership batch — the scoped rollback. ALL-OR-NOTHING.

    python scripts/rollback_strict_safe_ownership.py --snapshot <dir-or-csv>
    python scripts/rollback_strict_safe_ownership.py --snapshot <dir-or-csv> --apply \\
        --actor-user-id <id> --confirm ROLLBACK-STRICT-SAFE-OWNERSHIP-1-541

SCOPED TO ONE VERIFIED SNAPSHOT
-------------------------------
This reverses exactly the document ids recorded in one apply's snapshot, and nothing else. There is
no "unassign everything this batch might have touched" predicate, because ownership has no sentinel
to key on: a document owned by person 258 looks identical whether this batch assigned it or a
reviewer did last year. The snapshot is therefore the only safe scope, and its digest is verified
before a single row is read from it.

EXACT RESTORATION
-----------------
The snapshot records each row's prior person_id, household_id, organization_id, review_status and
tags verbatim, and this writes those values back rather than assuming they were NULL. Restoring by
assumption would be a second guess about production state; restoring from the record is not.

FAIL CLOSED ON DRIFT
--------------------
Every row must still be owned by exactly the person this batch assigned. If any row has moved on —
reassigned, unassigned by hand, archived, deleted — the WHOLE rollback aborts. Reverting a subset
would leave the batch neither applied nor reversed, which is the one state nobody can reason about.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SNAPSHOT_CSV = "rollback_snapshot_strict_safe_ownership.csv"


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").upper()


def confirm_phrase(rows: int) -> str:
    from app.services.document_strict_safe_ownership import BATCH_ID
    return f"ROLLBACK-{_slug(BATCH_ID)}-{rows}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _int_or_none(value):
    value = (value or "").strip()
    return int(value) if value else None


def load_snapshot(snapshot) -> tuple[list[dict], Path, str]:
    """Resolve the snapshot csv and verify it still matches the digest recorded at apply time."""
    p = Path(snapshot)
    csv_path = p if p.is_file() else p / SNAPSHOT_CSV
    if not csv_path.is_file():
        raise Abort(f"ABORT: snapshot csv not found at {csv_path}")

    digest = sha256_of(csv_path)
    meta_path = csv_path.parent / "manifest.json"
    if not meta_path.is_file():
        raise Abort(f"ABORT: {meta_path} missing; cannot verify the snapshot is unmodified")
    recorded = json.loads(meta_path.read_text(encoding="utf-8")).get("snapshot_sha256")
    if recorded != digest:
        raise Abort(f"ABORT: snapshot SHA256 {digest} != recorded {recorded} — the snapshot has "
                    "been modified since the apply")

    with csv_path.open(newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    if not raw:
        raise Abort("ABORT: snapshot is empty")

    rows, seen = [], set()
    for r in raw:
        did = int(r["document_id"])
        if did in seen:
            raise Abort(f"ABORT: duplicate document_id {did} in snapshot")
        seen.add(did)
        rows.append({
            "document_id": did,
            "prior_person_id": _int_or_none(r.get("prior_person_id")),
            "prior_household_id": _int_or_none(r.get("prior_household_id")),
            "prior_organization_id": _int_or_none(r.get("prior_organization_id")),
            "prior_review_status": r.get("prior_review_status") or "",
            "prior_tags_json": r.get("prior_tags_json") or "null",
            "destination_person_id": _int_or_none(r.get("destination_person_id")),
        })
    return rows, csv_path, digest


_LOCK_SQL = """
    select id, person_id, household_id, organization_id, review_status, tags, status, archived,
           deleted_at
      from documents where id = any(:ids) order by id for update
"""

_RESTORE_SQL = """
    update documents
       set person_id = :prior_person_id,
           household_id = :prior_household_id,
           organization_id = :prior_organization_id,
           review_status = :prior_review_status,
           tags = cast(:prior_tags as jsonb)
     where id = :id
       and person_id = :assigned_person_id
    returning id
"""


def run(snapshot, *, apply_changes=False, confirm=None, actor_user_id=None, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event

    rows, csv_path, digest = load_snapshot(snapshot)
    want = confirm_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    report = {"rows": len(rows), "restored": 0, "drift": [], "committed": False,
              "snapshot": str(csv_path), "snapshot_sha256": digest, "confirm_phrase": want}

    out(f"snapshot: {csv_path}")
    out(f"  sha256 verified: {digest}")
    out(f"  scope: {len(ids)} document ids from this snapshot only")

    trans_conn = engine.connect()
    trans = trans_conn.begin()
    try:
        conn = trans_conn
        locked = {r["id"]: dict(r) for r in conn.execute(text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != ids:
            missing = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(missing)} snapshot documents did not lock: {missing[:10]}")

        for did in ids:
            cur, snap = locked[did], by_id[did]
            if cur["person_id"] != snap["destination_person_id"]:
                report["drift"].append(
                    (did, f"person_id is {cur['person_id']}, this batch assigned "
                          f"{snap['destination_person_id']}"))
            elif cur["household_id"] is not None or cur["organization_id"] is not None:
                report["drift"].append((did, "gained a household/organization since the apply"))
        if report["drift"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["drift"][:5])
            raise Abort(f"ABORT: {len(report['drift'])} of {len(ids)} rows have drifted since the "
                        f"apply — {head}")
        out(f"  drift check: all {len(ids)} rows still owned by the person this batch assigned")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            trans.rollback()
            return report

        request_id = f"strict-safe-ownership-rollback:{digest[:12]}"
        for did in ids:
            snap = by_id[did]
            updated = conn.execute(text(_RESTORE_SQL), {
                "id": did,
                "prior_person_id": snap["prior_person_id"],
                "prior_household_id": snap["prior_household_id"],
                "prior_organization_id": snap["prior_organization_id"],
                "prior_review_status": snap["prior_review_status"],
                "prior_tags": snap["prior_tags_json"],
                "assigned_person_id": snap["destination_person_id"]}).first()
            if updated is None:
                raise RuntimeError(f"document {did} could not be restored (ownership moved)")
            report["restored"] += 1
            write_audit_event(
                action="document.ownership_rollback", entity_type="document", entity_id=did,
                actor_user_id=actor_user_id, request_id=request_id,
                metadata={"document_id": did, "batch_id": "STRICT-SAFE-OWNERSHIP-1",
                          "reverted_person_id": snap["destination_person_id"],
                          "restored_person_id": snap["prior_person_id"],
                          "snapshot_sha256": digest},
                conn=conn)

        # post-write: exact restoration for targets, and nothing outside the snapshot touched
        for did in ids:
            cur = conn.execute(text(
                "select person_id, household_id, organization_id, review_status, tags "
                "from documents where id = :i"), {"i": did}).mappings().one()
            snap = by_id[did]
            if cur["person_id"] != snap["prior_person_id"] \
                    or cur["household_id"] != snap["prior_household_id"] \
                    or cur["organization_id"] != snap["prior_organization_id"]:
                raise RuntimeError(f"document {did} ownership not exactly restored")
            if (cur["review_status"] or "") != snap["prior_review_status"]:
                raise RuntimeError(f"document {did} review_status not exactly restored")
            if json.dumps(cur["tags"], sort_keys=True, ensure_ascii=False) != snap["prior_tags_json"]:
                raise RuntimeError(f"document {did} tags not exactly restored")

        trans.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['restored']} restorations to their exact prior state")
    except BaseException:
        if not report["committed"]:
            trans.rollback()
        raise
    finally:
        trans_conn.close()

    receipt = csv_path.parent / "rollback_receipt.json"
    receipt.write_text(json.dumps({
        "rolled_back_at": datetime.now(UTC).isoformat(), "batch_id": "STRICT-SAFE-OWNERSHIP-1",
        "rows_restored": report["restored"], "snapshot_sha256": digest,
        "actor_user_id": actor_user_id, "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reverse one applied strict-safe ownership batch.")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    args = ap.parse_args(argv)
    run(args.snapshot, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
