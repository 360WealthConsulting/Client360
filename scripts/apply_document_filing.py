#!/usr/bin/env python3
"""Document filing persistence BATCH 1 — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_document_filing.py --preview <frozen csv>

    # writes, and only with every key turned at once
    python scripts/apply_document_filing.py --preview <frozen csv> \\
        --actor-user-id 1 --confirm APPLY-DOCUMENT-FILING-BATCH1-16304 --apply

WHAT IT WRITES
--------------
``document_folders`` rows for the reviewed hierarchy, and ``documents.folder_id`` on exactly the
reviewed documents. **One column on the documents table, and nothing else.** Ownership, category,
classification, subcategory, tags, display_name, review_status, lifecycle and every storage field are
fingerprinted before and after and proved identical before the transaction may commit. No file is
moved, renamed, copied or deleted; this batch never touches storage at all.

WHY THE FOLDER INSERT IS NOT create_folder()
---------------------------------------------
``document_platform.service.create_folder`` opens its own ``engine.begin()`` per call. Three thousand
calls would be three thousand transactions, and a half-built folder tree is a state nobody reviewed.
The inserts here run in the caller's transaction, parents before children, so the tree and the
assignments commit together or not at all — the same reason the strict-safe ownership batches pass
``conn`` into the canonical resolver rather than letting it own its own transaction.

IDEMPOTENCY IS THREE CASES, NOT A REPAIR
-----------------------------------------
    PRISTINE        no batch folders, no target assignments      -> apply
    ALREADY_APPLIED every folder and every assignment is exactly
                    what this plan says it should be             -> report NO-OP, write nothing
    PARTIAL         anything else                                -> ABORT

A partial state is never repaired forward. Half of a reviewed batch is a batch nobody reviewed, and
silently completing it would hide whatever produced the half.
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

REPORT_ROOT = REPO_ROOT / "var" / "document_filing_batch1"

SNAPSHOT_CSV = "rollback_snapshot_document_filing_batch1.csv"
SNAPSHOT_COLUMNS = ["document_id", "previous_folder_id", "target_folder_code",
                    "target_folder_path", "scope_type", "scope_id"]

#: The one action this batch writes. One row per document assignment.
AUDIT_ACTION = "document.filing_folder_assigned"

#: Only this actor may apply the reviewed batch.
REQUIRED_ACTOR_USER_ID = 1

#: The document columns this batch must NOT change. Fingerprinted before and after.
PROTECTED_COLUMNS = ("person_id", "household_id", "organization_id", "category", "classification",
                     "subcategory", "tags", "display_name", "review_status", "status", "archived",
                     "deleted_at", "storage_path", "storage_uri", "original_name", "stored_name",
                     "sha256")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def sha256_of(path: Path) -> str:
    from app.services.document_filing_apply import sha256_of as _sha
    return _sha(path)


# --- fingerprints ---------------------------------------------------------------------------------

def _protected_fingerprint_sql(where: str) -> str:
    parts = " || '|' || ".join(f"coalesce({c}::text,'')" for c in PROTECTED_COLUMNS)
    return (f"select md5(string_agg(id::text || '|' || {parts}, E'\n' order by id)) "
            f"from documents where {where}")


_TARGET_PROTECTED_FP = _protected_fingerprint_sql("id = any(:ids)")
_NON_TARGET_FOLDER_FP = ("select md5(string_agg(id::text || '|' || coalesce(folder_id::text,''), "
                         "E'\n' order by id)) from documents where id <> all(:ids)")

_LOCK_SQL = """
    select id, folder_id, person_id, household_id, organization_id, status, archived, deleted_at,
           review_status
      from documents where id = any(:ids) order by id for update
