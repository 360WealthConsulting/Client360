#!/usr/bin/env python3
"""Strict-safe ownership BATCH 3 — the scoped rollback. ALL-OR-NOTHING.

    python scripts/rollback_strict_safe_ownership_batch3.py --snapshot <dir-or-csv>
    python scripts/rollback_strict_safe_ownership_batch3.py --snapshot <dir-or-csv> --apply \\
        --actor-user-id 1 --confirm ROLLBACK-STRICT-SAFE-OWNERSHIP-3-5

WHY BATCH 3 GETS ITS OWN ROLLBACK
---------------------------------
A reversal is part of the record of the batch that actually happened, so the confirmation phrase, the
audit metadata and the receipt must name Batch 3. Batch 1's and Batch 2's rollbacks are untouched and
remain the only tools for their own snapshots; this one refuses theirs (see :func:`load_snapshot`),
so no batch can be reversed through another batch's tooling.

SCOPED TO ONE VERIFIED SNAPSHOT
-------------------------------
This reverses exactly the document ids recorded in one Batch 3 apply's snapshot, and nothing else.
Ownership has no sentinel to key on — a document owned by person 7841 looks identical whether this
batch assigned it or a reviewer did last year — so the snapshot is the only safe scope. Its digest is
verified before a single row is read from it, and a fingerprint over every OTHER document is compared
before and after, so a widened scope cannot pass unnoticed.

EXACT RESTORATION
-----------------
The snapshot records each row's prior person_id, household_id, organization_id, review_status and
tags verbatim, and this writes those values back rather than assuming they were NULL. Restoring by
assumption would be a second guess about production state; restoring from the record is not.

FAIL CLOSED ON DRIFT
--------------------
Every row must still be owned by exactly the person this batch assigned. If any row has moved on —
reassigned, unassigned by hand, given a household — the WHOLE rollback aborts. Reverting a subset
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

#: Where ``scripts/apply_strict_safe_ownership_batch3.py`` writes its snapshots. A snapshot outside
#: this tree is refused unless the operator names a different root explicitly.
SNAPSHOT_ROOT = REPO_ROOT / "var" / "strict_safe_ownership_batch3"

#: The snapshot filename the Batch 3 apply writes. Batch 1 and Batch 2 share a different filename
#: between them, so even the name does not collide here.
SNAPSHOT_CSV = "rollback_snapshot_strict_safe_ownership_batch3.csv"

#: The batch id a Batch 3 snapshot's manifest.json must carry.
EXPECTED_BATCH_ID = "STRICT-SAFE-OWNERSHIP-3"

#: Columns only a Batch 3 snapshot has — the second, independent proof of which batch wrote this
#: snapshot. A manifest.json can be hand-edited; a whole CSV shape less easily.
BATCH3_MARKER_COLUMNS = ("rule", "support_json")

#: Columns that belong to an EARLIER batch. Seeing either means this is not a Batch 3 snapshot,
#: whatever its manifest claims: ``corroborator_count`` is batch 1's, and ``matching_folder`` is
#: batch 2's — batch 3 records its folder inside ``support_json``, never as a top-level column.
#:
#: ``independent_document_corroborator_count`` is deliberately NOT here: batch 2 and batch 3 both
#: write it, because it records the same true fact about both (one CONTACT corroborator, with the
#: filing evidence kept out of that count). It cannot tell the two apart, so the discrimination is
#: left to the markers that can.
FOREIGN_MARKER_COLUMNS = ("corroborator_count", "matching_folder")

#: The prior-state columns that must be present to restore anything at all.
REQUIRED_PRIOR_COLUMNS = ("document_id", "prior_person_id", "prior_household_id",
                          "prior_organization_id", "prior_review_status", "prior_tags_json",
                          "destination_person_id")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").upper()


def confirm_phrase(rows: int) -> str:
    """``ROLLBACK-STRICT-SAFE-OWNERSHIP-3-<rows>`` — for the reviewed batch, ...-3-5."""
    from app.services.document_strict_safe_ownership_batch3 import BATCH_ID
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


def load_snapshot(snapshot, *, snapshot_root=SNAPSHOT_ROOT) -> tuple[list[dict], Path, str]:
    """Resolve the snapshot, prove it is a BATCH 3 snapshot, and verify it is unmodified.

    Four independent gates, all before any database connection is opened: the snapshot must live
    under the Batch 3 root, its manifest must name Batch 3, its columns must be Batch 3's and must
    not be an earlier batch's, and its content must still hash to the digest recorded at apply time.
    """
    p = Path(snapshot)
    csv_path = p if p.is_file() else p / SNAPSHOT_CSV
    if not csv_path.is_file():
        raise Abort(f"ABORT: snapshot csv not found at {csv_path}")

    root = Path(snapshot_root).resolve()
    resolved = csv_path.resolve()
    if not resolved.is_relative_to(root):
        raise Abort(f"ABORT: {resolved} is not under the batch 3 snapshot root {root}. "
                    "This tool reverses batch 3 applies only; use --snapshot-root deliberately if "
                    "the batch 3 snapshots genuinely live elsewhere.")

    meta_path = csv_path.parent / "manifest.json"
    if not meta_path.is_file():
        raise Abort(f"ABORT: {meta_path} missing; cannot verify the snapshot is unmodified")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    batch_id = meta.get("batch_id")
    if batch_id != EXPECTED_BATCH_ID:
        raise Abort(f"ABORT: snapshot is from batch {batch_id!r}, not {EXPECTED_BATCH_ID!r}. "
                    "Reverse an earlier batch with its own rollback script.")

    digest = sha256_of(csv_path)
    recorded = meta.get("snapshot_sha256")
    if recorded != digest:
        raise Abort(f"ABORT: snapshot SHA256 {digest} != recorded {recorded} — the snapshot has "
                    "been modified since the apply")

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = set(reader.fieldnames or ())
        raw = list(reader)
    foreign = [c for c in FOREIGN_MARKER_COLUMNS if c in columns]
    if foreign:
        raise Abort(f"ABORT: snapshot carries the earlier-batch columns {foreign}; "
                    "this tool reverses batch 3 applies only")
    missing_marker = [c for c in BATCH3_MARKER_COLUMNS if c not in columns]
    if missing_marker:
        raise Abort(f"ABORT: snapshot is missing batch 3 columns {missing_marker}; "
                    "it was not written by the batch 3 apply")
    missing_prior = [c for c in REQUIRED_PRIOR_COLUMNS if c not in columns]
    if missing_prior:
        raise Abort(f"ABORT: snapshot is missing prior-state columns {missing_prior}")
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
            "rule": (r.get("rule") or "").strip(),
            "corroborator": (r.get("corroborator") or "").strip(),
        })
    return rows, csv_path, digest


_LOCK_SQL = """
    select id, person_id, household_id, organization_id, review_status, tags, status, archived,
           deleted_at
      from documents where id = any(:ids) order by id for update
