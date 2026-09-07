#!/usr/bin/env python3
"""Guarded apply for the strict-safe document tax-year batch. Writes ONE column, nothing else.

    python scripts/apply_document_tax_year.py --candidate <csv>                       # dry run
    python scripts/apply_document_tax_year.py --candidate <csv> --candidate-sha256 <sha> \\
        --apply --confirm APPLY-STRICT-SAFE-TAX-YEAR-1-<n> --actor-user-id 1

WHAT IT MAY CHANGE
-------------------
``documents.tax_year``, on exactly the approved rows, where it is currently NULL. Nothing else. Not
tags, not ownership, not category/classification/subcategory, not folder_id, not status or
review_status, not display_name, not OCR rows or ocr_status, not document_sources, not
document_folders. Every one of those is fingerprinted before and after and a difference aborts.

WHY THIS DOES NOT RUN OCR
--------------------------
The evidence is a separate, already-completed non-persistent extraction pass. Re-extracting 756
documents takes hours; doing it while holding row locks would be a self-inflicted outage. Instead
the evidence is bound to CONTENT: the plan records each document's ``sha256``, the plan builder
re-hashes the file on disk to prove those bytes are the bytes the extractor read, and this script
re-checks ``documents.sha256`` under lock. If a document's content changed at any point, its hash
moved, the plan digest moved, and the batch refuses. Time is not what makes the evidence valid —
the content hash is. That is the TOCTOU protection, and it costs one comparison per row instead of
four hours.

This script never writes ``document_ocr`` and never touches ``documents.ocr_status``.
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

AUDIT_ACTION = "document.tax_year_resolved"
ADVISORY_LOCK_KEY = "strict-safe-tax-year"
REQUIRED_ACTOR_USER_ID = 1
REPORT_ROOT = REPO_ROOT / "var" / "strict_safe_tax_year"

SNAPSHOT_COLUMNS = ["document_id", "previous_tax_year", "new_tax_year", "sha256", "owner_scope"]

#: Document columns this batch must NOT change. Fingerprinted before and after the write.
PROTECTED_COLUMNS = ("person_id", "household_id", "organization_id", "category", "classification",
                     "subcategory", "tags", "display_name", "review_status", "status", "archived",
                     "deleted_at", "folder_id", "ocr_status", "sha256", "storage_uri",
                     "storage_path")

_PROTECTED_FP = ("select md5(string_agg(id::text || '|' || " +
                 " || '|' || ".join(f"coalesce({c}::text,'')" for c in PROTECTED_COLUMNS) +
                 ", '~' order by id)) from documents where id = any(:ids)")
_FOLDERS_FP = "select md5(string_agg(f::text, E'\\n' order by f.id)) from document_folders f"
_NON_TARGET_TAX_YEAR_FP = ("select md5(string_agg(id::text || '|' || coalesce(tax_year::text,''), "
                           "'~' order by id)) from documents where not (id = any(:ids))")


class Abort(SystemExit):
    pass


def _out(report, message):
    report["log"].append(message)
    print(message)


def run(candidate_csv, *, apply_changes=False, confirm=None, actor_user_id=None,
        candidate_sha256=None, report_dir=None, verify_file_hash=True) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.document_tax_year_resolution import (
        BATCH_ID,
        build_plan,
        confirm_phrase,
        plan_census,
        plan_digest,
    )

    report = {
        "batch_id": BATCH_ID, "assigned": 0, "audit_rows": 0, "committed": False,
        "dry_run": not apply_changes, "log": [], "snapshot": None, "snapshot_sha256": None,
        "report_dir": None, "request_id": str(uuid.uuid4()),
        "started_at": datetime.now(UTC).isoformat(),
    }

    connection = engine.connect()
    transaction = connection.begin()
    try:
        # One writer at a time. Two concurrent applies would each pass their own re-derivation.
        connection.execute(text("select pg_advisory_xact_lock(hashtext(:k))"),
                           {"k": ADVISORY_LOCK_KEY})

        plan = build_plan(connection, candidate_csv,
                          expect_sha=candidate_sha256 or None,
                          verify_file_hash=verify_file_hash)
        documents = plan["documents"]
        digest = plan_digest(plan)
        census = plan_census(plan)
        report.update({"plan_digest": digest, "census": census,
                       "candidate_documents": plan["candidate_count"],
                       "planned_documents": len(documents),
                       "dropped": plan["dropped"]})
        _out(report, f"{BATCH_ID}")
        _out(report, f"  candidate rows : {plan['candidate_count']}")
        _out(report, f"  planned rows   : {len(documents)}")
        _out(report, f"  plan digest    : {digest}")
        _out(report, f"  census         : {census}")

        if plan["dropped"]:
            for entry in plan["dropped"][:10]:
                _out(report, f"  DROPPED {entry['document_id']}: {entry['reason']}")
            raise Abort(f"ABORT: {len(plan['dropped'])} candidate rows no longer satisfy the "
                        "strict-safe rule. The approved plan has drifted; re-review it.")
        if not documents:
            raise Abort("ABORT: the plan is empty")

        expected_phrase = confirm_phrase(len(documents))
        if apply_changes:
            if confirm != expected_phrase:
                raise Abort(f"ABORT: confirmation phrase {confirm!r} != {expected_phrase!r}")
            if int(actor_user_id or 0) != REQUIRED_ACTOR_USER_ID:
                raise Abort(f"ABORT: actor {actor_user_id!r} is not the approved actor "
                            f"{REQUIRED_ACTOR_USER_ID}")

        ids = [row["document_id"] for row in documents]
        protected_before = connection.execute(text(_PROTECTED_FP), {"ids": ids}).scalar()
        folders_before = connection.execute(text(_FOLDERS_FP)).scalar()
        non_target_before = connection.execute(text(_NON_TARGET_TAX_YEAR_FP), {"ids": ids}).scalar()

        # Lock exactly the target rows, then re-read them under that lock.
        locked = {r["id"]: dict(r) for r in connection.execute(text(
            "select id, tax_year, sha256, status, archived, deleted_at, person_id, household_id, "
            "       organization_id from documents where id = any(:ids) order by id for update"),
            {"ids": ids}).mappings()}

        drift = []
        for row in documents:
            document_id = row["document_id"]
            live = locked.get(document_id)
            if live is None:
                drift.append((document_id, "row vanished before the write"))
                continue
            if live["tax_year"] is not None:
                drift.append((document_id, f"tax_year became {live['tax_year']}"))
            if live["sha256"] != row["sha256"]:
                drift.append((document_id, "content hash changed — the evidence no longer binds"))
            if live["status"] == "deleted" or live["deleted_at"] is not None or live["archived"]:
                drift.append((document_id, "document is no longer live"))
        if drift:
            for document_id, reason in drift[:5]:
                _out(report, f"  DRIFT {document_id}: {reason}")
            raise Abort(f"ABORT: {len(drift)} rows no longer match the approved plan")
        _out(report, f"  revalidated {len(documents)} documents under lock — no drift")

        if not apply_changes:
            _out(report, "  DRY RUN — every gate passed; nothing written, no snapshot taken")
            transaction.rollback()
            return report

        out_dir = Path(report_dir or (REPORT_ROOT / BATCH_ID))
        out_dir.mkdir(parents=True, exist_ok=True)
        snapshot = out_dir / "rollback_snapshot_tax_year.csv"
        with snapshot.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for row in documents:
                writer.writerow({
                    "document_id": row["document_id"],
                    "previous_tax_year": locked[row["document_id"]]["tax_year"]
                    if locked[row["document_id"]]["tax_year"] is not None else "",
                    "new_tax_year": row["tax_year"], "sha256": row["sha256"],
                    "owner_scope": row["owner_scope"]})
        from app.services.document_tax_year_resolution import sha256_of
        report["snapshot"] = str(snapshot)
        report["snapshot_sha256"] = sha256_of(snapshot)
        _out(report, f"  rollback snapshot: {snapshot}")

        for row in documents:
            updated = connection.execute(text(
                "update documents set tax_year = :year where id = :id and tax_year is null "
                "returning id"), {"year": row["tax_year"], "id": row["document_id"]}).first()
            if updated is None:
                raise Abort(f"ABORT: document {row['document_id']} was not updated — "
                            "another writer reached it inside this transaction")
            report["assigned"] += 1
            write_audit_event(conn=connection, action=AUDIT_ACTION,
                              entity_type="document", entity_id=row["document_id"],
                              actor_user_id=actor_user_id, request_id=report["request_id"],
                              metadata={"batch_id": BATCH_ID, "plan_digest": digest,
                                        "tax_year": row["tax_year"],
                                        "previous_tax_year": None,
                                        "extraction_rule": row["extraction_rule"],
                                        "evidence_source": row["evidence_source"],
                                        "form_family": row["form_family"],
                                        "sha256": row["sha256"]})
            report["audit_rows"] += 1

        if report["assigned"] != len(documents):
            raise Abort(f"ABORT: updated {report['assigned']} rows, expected {len(documents)}")

        wrong = connection.execute(text(
            "select count(*) from documents d where d.id = any(:ids) and d.tax_year is null"),
            {"ids": ids}).scalar()
        if wrong:
            raise Abort(f"ABORT: {wrong} target documents still have a NULL tax_year")
        if connection.execute(text(_PROTECTED_FP), {"ids": ids}).scalar() != protected_before:
            raise Abort("ABORT: a protected document field changed")
        if connection.execute(text(_FOLDERS_FP)).scalar() != folders_before:
            raise Abort("ABORT: the folder table changed")
        if connection.execute(text(_NON_TARGET_TAX_YEAR_FP), {"ids": ids}).scalar() != non_target_before:
            raise Abort("ABORT: a tax_year outside the batch changed")

        transaction.commit()
        report["committed"] = True
        report["report_dir"] = str(out_dir)
        _out(report, f"  COMMITTED {report['assigned']} tax years, {report['audit_rows']} audit rows")
        _out(report, "  post-write checks: every target set, protected fields unchanged, folder "
                     "table unchanged, non-target tax_year values unchanged")
        (out_dir / "receipt.json").write_text(
            json.dumps({k: v for k, v in report.items() if k != "log"},
                       indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8", newline="\n")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Guarded strict-safe tax-year apply.")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-sha256")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--actor-user-id", type=int)
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)
    run(args.candidate, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id, candidate_sha256=args.candidate_sha256,
        report_dir=args.report_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
