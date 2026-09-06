#!/usr/bin/env python3
"""Document filing persistence BATCH 2 — the scoped rollback. ALL-OR-NOTHING.

    python scripts/rollback_document_filing_batch2.py --receipt <apply_receipt.json>
    python scripts/rollback_document_filing_batch2.py --receipt <apply_receipt.json> --apply \\
        --actor-user-id 1 --confirm ROLLBACK-DOCUMENT-FILING-BATCH2-550

THE DIFFERENCE FROM BATCH 1's ROLLBACK
---------------------------------------
Batch 1 created every folder it used, so reversing it meant deleting all of them. Batch 2 created
only FOUR of the 381 nodes it files into; the other 377 belong to Batch 1 and are production state
holding thousands of other documents. So this rollback deletes strictly the codes the receipt records
as created, and the snapshot carries a per-row ``created_folder`` flag so a reused Batch 1 folder can
never be mistaken for one of ours. Deleting a Batch 1 folder would unfile Batch 1's documents through
``ON DELETE SET NULL`` — silently, and with no record of where they had been.

WHAT IT RESTORES
----------------
Each of the 550 documents to the ``previous_folder_id`` the snapshot recorded, then the four created
folders, children before parents. Never by relying on ``ON DELETE SET NULL``: that would destroy the
evidence of what had been filed where at the moment it mattered most.

IT REFUSES ANYTHING BUILT ON TOP
---------------------------------
If a created folder has acquired a document from outside this batch, or a child folder somebody else
made, reversing would take their work with it. Both abort.
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

REQUIRED_ACTOR_USER_ID = 1
AUDIT_ACTION = "document.filing_folder_unassigned"
ADVISORY_LOCK_KEY = 0x0DF12B02

SNAPSHOT_COLUMNS = ("document_id", "previous_folder_id", "target_folder_code",
                    "target_folder_path", "scope_type", "scope_id", "created_folder")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _int_or_none(value):
    value = (value or "").strip()
    return int(value) if value else None


def load_receipt(receipt_path, *, candidate_csv=None,
                 expect_candidate_sha=None) -> tuple[dict, list[dict], Path]:
    """Receipt + snapshot, each proved against the other before a connection is opened."""
    from app.services.document_filing_batch2 import (
        BATCH_NAME,
        CANDIDATE_CSV_SHA256,
        build_plan,
        sha256_of,
    )

    path = Path(receipt_path)
    if not path.is_file():
        raise Abort(f"ABORT: receipt not found: {path}")
    receipt = json.loads(path.read_text(encoding="utf-8"))

    if receipt.get("batch") != BATCH_NAME:
        raise Abort(f"ABORT: receipt is for batch {receipt.get('batch')!r}, not {BATCH_NAME!r}")
    if receipt.get("committed") is not True:
        raise Abort("ABORT: receipt does not record a committed apply")
    # Defence in depth for production: the receipt must name the REVIEWED artifact, not merely a
    # self-consistent one. Overridable so a test can drive the real path with its own fixture.
    expected_sha = CANDIDATE_CSV_SHA256 if expect_candidate_sha is None else expect_candidate_sha
    if receipt.get("candidate_csv_sha256") != expected_sha:
        raise Abort(f"ABORT: receipt candidate SHA {receipt.get('candidate_csv_sha256')} != "
                    f"{expected_sha}")

    snapshot_path = Path(receipt.get("snapshot") or "")
    if not snapshot_path.is_file():
        raise Abort(f"ABORT: snapshot not found: {snapshot_path}")
    digest = sha256_of(snapshot_path)
    if digest != receipt.get("snapshot_sha256"):
        raise Abort(f"ABORT: snapshot SHA256 {digest} != recorded "
                    f"{receipt.get('snapshot_sha256')} — the snapshot has been modified")

    with snapshot_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in SNAPSHOT_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise Abort(f"ABORT: snapshot is missing columns {missing}")
        raw = list(reader)
    if not raw:
        raise Abort("ABORT: snapshot is empty")

    rows, seen = [], set()
    for r in raw:
        document_id = int(r["document_id"])
        if document_id in seen:
            raise Abort(f"ABORT: duplicate document_id {document_id} in snapshot")
        seen.add(document_id)
        rows.append({"document_id": document_id,
                     "previous_folder_id": _int_or_none(r.get("previous_folder_id")),
                     "target_folder_code": r["target_folder_code"],
                     "created_folder": str(r.get("created_folder", "0")).strip() == "1"})

    # The receipt's list of created codes is the authority on what may be deleted; the snapshot's
    # flags must agree with it, and neither may name a code the plan does not contain.
    created = set(receipt.get("created_folder_codes") or [])
    flagged = {r["target_folder_code"] for r in rows if r["created_folder"]}
    if not flagged <= created:
        raise Abort(f"ABORT: snapshot flags folders the receipt does not list as created: "
                    f"{sorted(flagged - created)[:5]}")

    if candidate_csv is not None:
        plan = build_plan(candidate_csv)
        if plan["plan_digest"] != receipt.get("plan_digest"):
            raise Abort(f"ABORT: rebuilt plan digest {plan['plan_digest']} != receipt "
                        f"{receipt.get('plan_digest')}")
        if plan["folder_manifest_digest"] != receipt.get("folder_manifest_digest"):
            raise Abort("ABORT: rebuilt folder manifest digest does not match the receipt")
        if len(plan["documents"]) != len(rows):
            raise Abort(f"ABORT: plan has {len(plan['documents'])} documents, snapshot has "
                        f"{len(rows)}")
        plan_codes = {f["code"] for f in plan["folders"]}
        if not created <= plan_codes:
            raise Abort("ABORT: the receipt lists created folders the plan does not define")
    return receipt, rows, snapshot_path


_LOCK_SQL = """
    select id, folder_id, status, archived, deleted_at
      from documents where id = any(:ids) order by id for update
