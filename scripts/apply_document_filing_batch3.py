#!/usr/bin/env python3
"""Document filing persistence BATCH 3 — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_document_filing_batch3.py --candidate <frozen csv>

    # writes, and only with every key turned at once
    python scripts/apply_document_filing_batch3.py --candidate <frozen csv> \\
        --actor-user-id 1 --confirm APPLY-DOCUMENT-FILING-BATCH3-12 --apply

WHAT IT WRITES
--------------
The folder rows this plan needs that do not exist yet — 12 of them, the whole tree, because no
earlier batch reached these six clients — and ``documents.folder_id`` on exactly 12 documents.
The counts come from the plan, not from this script: any node an earlier batch already created is
REUSED, and this batch never renames, reparents, reclassifies or recreates one.

THE HARD PART IS NOT THE WRITE, IT IS THE TREE THAT IS ALREADY THERE
---------------------------------------------------------------------
Batch 1 could assert ``document_folders`` was empty. Batch 3 cannot: 3,134 rows are production state,
and a global count is far too weak a gate — it would pass while a folder had been renamed, reparented
or swapped underneath the plan. So this script proves the tree three ways instead:

* every node the plan expects to ALREADY exist is checked for exact name, exact parent and NULL
  classification, and is locked ``FOR UPDATE`` so it cannot be renamed or reparented mid-transaction;
* every node the plan expects to be ABSENT must genuinely be absent, and the unique index on ``code``
  is what makes a concurrent creation fail rather than silently merge;
* a fingerprint over every folder row EXCLUDING the ones being created must be byte-identical
  before and after, which catches a change to any pre-existing row, and the total must rise by
  exactly the number created.

A transaction-scoped advisory lock serialises two Batch 3 applies against each other, so the "absent"
finding cannot go stale between the check and the insert.

NO YEAR, ON PURPOSE
-------------------
These 12 documents were held back because their tax-year evidence contradicts itself. The plan layer
refuses any destination containing ``--year-``, and this script re-proves depth 2 for every row. The
batch declines to choose a year rather than choosing one quietly.

CLIENT CORROBORATION IS AS STRONG AS BATCH 1 AND 2 — IT WAS NOT, AND THAT IS WHY THIS EXISTS
---------------------------------------------------------------------------------------------
The first cut of this batch took 49 rows whose destination CLIENT rested on stored ownership
alone. It read the absence of ``no_client_confirmation_in_path`` from ``reasons`` as evidence of
confirmation; it is not. The preview returns ``conflicting_filing_evidence`` BEFORE it runs the
confirmation check, so that reason can never appear for this lane no matter what the paths say.

The plan layer now re-derives the corroboration itself, from each row's own recorded evidence,
and admits a row only when an available client filing path MECHANICALLY names the stored owner
under one of two deterministic rules (see :mod:`app.services.document_filing_batch3`). Of the 49,
7 match directly, 5 match a business's legal suffix under the reviewed primitive, 33 would need a
nickname, an initialism, a spacing variant or a dropped word, and 4 have a path that names no
client at all. The 37 are excluded and belong in a human review lane. What remains true of the
lane is unchanged: no different-client context, no category conflict, every conflict a tax-year
conflict.
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

REPORT_ROOT = REPO_ROOT / "var" / "document_filing_batch3"

SNAPSHOT_CSV = "rollback_snapshot_document_filing_batch3.csv"
SNAPSHOT_COLUMNS = ["document_id", "previous_folder_id", "target_folder_code",
                    "target_folder_path", "scope_type", "scope_id", "created_folder"]

#: The same action Batch 1 writes — it is the same kind of event. The batch is named in the
#: metadata and the request_id, which is what distinguishes and counts them.
AUDIT_ACTION = "document.filing_folder_assigned"

#: Batch 3 takes its actor from the CLI rather than pinning one, but an apply must still name a
#: real actor: an ownership-adjacent write with no attributable person is not auditable.
MINIMUM_ACTOR_USER_ID = 1

#: Document columns this batch must NOT change. Fingerprinted before and after.
PROTECTED_COLUMNS = ("person_id", "household_id", "organization_id", "category", "classification",
                     "subcategory", "tags", "display_name", "review_status", "status", "archived",
                     "deleted_at", "storage_path", "storage_uri", "original_name", "stored_name",
                     "sha256")

#: Key for the transaction-scoped advisory lock that serialises Batch 3 applies.
ADVISORY_LOCK_KEY = 0x0DF12B03


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _protected_fingerprint_sql() -> str:
    parts = " || '|' || ".join(f"coalesce({c}::text,'')" for c in PROTECTED_COLUMNS)
    return (f"select md5(string_agg(id::text || '|' || {parts}, E'\n' order by id)) "
            "from documents where id = any(:ids)")


_TARGET_PROTECTED_FP = _protected_fingerprint_sql()
_NON_TARGET_FOLDER_FP = ("select md5(string_agg(id::text || '|' || coalesce(folder_id::text,''), "
                         "E'\n' order by id)) from documents where id <> all(:ids)")

#: Fingerprint over the folder tree EXCLUDING the codes this batch creates. Must not move.
_FOLDER_TREE_FP = ("select md5(string_agg(id::text || '|' || code || '|' || name || '|' || "
                   "coalesce(parent_folder_id::text,'') || '|' || coalesce(classification,'') || "
                   "'|' || coalesce(created_by::text,''), E'\n' order by id)) "
                   "from document_folders where code <> all(:new_codes)")

_LOCK_DOCUMENTS_SQL = """
    select id, folder_id, person_id, household_id, organization_id, status, archived, deleted_at,
           review_status
      from documents where id = any(:ids) order by id for update
