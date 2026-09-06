#!/usr/bin/env python3
"""PHASE B — canonical document filing. Documents only. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_canonical_filing.py --manifest var/canonical_filing/phase_b_filing_manifest.json

    # writes, and only with every key turned at once
    python scripts/apply_canonical_filing.py --manifest <path> --manifest-sha256 <sha> \\
        --actor-user-id 1 --confirm APPLY-CANONICAL-FILING-PHASE_B_DOCUMENTS-<n> --apply

WHAT IT MAY TOUCH
------------------
``documents.folder_id``, on exactly the manifest's rows. One column. Ownership, category,
classification, subcategory, tags, display_name, review_status, lifecycle and every storage field
are fingerprinted before and after and proved identical. ``document_folders`` is fingerprinted too,
which is how "Phase B cannot create a folder" becomes a checked property rather than a promise: if
the folder table changes at all during this transaction, the transaction aborts.

IT FILES INTO AN APPROVED TREE OR IT DOES NOT RUN
--------------------------------------------------
Every folder the manifest names must already exist, with the identity Phase A approved. A missing
folder is an abort, never a create — "create what's missing" is exactly how a Phase B silently
becomes a Phase A. The live folders are re-hashed and compared to the Phase A folder-manifest digest
recorded in this manifest, so Phase B cannot be pointed at a tree nobody approved, and cannot
tolerate one that changed after approval.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_ROOT = REPO_ROOT / "var" / "canonical_filing_phase_b"
SNAPSHOT_CSV = "rollback_snapshot_phase_b_filing.csv"
SNAPSHOT_COLUMNS = ["document_id", "previous_folder_id", "target_folder_id", "target_folder_code",
                    "owner_scope_type", "owner_scope_id", "service_code", "tax_year"]

AUDIT_ACTION = "document.canonical_filed"
REQUIRED_ACTOR_USER_ID = 1
ADVISORY_LOCK_KEY = "canonical-filing-phase-b"

#: Columns Phase B must not change. Fingerprinted before and after on the target rows.
PROTECTED_COLUMNS = ("person_id", "household_id", "organization_id", "category", "classification",
                     "subcategory", "tags", "display_name", "review_status", "status", "archived",
                     "deleted_at", "storage_path", "storage_uri", "original_name", "stored_name",
                     "sha256")

_FOLDERS_FINGERPRINT = ("select md5(string_agg(f::text, E'\n' order by f.id)) "
                        "from document_folders f")
_NON_TARGET_FOLDER_FP = ("select md5(string_agg(id::text || '|' || coalesce(folder_id::text,''), "
                         "E'\n' order by id)) from documents where id <> all(:ids)")

_LOCK_SQL = """
    select id, folder_id, person_id, household_id, organization_id, status, archived, deleted_at,
           review_status
      from documents where id = any(:ids) order by id for update