"""


def run(receipt_path, *, candidate_csv=None, apply_changes=False, confirm=None, actor_user_id=None,
        out=print, expect_candidate_sha=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.document_filing_batch2 import BATCH_NAME, rollback_phrase

    receipt, rows, snapshot_path = load_receipt(receipt_path, candidate_csv=candidate_csv,
                                                expect_candidate_sha=expect_candidate_sha)
    want = rollback_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id != REQUIRED_ACTOR_USER_ID:
        raise Abort(f"ABORT: --apply requires --actor-user-id {REQUIRED_ACTOR_USER_ID}")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    created_codes = sorted(set(receipt.get("created_folder_codes") or []))
    report = {"batch": BATCH_NAME, "rows": len(rows), "restored": 0, "folders_deleted": 0,
              "committed": False, "confirm_phrase": want, "drift": [],
              "snapshot": str(snapshot_path), "created_codes": created_codes,
              "request_id": f"document-filing-rollback:{BATCH_NAME}:"
                            f"{receipt['plan_digest'][:12]}"}

    out(f"receipt:  {receipt_path}")
    out(f"snapshot: {snapshot_path}")
    out(f"  batch {BATCH_NAME}, {len(rows)} documents, "
        f"{len(created_codes)} folders eligible for deletion")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        target_codes = sorted({r["target_folder_code"] for r in rows})
        folder_rows = {r["code"]: dict(r) for r in connection.execute(text(
            "select id, code, name, parent_folder_id from document_folders "
            "where code = any(:codes) order by id for update"),
            {"codes": sorted(set(target_codes) | set(created_codes))}).mappings()}
        absent = sorted(set(target_codes) - set(folder_rows))
        if absent:
            raise Abort(f"ABORT: {len(absent)} destination folders no longer exist: {absent[:5]}")
        code_by_id = {v["id"]: k for k, v in folder_rows.items()}

        locked = {r["id"]: dict(r) for r in connection.execute(
            text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != ids:
            gone = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(gone)} snapshot documents did not lock: {gone[:5]}")

        for document_id in ids:
            current, snap = locked[document_id], by_id[document_id]
            actual = code_by_id.get(current["folder_id"])
            if actual != snap["target_folder_code"]:
                report["drift"].append(
                    (document_id, f"is in {actual!r}, this batch filed it in "
                                  f"{snap['target_folder_code']!r}"))
        if report["drift"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["drift"][:5])
            raise Abort(f"ABORT: {len(report['drift'])} documents have moved since the apply — "
                        f"{head}")

        created_ids = [folder_rows[c]["id"] for c in created_codes if c in folder_rows]
        if created_ids:
            outsiders = connection.execute(text(
                "select count(*) from documents where folder_id = any(:fids) and id <> all(:ids)"),
                {"fids": created_ids, "ids": ids}).scalar()
            if outsiders:
                raise Abort(f"ABORT: {outsiders} documents outside this batch are filed in folders "
                            "it created — reversing would unfile somebody else's work")
            strays = connection.execute(text(
                "select count(*) from document_folders where parent_folder_id = any(:fids) "
                "and id <> all(:fids)"), {"fids": created_ids}).scalar()
            if strays:
                raise Abort(f"ABORT: {strays} folders created outside this batch sit beneath the "
                            "folders it created")
        out(f"  drift check: {len(ids)} documents still exactly as this batch filed them")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            transaction.rollback()
            return report

        for document_id in ids:
            snap = by_id[document_id]
            restored = connection.execute(text(
                "update documents set folder_id = :previous where id = :id "
                "and folder_id = :current returning id"),
                {"previous": snap["previous_folder_id"], "id": document_id,
                 "current": folder_rows[snap["target_folder_code"]]["id"]}).first()
            if restored is None:
                raise RuntimeError(f"document {document_id} could not be unfiled")
            report["restored"] += 1
            write_audit_event(
                action=AUDIT_ACTION, entity_type="document", entity_id=document_id,
                actor_user_id=actor_user_id, request_id=report["request_id"],
                metadata={"batch": BATCH_NAME, "document_id": document_id,
                          "restored_folder_id": snap["previous_folder_id"],
                          "removed_folder_code": snap["target_folder_code"],
                          "plan_digest": receipt["plan_digest"]},
                conn=connection)

        # Delete ONLY what this batch created, children before parents. A reused Batch 1 folder is
        # not in created_codes and is therefore untouchable here.
        for code in sorted(created_codes, key=lambda c: (-c.count("--"), c)):
            row = folder_rows.get(code)
            if row is None:
                raise RuntimeError(f"created folder {code} is already gone")
            deleted = connection.execute(text(
                "delete from document_folders where id = :id "
                "and not exists (select 1 from documents where folder_id = :id) "
                "and not exists (select 1 from document_folders child "
                "                 where child.parent_folder_id = :id) returning id"),
                {"id": row["id"]}).first()
            if deleted is None:
                raise RuntimeError(f"folder {code} could not be deleted (still referenced)")
            report["folders_deleted"] += 1

        still_filed = connection.execute(text(
            "select count(*) from documents where id = any(:ids) and folder_id is not null"),
            {"ids": ids}).scalar()
        if still_filed:
            raise RuntimeError(f"{still_filed} documents are still filed after the rollback")
        survivors = connection.execute(text(
            "select count(*) from document_folders where code = any(:codes)"),
            {"codes": created_codes}).scalar()
        if survivors:
            raise RuntimeError(f"{survivors} folders this batch created survived the rollback")
        # The folders this batch REUSED must still be there.
        reused = sorted(set(target_codes) - set(created_codes))
        if reused:
            present = connection.execute(text(
                "select count(*) from document_folders where code = any(:codes)"),
                {"codes": reused}).scalar()
            if present != len(reused):
                raise RuntimeError(f"only {present} of {len(reused)} reused folders survive — the "
                                   "rollback deleted a folder it did not create")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['restored']} documents unfiled, "
            f"{report['folders_deleted']} folders deleted; "
            f"{len(set(target_codes) - set(created_codes))} reused folders left intact")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    out_path = Path(receipt_path).parent / "rollback_receipt.json"
    out_path.write_bytes((json.dumps({
        "rolled_back_at": datetime.now(UTC).isoformat(), "batch": BATCH_NAME,
        "documents_restored": report["restored"], "folders_deleted": report["folders_deleted"],
        "deleted_folder_codes": created_codes, "plan_digest": receipt["plan_digest"],
        "snapshot": report["snapshot"], "actor_user_id": actor_user_id,
        "confirm_phrase": want, "committed": True,
    }, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    out(f"  receipt: {out_path}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reverse the document filing batch 2 apply.")
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--candidate", default=None,
                    help="the frozen candidate CSV; when given, the plan digest is re-derived")
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    args = ap.parse_args(argv)
    run(args.receipt, candidate_csv=args.candidate, apply_changes=args.apply,
        confirm=args.confirm, actor_user_id=args.actor_user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