"""

_LOCK_FOLDERS_SQL = """
    select id, code, name, parent_folder_id, classification, created_by
      from document_folders where code = any(:codes) order by id for update
"""


def sha256_of(path: Path) -> str:
    from app.services.document_filing_batch3 import sha256_of as _sha
    return _sha(path)


def confirm_phrase_for(plan) -> str:
    from app.services.document_filing_batch3 import confirm_phrase
    return confirm_phrase(len(plan["documents"]))


# --- live state -----------------------------------------------------------------------------------

def classify_state(conn, plan, folder_rows) -> tuple[str, list[str], list[dict]]:
    """PRISTINE, ALREADY_APPLIED or PARTIAL, the reasons, and the nodes that must be created.

    ``folder_rows`` is the LOCKED snapshot of every plan node that currently exists, so the verdict
    and the inserts that follow are decided against the same rows nobody else can move.
    """
    from sqlalchemy import text

    expect_new = plan.get("expect_new_folders")
    expect_reused = plan.get("expect_reused_folders")
    ids = [d["document_id"] for d in plan["documents"]]
    assigned = dict(conn.execute(text(
        "select id, folder_id from documents where id = any(:ids)"), {"ids": ids}).all())
    filed = sum(1 for v in assigned.values() if v is not None)

    present = {code: row for code, row in folder_rows.items()}
    missing = [node for node in plan["folders"] if node["code"] not in present]
    code_by_id = {row["id"]: code for code, row in present.items()}

    problems: list[str] = []
    # Whatever the verdict, an existing node the plan relies on must be COMPATIBLE. An incompatible
    # one is never repaired: the plan's destination would not mean what it was reviewed to mean.
    for node in plan["folders"]:
        row = present.get(node["code"])
        if row is None:
            continue
        if row["name"] != node["name"]:
            problems.append(f"folder {node['code']} is named {row['name']!r}, plan says "
                            f"{node['name']!r}")
        if row["classification"] is not None:
            problems.append(f"folder {node['code']} has a non-NULL classification")
        actual_parent = code_by_id.get(row["parent_folder_id"]) if row["parent_folder_id"] else None
        if actual_parent != node["parent_code"]:
            problems.append(f"folder {node['code']} parent is {actual_parent!r}, plan says "
                            f"{node['parent_code']!r}")
    if problems:
        return "PARTIAL", problems, missing

    if not missing and filed == len(ids):
        for document in plan["documents"]:
            actual = code_by_id.get(assigned.get(document["document_id"]))
            if actual != document["folder_code"]:
                problems.append(f"document {document['document_id']} is filed in {actual!r}, plan "
                                f"says {document['folder_code']!r}")
        if problems:
            return "PARTIAL", problems, missing
        return "ALREADY_APPLIED", [], missing

    if filed:
        problems.append(f"{filed} of {len(ids)} target documents are already filed")
    if expect_new is not None and len(missing) != expect_new:
        problems.append(f"{len(missing)} plan folders are missing, expected exactly {expect_new}")
    if expect_reused is not None and len(present) != expect_reused:
        problems.append(f"{len(present)} plan folders already exist, expected exactly "
                        f"{expect_reused}")
    if problems:
        return "PARTIAL", problems, missing
    return "PRISTINE", [], missing


def _reference_maps(conn, plan):
    from sqlalchemy import text
    pids = sorted({d["scope_id"] for d in plan["documents"] if d["scope_type"] == "person"})
    hids = sorted({d["scope_id"] for d in plan["documents"] if d["scope_type"] == "household"})
    oids = sorted({d["scope_id"] for d in plan["documents"] if d["scope_type"] == "organization"})
    people = {r["id"]: dict(r) for r in conn.execute(text(
        "select id, first_name, last_name, full_name, household_id from people where id=any(:i)"),
        {"i": pids}).mappings()} if pids else {}
    households = {r["id"]: dict(r) for r in conn.execute(text(
        "select id, name from households where id=any(:i)"), {"i": hids}).mappings()} if hids else {}
    organizations = {r["id"]: dict(r) for r in conn.execute(text(
        "select id, name from relationship_entities where id=any(:i)"),
        {"i": oids}).mappings()} if oids else {}
    return people, households, organizations


def verify_targets(conn, plan, locked) -> list[tuple[int, str]]:
    """Re-prove every reviewed document against live state, using the preview's own scope rule."""
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
            failures.append((document_id, f"filing scope is {scope['filing_scope_state']}"))
            continue
        if scope["proposed_scope_type"] != document["scope_type"] \
                or scope["proposed_scope_id"] != document["scope_id"]:
            failures.append((document_id,
                             f"ownership drifted to {scope['proposed_scope_type']}:"
                             f"{scope['proposed_scope_id']}, reviewed as "
                             f"{document['scope_type']}:{document['scope_id']}"))
            continue
        if scope["proposed_scope_name"] != document["scope_name"]:
            failures.append((document_id, f"scope name drifted to "
                                          f"{scope['proposed_scope_name']!r}"))
    return failures


