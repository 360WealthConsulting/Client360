#!/usr/bin/env python3
"""PHASE A ROLLBACK — remove the folders this batch created. Drift-fatal.

    python scripts/rollback_canonical_folders.py --snapshot <snapshot csv>          # dry run
    python scripts/rollback_canonical_folders.py --snapshot <path> --snapshot-sha256 <sha> \\
        --actor-user-id 1 --confirm ROLLBACK-CANONICAL-FILING-PHASE_A_FOLDERS-<n> --apply

Deletes ONLY the rows the snapshot records as created by the batch (``existed_before = 0``).
Folders that already existed are left alone — rolling back a reuse would destroy somebody else's
folder.

DRIFT IS FATAL, AND SO IS AN ATTACHED DOCUMENT
-----------------------------------------------
If a folder now holds documents, or has acquired children the snapshot does not know about, the
rollback aborts rather than cascading. ``document_folders.parent_folder_id`` is ``ON DELETE SET
NULL``, so a blind delete would silently orphan a subtree instead of failing — which is precisely
the kind of quiet damage a rollback must never do.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AUDIT_ACTION = "document.canonical_folder_rolled_back"
REQUIRED_ACTOR_USER_ID = 1
ADVISORY_LOCK_KEY = "canonical-filing-phase-a"

_DOCUMENTS_FINGERPRINT = "select md5(string_agg(d::text, E'\n' order by d.id)) from documents d"


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def load_snapshot(path, *, expect_sha=None):
    from app.services.filing_manifest import sha256_of

    snapshot_path = Path(path)
    if not snapshot_path.is_file():
        raise Abort(f"ABORT: snapshot not found: {snapshot_path}")
    digest = sha256_of(snapshot_path)
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: snapshot SHA256 {digest} != approved {expect_sha}")
    with snapshot_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise Abort("ABORT: snapshot is empty")
    created = [r for r in rows if str(r.get("existed_before")).strip() == "0"]
    return rows, created, digest


def run(snapshot_path, *, apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_sha256=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.filing_manifest import PHASE_A, rollback_phrase

    rows, created, digest = load_snapshot(snapshot_path, expect_sha=snapshot_sha256)
    expected_phrase = rollback_phrase(PHASE_A, len(rows))
    report = {"phase": PHASE_A, "snapshot_sha256": digest, "snapshot_rows": len(rows),
              "created_by_batch": len(created), "deleted": 0, "committed": False,
              "dry_run": not apply_changes, "request_id": str(uuid.uuid4()), "log": []}

    def out(message):
        report["log"].append(message)
        print(message)

    out(f"PHASE A ROLLBACK — snapshot {digest}")
    out(f"  snapshot rows: {len(rows)}  created by the batch: {len(created)}")

    if apply_changes:
        if confirm != expected_phrase:
            raise Abort(f"ABORT: confirmation phrase {confirm!r} != {expected_phrase!r}")
        if int(actor_user_id or 0) != REQUIRED_ACTOR_USER_ID:
            raise Abort(f"ABORT: actor {actor_user_id!r} is not the approved actor "
                        f"{REQUIRED_ACTOR_USER_ID}")

    codes = sorted({r["folder_code"] for r in created})
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text("select pg_advisory_xact_lock(hashtext(:key))"),
                           {"key": ADVISORY_LOCK_KEY})
        fingerprint_before = connection.execute(text(_DOCUMENTS_FINGERPRINT)).scalar()
        live = {r["code"]: dict(r) for r in connection.execute(text(
            "select id, code from document_folders where code = any(:codes) order by code "
            "for update"), {"codes": codes}).mappings()} if codes else {}

        missing = sorted(set(codes) - set(live))
        if missing:
            raise Abort(f"ABORT: {len(missing)} folders the batch created no longer exist "
                        f"(e.g. {missing[:5]}) — state drifted since the apply")

        folder_ids = [record["id"] for record in live.values()]
        if folder_ids:
            attached = connection.execute(text(
                "select count(*) from documents where folder_id = any(:ids)"),
                {"ids": folder_ids}).scalar()
            if attached:
                raise Abort(f"ABORT: {attached} documents are filed in these folders — roll back "
                            "phase B before phase A")
            foreign_children = connection.execute(text(
                "select count(*) from document_folders where parent_folder_id = any(:ids) "
                "  and id <> all(:ids)"), {"ids": folder_ids}).scalar()
            if foreign_children:
                raise Abort(f"ABORT: {foreign_children} folders outside this batch are children of "
                            "folders it created — deleting would orphan them")

        if not apply_changes:
            out(f"  DRY RUN — would delete {len(folder_ids)} folders; nothing written")
            transaction.rollback()
            report["would_delete"] = len(folder_ids)
            return report

        # Children first: parent_folder_id is ON DELETE SET NULL, so deleting a parent early would
        # detach its children instead of failing.
        for code in sorted(live, key=lambda c: -len(c)):
            record = live[code]
            connection.execute(text("delete from document_folders where id = :id"),
                               {"id": record["id"]})
            report["deleted"] += 1
            write_audit_event(
                action=AUDIT_ACTION, entity_type="document_folder", entity_id=record["id"],
                actor_user_id=actor_user_id, request_id=report["request_id"],
                metadata={"phase": PHASE_A, "folder_code": code, "snapshot_sha256": digest},
                conn=connection)

        remaining = connection.execute(text(
            "select count(*) from document_folders where code = any(:codes)"),
            {"codes": codes}).scalar() if codes else 0
        if remaining:
            raise Abort(f"ABORT: {remaining} folders survived the rollback")
        if connection.execute(text(_DOCUMENTS_FINGERPRINT)).scalar() != fingerprint_before:
            raise Abort("ABORT: the documents table changed during the phase A rollback")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED — deleted {report['deleted']} folders; documents unchanged")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PHASE A ROLLBACK — delete created folders.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--snapshot-sha256", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--actor-user-id", type=int, default=None)
    args = parser.parse_args(argv)
    report = run(args.snapshot, apply_changes=args.apply, confirm=args.confirm,
                 actor_user_id=args.actor_user_id, snapshot_sha256=args.snapshot_sha256)
    print(json.dumps({k: v for k, v in report.items() if k != "log"}, indent=2, default=str))
    return 0 if (report["committed"] or report["dry_run"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
