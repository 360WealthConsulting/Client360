#!/usr/bin/env python3
"""Document filing persistence BATCH 1 — the scoped rollback. ALL-OR-NOTHING.

    python scripts/rollback_document_filing.py --receipt <apply_receipt.json>
    python scripts/rollback_document_filing.py --receipt <apply_receipt.json> --apply \\
        --actor-user-id 1 --confirm ROLLBACK-DOCUMENT-FILING-BATCH1-16304

WHAT IT RESTORES
----------------
Each document's ``folder_id`` as it was before the batch (NULL, per the snapshot), and then the
folder rows the batch created — years, then categories, then clients. Children before parents,
explicitly: ``document_folders.parent_folder_id`` is ``ON DELETE SET NULL``, so deleting a client
first would silently ORPHAN its categories rather than fail, leaving a tree nobody can account for.
The same applies to ``documents.folder_id`` — relying on SET NULL to unfile documents would delete
the evidence of what had been filed where. So this reverses by hand, in order, and proves each step.

IT REFUSES ANYTHING THAT IS NOT EXACTLY THIS BATCH
---------------------------------------------------
The receipt, the snapshot and the frozen preview must all agree before a database connection opens:
the receipt must say ``committed``, the snapshot must hash to what the receipt recorded, and the
frozen CSV SHA and plan digest must match the batch being reversed. Under the lock it further
requires that every target document still carries exactly the folder this batch gave it, that the
folder tree still looks exactly as the batch created it, that no folder has acquired a document from
outside the batch, and that no unexpected child folder has appeared beneath one. Any of those means
somebody has built on this batch, and reversing it would take their work with it.
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

SNAPSHOT_COLUMNS = ("document_id", "previous_folder_id", "target_folder_code",
                    "target_folder_path", "scope_type", "scope_id")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _int_or_none(value):
    value = (value or "").strip()
    return int(value) if value else None


def load_receipt(receipt_path, *, preview_csv=None) -> tuple[dict, list[dict], Path]:
    """Receipt + snapshot, each proved against the other before anything else happens."""
    from app.services.document_filing_apply import (
        BATCH_NAME,
        FROZEN_CSV_SHA256,
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
    if receipt.get("frozen_csv_sha256") != FROZEN_CSV_SHA256:
        raise Abort(f"ABORT: receipt frozen CSV SHA {receipt.get('frozen_csv_sha256')} != "
                    f"{FROZEN_CSV_SHA256}")

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
                     "scope_type": r["scope_type"], "scope_id": int(r["scope_id"])})

    # When the frozen preview is available, re-derive the plan and require the digest recorded in
    # the receipt. That proves the tree we are about to delete is the tree this batch created.
    if preview_csv is not None:
        plan = build_plan(preview_csv)
        if plan["plan_digest"] != receipt.get("plan_digest"):
            raise Abort(f"ABORT: rebuilt plan digest {plan['plan_digest']} != receipt "
                        f"{receipt.get('plan_digest')}")
        if plan["folder_manifest_digest"] != receipt.get("folder_manifest_digest"):
            raise Abort("ABORT: rebuilt folder manifest digest does not match the receipt")
        if len(plan["documents"]) != len(rows):
            raise Abort(f"ABORT: plan has {len(plan['documents'])} documents, snapshot has "
                        f"{len(rows)}")
    return receipt, rows, snapshot_path


def ancestor_codes(target_codes) -> set[str]:
    """Every folder code this batch created: each destination plus its ancestors.

    The codes are structural — ``client-…--category-…--year-…`` — so a destination names its own
    ancestry and the whole created set is derivable from the snapshot alone. That matters: the set
    decides what gets deleted, and deriving it from the record rather than from the live table is
    what keeps the rollback scoped to this batch.
    """
    codes: set[str] = set()
    for code in target_codes:
        parts = str(code).split("--")
        for depth in range(1, len(parts) + 1):
            codes.add("--".join(parts[:depth]))
    return codes


_LOCK_SQL = """
    select id, folder_id, status, archived, deleted_at
      from documents where id = any(:ids) order by id for update
