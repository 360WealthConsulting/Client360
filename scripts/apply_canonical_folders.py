#!/usr/bin/env python3
"""PHASE A — canonical folder materialization. Folders only. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_canonical_folders.py --manifest var/canonical_filing/phase_a_folder_manifest.json

    # writes, and only with every key turned at once
    python scripts/apply_canonical_folders.py --manifest <path> --manifest-sha256 <sha> \\
        --actor-user-id 1 --confirm APPLY-CANONICAL-FILING-PHASE_A_FOLDERS-<n> --apply

WHAT IT MAY TOUCH
------------------
``document_folders``, and nothing else. Every column of every ``documents`` row is fingerprinted
before and after and proved identical before the transaction may commit — so "Phase A cannot mutate
a document" is enforced by the database state, not by the absence of an UPDATE statement.

IDEMPOTENCY AND CONCURRENCY
----------------------------
Folders are matched on their owner-scoped natural identity, never on the display label. A folder
that already exists with the right identity is REUSED, not duplicated, so a re-run is a no-op that
still verifies the tree. Two Phase A runs at once are serialized by a transaction-scoped advisory
lock, and the partial unique index on the identity tuple is the second line of defence: even if the
lock were bypassed, a duplicate insert would be refused by Postgres rather than silently accepted.

The manifest is byte-pinned and phase-tagged. A Phase B manifest handed to this script is rejected
on its phase tag before anything is read.
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

REPORT_ROOT = REPO_ROOT / "var" / "canonical_filing_phase_a"
SNAPSHOT_CSV = "rollback_snapshot_phase_a_folders.csv"
SNAPSHOT_COLUMNS = ["folder_code", "existed_before", "folder_id", "kind",
                    "owner_scope_type", "owner_scope_id", "service_code", "tax_year"]

AUDIT_ACTION = "document.canonical_folder_materialized"
REQUIRED_ACTOR_USER_ID = 1

#: Serializes concurrent Phase A runs. Transaction-scoped: released on commit or rollback.
ADVISORY_LOCK_KEY = "canonical-filing-phase-a"

#: Proves Phase A touched no document. md5 over every column of every row.
_DOCUMENTS_FINGERPRINT = "select md5(string_agg(d::text, E'\n' order by d.id)) from documents d"

_REQUIRED_FOLDER_COLUMNS = ("owner_scope_type", "owner_scope_id", "folder_kind", "service_code",
                            "tax_year", "owner_source_label")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _out(report, message):
    report["log"].append(message)
    print(message)


def load_manifest(path, *, expect_sha=None):
    from app.services.canonical_filing_phases import folder_manifest_digest_of
    from app.services.filing_manifest import (
        PHASE_A,
        ManifestError,
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
        require_phase(manifest, PHASE_A)
        folders = manifest.get("folders") or []
        require(bool(folders), "manifest contains no folders")
        require(len(folders) == manifest.get("folder_count"),
                f"manifest lists {len(folders)} folders but claims {manifest.get('folder_count')}")
        recomputed = folder_manifest_digest_of(folders)
        require(recomputed == manifest.get("folder_manifest_digest"),
                f"folder manifest digest {recomputed} != recorded "
                f"{manifest.get('folder_manifest_digest')} — the manifest was edited")
    except ManifestError as exc:
        raise Abort(str(exc)) from exc
    return manifest, digest


def _preflight_schema(connection):
    """The identity columns must exist. Without them a folder has no identity but its label."""
    from sqlalchemy import text

    present = {r[0] for r in connection.execute(text(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = 'document_folders'"))}
    missing = [c for c in _REQUIRED_FOLDER_COLUMNS if c not in present]
    if missing:
        raise Abort(f"ABORT: document_folders is missing {missing} — migration cf01 has not been "
                    "applied to this database")


def _existing_by_identity(connection, folders):
    from sqlalchemy import text

    codes = sorted({f["code"] for f in folders})
    rows = connection.execute(text(
        "select id, code, name, parent_folder_id, folder_kind, owner_scope_type, owner_scope_id, "
        "       service_code, tax_year "
        "  from document_folders "
        " where owner_scope_type is not null or code = any(:codes)"), {"codes": codes}).mappings()
    by_identity, by_code = {}, {}
    for row in rows:
        record = dict(row)
        by_code[record["code"]] = record
        if record["owner_scope_type"] is not None:
            key = (record["owner_scope_type"], record["owner_scope_id"], record["folder_kind"],
                   record["service_code"] or "", -1 if record["tax_year"] is None
                   else int(record["tax_year"]))
            by_identity[key] = record
    return by_identity, by_code


def _identity_of(node):
    return (node["owner_scope_type"], int(node["owner_scope_id"]), node["kind"],
            node["service_code"] or "", -1 if node["tax_year"] is None else int(node["tax_year"]))


def run(manifest_path, *, apply_changes=False, confirm=None, actor_user_id=None,
        manifest_sha256=None, report_dir=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.filing_manifest import PHASE_A, confirm_phrase

    manifest, manifest_digest = load_manifest(manifest_path, expect_sha=manifest_sha256)
    folders = manifest["folders"]
    expected_phrase = confirm_phrase(PHASE_A, len(folders))

    report = {
        "phase": PHASE_A, "batch_id": manifest["batch_id"],
        "manifest_sha256": manifest_digest,
        "folder_manifest_digest": manifest["folder_manifest_digest"],
        "manifest_folders": len(folders), "created": 0, "reused": 0, "audit_rows": 0,
        "committed": False, "dry_run": not apply_changes, "log": [],
        "snapshot": None, "snapshot_sha256": None, "report_dir": None,
        "request_id": str(uuid.uuid4()),
    }
    _out(report, f"PHASE A — {manifest['batch_id']}")
    _out(report, f"  manifest sha256: {manifest_digest}")
    _out(report, f"  folders in manifest: {len(folders)} "
                 f"({manifest['census']}) destinations={manifest['destinations']}")

    if apply_changes:
        if confirm != expected_phrase:
            raise Abort(f"ABORT: confirmation phrase {confirm!r} != {expected_phrase!r}")
        if int(actor_user_id or 0) != REQUIRED_ACTOR_USER_ID:
            raise Abort(f"ABORT: actor {actor_user_id!r} is not the approved actor "
                        f"{REQUIRED_ACTOR_USER_ID}")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        _preflight_schema(connection)
        connection.execute(text("select pg_advisory_xact_lock(hashtext(:key))"),
                           {"key": ADVISORY_LOCK_KEY})
        fingerprint_before = connection.execute(text(_DOCUMENTS_FINGERPRINT)).scalar()

        by_identity, by_code = _existing_by_identity(connection, folders)
        to_create, reuse = [], {}
        for node in folders:
            identity = _identity_of(node)
            existing = by_identity.get(identity)
            if existing is not None:
                reuse[node["code"]] = existing
                if existing["code"] != node["code"]:
                    raise Abort(
                        f"ABORT: folder identity {identity} already exists as code "
                        f"{existing['code']!r}, manifest expects {node['code']!r}")
                continue
            clash = by_code.get(node["code"])
            if clash is not None:
                raise Abort(f"ABORT: code {node['code']!r} is taken by folder {clash['id']} with a "
                            f"different identity — refusing to reuse it")
            to_create.append(node)

        _out(report, f"  live state: {len(reuse)} reusable, {len(to_create)} to create")

        if not apply_changes:
            _out(report, "  DRY RUN — every gate passed; nothing written, no snapshot taken")
            transaction.rollback()
            report["created"], report["reused"] = 0, len(reuse)
            report["would_create"] = len(to_create)
            return report

        out_dir = Path(report_dir or (REPORT_ROOT / manifest["batch_id"]))
        snapshot_path, snapshot_sha = write_snapshot(folders, reuse, out_dir)
        report["snapshot"], report["snapshot_sha256"] = str(snapshot_path), snapshot_sha
        report["report_dir"] = str(out_dir)
        _out(report, f"  rollback snapshot: {snapshot_path}")

        folder_ids = {code: record["id"] for code, record in reuse.items()}
        for node in folders:
            if node["code"] in folder_ids:
                continue
            parent_id = folder_ids[node["parent_code"]] if node["parent_code"] else None
            if node["parent_code"] and parent_id is None:
                raise Abort(f"ABORT: parent {node['parent_code']!r} of {node['code']!r} has no id")
            new_id = connection.execute(text(
                "insert into document_folders "
                " (code, name, parent_folder_id, classification, created_by, owner_scope_type, "
                "  owner_scope_id, folder_kind, service_code, tax_year, owner_source_label) "
                "values (:code, :name, :parent_folder_id, null, :created_by, :owner_scope_type, "
                "        :owner_scope_id, :folder_kind, :service_code, :tax_year, :source_label) "
                "on conflict do nothing returning id"),
                {"code": node["code"], "name": node["name"], "parent_folder_id": parent_id,
                 "created_by": actor_user_id, "owner_scope_type": node["owner_scope_type"],
                 "owner_scope_id": node["owner_scope_id"], "folder_kind": node["kind"],
                 "service_code": node["service_code"], "tax_year": node["tax_year"],
                 "source_label": node.get("owner_source_label")}).scalar()
            if new_id is None:
                raise Abort(f"ABORT: folder {node['code']!r} was created concurrently — "
                            "the batch is no longer the plan that was reviewed")
            folder_ids[node["code"]] = new_id
            report["created"] += 1
            write_audit_event(
                action=AUDIT_ACTION, entity_type="document_folder", entity_id=new_id,
                actor_user_id=actor_user_id, request_id=report["request_id"],
                metadata={"phase": PHASE_A, "batch_id": manifest["batch_id"],
                          "folder_code": node["code"], "folder_kind": node["kind"],
                          "owner_scope_type": node["owner_scope_type"],
                          "owner_scope_id": node["owner_scope_id"],
                          "service_code": node["service_code"], "tax_year": node["tax_year"],
                          "manifest_sha256": manifest_digest,
                          "folder_manifest_digest": manifest["folder_manifest_digest"]},
                conn=connection)
        report["reused"] = len(reuse)
        _out(report, f"  created {report['created']} folders, reused {report['reused']}")

        # --- post-write invariants, all inside the transaction
        if report["created"] + report["reused"] != len(folders):
            raise Abort(f"ABORT: {report['created']} + {report['reused']} != {len(folders)}")
        live, _ = _existing_by_identity(connection, folders)
        for node in folders:
            record = live.get(_identity_of(node))
            if record is None:
                raise Abort(f"ABORT: folder {node['code']!r} is absent after the write")
            if record["code"] != node["code"] or record["name"] != node["name"]:
                raise Abort(f"ABORT: folder {node['code']!r} does not match the manifest")
            expected_parent = folder_ids[node["parent_code"]] if node["parent_code"] else None
            if record["parent_folder_id"] != expected_parent:
                raise Abort(f"ABORT: folder {node['code']!r} has the wrong parent")

        fingerprint_after = connection.execute(text(_DOCUMENTS_FINGERPRINT)).scalar()
        if fingerprint_after != fingerprint_before:
            raise Abort("ABORT: the documents table changed during phase A — phase A must never "
                        "mutate a document")

        audit_rows = connection.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": report["request_id"], "a": AUDIT_ACTION}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != report["created"]:
            raise Abort(f"ABORT: {audit_rows} audit rows for {report['created']} folders")

        transaction.commit()
        report["committed"] = True
        _out(report, f"  COMMITTED {report['created']} folders, {audit_rows} audit rows; "
                     "documents fingerprint unchanged")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    out_dir = Path(report["report_dir"])
    (out_dir / "phase_a_receipt.json").write_text(
        json.dumps({k: v for k, v in report.items() if k != "log"}, indent=2, sort_keys=True,
                   default=str) + "\n", encoding="utf-8", newline="\n")
    return report


def write_snapshot(folders, reuse, out_dir: Path):
    from app.services.filing_manifest import sha256_of

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SNAPSHOT_CSV
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for node in folders:
            existing = reuse.get(node["code"])
            writer.writerow({
                "folder_code": node["code"],
                "existed_before": int(existing is not None),
                "folder_id": existing["id"] if existing else "",
                "kind": node["kind"], "owner_scope_type": node["owner_scope_type"],
                "owner_scope_id": node["owner_scope_id"],
                "service_code": node["service_code"] or "",
                "tax_year": node["tax_year"] if node["tax_year"] is not None else "",
            })
    return path, sha256_of(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PHASE A — canonical folder materialization.")
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