# --- snapshot / receipt ----------------------------------------------------------------------------

def write_snapshot(plan, locked, created_codes, out_dir: Path) -> tuple[Path, str]:
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
                "scope_type": document["scope_type"], "scope_id": document["scope_id"],
                # Whether THIS batch created the destination decides whether the rollback may
                # delete it. A reused Batch 1 folder must survive a Batch 3 rollback.
                "created_folder": "1" if document["folder_code"] in created_codes else "0",
            })
    return path, sha256_of(path)


def _write_json(path: Path, payload) -> None:
    path.write_bytes((json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
                      + "\n").encode("utf-8"))


def _baseline_commit() -> str | None:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                                       text=True).strip()
    except Exception:       # noqa: BLE001 - provenance is best-effort, never a gate
        return None


# --- the run ---------------------------------------------------------------------------------------

def run(candidate_csv, *, apply_changes=False, confirm=None, actor_user_id=None,
        candidate_json=None, output_root=REPORT_ROOT, out=print, _plan_overrides=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.document_filing_batch3 import (
        BATCH_NAME,
        FOLDER_CLASSIFICATION,
        build_plan,
        confirm_phrase,
        verify_json_candidate,
    )

    plan = build_plan(candidate_csv, **(_plan_overrides or {}))
    json_sha = verify_json_candidate(candidate_json) if candidate_json else None
    documents, folders = plan["documents"], plan["folders"]
    want_phrase = confirm_phrase(len(documents))
    if apply_changes and confirm != want_phrase:
        raise Abort(f"ABORT: --apply requires --confirm {want_phrase}")
    if apply_changes and (actor_user_id is None or int(actor_user_id) < MINIMUM_ACTOR_USER_ID):
        raise Abort("ABORT: --apply requires --actor-user-id (a real user id)")

    ids = [d["document_id"] for d in documents]
    plan_codes = [f["code"] for f in folders]
    request_id = f"document-filing:{BATCH_NAME}:{plan['plan_digest'][:12]}"
    report = {
        "batch": BATCH_NAME, "rows": len(documents), "folder_nodes": len(folders),
        "plan_digest": plan["plan_digest"],
        "folder_manifest_digest": plan["folder_manifest_digest"],
        "candidate_csv_sha256": plan["candidate_csv_sha256"], "confirm_phrase": want_phrase,
        "request_id": request_id, "state": None, "folders_created": 0, "folders_reused": 0,
        "assigned": 0, "audit_rows": 0, "validated": 0, "committed": False, "snapshot": None,
        "snapshot_sha256": None, "failures": [], "created_codes": [], "report_dir": None,
    }

    out(f"frozen candidate: {candidate_csv}")
    out(f"  csv sha256 verified: {plan['candidate_csv_sha256']}")
    if json_sha:
        out(f"  json sha256 verified: {json_sha}")
    out(f"  plan: {len(documents)} documents, {len(folders)} folder nodes (all depth 2)")
    out(f"  plan digest:   {plan['plan_digest']}")
    out(f"  folder digest: {plan['folder_manifest_digest']}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(output_root) / f"document-filing-batch3-{stamp}"

    connection = engine.connect()
    transaction = connection.begin()
    try:
        if connection.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; the apply needs a writable session")

        # Serialise Batch 3 applies against each other for the life of this transaction, so the
        # "these folders are absent" finding cannot go stale before the inserts.
        connection.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        # Lock every plan node that exists BEFORE deciding anything about the tree.
        folder_rows = {r["code"]: dict(r) for r in connection.execute(
            text(_LOCK_FOLDERS_SQL), {"codes": plan_codes}).mappings()}
        state, problems, missing = classify_state(connection, plan, folder_rows)
        report["state"] = state
        report["folders_reused"] = len(folder_rows)
        report["created_codes"] = [n["code"] for n in missing]
        out(f"  live state: {state} ({len(folder_rows)} plan folders exist, "
            f"{len(missing)} to create)")
        if state == "PARTIAL":
            head = "; ".join(problems[:5])
            raise Abort(f"ABORT: partial or incompatible filing state — {head}. This batch never "
                        "repairs forward.")
        if state == "ALREADY_APPLIED":
            out("  ALREADY_APPLIED — every folder and assignment already matches this plan.")
            transaction.rollback()
            return report

        locked = {r["id"]: dict(r) for r in connection.execute(
            text(_LOCK_DOCUMENTS_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != sorted(ids):
            absent = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: locked set is not the reviewed set — {len(absent)} missing "
                        f"{absent[:5]}")
        out(f"  locked {len(locked)} documents FOR UPDATE (exact set equality)")

        failures = verify_targets(connection, plan, locked)
        report["failures"] = failures[:20]
        if failures:
            head = "; ".join(f"{d}:{w}" for d, w in failures[:5])
            raise Abort(f"ABORT: {len(failures)} of {len(ids)} documents no longer validate — "
                        f"{head}")
        report["validated"] = len(ids)
        out(f"  revalidated under lock: {report['validated']}/{len(ids)} "
            "(active, unarchived, unfiled, ownership scope unchanged)")

        new_codes = sorted(n["code"] for n in missing)
        fp_before = {
            "protected": connection.execute(text(_TARGET_PROTECTED_FP), {"ids": ids}).scalar(),
            "non_target_folder": connection.execute(text(_NON_TARGET_FOLDER_FP),
                                                    {"ids": ids}).scalar(),
            "folder_tree": connection.execute(text(_FOLDER_TREE_FP),
                                              {"new_codes": new_codes}).scalar(),
        }
        folders_before = connection.execute(text("select count(*) from document_folders")).scalar()

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written, no snapshot taken")
            transaction.rollback()
            out_dir.mkdir(parents=True, exist_ok=True)
            _write_json(out_dir / "plan_manifest.json",
                        {k: plan[k] for k in ("batch", "candidate_csv_sha256", "census",
                                              "plan_digest", "folder_manifest_digest")})
            _write_json(out_dir / "folder_manifest.json", plan["folders"])
            _write_json(out_dir / "dry_run_diagnostics.json", {
                "checked_at": datetime.now(UTC).isoformat(),
                "state": state, "validated": report["validated"],
                "folders_existing": len(folder_rows), "folders_to_create": new_codes,
                "folder_rows_before": folders_before, "failures": report["failures"]})
            report["report_dir"] = str(out_dir)
            out(f"  report: {out_dir}")
            return report

        snapshot_path, snapshot_sha = write_snapshot(plan, locked, set(new_codes), out_dir)
        report["snapshot"], report["snapshot_sha256"] = str(snapshot_path), snapshot_sha
        report["report_dir"] = str(out_dir)
        out(f"  rollback snapshot: {snapshot_path}")
        out(f"  snapshot sha256:   {snapshot_sha}")

        # --- create ONLY the missing nodes, parents before children
        folder_ids = {code: row["id"] for code, row in folder_rows.items()}
        for node in folders:                        # plan order is clients then categories
            if node["code"] in folder_ids:
                continue
            parent_id = folder_ids[node["parent_code"]] if node["parent_code"] else None
            if node["parent_code"] and parent_id is None:
                raise RuntimeError(f"folder {node['code']} has no parent to attach to")
            new_id = connection.execute(text(
                "insert into document_folders (code, name, parent_folder_id, classification, "
                "created_by) values (:code, :name, :parent_folder_id, :classification, :created_by) "
                "returning id"),
                {"code": node["code"], "name": node["name"], "parent_folder_id": parent_id,
                 "classification": FOLDER_CLASSIFICATION,
                 "created_by": actor_user_id}).scalar_one()
            folder_ids[node["code"]] = new_id
            report["folders_created"] += 1
        out(f"  created {report['folders_created']} folders, reused {report['folders_reused']}")

        # --- assignments: folder_id ONLY
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
                    "candidate_csv_sha256": plan["candidate_csv_sha256"],
                    "plan_digest": plan["plan_digest"], "request_id": request_id,
                    # NOT a ``tax_year`` key: ``audit.redact_metadata`` redacts that name, so a
                    # NULL year would be stored as "[REDACTED]" and read as a year that was
                    # recorded and hidden — the precise opposite of what this batch did. The
                    # depth records the same fact in a form the audit trail keeps.
                    "destination_depth": 2,
                },
                conn=connection)
        out(f"  filed {report['assigned']} documents")

        # --- post-write invariants
        if report["folders_created"] != len(missing):
            raise RuntimeError(f"{report['folders_created']} folders created, expected "
                               f"{len(missing)}")
        if report["assigned"] != len(documents):
            raise RuntimeError(f"{report['assigned']} assignments, expected {len(documents)}")

        placed = dict(connection.execute(text(
            "select d.id, f.code from documents d join document_folders f on f.id = d.folder_id "
            "where d.id = any(:ids)"), {"ids": ids}).all())
        wrong = [d["document_id"] for d in documents
                 if placed.get(d["document_id"]) != d["folder_code"]]
        if wrong:
            raise RuntimeError(f"{len(wrong)} documents are in the wrong folder: {wrong[:5]}")
        if any("--year-" in code for code in placed.values()):
            raise RuntimeError("a batch 3 document was filed into a YEAR folder")

        folders_after = connection.execute(text("select count(*) from document_folders")).scalar()
        if folders_after != folders_before + len(missing):
            raise RuntimeError(f"folder rows went {folders_before} -> {folders_after}, expected "
                               f"+{len(missing)}")
        bad_new = connection.execute(text(
            "select count(*) from document_folders where code = any(:codes) "
            "and (classification is not null or created_by is distinct from :actor)"),
            {"codes": new_codes, "actor": actor_user_id}).scalar()
        if bad_new:
            raise RuntimeError(f"{bad_new} newly created folders are misconfigured")

        fp_after = {
            "protected": connection.execute(text(_TARGET_PROTECTED_FP), {"ids": ids}).scalar(),
            "non_target_folder": connection.execute(text(_NON_TARGET_FOLDER_FP),
                                                    {"ids": ids}).scalar(),
            "folder_tree": connection.execute(text(_FOLDER_TREE_FP),
                                              {"new_codes": new_codes}).scalar(),
        }
        for key in ("protected", "non_target_folder", "folder_tree"):
            if fp_after[key] != fp_before[key]:
                raise RuntimeError(f"{key} fingerprint changed — this batch touched more than "
                                   "documents.folder_id on its rows and the four new folders")

        audit_rows = connection.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": request_id, "a": AUDIT_ACTION}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != len(documents):
            raise RuntimeError(f"{audit_rows} audit rows for {len(documents)} assignments")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['folders_created']} new folders, {report['assigned']} "
            f"assignments, {report['audit_rows']} audit rows")
        out("  post-write checks: exact destinations, no year folders, existing tree byte-identical, "
            "protected document fields and non-target folder_id all unchanged")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    _write_json(out_dir / "plan_manifest.json",
                {k: plan[k] for k in ("batch", "candidate_csv_sha256", "census", "plan_digest",
                                      "folder_manifest_digest")})
    _write_json(out_dir / "folder_manifest.json", plan["folders"])
    _write_json(out_dir / "apply_receipt.json", {
        "applied_at": datetime.now(UTC).isoformat(), "baseline_commit": _baseline_commit(),
        "batch": BATCH_NAME, "candidate_csv_sha256": plan["candidate_csv_sha256"],
        "plan_digest": plan["plan_digest"],
        "folder_manifest_digest": plan["folder_manifest_digest"],
        "snapshot": report["snapshot"], "snapshot_sha256": report["snapshot_sha256"],
        "folders_created": report["folders_created"], "created_folder_codes": report["created_codes"],
        "folders_reused": report["folders_reused"],
        "document_assignments": report["assigned"], "audit_rows": report["audit_rows"],
        "actor_user_id": actor_user_id, "request_id": request_id,
        "confirm_phrase": want_phrase, "committed": True,
    })
    out(f"  receipt: {out_dir / 'apply_receipt.json'}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply the frozen document filing batch 3.")
    ap.add_argument("--candidate", required=True, help="the frozen batch 3 candidate CSV")
    ap.add_argument("--candidate-json", default=None,
                    help="the frozen batch 3 candidate JSON; its SHA is pinned when given")
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    ap.add_argument("--output-root", default=str(REPORT_ROOT))
    args = ap.parse_args(argv)
    run(args.candidate, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id, candidate_json=args.candidate_json,
        output_root=Path(args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
