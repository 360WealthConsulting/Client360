#!/usr/bin/env python3
"""Reusable, manifest-driven CLEARING of a duplicate owner column. ALL-OR-NOTHING. DRY-RUN DEFAULT.

WHY THIS EXISTS ALONGSIDE apply_ownership_transfer.py
-----------------------------------------------------
That tool TRANSFERS. Its expected-owner check builds a SINGLE-column tuple, so it cannot express
"this row currently carries a person AND an organization", and its write sets a new owner column.
This operation differs in both halves: the expected state is a MULTI-owner tuple, and the write
CLEARS one named column while leaving every other owner column exactly as it was. Routing this
through the transfer tool would mean weakening the check that makes that tool safe, so it is left
untouched and this is separate.

A duplicate owner is not always wrong. A document routinely carries a person AND the household
that person belongs to, and the pipeline's conflict rule compares per entity type precisely so
that pairing reads as agreement. This tool is for the cases a human has reviewed and decided are
duplicates, named one at a time in a manifest. It never decides that for itself.

WHAT THIS WRITES, AND NOTHING ELSE
    documents.<one named owner column>  ->  NULL
    documents.updated_at
    audit_events                        (one document.ownership_conflict_resolved per row, chained)

The other owner columns are never written. No entity is created, merged, retired or renamed. No
file moves. A row can never be left with zero owners: clearing the last owner is refused.

THE EXPECTED TUPLE IS THE SAFETY PROPERTY
    Each row pins the exact (person_id, household_id, organization_id) it was reviewed against,
    and the UPDATE repeats that whole tuple in its own WHERE clause. A row that moved between
    review and apply matches nothing, updates nothing, and aborts the batch.

USAGE
    python scripts/apply_duplicate_owner_clear.py --manifest <csv> --manifest-json <json> \\
        --expect-sha256 <csv-sha> --expect-json-sha256 <json-sha> \\
        --expect-digest <plan-digest> --expect-rows <n> --production-database <name>

    ... --actor-user-id <id> --confirm APPLY-DUPLICATE-OWNER-CLEAR-<digest12>-<rows> --apply
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST_COLUMNS = (
    "document_id",
    "expected_person_id", "expected_household_id", "expected_organization_id",
    "clear_column", "cleared_owner_state",
    "evidence", "reviewed_by", "applied",
)
OWNER_COLUMNS = {"person": "person_id", "household": "household_id",
                 "organization": "organization_id"}
#: Where each owner type lives, and the column that says whether it is still in use. households
#: carries no such column, so an existing household row counts as active.
OWNER_TABLE = {"person": ("people", "active"), "household": ("households", None),
               "organization": ("relationship_entities", "active")}
OWNER_STATES = ("active", "inactive")
MANIFEST_VERSION = 1


class Abort(SystemExit):
    """A gate refused. Always raised BEFORE any write."""


# --- digests ---------------------------------------------------------------------------------

def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_digest(rows) -> str:
    """Digest of the PLAN. Row order and prose cannot change it; the tuples and the target can."""
    canonical = sorted(
        ({"document_id": r["document_id"],
          "expected_person_id": r["expected_person_id"],
          "expected_household_id": r["expected_household_id"],
          "expected_organization_id": r["expected_organization_id"],
          "clear_column": r["clear_column"]} for r in rows),
        key=lambda r: r["document_id"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def confirm_phrase(digest: str, rows: int) -> str:
    """Carries BOTH the plan digest and the row count, so a phrase cannot be reused."""
    return f"APPLY-DUPLICATE-OWNER-CLEAR-{digest[:12].upper()}-{rows}"


# --- manifest --------------------------------------------------------------------------------

def _opt_int(value):
    value = (value or "").strip()
    return int(value) if value else None


def load_manifest(path, *, expect_sha, expect_rows, expect_digest):
    """Read and structurally validate the manifest. Expectations are SUPPLIED, never derived."""
    if not expect_sha:
        raise Abort("ABORT: --expect-sha256 is required")
    if expect_rows is None:
        raise Abort("ABORT: --expect-rows is required")
    if not expect_digest:
        raise Abort("ABORT: --expect-digest is required")
    path = Path(path)
    if not path.is_file():
        raise Abort(f"ABORT: manifest not found: {path}")
    actual = sha256_of(path)
    if actual != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {actual} != approved {expect_sha}")

    with path.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    if len(raw) != expect_rows:
        raise Abort(f"ABORT: manifest has {len(raw)} rows, approved {expect_rows}")

    rows, seen = [], set()
    for r in raw:
        for col in MANIFEST_COLUMNS:
            if col not in r:
                raise Abort(f"ABORT: manifest is missing the {col!r} column")
        if (r["applied"] or "").strip().upper() != "NO":
            raise Abort("ABORT: a manifest row is not applied=NO")
        did = int(r["document_id"])
        if did in seen:
            raise Abort(f"ABORT: duplicate document_id {did}")
        seen.add(did)

        clear = (r["clear_column"] or "").strip().lower()
        if clear not in OWNER_COLUMNS:
            raise Abort(f"ABORT: document {did}: unknown clear_column {clear!r}")
        state = (r["cleared_owner_state"] or "").strip().lower()
        if state not in OWNER_STATES:
            raise Abort(f"ABORT: document {did}: cleared_owner_state must be one of "
                        f"{OWNER_STATES}, got {state!r}")
        expected = {"person": _opt_int(r["expected_person_id"]),
                    "household": _opt_int(r["expected_household_id"]),
                    "organization": _opt_int(r["expected_organization_id"])}
        present = [k for k, v in expected.items() if v is not None]
        if len(present) < 2:
            raise Abort(f"ABORT: document {did}: the expected tuple names {len(present)} owner(s); "
                        "this tool only clears a DUPLICATE owner")
        if expected[clear] is None:
            raise Abort(f"ABORT: document {did}: clear_column {clear!r} is not set in the "
                        "expected tuple")
        if len(present) - 1 < 1:
            raise Abort(f"ABORT: document {did}: clearing would leave the document unowned")
        if not (r["evidence"] or "").strip():
            raise Abort(f"ABORT: document {did}: evidence is required")
        if not (r["reviewed_by"] or "").strip():
            raise Abort(f"ABORT: document {did}: reviewed_by is required")
        rows.append({"document_id": did, "expected": expected, "clear_column": clear,
                     "cleared_owner_id": expected[clear], "cleared_owner_state": state,
                     "expected_person_id": expected["person"],
                     "expected_household_id": expected["household"],
                     "expected_organization_id": expected["organization"],
                     "retained": {k: v for k, v in expected.items()
                                  if k != clear and v is not None},
                     "evidence": r["evidence"].strip(),
                     "reviewed_by": r["reviewed_by"].strip()})

    live = plan_digest(rows)
    if live != expect_digest:
        raise Abort(f"ABORT: plan digest {live} != approved {expect_digest}")
    return rows, actual, live


def verify_json_sidecar(path, *, expect_json_sha, rows, digest):
    if path is None or expect_json_sha is None:
        raise Abort("ABORT: --manifest-json and --expect-json-sha256 are both required")
    path = Path(path)
    if not path.is_file():
        raise Abort(f"ABORT: manifest json not found: {path}")
    actual = sha256_of(path)
    if actual != expect_json_sha:
        raise Abort(f"ABORT: manifest json SHA256 {actual} != approved {expect_json_sha}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    for key in ("manifest_version", "digest", "row_count", "reviewed_by", "created_at"):
        if key not in doc:
            raise Abort(f"ABORT: manifest json is missing {key!r}")
    if int(doc["manifest_version"]) != MANIFEST_VERSION:
        raise Abort(f"ABORT: manifest_version {doc['manifest_version']} != supported "
                    f"{MANIFEST_VERSION}")
    if doc["digest"] != digest:
        raise Abort(f"ABORT: manifest json digest {doc['digest']} != plan digest {digest}")
    if int(doc["row_count"]) != len(rows):
        raise Abort(f"ABORT: manifest json row_count {doc['row_count']} != {len(rows)}")
    return doc


# --- database identity -----------------------------------------------------------------------

def assert_database(conn, expected_name, *, allow_disposable=False):
    """The target must be NAMED and must be the database actually connected to."""
    from sqlalchemy import text

    from app.safety import is_test_database

    if not expected_name:
        raise Abort("ABORT: --production-database is required; name the database explicitly")
    actual = conn.execute(text("select current_database()")).scalar_one()
    if actual != expected_name:
        raise Abort(f"ABORT: connected to {actual!r}, but --production-database says "
                    f"{expected_name!r}")
    if is_test_database(str(conn.engine.url)) and not allow_disposable:
        raise Abort(f"ABORT: {actual!r} is a disposable test database; refusing to treat it as "
                    "production")
    return actual


# --- per-row verification --------------------------------------------------------------------

def verify_row(conn, row, doc):
    """Every gate for ONE row, under the row lock. A non-empty result aborts the WHOLE batch."""
    from sqlalchemy import text

    from app.services.households import PERMANENT_REJECT_DOCUMENT_IDS

    fail = []
    if doc is None:
        return ["document not found"]
    if not (doc["status"] == "active" and doc["deleted_at"] is None
            and doc["archived"] is False and doc["archived_at"] is None):
        fail.append("document fails the canonical live predicate")
    if doc["id"] in PERMANENT_REJECT_DOCUMENT_IDS:
        fail.append("permanent reject document")

    current = (doc["person_id"], doc["household_id"], doc["organization_id"])
    expected = (row["expected"]["person"], row["expected"]["household"],
                row["expected"]["organization"])
    if current != expected:
        fail.append(f"current owner {current} != expected {expected}")

    table, active_col = OWNER_TABLE[row["clear_column"]]
    cols = f"id, {active_col}" if active_col else "id"
    owner = conn.execute(text(f"select {cols} from {table} where id = :i"),
                         {"i": row["cleared_owner_id"]}).mappings().first()
    if owner is None:
        fail.append(f"{row['clear_column']} #{row['cleared_owner_id']} does not exist")
    else:
        # The manifest ASSERTS the state the reviewer saw; reality must agree. Clearing an owner
        # the reviewer believed retired, which is in fact still live, is exactly the mistake this
        # catches.
        actual_state = "active" if (active_col is None or owner[active_col]) else "inactive"
        if actual_state != row["cleared_owner_state"]:
            fail.append(f"{row['clear_column']} #{row['cleared_owner_id']} is {actual_state}, "
                        f"manifest asserts {row['cleared_owner_state']}")
    return fail


def _where_and_params(row):
    """The UPDATE's WHERE, repeating the whole expected tuple so a moved row matches nothing."""
    sets = f"{OWNER_COLUMNS[row['clear_column']]} = NULL"
    clauses, params = ["id = :id"], {"id": row["document_id"]}
    for kind, column in OWNER_COLUMNS.items():
        value = row["expected"][kind]
        if value is None:
            clauses.append(f"{column} IS NULL")
        else:
            clauses.append(f"{column} = :exp_{kind}")
            params[f"exp_{kind}"] = value
    clauses += ["status = 'active'", "deleted_at IS NULL", "archived = false",
                "archived_at IS NULL"]
    sql = (f"UPDATE documents SET {sets}, updated_at = now() "
           f"WHERE {' AND '.join(clauses)} RETURNING id")
    return sql, params