"""


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _protected_fingerprint_sql() -> str:
    parts = " || '|' || ".join(f"coalesce({c}::text,'')" for c in PROTECTED_COLUMNS)
    return (f"select md5(string_agg(id::text || '|' || {parts}, E'\n' order by id)) "
            "from documents where id = any(:ids)")


_TARGET_PROTECTED_FP = _protected_fingerprint_sql()


def _out(report, message):
    report["log"].append(message)
    print(message)


def load_manifest(path, *, expect_sha=None):
    from app.services.canonical_filing_phases import ASSIGNMENT_FIELDS
    from app.services.filing_manifest import (
        PHASE_B,
        ManifestError,
        digest_of,
        require,
        require_phase,
        sha256_of,
    )

    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise Abort(f"ABORT: manifest not found: {manifest_path}")
    digest = sha256_of(manifest_path)
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        require_phase(manifest, PHASE_B)
        assignments = manifest.get("assignments") or []
        require(bool(assignments), "manifest contains no assignments")
        require(len(assignments) == manifest.get("document_count"),
                f"manifest lists {len(assignments)} assignments but claims "
                f"{manifest.get('document_count')}")
        require(bool(manifest.get("folder_manifest_digest")),
                "manifest does not name the phase A folder manifest it was planned against")
        recomputed = digest_of([{k: a[k] for k in ASSIGNMENT_FIELDS} for a in assignments])
        require(recomputed == manifest.get("assignment_digest"),
                f"assignment digest {recomputed} != recorded {manifest.get('assignment_digest')} "
                "— the manifest was edited")
        seen = set()
        for assignment in assignments:
            document_id = int(assignment["document_id"])
            require(document_id not in seen, f"duplicate document_id {document_id}")
            seen.add(document_id)
    except ManifestError as exc:
        raise Abort(str(exc)) from exc
    return manifest, digest


def _live_folders(connection, codes):
    from sqlalchemy import text

    rows = connection.execute(text(
        "select id, code, name, parent_folder_id, folder_kind, owner_scope_type, owner_scope_id, "
        "       service_code, tax_year "
        "  from document_folders where owner_scope_type is not null order by id"), {}).mappings()
    live = {r["code"]: dict(r) for r in rows}
    missing = sorted(set(codes) - set(live))
    return live, missing


def _verify_folder_tree(connection, manifest, codes):
    """Every named folder exists, and the live canonical tree hashes to the approved digest."""
    from app.services.canonical_filing_phases import folder_manifest_digest_of

    live, missing = _live_folders(connection, codes)
    if missing:
        raise Abort(f"ABORT: {len(missing)} approved folders do not exist "
                    f"(e.g. {missing[:5]}) — phase A has not run, or its result changed. "
                    "Phase B never creates a folder.")
    by_id = {record["id"]: record for record in live.values()}
    nodes = []
    for record in live.values():
        parent = by_id.get(record["parent_folder_id"])
        nodes.append({
            "code": record["code"], "kind": record["folder_kind"], "name": record["name"],
            "parent_code": parent["code"] if parent else None,
            "owner_scope_type": record["owner_scope_type"],
            "owner_scope_id": record["owner_scope_id"],
            "service_code": record["service_code"],
            "tax_year": None if record["tax_year"] is None else int(record["tax_year"]),
        })
    digest = folder_manifest_digest_of(nodes)
    if digest != manifest["folder_manifest_digest"]:
        raise Abort(f"ABORT: live folder tree digest {digest} != the phase A manifest digest "
                    f"{manifest['folder_manifest_digest']} this batch was planned against")
    return live


def run(manifest_path, *, apply_changes=False, confirm=None, actor_user_id=None,
        manifest_sha256=None, report_dir=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.filing_manifest import PHASE_B, confirm_phrase

    manifest, manifest_digest = load_manifest(manifest_path, expect_sha=manifest_sha256)
    assignments = manifest["assignments"]
    ids = sorted(int(a["document_id"]) for a in assignments)
    codes = sorted({a["folder_code"] for a in assignments})
    expected_phrase = confirm_phrase(PHASE_B, len(assignments))

    report = {
        "phase": PHASE_B, "batch_id": manifest["batch_id"],
        "manifest_sha256": manifest_digest,
        "assignment_digest": manifest["assignment_digest"],
        "folder_manifest_digest": manifest["folder_manifest_digest"],
        "manifest_documents": len(assignments), "assigned": 0, "audit_rows": 0,
        "committed": False, "dry_run": not apply_changes, "log": [],
        "snapshot": None, "snapshot_sha256": None, "report_dir": None,
        "request_id": str(uuid.uuid4()),
    }
    _out(report, f"PHASE B — {manifest['batch_id']}")
    _out(report, f"  manifest sha256: {manifest_digest}")
    _out(report, f"  documents: {len(assignments)}  destinations: {len(codes)}")

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
        live_folders = _verify_folder_tree(connection, manifest, codes)
        _out(report, f"  folder tree verified against the phase A digest ({len(codes)} targets)")

        fingerprints_before = {
            "protected": connection.execute(text(_TARGET_PROTECTED_FP), {"ids": ids}).scalar(),
            "non_target_folder": connection.execute(text(_NON_TARGET_FOLDER_FP),
                                                    {"ids": ids}).scalar(),
            "folders": connection.execute(text(_FOLDERS_FINGERPRINT)).scalar(),
        }
        locked = {r["id"]: dict(r) for r in
                  connection.execute(text(_LOCK_SQL), {"ids": ids}).mappings()}

        failures = []
        for assignment in assignments:
            document_id = int(assignment["document_id"])
            row = locked.get(document_id)
            if row is None:
                failures.append((document_id, "did not lock"))
            elif row["status"] == "deleted" or row["deleted_at"] is not None:
                failures.append((document_id, "deleted"))
            elif row["archived"]:
                failures.append((document_id, "archived"))
            elif row["folder_id"] is not None:
                failures.append((document_id, f"already filed in folder {row['folder_id']}"))
            else:
                # Ownership must still be exactly one scope, and the same one the manifest
                # approved. Counting the non-null scope columns is what makes this equivalent to
                # the preview's ``filing_scope_state == "resolved"`` rather than merely checking
                # that the approved column happens to still hold the approved value.
                scope_type = assignment["owner_scope_type"]
                present = [k for k in ("person_id", "household_id", "organization_id")
                           if row.get(k) is not None]
                current = row.get(f"{scope_type}_id")
                if len(present) != 1:
                    failures.append((document_id,
                                     f"ownership is no longer a single scope: {present}"))
                elif current != assignment["owner_scope_id"]:
                    failures.append((document_id,
                                     f"ownership drifted: {scope_type}_id is {current!r}, manifest "
                                     f"approved {assignment['owner_scope_id']!r}"))
        if failures:
            for document_id, why in failures[:10]:
                _out(report, f"    DRIFT document {document_id}: {why}")
            raise Abort(f"ABORT: {len(failures)} documents no longer match the approved manifest")
        _out(report, f"  revalidated {len(assignments)} documents under lock — no drift")

        if not apply_changes:
            _out(report, "  DRY RUN — every gate passed; nothing written, no snapshot taken")
            transaction.rollback()
            return report

        out_dir = Path(report_dir or (REPORT_ROOT / manifest["batch_id"]))
        snapshot_path, snapshot_sha = write_snapshot(assignments, locked, live_folders, out_dir)
        report["snapshot"], report["snapshot_sha256"] = str(snapshot_path), snapshot_sha
        report["report_dir"] = str(out_dir)
        _out(report, f"  rollback snapshot: {snapshot_path}")

        for assignment in assignments:
            document_id = int(assignment["document_id"])
            target = live_folders[assignment["folder_code"]]["id"]
            updated = connection.execute(text(
                "update documents set folder_id = :folder_id "
                " where id = :id and folder_id is null "
                "   and status <> 'deleted' and deleted_at is null and archived = false "
                "returning id"), {"folder_id": target, "id": document_id}).first()
            if updated is None:
                raise Abort(f"ABORT: document {document_id} did not file (state moved under lock)")
            report["assigned"] += 1
            write_audit_event(
                action=AUDIT_ACTION, entity_type="document", entity_id=document_id,
                actor_user_id=actor_user_id, request_id=report["request_id"],
                metadata={"phase": PHASE_B, "batch_id": manifest["batch_id"],
                          "document_id": document_id,
                          "previous_folder_id": locked[document_id]["folder_id"],
                          "new_folder_id": target, "folder_code": assignment["folder_code"],
                          "folder_path": assignment.get("folder_path"),
                          "service_code": assignment["service_code"],
                          "service_source": assignment.get("service_source"),
                          "derivation_rule": assignment.get("derivation_rule"),
                          "backing_document_count": assignment.get("backing_document_count"),
                          "backing_digest": assignment.get("backing_digest"),
                          "tax_year": assignment["tax_year"],
                          "manifest_sha256": manifest_digest,
                          "assignment_digest": manifest["assignment_digest"]},
                conn=connection)
        _out(report, f"  filed {report['assigned']} documents")

        # --- post-write invariants, all inside the transaction
        if report["assigned"] != len(assignments):
            raise Abort(f"ABORT: {report['assigned']} assignments, expected {len(assignments)}")
        placed = dict(connection.execute(text(
            "select d.id, f.code from documents d join document_folders f on f.id = d.folder_id "
            " where d.id = any(:ids)"), {"ids": ids}).all())
        wrong = [a["document_id"] for a in assignments
                 if placed.get(int(a["document_id"])) != a["folder_code"]]
        if wrong:
            raise Abort(f"ABORT: {len(wrong)} documents are in the wrong folder: {wrong[:5]}")

        fingerprints_after = {
            "protected": connection.execute(text(_TARGET_PROTECTED_FP), {"ids": ids}).scalar(),
            "non_target_folder": connection.execute(text(_NON_TARGET_FOLDER_FP),
                                                    {"ids": ids}).scalar(),
            "folders": connection.execute(text(_FOLDERS_FINGERPRINT)).scalar(),
        }
        if fingerprints_after["folders"] != fingerprints_before["folders"]:
            raise Abort("ABORT: document_folders changed during phase B — phase B must never "
                        "create or modify a folder")
        for key in ("protected", "non_target_folder"):
            if fingerprints_after[key] != fingerprints_before[key]:
                raise Abort(f"ABORT: {key} fingerprint changed — the batch touched more than "
                            "documents.folder_id on its own rows")

        audit_rows = connection.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": report["request_id"], "a": AUDIT_ACTION}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != len(assignments):
            raise Abort(f"ABORT: {audit_rows} audit rows for {len(assignments)} assignments")

        transaction.commit()
        report["committed"] = True
        _out(report, f"  COMMITTED {report['assigned']} assignments, {audit_rows} audit rows; "
                     "folder table and protected document fields unchanged")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    out_dir = Path(report["report_dir"])
    (out_dir / "phase_b_receipt.json").write_text(
        json.dumps({k: v for k, v in report.items() if k != "log"}, indent=2, sort_keys=True,
                   default=str) + "\n", encoding="utf-8", newline="\n")
    return report


def write_snapshot(assignments, locked, live_folders, out_dir: Path):
    from app.services.filing_manifest import sha256_of

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SNAPSHOT_CSV
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for assignment in assignments:
            document_id = int(assignment["document_id"])
            writer.writerow({
                "document_id": document_id,
                "previous_folder_id": locked[document_id]["folder_id"]
                if locked[document_id]["folder_id"] is not None else "",
                "target_folder_id": live_folders[assignment["folder_code"]]["id"],
                "target_folder_code": assignment["folder_code"],
                "owner_scope_type": assignment["owner_scope_type"],
                "owner_scope_id": assignment["owner_scope_id"],
                "service_code": assignment["service_code"], "tax_year": assignment["tax_year"],
            })
    return path, sha256_of(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PHASE B — canonical document filing.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", default=None)
    parser.add_argument("--apply", action="store_true", help="write (default is dry run)")
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--actor-user-id", type=int, default=None)
    parser.add_argument("--report-dir", default=None)
    args = parser.parse_args(argv)

    report = run(args.manifest, apply_changes=args.apply, confirm=args.confirm,
                 actor_user_id=args.actor_user_id, manifest_sha256=args.manifest_sha256,
                 report_dir=args.report_dir)
    print(f"\n  started at {datetime.now(UTC).isoformat()}")
    return 0 if (report["committed"] or report["dry_run"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