"""


# --- live state -----------------------------------------------------------------------------------

def _existing_folders(conn, codes):
    from sqlalchemy import text
    rows = conn.execute(text(
        "select id, code, name, parent_folder_id, classification, created_by "
        "from document_folders where code = any(:codes) order by code"), {"codes": codes}).mappings()
    return {r["code"]: dict(r) for r in rows}


def classify_state(conn, plan) -> tuple[str, list[str]]:
    """PRISTINE, ALREADY_APPLIED or PARTIAL — plus the reasons for a partial verdict."""
    from sqlalchemy import text

    codes = [f["code"] for f in plan["folders"]]
    ids = [d["document_id"] for d in plan["documents"]]
    existing = _existing_folders(conn, codes)
    total_folders = conn.execute(text("select count(*) from document_folders")).scalar() or 0
    assigned = dict(conn.execute(text(
        "select id, folder_id from documents where id = any(:ids)"), {"ids": ids}).all())
    with_folder = sum(1 for v in assigned.values() if v is not None)

    if not existing and total_folders == 0 and with_folder == 0:
        return "PRISTINE", []

    problems: list[str] = []
    if len(existing) != len(codes):
        problems.append(f"{len(existing)} of {len(codes)} batch folder codes exist")
    if total_folders != len(codes):
        problems.append(f"document_folders holds {total_folders} rows, this batch defines "
                        f"{len(codes)}")
    if with_folder != len(ids):
        problems.append(f"{with_folder} of {len(ids)} target documents carry a folder_id")
    if problems:
        return "PARTIAL", problems

    # Every code exists and every target is assigned: it is ALREADY_APPLIED only if each row is
    # exactly what this plan says, hierarchy included.
    by_id = {v["id"]: k for k, v in existing.items()}
    for node in plan["folders"]:
        row = existing[node["code"]]
        if row["name"] != node["name"]:
            problems.append(f"folder {node['code']} name is {row['name']!r}, expected "
                            f"{node['name']!r}")
        if row["classification"] is not None:
            problems.append(f"folder {node['code']} classification is not NULL")
        expected_parent = node["parent_code"]
        actual_parent = by_id.get(row["parent_folder_id"]) if row["parent_folder_id"] else None
        if actual_parent != expected_parent:
            problems.append(f"folder {node['code']} parent is {actual_parent!r}, expected "
                            f"{expected_parent!r}")
    code_by_folder_id = {v["id"]: k for k, v in existing.items()}
    for document in plan["documents"]:
        actual = code_by_folder_id.get(assigned.get(document["document_id"]))
        if actual != document["folder_code"]:
            problems.append(f"document {document['document_id']} points at {actual!r}, expected "
                            f"{document['folder_code']!r}")
    if problems:
        return "PARTIAL", problems
    return "ALREADY_APPLIED", []


def _reference_maps(conn, plan):
    """People / household / organization rows needed to re-prove scope, via the preview's own rule."""
    from sqlalchemy import text

    people_ids = sorted({d["scope_id"] for d in plan["documents"] if d["scope_type"] == "person"})
    household_ids = sorted({d["scope_id"] for d in plan["documents"]
                            if d["scope_type"] == "household"})
    organization_ids = sorted({d["scope_id"] for d in plan["documents"]
                               if d["scope_type"] == "organization"})
    people = {r["id"]: dict(r) for r in conn.execute(text(
        "select id, first_name, last_name, full_name, household_id from people "
        "where id = any(:ids)"), {"ids": people_ids}).mappings()} if people_ids else {}
    households = {r["id"]: dict(r) for r in conn.execute(text(
        "select id, name from households where id = any(:ids)"),
        {"ids": household_ids}).mappings()} if household_ids else {}
    organizations = {r["id"]: dict(r) for r in conn.execute(text(
        "select id, name from relationship_entities where id = any(:ids)"),
        {"ids": organization_ids}).mappings()} if organization_ids else {}
    return people, households, organizations


def verify_targets(conn, plan, locked) -> list[tuple[int, str]]:
    """Re-prove every reviewed row against live state. Returns the failures, empty when all hold.

    Scope is re-derived with ``document_filing_preview.client_scope`` — the same function the preview
    used — rather than re-stated here, so this cannot drift from what was reviewed.
    """
    from app.services.document_filing_preview import client_scope

    people, households, organizations = _reference_maps(conn, plan)
    failures: list[tuple[int, str]] = []
    for document in plan["documents"]:
        document_id = document["document_id"]
        row = locked.get(document_id)
        if row is None:
            failures.append((document_id, "did not lock"))
            continue
        if row["status"] == "deleted" or row["deleted_at"] is not None:
            failures.append((document_id, "deleted"))
            continue
        if row["archived"]:
            failures.append((document_id, "archived"))
            continue
        if row["folder_id"] is not None:
            failures.append((document_id, f"already filed in folder {row['folder_id']}"))
            continue
        scope = client_scope(dict(row), people_by_id=people, households_by_id=households,
                             organizations_by_id=organizations)
        if scope["filing_scope_state"] != "resolved":
            failures.append((document_id,
                             f"filing scope is {scope['filing_scope_state']}"))
            continue
        if scope["proposed_scope_type"] != document["scope_type"] \
                or scope["proposed_scope_id"] != document["scope_id"]:
            failures.append((document_id,
                             f"ownership drifted to {scope['proposed_scope_type']}:"
                             f"{scope['proposed_scope_id']}, reviewed as "
                             f"{document['scope_type']}:{document['scope_id']}"))
            continue
        if scope["proposed_scope_name"] != document["scope_name"]:
            failures.append((document_id,
                             f"scope name drifted to {scope['proposed_scope_name']!r}"))
    return failures