"""

_NON_TARGET_FP = """
    select md5(string_agg(
        id::text||'|'||coalesce(person_id::text,'')||'|'||coalesce(household_id::text,'')||'|'||
        coalesce(organization_id::text,'')||'|'||coalesce(review_status,''),
        E'\n' order by id))
      from documents where id <> all(:ids)
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


def run(snapshot, *, apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_root=SNAPSHOT_ROOT, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event

    rows, csv_path, digest = load_snapshot(snapshot, snapshot_root=snapshot_root)
    want = confirm_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    report = {"rows": len(rows), "restored": 0, "drift": [], "committed": False,
              "snapshot": str(csv_path), "snapshot_sha256": digest, "confirm_phrase": want,
              "batch_id": EXPECTED_BATCH_ID}

    out(f"snapshot: {csv_path}")
    out(f"  batch id verified: {EXPECTED_BATCH_ID}")
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

        fp_before = conn.execute(text(_NON_TARGET_FP), {"ids": ids}).scalar()

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

        request_id = f"strict-safe-ownership-batch3-rollback:{digest[:12]}"
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
                metadata={"document_id": did, "batch_id": EXPECTED_BATCH_ID,
                          "rule": snap["rule"], "corroborator": snap["corroborator"],
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

        if conn.execute(text(_NON_TARGET_FP), {"ids": ids}).scalar() != fp_before:
            raise RuntimeError("non-target fingerprint changed — the rollback reached beyond the "
                               "snapshot's document ids")

        trans.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['restored']} restorations to their exact prior state")
        out("  post-write checks: exact ownership/review_status/tags restoration, non-target "
            "fingerprint unchanged")
    except BaseException:
        if not report["committed"]:
            trans.rollback()
        raise
    finally:
        trans_conn.close()

    receipt = csv_path.parent / "rollback_receipt.json"
    receipt.write_text(json.dumps({
        "rolled_back_at": datetime.now(UTC).isoformat(), "batch_id": EXPECTED_BATCH_ID,
        "rows_restored": report["restored"], "snapshot_sha256": digest,
        "actor_user_id": actor_user_id, "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reverse one applied strict-safe ownership BATCH 3 batch.")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    ap.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT))
    args = ap.parse_args(argv)
    run(args.snapshot, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id, snapshot_root=Path(args.snapshot_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