"""


def run(receipt_path, *, preview_csv=None, apply_changes=False, confirm=None, actor_user_id=None,
        out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.document_filing_apply import BATCH_NAME, rollback_phrase

    receipt, rows, snapshot_path = load_receipt(receipt_path, preview_csv=preview_csv)
    want = rollback_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id != REQUIRED_ACTOR_USER_ID:
        raise Abort(f"ABORT: --apply requires --actor-user-id {REQUIRED_ACTOR_USER_ID}")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    codes = sorted({r["target_folder_code"] for r in rows})
    request_id = f"document-filing-rollback:{BATCH_NAME}:{receipt['plan_digest'][:12]}"
    report = {"batch": BATCH_NAME, "rows": len(rows), "restored": 0, "folders_deleted": 0,
              "committed": False, "confirm_phrase": want, "drift": [],
              "snapshot": str(snapshot_path), "request_id": request_id}

    out(f"receipt:  {receipt_path}")
    out(f"snapshot: {snapshot_path}")
    out(f"  batch {BATCH_NAME}, {len(rows)} documents, plan digest {receipt['plan_digest']}")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        # The batch's folder set is DERIVED FROM THE SNAPSHOT, not from a prefix scan of the table.
        # Scanning for a naming prefix would sweep in any folder somebody else happened to create
        # with a similar code and then delete it as if this batch had made it.
        batch_codes = ancestor_codes(codes)
        folder_rows = {r["code"]: dict(r) for r in connection.execute(text(
            "select id, code, name, parent_folder_id from document_folders "
            "where code = any(:codes) order by id"),
            {"codes": sorted(batch_codes)}).mappings()}
        target_codes = set(codes)
        missing = sorted(batch_codes - set(folder_rows))
        if missing:
            raise Abort(f"ABORT: {len(missing)} batch folders no longer exist: {missing[:5]}")
        assert target_codes <= batch_codes

        folder_id_to_code = {v["id"]: k for k, v in folder_rows.items()}
        batch_folder_ids = {folder_rows[c]["id"] for c in batch_codes}

        locked = {r["id"]: dict(r) for r in connection.execute(
            text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != ids:
            missing = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(missing)} snapshot documents did not lock: {missing[:5]}")

        for document_id in ids:
            current, snap = locked[document_id], by_id[document_id]
            actual_code = folder_id_to_code.get(current["folder_id"])
            if actual_code != snap["target_folder_code"]:
                report["drift"].append(
                    (document_id, f"is in {actual_code!r}, this batch filed it in "
                                  f"{snap['target_folder_code']!r}"))
        if report["drift"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["drift"][:5])
            raise Abort(f"ABORT: {len(report['drift'])} documents have moved since the apply — "
                        f"{head}")

        # No folder this batch created may hold a document from outside the batch...
        outsiders = connection.execute(text(
            "select count(*) from documents where folder_id = any(:fids) and id <> all(:ids)"),
            {"fids": sorted(batch_folder_ids), "ids": ids}).scalar()
        if outsiders:
            raise Abort(f"ABORT: {outsiders} documents outside this batch are filed in folders it "
                        "created — reversing would unfile somebody else's work")
        # ...and no folder outside the batch may have appeared beneath one of its folders.
        strays = connection.execute(text(
            "select count(*) from document_folders where parent_folder_id = any(:fids) "
            "and id <> all(:fids)"), {"fids": sorted(batch_folder_ids)}).scalar()
        if strays:
            raise Abort(f"ABORT: {strays} folders created outside this batch sit beneath its "
                        "folders")
        out(f"  drift check: {len(ids)} documents still exactly as this batch filed them; "
            f"{len(batch_folder_ids)} folders unreferenced from outside")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            transaction.rollback()
            return report

        # 1. unfile the documents (never rely on ON DELETE SET NULL)
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
                actor_user_id=actor_user_id, request_id=request_id,
                metadata={"batch": BATCH_NAME, "document_id": document_id,
                          "restored_folder_id": snap["previous_folder_id"],
                          "removed_folder_code": snap["target_folder_code"],
                          "plan_digest": receipt["plan_digest"], "request_id": request_id},
                conn=connection)

        # 2. delete folders children-before-parents: years, categories, clients
        def _depth(code):
            return code.count("--")
        for code in sorted(batch_codes, key=lambda c: (-_depth(c), c)):
            deleted = connection.execute(text(
                "delete from document_folders where id = :id "
                "and not exists (select 1 from documents where folder_id = :id) "
                "and not exists (select 1 from document_folders child "
                "                 where child.parent_folder_id = :id) returning id"),
                {"id": folder_rows[code]["id"]}).first()
            if deleted is None:
                raise RuntimeError(f"folder {code} could not be deleted (still referenced)")
            report["folders_deleted"] += 1

        remaining = connection.execute(text(
            "select count(*) from documents where id = any(:ids) and folder_id is not null"),
            {"ids": ids}).scalar()
        if remaining:
            raise RuntimeError(f"{remaining} documents are still filed after the rollback")
        left = connection.execute(text(
            "select count(*) from document_folders where code = any(:codes)"),
            {"codes": sorted(batch_codes)}).scalar()
        if left:
            raise RuntimeError(f"{left} batch folders survived the rollback")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['restored']} documents unfiled, "
            f"{report['folders_deleted']} folders deleted (children before parents)")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    receipt_out = Path(receipt_path).parent / "rollback_receipt.json"
    receipt_out.write_bytes((json.dumps({
        "rolled_back_at": datetime.now(UTC).isoformat(), "batch": BATCH_NAME,
        "documents_restored": report["restored"], "folders_deleted": report["folders_deleted"],
        "plan_digest": receipt["plan_digest"], "snapshot": report["snapshot"],
        "actor_user_id": actor_user_id, "confirm_phrase": want, "committed": True,
    }, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    out(f"  receipt: {receipt_out}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reverse the document filing batch 1 apply.")
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--preview", default=None,
                    help="the frozen preview CSV; when given, the plan digest is re-derived")
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    args = ap.parse_args(argv)
    run(args.receipt, preview_csv=args.preview, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