# --- snapshot / receipt ----------------------------------------------------------------------------

def write_snapshot(plan, locked, out_dir: Path) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SNAPSHOT_CSV
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for document in sorted(plan["documents"], key=lambda d: d["document_id"]):
            previous = locked[document["document_id"]]["folder_id"]
            writer.writerow({
                "document_id": document["document_id"],
                "previous_folder_id": "" if previous is None else previous,
                "target_folder_code": document["folder_code"],
                "target_folder_path": document["folder_path"],
                "scope_type": document["scope_type"],
                "scope_id": document["scope_id"],
            })
    return path, sha256_of(path)


def _write_json(path: Path, payload) -> None:
    path.write_bytes((json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
                      + "\n").encode("utf-8"))


# --- the run ---------------------------------------------------------------------------------------

def confirm_phrase_for(plan) -> str:
    """The phrase this plan demands. Derived from the plan, never typed in twice."""
    from app.services.document_filing_apply import confirm_phrase
    return confirm_phrase(len(plan["documents"]))


def run(preview_csv, *, apply_changes=False, confirm=None, actor_user_id=None,
        output_root=REPORT_ROOT, out=print, _plan_overrides=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.document_filing_apply import (
        BATCH_NAME,
        FOLDER_CLASSIFICATION,
        FROZEN_CSV_SHA256,
        build_plan,
        confirm_phrase,
    )

    # ``_plan_overrides`` exists only so tests can drive the real code path with a small synthetic
    # preview. Production always uses the frozen constants: the default is the reviewed batch, and a
    # caller cannot relax a gate by omission.
    plan = build_plan(preview_csv, **(_plan_overrides or {}))
    documents, folders = plan["documents"], plan["folders"]
    want_phrase = confirm_phrase(len(documents))
    if apply_changes and confirm != want_phrase:
        raise Abort(f"ABORT: --apply requires --confirm {want_phrase}")
    if apply_changes and actor_user_id != REQUIRED_ACTOR_USER_ID:
        raise Abort(f"ABORT: --apply requires --actor-user-id {REQUIRED_ACTOR_USER_ID}")

    ids = [d["document_id"] for d in documents]
    request_id = f"document-filing:{BATCH_NAME}:{plan['plan_digest'][:12]}"
    report = {
        "batch": BATCH_NAME, "rows": len(documents), "folders_expected": len(folders),
        "plan_digest": plan["plan_digest"],
        "folder_manifest_digest": plan["folder_manifest_digest"],
        "frozen_csv_sha256": FROZEN_CSV_SHA256, "confirm_phrase": want_phrase,
        "request_id": request_id, "state": None, "folders_created": 0, "assigned": 0,
        "audit_rows": 0, "validated": 0, "committed": False, "snapshot": None,
        "snapshot_sha256": None, "failures": [], "report_dir": None,
    }

    out(f"frozen preview: {preview_csv}")
    out(f"  sha256 verified: {FROZEN_CSV_SHA256}")
    out(f"  plan: {len(documents)} documents, {len(folders)} folders")
    out(f"  plan digest:   {plan['plan_digest']}")
    out(f"  folder digest: {plan['folder_manifest_digest']}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(output_root) / f"document-filing-batch1-{stamp}"

    connection = engine.connect()
    transaction = connection.begin()
    try:
        if connection.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; the apply needs a writable session")

        state, problems = classify_state(connection, plan)
        report["state"] = state
        out(f"  live state: {state}")
        if state == "PARTIAL":
            head = "; ".join(problems[:5])
            raise Abort(f"ABORT: partial or conflicting filing state — {head}. This batch never "
                        "repairs forward.")
        if state == "ALREADY_APPLIED":
            out("  ALREADY_APPLIED — every folder and assignment already matches this plan. "
                "Nothing to do.")
            transaction.rollback()
            return report

        locked = {r["id"]: dict(r) for r in connection.execute(
            text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != sorted(ids):
            missing = sorted(set(ids) - set(locked))
            extra = sorted(set(locked) - set(ids))
            raise Abort(f"ABORT: locked set is not the reviewed set — {len(missing)} missing "
                        f"{missing[:5]}, {len(extra)} extra {extra[:5]}")
        out(f"  locked {len(locked)} documents FOR UPDATE (exact set equality)")

        failures = verify_targets(connection, plan, locked)
        report["failures"] = failures[:20]
        if failures:
            head = "; ".join(f"{d}:{w}" for d, w in failures[:5])
            raise Abort(f"ABORT: {len(failures)} of {len(ids)} documents no longer validate — {head}")
        report["validated"] = len(ids)
        out(f"  revalidated under lock: {report['validated']}/{len(ids)} "
            "(active, unarchived, unfiled, ownership scope unchanged)")

        fp_before = {
            "protected": connection.execute(text(_TARGET_PROTECTED_FP), {"ids": ids}).scalar(),
            "non_target_folder": connection.execute(text(_NON_TARGET_FOLDER_FP),
                                                    {"ids": ids}).scalar(),
        }

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written, no snapshot taken")
            transaction.rollback()
            out_dir.mkdir(parents=True, exist_ok=True)
            _write_json(out_dir / "plan_manifest.json",
                        {k: plan[k] for k in ("batch", "frozen_csv_sha256", "census",
                                              "plan_digest", "folder_manifest_digest")})
            _write_json(out_dir / "folder_manifest.json", plan["folders"])
            _write_json(out_dir / "dry_run_diagnostics.json",
                        {**{k: report[k] for k in ("batch", "rows", "folders_expected", "state",
                                                   "plan_digest", "folder_manifest_digest",
                                                   "validated", "failures")},
                         "checked_at": datetime.now(UTC).isoformat()})
            report["report_dir"] = str(out_dir)
            out(f"  report: {out_dir}")
            return report

        snapshot_path, snapshot_sha = write_snapshot(plan, locked, out_dir)
        report["snapshot"], report["snapshot_sha256"] = str(snapshot_path), snapshot_sha
        report["report_dir"] = str(out_dir)
        out(f"  rollback snapshot: {snapshot_path}")
        out(f"  snapshot sha256:   {snapshot_sha}")

        # --- folders, parents before children, in THIS transaction
        folder_ids: dict[str, int] = {}
        for node in folders:
            parent_id = folder_ids[node["parent_code"]] if node["parent_code"] else None
            new_id = connection.execute(text(
                "insert into document_folders (code, name, parent_folder_id, classification, "
                "created_by) values (:code, :name, :parent_folder_id, :classification, :created_by) "
                "returning id"),
                {"code": node["code"], "name": node["name"], "parent_folder_id": parent_id,
                 "classification": FOLDER_CLASSIFICATION,
                 "created_by": actor_user_id}).scalar_one()
            folder_ids[node["code"]] = new_id
            report["folders_created"] += 1
        out(f"  created {report['folders_created']} folders (clients, categories, years)")

        # --- assignments: folder_id ONLY, re-checked in the statement itself
        for document in documents:
            target = folder_ids[document["folder_code"]]
            updated = connection.execute(text(
                "update documents set folder_id = :folder_id "
                " where id = :id and folder_id is null "
                "   and status <> 'deleted' and deleted_at is null and archived = false "
                "returning id"),
                {"folder_id": target, "id": document["document_id"]}).first()
            if updated is None:
                raise RuntimeError(f"document {document['document_id']} did not file "
                                   "(state moved under the lock)")
            report["assigned"] += 1
            write_audit_event(
                action=AUDIT_ACTION, entity_type="document", entity_id=document["document_id"],
                actor_user_id=actor_user_id, request_id=request_id,
                metadata={
                    "batch": BATCH_NAME, "document_id": document["document_id"],
                    "previous_folder_id": locked[document["document_id"]]["folder_id"],
                    "new_folder_id": target, "folder_code": document["folder_code"],
                    "proposed_folder_path": document["folder_path"],
                    "frozen_csv_sha256": FROZEN_CSV_SHA256,
                    "plan_digest": plan["plan_digest"], "request_id": request_id,
                },
                conn=connection)
        out(f"  filed {report['assigned']} documents")

        # --- post-write invariants, all inside the transaction
        if report["folders_created"] != len(folders):
            raise RuntimeError(f"{report['folders_created']} folders created, expected "
                               f"{len(folders)}")
        if report["assigned"] != len(documents):
            raise RuntimeError(f"{report['assigned']} assignments, expected {len(documents)}")

        placed = dict(connection.execute(text(
            "select d.id, f.code from documents d join document_folders f on f.id = d.folder_id "
            "where d.id = any(:ids)"), {"ids": ids}).all())
        if len(placed) != len(ids):
            raise RuntimeError(f"{len(ids) - len(placed)} documents have no folder after the write")
        wrong = [d["document_id"] for d in documents
                 if placed.get(d["document_id"]) != d["folder_code"]]
        if wrong:
            raise RuntimeError(f"{len(wrong)} documents are in the wrong folder: {wrong[:5]}")

        total_folders = connection.execute(text("select count(*) from document_folders")).scalar()
        if total_folders != len(folders):
            raise RuntimeError(f"document_folders holds {total_folders} rows, expected "
                               f"{len(folders)}")
        bad_folders = connection.execute(text(
            "select count(*) from document_folders where classification is not null "
            "or created_by is distinct from :actor"), {"actor": actor_user_id}).scalar()
        if bad_folders:
            raise RuntimeError(f"{bad_folders} folders have a classification or a wrong creator")

        fp_after = {
            "protected": connection.execute(text(_TARGET_PROTECTED_FP), {"ids": ids}).scalar(),
            "non_target_folder": connection.execute(text(_NON_TARGET_FOLDER_FP),
                                                    {"ids": ids}).scalar(),
        }
        for key in ("protected", "non_target_folder"):
            if fp_after[key] != fp_before[key]:
                raise RuntimeError(f"{key} fingerprint changed — the batch touched more than "
                                   "documents.folder_id on its own rows")

        audit_rows = connection.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": request_id, "a": AUDIT_ACTION}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != len(documents):
            raise RuntimeError(f"{audit_rows} audit rows for {len(documents)} assignments")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['folders_created']} folders, {report['assigned']} assignments, "
            f"{report['audit_rows']} audit rows")
        out("  post-write checks: exact destinations, folder tree exact, protected document fields "
            "and non-target folder_id all unchanged")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    _write_json(out_dir / "plan_manifest.json",
                {k: plan[k] for k in ("batch", "frozen_csv_sha256", "census", "plan_digest",
                                      "folder_manifest_digest")})
    _write_json(out_dir / "folder_manifest.json", plan["folders"])
    _write_json(out_dir / "apply_receipt.json", {
        "applied_at": datetime.now(UTC).isoformat(),
        "baseline_commit": _baseline_commit(),
        "batch": BATCH_NAME,
        "frozen_csv_sha256": FROZEN_CSV_SHA256,
        "plan_digest": plan["plan_digest"],
        "folder_manifest_digest": plan["folder_manifest_digest"],
        "snapshot": report["snapshot"], "snapshot_sha256": report["snapshot_sha256"],
        "folders_created": report["folders_created"],
        "document_assignments": report["assigned"],
        "audit_rows": report["audit_rows"], "actor_user_id": actor_user_id,
        "request_id": request_id, "confirm_phrase": want_phrase, "committed": True,
    })
    out(f"  receipt: {out_dir / 'apply_receipt.json'}")
    return report


def _baseline_commit() -> str | None:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                                       text=True).strip()
    except Exception:       # noqa: BLE001 - provenance is best-effort, never a gate
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply the frozen document filing batch 1.")
    ap.add_argument("--preview", required=True, help="the frozen filing preview CSV")
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    ap.add_argument("--output-root", default=str(REPORT_ROOT))
    args = ap.parse_args(argv)
    run(args.preview, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id, output_root=Path(args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