# --- the run ---------------------------------------------------------------------------------

def run(manifest, *, manifest_json, expect_sha, expect_json_sha, expect_digest, expect_rows,
        production_database, apply_changes=False, confirm=None, actor_user_id=None,
        allow_disposable_database=False, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event

    rows, csv_sha, digest = load_manifest(manifest, expect_sha=expect_sha,
                                          expect_rows=expect_rows, expect_digest=expect_digest)
    sidecar = verify_json_sidecar(manifest_json, expect_json_sha=expect_json_sha,
                                  rows=rows, digest=digest)
    want = confirm_phrase(digest, len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id; an ownership change needs an actor")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    report = {"rows": len(rows), "validated": 0, "cleared": 0, "audit_rows": 0,
              "committed": False, "dry_run": not apply_changes, "failures": [],
              "confirm_phrase": want, "manifest_sha256": csv_sha, "plan_digest": digest,
              "manifest_version": sidecar["manifest_version"], "database": None, "clears": []}

    out(f"manifest: {manifest}")
    out(f"  rows={len(rows)} digest={digest[:16]}... version={sidecar['manifest_version']}")

    with engine.begin() as conn:
        trans = conn.get_transaction()
        report["database"] = assert_database(conn, production_database,
                                             allow_disposable=allow_disposable_database)
        out(f"  database: {report['database']}")

        locked = conn.execute(text(
            "select id, person_id, household_id, organization_id, status, archived, archived_at, "
            "deleted_at from documents where id = any(:ids) order by id for update"),
            {"ids": ids}).mappings().all()
        if {r["id"] for r in locked} != set(ids):
            raise Abort(f"ABORT: documents not found: "
                        f"{sorted(set(ids) - {r['id'] for r in locked})}")
        out(f"  locked {len(locked)} rows FOR UPDATE (exact set equality)")

        # census per cleared owner, taken BEFORE the write, so an out-of-scope move is provable
        census = {}
        for key in {(r["clear_column"], r["cleared_owner_id"]) for r in rows}:
            column = OWNER_COLUMNS[key[0]]
            census[key] = conn.execute(
                text(f"select count(*) from documents where {column} = :i"),
                {"i": key[1]}).scalar_one()
        retained_census = {}
        for r in rows:
            for kind, value in r["retained"].items():
                column = OWNER_COLUMNS[kind]
                retained_census[(kind, value)] = conn.execute(
                    text(f"select count(*) from documents where {column} = :i"),
                    {"i": value}).scalar_one()
        total = conn.execute(text("select count(*) from documents")).scalar_one()

        for doc in locked:
            fails = verify_row(conn, by_id[doc["id"]], doc)
            if fails:
                report["failures"].append({"document_id": doc["id"], "reasons": fails})
            else:
                report["validated"] += 1
        if report["failures"]:
            for f in report["failures"]:
                out(f"    INVALID #{f['document_id']}: {'; '.join(f['reasons'])}")
            raise RuntimeError(f"{len(report['failures'])} row(s) failed verification — "
                               "all-or-nothing, nothing is applied and no row is skipped")
        out(f"  verification: valid={report['validated']} invalid=0")

        for r in rows:
            report["clears"].append(
                {"document_id": r["document_id"], "cleared": r["clear_column"],
                 "retained": sorted(r["retained"])})
        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written")
            trans.rollback()
            return report

        request_id = f"duplicate-owner-clear:{digest[:12]}:{len(rows)}"
        stamped = datetime.now(UTC).isoformat()
        for r in rows:
            sql, params = _where_and_params(r)
            if conn.execute(text(sql), params).first() is None:
                raise RuntimeError(f"document {r['document_id']} did not clear "
                                   "(ownership moved under the lock)")
            report["cleared"] += 1
            write_audit_event(
                action="document.ownership_conflict_resolved", entity_type="document",
                entity_id=r["document_id"], actor_user_id=actor_user_id, request_id=request_id,
                metadata={"document_id": r["document_id"],
                          "cleared_owner_type": r["clear_column"],
                          "cleared_owner_id": r["cleared_owner_id"],
                          "cleared_owner_state": r["cleared_owner_state"],
                          "retained_owners": {k: v for k, v in r["retained"].items()},
                          "expected_owner_tuple": {
                              "person_id": r["expected"]["person"],
                              "household_id": r["expected"]["household"],
                              "organization_id": r["expected"]["organization"]},
                          "actor_user_id": actor_user_id,
                          "evidence": r["evidence"], "reviewed_by": r["reviewed_by"],
                          "manifest_digest": digest, "manifest_sha256": csv_sha,
                          "manifest_version": sidecar["manifest_version"],
                          "applied_at": stamped,
                          "tool": "scripts/apply_duplicate_owner_clear.py"},
                conn=conn)
            report["audit_rows"] += 1

        # --- post-write invariants, all inside the transaction ---------------------------
        after = conn.execute(text(
            "select id, person_id, household_id, organization_id from documents "
            "where id = any(:ids)"), {"ids": ids}).mappings().all()
        for doc in after:
            r = by_id[doc["id"]]
            got = {"person": doc["person_id"], "household": doc["household_id"],
                   "organization": doc["organization_id"]}
            if got[r["clear_column"]] is not None:
                raise RuntimeError(f"document {doc['id']}: the cleared column is still set")
            for kind, value in r["retained"].items():
                if got[kind] != value:
                    raise RuntimeError(f"document {doc['id']}: retained {kind} changed "
                                       f"({got[kind]} != {value})")
            if all(v is None for v in got.values()):
                raise RuntimeError(f"document {doc['id']}: left with no owner")
        for key, before in census.items():
            column = OWNER_COLUMNS[key[0]]
            moved = sum(1 for r in rows
                        if (r["clear_column"], r["cleared_owner_id"]) == key)
            now = conn.execute(text(f"select count(*) from documents where {column} = :i"),
                               {"i": key[1]}).scalar_one()
            if now != before - moved:
                raise RuntimeError(f"{key[0]} #{key[1]} owns {now}, expected {before - moved} — "
                                   "a document outside the manifest changed")
        for key, before in retained_census.items():
            column = OWNER_COLUMNS[key[0]]
            now = conn.execute(text(f"select count(*) from documents where {column} = :i"),
                               {"i": key[1]}).scalar_one()
            if now != before:
                raise RuntimeError(f"retained {key[0]} #{key[1]} count changed "
                                   f"({before} -> {now}); it must not")
        if conn.execute(text("select count(*) from documents")).scalar_one() != total:
            raise RuntimeError("the documents row count changed")
        if report["cleared"] != len(rows):
            raise RuntimeError(f"cleared {report['cleared']} != manifest {len(rows)}")

        report["committed"] = True
        out(f"  COMMITTED {report['cleared']} clears, {report['audit_rows']} audit rows")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--manifest-json", required=True)
    ap.add_argument("--expect-sha256", required=True)
    ap.add_argument("--expect-json-sha256", required=True)
    ap.add_argument("--expect-digest", required=True)
    ap.add_argument("--expect-rows", required=True, type=int)
    ap.add_argument("--production-database", required=True,
                    help="the database this manifest was reviewed against; must match")
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    args = ap.parse_args(argv)
    if args.dry_run and args.apply:
        raise Abort("ABORT: choose --dry-run or --apply, not both")
    report = run(args.manifest, manifest_json=args.manifest_json,
                 expect_sha=args.expect_sha256, expect_json_sha=args.expect_json_sha256,
                 expect_digest=args.expect_digest, expect_rows=args.expect_rows,
                 production_database=args.production_database,
                 apply_changes=args.apply, confirm=args.confirm,
                 actor_user_id=args.actor_user_id)
    return 0 if (report["committed"] or not args.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())
