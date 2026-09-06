#!/usr/bin/env python3
"""Batch 1 non-client exclusion — the scoped rollback.

    python scripts/rollback_nonclient_exclusion.py --snapshot <dir-or-csv>
    python scripts/rollback_nonclient_exclusion.py --snapshot <dir-or-csv> --apply \\
        --actor-user-id <id> --confirm ROLLBACK-NONCLIENT-BATCH1-2990

SCOPED TO THE SNAPSHOT, NEVER TO THE SENTINEL
---------------------------------------------
This reverses exactly the document ids recorded in one apply's snapshot, and nothing else. It
deliberately does NOT offer a blanket ``UPDATE ... WHERE review_status = 'excluded_nonclient'``:
that predicate would also catch a later batch, a manual classification, or a future lane that
adopts the same sentinel, and would reverse work this snapshot never authorised.

EXACT RESTORATION
-----------------
The snapshot records each row's prior ``review_status`` AND its prior ``tags`` document verbatim, so
this writes those values back rather than deriving them. That is stronger than dropping the
exclusion key: if the apply merged into an existing tags object, the object is restored to exactly
what it was, byte for byte.

FAIL CLOSED ON DRIFT
--------------------
The snapshot's own SHA256 is verified before anything is read from it, so a tampered snapshot is
refused. Then every row must still carry the sentinel this snapshot applied; if any row has moved
on — reclassified, reversed by hand, deleted — the WHOLE rollback aborts rather than partially
reverting a batch someone else has already touched.
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

BATCH_ID = "NONCLIENT-BATCH1"
SNAPSHOT_CSV = "rollback_snapshot_nonclient_exclusion.csv"


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def confirm_phrase(rows: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", BATCH_ID).strip("-").upper()
    return f"ROLLBACK-{slug}-{rows}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_snapshot(snapshot) -> tuple[list[dict], Path, str]:
    """Resolve the snapshot csv and verify it still matches the digest recorded at apply time."""
    p = Path(snapshot)
    csv_path = p if p.is_file() else p / SNAPSHOT_CSV
    if not csv_path.is_file():
        raise Abort(f"ABORT: snapshot csv not found at {csv_path}")

    digest = sha256_of(csv_path)
    meta_path = csv_path.parent / "manifest.json"
    if meta_path.is_file():
        recorded = json.loads(meta_path.read_text(encoding="utf-8")).get("snapshot_sha256")
        if recorded and recorded != digest:
            raise Abort(f"ABORT: snapshot SHA256 {digest} != recorded {recorded} — the snapshot "
                        "has been modified since the apply")
    else:
        raise Abort(f"ABORT: {meta_path} missing; cannot verify the snapshot is unmodified")

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
            "prev_review_status": r.get("prev_review_status") or "",
            "prev_tags_json": r.get("prev_tags_json") or "null",
            "matched_rule": r.get("matched_rule") or "",
        })
    return rows, csv_path, digest


_LOCK_SQL = """
    select id, review_status, tags from documents where id = any(:ids) order by id for update
"""

_RESTORE_SQL = """
    update documents
       set review_status = :prev_status,
           tags = cast(:prev_tags as jsonb)
     where id = :id
       and review_status = :sentinel
    returning id
"""


def run(snapshot, *, apply_changes=False, confirm=None, actor_user_id=None, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services import document_nonclient_exclusion as nx

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
        locked = {r["id"]: dict(r) for r in conn.execute(
            text(_LOCK_SQL), {"ids": ids}).mappings()}

        for did in ids:
            cur = locked.get(did)
            if cur is None:
                report["drift"].append((did, "row no longer exists"))
            elif cur["review_status"] != nx.EXCLUDED_REVIEW_STATUS:
                report["drift"].append(
                    (did, f"review_status is {cur['review_status']!r}, not the sentinel"))
        if report["drift"]:
            head = "; ".join(f"{d}:{why}" for d, why in report["drift"][:5])
            raise Abort(f"ABORT: {len(report['drift'])} of {len(ids)} rows have drifted since the "
                        f"apply — {head}")
        out(f"  drift check: all {len(ids)} rows still carry the sentinel")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            trans.rollback()
            return report

        request_id = f"nonclient-batch1-rollback:{BATCH_ID}:{digest[:12]}"
        for did in ids:
            snap = by_id[did]
            updated = conn.execute(text(_RESTORE_SQL), {
                "id": did, "prev_status": snap["prev_review_status"],
                "prev_tags": snap["prev_tags_json"],
                "sentinel": nx.EXCLUDED_REVIEW_STATUS}).first()
            if updated is None:
                raise RuntimeError(f"document {did} could not be restored (lost the sentinel)")
            report["restored"] += 1
            write_audit_event(
                action="document.nonclient_exclusion_reversed", entity_type="document",
                entity_id=did, actor_user_id=actor_user_id, request_id=request_id,
                metadata={"document_id": did, "batch_id": BATCH_ID,
                          "restored_review_status": snap["prev_review_status"],
                          "snapshot_sha256": digest, "matched_rule": snap["matched_rule"]},
                conn=conn)

        # post-write: exact restoration, and nothing outside the snapshot touched
        for did in ids:
            cur = conn.execute(text(
                "select review_status, tags from documents where id = :i"),
                {"i": did}).mappings().one()
            snap = by_id[did]
            if (cur["review_status"] or "") != snap["prev_review_status"]:
                raise RuntimeError(f"document {did} review_status not exactly restored")
            if json.dumps(cur["tags"], sort_keys=True, ensure_ascii=False) != snap["prev_tags_json"]:
                raise RuntimeError(f"document {did} tags not exactly restored")
        still = conn.execute(text(
            "select count(*) from documents where id = any(:ids) and review_status = :s"),
            {"ids": ids, "s": nx.EXCLUDED_REVIEW_STATUS}).scalar()
        if still:
            raise RuntimeError(f"{still} rows still carry the sentinel after rollback")

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
    ap = argparse.ArgumentParser(description="Reverse one applied Batch 1 non-client exclusion.")
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
