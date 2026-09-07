#!/usr/bin/env python3
"""PHASE B ROLLBACK — restore ``documents.folder_id`` to what the snapshot recorded. Drift-fatal.

    python scripts/rollback_canonical_filing.py --snapshot <snapshot csv>            # dry run
    python scripts/rollback_canonical_filing.py --snapshot <path> --snapshot-sha256 <sha> \\
        --actor-user-id 1 --confirm ROLLBACK-CANONICAL-FILING-PHASE_B_DOCUMENTS-<n> --apply

Restores one column on the batch's own rows. It deletes no folder — Phase A owns folders, and a
rollback that removed them would be performing the other phase's work under this phase's
authorization.

DRIFT IS FATAL
---------------
Every document must still hold exactly the folder the batch put it in. If one has been re-filed,
deleted or archived since, the whole rollback aborts: a partial restore is a state nobody reviewed.
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

AUDIT_ACTION = "document.canonical_filing_rolled_back"
REQUIRED_ACTOR_USER_ID = 1
ADVISORY_LOCK_KEY = "canonical-filing-phase-b"

_FOLDERS_FINGERPRINT = ("select md5(string_agg(f::text, E'\n' order by f.id)) "
                        "from document_folders f")


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
    return rows, digest


def run(snapshot_path, *, apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_sha256=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.filing_manifest import PHASE_B, rollback_phrase

    rows, digest = load_snapshot(snapshot_path, expect_sha=snapshot_sha256)
    expected_phrase = rollback_phrase(PHASE_B, len(rows))
    ids = sorted(int(r["document_id"]) for r in rows)
    report = {"phase": PHASE_B, "snapshot_sha256": digest, "snapshot_rows": len(rows),
              "restored": 0, "committed": False, "dry_run": not apply_changes,
              "request_id": str(uuid.uuid4()), "log": []}

    def out(message):
        report["log"].append(message)
        print(message)

    out(f"PHASE B ROLLBACK — snapshot {digest}")
    out(f"  documents in snapshot: {len(rows)}")

    if apply_changes:
        if confirm != expected_phrase:
            raise Abort(f"ABORT: confirmation phrase {confirm!r} != {expected_phrase!r}")
        if int(actor_user_id or 0) != REQUIRED_ACTOR_USER_ID:
            raise Abort(f"ABORT: actor {actor_user_id!r} is not the approved actor "
                        f"{REQUIRED_ACTOR_USER_ID}")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text("select pg_advisory_xact_lock(hashtext(:key))"),
                           {"key": ADVISORY_LOCK_KEY})
        folders_before = connection.execute(text(_FOLDERS_FINGERPRINT)).scalar()
        locked = {r["id"]: dict(r) for r in connection.execute(text(
            "select id, folder_id, status, archived, deleted_at from documents "
            " where id = any(:ids) order by id for update"), {"ids": ids}).mappings()}

        failures = []
        for row in rows:
            document_id = int(row["document_id"])
            live = locked.get(document_id)
            expected = int(row["target_folder_id"]) if str(row["target_folder_id"]).strip() else None
            if live is None:
                failures.append((document_id, "did not lock"))
            elif live["folder_id"] != expected:
                failures.append((document_id,
                                 f"folder_id is {live['folder_id']!r}, the batch set {expected!r}"))
        if failures:
            for document_id, why in failures[:10]:
                out(f"    DRIFT document {document_id}: {why}")
            raise Abort(f"ABORT: {len(failures)} documents drifted since the apply")
        out(f"  revalidated {len(rows)} documents under lock — no drift")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            transaction.rollback()
            return report

        for row in rows:
            document_id = int(row["document_id"])
            previous = (int(row["previous_folder_id"])
                        if str(row["previous_folder_id"]).strip() else None)
            updated = connection.execute(text(
                "update documents set folder_id = :previous where id = :id returning id"),
                {"previous": previous, "id": document_id}).first()
            if updated is None:
                raise Abort(f"ABORT: document {document_id} did not restore")
            report["restored"] += 1
            write_audit_event(
                action=AUDIT_ACTION, entity_type="document", entity_id=document_id,
                actor_user_id=actor_user_id, request_id=report["request_id"],
                metadata={"phase": PHASE_B, "document_id": document_id,
                          "restored_folder_id": previous,
                          "removed_folder_id": row["target_folder_id"],
                          "snapshot_sha256": digest},
                conn=connection)

        if report["restored"] != len(rows):
            raise Abort(f"ABORT: {report['restored']} restored, expected {len(rows)}")
        if connection.execute(text(_FOLDERS_FINGERPRINT)).scalar() != folders_before:
            raise Abort("ABORT: document_folders changed during the phase B rollback — phase B "
                        "never owns folders")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED — restored {report['restored']} documents; folder tree unchanged")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PHASE B ROLLBACK — restore folder_id.")
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
