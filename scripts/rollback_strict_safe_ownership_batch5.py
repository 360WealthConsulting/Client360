#!/usr/bin/env python3
"""Strict-safe ownership BATCH 5 — the scoped rollback. ALL-OR-NOTHING.

    python scripts/rollback_strict_safe_ownership_batch5.py --snapshot <dir-or-csv>
    python scripts/rollback_strict_safe_ownership_batch5.py --snapshot <dir-or-csv> --apply \\
        --actor-user-id <id> --confirm ROLLBACK-STRICT-SAFE-OWNERSHIP-BATCH5-55

SCOPED TO ONE COMMITTED SNAPSHOT
--------------------------------
This reverses exactly the document ids recorded in one apply's snapshot, and nothing else. There is
no "unassign everything this batch might have touched" predicate, because ownership has no sentinel
to key on: a document owned by person 2297 looks identical whether this batch assigned it or a
reviewer did last year. The snapshot is therefore the only safe scope.

Two things are verified before a single row is read from it: the snapshot still hashes to what the
apply recorded, and the apply left a receipt marking the transaction COMMITTED. The second matters
because batch 5 writes its rollback artifact before the commit, so that the artifact can carry the
post-image and the audit ids — which means a snapshot can outlive a transaction that rolled back.
Replaying one of those would "restore" a state that was never changed.

EXACT RESTORATION
-----------------
The snapshot records each row's prior person_id, household_id, organization_id, review_status and
tags verbatim, and this writes those values back rather than assuming they were NULL. Restoring by
assumption would be a second guess about production state; restoring from the record is not.

FAIL CLOSED ON DRIFT
--------------------
Every row must still match the post-image the apply recorded — still owned by exactly the person
this batch assigned, still free of any household/organization scope. If any row has moved on, the
WHOLE rollback aborts. Reverting a subset would leave the batch neither applied nor reversed, which
is the one state nobody can reason about.

THE AUDIT TRAIL IS ADDITIVE
---------------------------
The original ``document.ownership_resolved`` events stay. The ledger is hash-chained and append-only,
and an assignment that really happened is a fact about the past even after it is reversed. Each
restoration adds a compensating ``document.ownership_rollback`` event naming the audit id it
reverses, which is the convention batch 1's rollback established.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SNAPSHOT_CSV = "rollback_snapshot_strict_safe_ownership_batch5.csv"


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _int_or_none(value):
    value = (value or "").strip()
    return int(value) if value else None


def load_snapshot(snapshot) -> tuple[list[dict], Path, str]:
    """Resolve the snapshot csv, verify its digest, and require a COMMITTED apply receipt."""
    from app.services.document_strict_safe_ownership_batch5 import sha256_of

    p = Path(snapshot)
    csv_path = p if p.is_file() else p / SNAPSHOT_CSV
    if not csv_path.is_file():
        raise Abort(f"ABORT: snapshot csv not found at {csv_path}")

    digest = sha256_of(csv_path)
    meta_path = csv_path.parent / "manifest.json"
    if not meta_path.is_file():
        raise Abort(f"ABORT: {meta_path} missing; cannot verify the snapshot is unmodified")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("snapshot_sha256") != digest:
        raise Abort(f"ABORT: snapshot SHA256 {digest} != recorded {meta.get('snapshot_sha256')} "
                    "— the snapshot has been modified since the apply")
    if not meta.get("committed"):
        raise Abort("ABORT: this snapshot's apply did not commit (manifest.json committed=false). "
                    "There is nothing to roll back.")
    if not (csv_path.parent / "apply_receipt.json").is_file():
        raise Abort("ABORT: apply_receipt.json missing; the apply did not complete.")

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
            "post_person_id": _int_or_none(r.get("post_person_id")),
            "post_household_id": _int_or_none(r.get("post_household_id")),
            "post_organization_id": _int_or_none(r.get("post_organization_id")),
            "assigned_person_id": _int_or_none(r.get("assigned_person_id")),
            "ownership_audit_id": _int_or_none(r.get("ownership_audit_id")),
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
       and household_id is not distinct from :post_household_id
       and organization_id is not distinct from :post_organization_id
    returning id
"""


def run(snapshot, *, apply_changes=False, confirm=None, actor_user_id=None, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.document_strict_safe_ownership_batch5 import BATCH_ID, confirm_phrase

    rows, csv_path, digest = load_snapshot(snapshot)
    want = confirm_phrase("ROLLBACK", len(rows))
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
            gone = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(gone)} snapshot documents did not lock: {gone[:10]}")

        for did in ids:
            cur, snap = locked[did], by_id[did]
            if cur["person_id"] != snap["assigned_person_id"]:
                report["drift"].append(
                    (did, f"person_id is {cur['person_id']}, this batch assigned "
                          f"{snap['assigned_person_id']}"))
            elif cur["household_id"] != snap["post_household_id"] \
                    or cur["organization_id"] != snap["post_organization_id"]:
                report["drift"].append((did, "an owner scope was added since the apply"))
        if report["drift"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["drift"][:5])
            raise Abort(f"ABORT: {len(report['drift'])} of {len(ids)} rows have drifted since the "
                        f"apply — {head}")
        out(f"  drift check: all {len(ids)} rows still match the post-image the apply recorded")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            trans.rollback()
            return report

        request_id = f"strict-safe-ownership-batch5-rollback:{digest[:12]}"
        for did in ids:
            snap = by_id[did]
            updated = conn.execute(text(_RESTORE_SQL), {
                "id": did,
                "prior_person_id": snap["prior_person_id"],
                "prior_household_id": snap["prior_household_id"],
                "prior_organization_id": snap["prior_organization_id"],
                "prior_review_status": snap["prior_review_status"],
                "prior_tags": snap["prior_tags_json"],
                "assigned_person_id": snap["assigned_person_id"],
                "post_household_id": snap["post_household_id"],
                "post_organization_id": snap["post_organization_id"]}).first()
            if updated is None:
                raise RuntimeError(f"document {did} could not be restored (ownership moved)")
            report["restored"] += 1
            write_audit_event(
                action="document.ownership_rollback", entity_type="document", entity_id=did,
                actor_user_id=actor_user_id, request_id=request_id,
                metadata={"document_id": did, "batch_id": BATCH_ID,
                          "reverted_person_id": snap["assigned_person_id"],
                          "restored_person_id": snap["prior_person_id"],
                          "reverses_audit_event_id": snap["ownership_audit_id"],
                          "snapshot_sha256": digest},
                conn=conn)

        # post-write: exact restoration for targets, nothing outside the snapshot touched
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
        "rolled_back_at": datetime.now(UTC).isoformat(), "batch_id": BATCH_ID,
        "rows_restored": report["restored"], "snapshot_sha256": digest,
        "actor_user_id": actor_user_id, "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reverse one applied strict-safe ownership BATCH 5 apply.")
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
