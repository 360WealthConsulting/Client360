#!/usr/bin/env python3
"""Reusable, manifest-driven ownership TRANSFER for reviewed documents. ALL-OR-NOTHING.

WHY THIS EXISTS
---------------
Every ownership write in this codebase is fail-closed on the document being UNOWNED:

* ``households.resolve_document_ownership`` assigns only when person_id, household_id and
  organization_id are all NULL, and returns ``already_owned`` otherwise;
* ``scripts/apply_owner_manifest.py`` fails any row whose owner columns are not all NULL;
* ``migration/joint_document_reownership`` moves person -> household only, and only for a jointly
  signed personal return whose current owner already belongs to the target household.

That is the right default and this tool does not weaken it. But a reviewed CORRECTION — a document
filed against the wrong client — cannot be expressed by any of them. Before this, the only
precedent was ``scripts/apply_strict_safe_ownership_batch4.py``, whose constants ARE the approval
for that one batch and which only ever clears ``person_id`` to NULL. That script is deliberately
left untouched; this is a new, parameterised tool.

WHAT THIS WRITES, AND NOTHING ELSE
    documents.person_id / household_id / organization_id   (the reviewed transfer, one owner type)
    documents.updated_at
    audit_events                                           (one per row, hash-chained)

No entity is created, merged, renamed or deleted. No file moves. No classification, tax year, OCR,
provenance, review_status, tag or vault row is touched.

THE EXPECTED OWNER IS THE SAFETY PROPERTY
    Each row names the owner it was reviewed against. The UPDATE repeats that expectation in its
    own WHERE clause, so a document whose ownership moved between review and apply matches nothing,
    updates nothing, and aborts the whole batch. Drift cannot be silently absorbed.

USAGE
    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_ownership_transfer.py --manifest <csv> --manifest-json <json> \\
        --expect-sha256 <csv-sha> --expect-json-sha256 <json-sha> \\
        --expect-digest <manifest-digest> --expect-rows <n> \\
        --production-database client360

    # writes, and only with every key turned at once
    python scripts/apply_ownership_transfer.py ... --actor-user-id <id> \\
        --confirm APPLY-OWNERSHIP-TRANSFER-<digest12>-<rows> --apply
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

#: Manifest schema. Every column is required on every row; nothing is inferred.
MANIFEST_COLUMNS = (
    "document_id",
    "expected_owner_type", "expected_owner_id",
    "new_owner_type", "new_owner_id",
    "source_system", "source_key",
    "evidence", "reviewed_by",
)
#: The JSON sidecar carries what a human approved ABOUT the manifest, not its rows.
JSON_REQUIRED = ("manifest_version", "digest", "row_count", "reviewed_by", "created_at")

OWNER_TYPES = ("person", "household", "organization")
_OWNER_COLUMN = {"person": "person_id", "household": "household_id",
                 "organization": "organization_id"}
#: Which table proves a target of each type exists, and the column that proves it is still live.
_OWNER_TABLE = {"person": ("people", "active"), "household": ("households", None),
                "organization": ("relationship_entities", "active")}

MANIFEST_VERSION = 1


class Abort(SystemExit):
    """A gate refused. Always raised BEFORE any write."""


# --- digests ---------------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_digest(rows: list[dict]) -> str:
    """Digest of the PLAN, not the file: same transfers in any row order give the same digest."""
    canonical = sorted(
        ({"document_id": r["document_id"],
          "expected_owner_type": r["expected_owner_type"],
          "expected_owner_id": r["expected_owner_id"],
          "new_owner_type": r["new_owner_type"],
          "new_owner_id": r["new_owner_id"]} for r in rows),
        key=lambda r: r["document_id"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def confirm_phrase(digest: str, rows: int) -> str:
    """Contains BOTH the manifest digest and the row count, so it cannot be reused."""
    return f"APPLY-OWNERSHIP-TRANSFER-{digest[:12].upper()}-{rows}"


# --- manifest --------------------------------------------------------------------------------

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

    digest = sha256_of(path)
    if digest != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")

    with path.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    if len(raw) != expect_rows:
        raise Abort(f"ABORT: manifest has {len(raw)} rows, approved {expect_rows}")

    rows, seen = [], set()
    for r in raw:
        for col in MANIFEST_COLUMNS:
            if col not in r:
                raise Abort(f"ABORT: manifest is missing the {col!r} column")
        try:
            did = int(r["document_id"])
            exp_id = int(r["expected_owner_id"])
            new_id = int(r["new_owner_id"])
        except (TypeError, ValueError) as exc:
            raise Abort(f"ABORT: non-integer id on a manifest row: {exc}") from exc
        exp_t = (r["expected_owner_type"] or "").strip().lower()
        new_t = (r["new_owner_type"] or "").strip().lower()
        if exp_t not in OWNER_TYPES:
            raise Abort(f"ABORT: document {did}: unknown expected_owner_type {exp_t!r}")
        if new_t not in OWNER_TYPES:
            raise Abort(f"ABORT: document {did}: unknown new_owner_type {new_t!r}")
        if (exp_t, exp_id) == (new_t, new_id):
            raise Abort(f"ABORT: document {did}: the transfer is a no-op")
        if did in seen:
            raise Abort(f"ABORT: duplicate document_id {did}")
        seen.add(did)
        if not (r["evidence"] or "").strip():
            raise Abort(f"ABORT: document {did}: evidence is required")
        if not (r["reviewed_by"] or "").strip():
            raise Abort(f"ABORT: document {did}: reviewed_by is required")
        rows.append({"document_id": did,
                     "expected_owner_type": exp_t, "expected_owner_id": exp_id,
                     "new_owner_type": new_t, "new_owner_id": new_id,
                     "source_system": (r["source_system"] or "").strip(),
                     "source_key": (r["source_key"] or "").strip(),
                     "evidence": r["evidence"].strip(),
                     "reviewed_by": r["reviewed_by"].strip()})

    live = plan_digest(rows)
    if live != expect_digest:
        raise Abort(f"ABORT: plan digest {live} != approved {expect_digest}")
    return rows, digest, live


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
    for key in JSON_REQUIRED:
        if key not in doc:
            raise Abort(f"ABORT: manifest json is missing {key!r}")
    if int(doc["manifest_version"]) != MANIFEST_VERSION:
        raise Abort(f"ABORT: manifest_version {doc['manifest_version']} "
                    f"!= supported {MANIFEST_VERSION}")
    if doc["digest"] != digest:
        raise Abort(f"ABORT: manifest json digest {doc['digest']} != plan digest {digest}")
    if int(doc["row_count"]) != len(rows):
        raise Abort(f"ABORT: manifest json row_count {doc['row_count']} != {len(rows)}")
    return doc


# --- database identity -----------------------------------------------------------------------

def assert_database(conn, expected_name, *, allow_disposable=False):
    """The target database must be NAMED on the command line and must be the one connected to.

    A disposable (test) database is refused for an apply unless the caller is the test suite
    itself, which passes ``allow_disposable``. Production and test can therefore never be
    mistaken for one another in either direction.
    """
    from sqlalchemy import text

    from app.safety import is_test_database

    if not expected_name:
        raise Abort("ABORT: --production-database is required; name the database explicitly")
    actual = conn.execute(text("select current_database()")).scalar_one()
    if actual != expected_name:
        raise Abort(f"ABORT: connected to {actual!r}, but --production-database says "
                    f"{expected_name!r}")
    url = str(conn.engine.url)
    if is_test_database(url) and not allow_disposable:
        raise Abort(f"ABORT: {actual!r} is a disposable test database; refusing to treat it as "
                    "production")
    return actual


# --- per-row verification --------------------------------------------------------------------

def _owner_tuple(owner_type, owner_id):
    """(person_id, household_id, organization_id) with exactly one slot filled."""
    return tuple(owner_id if _OWNER_COLUMN[owner_type] == col else None
                 for col in ("person_id", "household_id", "organization_id"))


def verify_row(conn, row, doc):
    """Every gate for ONE row, run inside the transaction under the row lock.

    Returns a list of failures. A non-empty list aborts the ENTIRE batch; a failed row is never
    demoted to a skip, because "apply the good ones" is how a manifest stops meaning what was
    reviewed.
    """
    from sqlalchemy import text

    from app.services.households import PERMANENT_REJECT_DOCUMENT_IDS

    did = row["document_id"]
    fail = []
    if doc is None:
        return ["document not found"]

    # guard 4 — the canonical live predicate, all four conditions
    if not (doc["status"] == "active" and doc["deleted_at"] is None
            and doc["archived"] is False and doc["archived_at"] is None):
        fail.append("document fails the canonical live predicate "
                    f"(status={doc['status']!r} deleted_at={doc['deleted_at']} "
                    f"archived={doc['archived']} archived_at={doc['archived_at']})")
    if did in PERMANENT_REJECT_DOCUMENT_IDS:
        fail.append("permanent reject document")

    # guard 5 — current owner must EXACTLY equal the frozen expectation
    expected = _owner_tuple(row["expected_owner_type"], row["expected_owner_id"])
    current = (doc["person_id"], doc["household_id"], doc["organization_id"])
    if current != expected:
        fail.append(f"current owner {current} != expected {expected}")

    # guard 7 — exactly one owner column may be set afterwards
    new = _owner_tuple(row["new_owner_type"], row["new_owner_id"])
    if sum(1 for v in new if v is not None) != 1:
        fail.append("the new owner does not name exactly one owner type")

    # guard 6 — the target must exist and still be active
    table, active_col = _OWNER_TABLE[row["new_owner_type"]]
    cols = f"id, {active_col}" if active_col else "id"
    target = conn.execute(text(f"select {cols} from {table} where id = :i"),
                          {"i": row["new_owner_id"]}).mappings().first()
    if target is None:
        fail.append(f"{row['new_owner_type']} #{row['new_owner_id']} does not exist")
    elif active_col and target[active_col] is False:
        fail.append(f"{row['new_owner_type']} #{row['new_owner_id']} is not active")
    return fail


_TRANSFER_SQL = """
UPDATE documents
   SET person_id = :new_person, household_id = :new_household,
       organization_id = :new_organization, updated_at = now()
 WHERE id = :id
   AND person_id IS NOT DISTINCT FROM :exp_person
   AND household_id IS NOT DISTINCT FROM :exp_household
   AND organization_id IS NOT DISTINCT FROM :exp_organization
   AND status = 'active' AND deleted_at IS NULL
   AND archived = false AND archived_at IS NULL
RETURNING id
"""


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
    report = {"rows": len(rows), "validated": 0, "transferred": 0, "audit_rows": 0,
              "committed": False, "dry_run": not apply_changes, "failures": [],
              "confirm_phrase": want, "manifest_sha256": csv_sha, "plan_digest": digest,
              "manifest_version": sidecar["manifest_version"],
              "database": None, "transfers": []}

    out(f"manifest: {manifest}")
    out(f"  rows={len(rows)} digest={digest[:16]}... version={sidecar['manifest_version']}")

    with engine.begin() as conn:
        trans = conn.get_transaction()
        report["database"] = assert_database(conn, production_database,
                                             allow_disposable=allow_disposable_database)
        out(f"  database: {report['database']}")

        # Lock exactly the manifest's rows, and prove the set is exactly what was reviewed.
        locked = conn.execute(text(
            "select id, person_id, household_id, organization_id, original_name, status, "
            "archived, archived_at, deleted_at from documents where id = any(:ids) "
            "order by id for update"), {"ids": ids}).mappings().all()
        if {r["id"] for r in locked} != set(ids):
            missing = sorted(set(ids) - {r["id"] for r in locked})
            raise Abort(f"ABORT: documents not found: {missing}")
        out(f"  locked {len(locked)} rows FOR UPDATE (exact set equality)")

        # guard 8 — a census of every other document owned by each former owner, taken BEFORE the
        # write, so an out-of-scope row moving is provable rather than assumed.
        former_census = {}
        for key in {(r["expected_owner_type"], r["expected_owner_id"]) for r in rows}:
            col = _OWNER_COLUMN[key[0]]
            n = conn.execute(text(f"select count(*) from documents where {col} = :i"),
                             {"i": key[1]}).scalar_one()
            former_census[key] = n
        total_documents = conn.execute(text("select count(*) from documents")).scalar_one()

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

        if not apply_changes:
            for r in rows:
                report["transfers"].append(
                    {"document_id": r["document_id"],
                     "from": f"{r['expected_owner_type']} #{r['expected_owner_id']}",
                     "to": f"{r['new_owner_type']} #{r['new_owner_id']}"})
            out("  DRY RUN — every gate passed; nothing written")
            trans.rollback()
            return report

        request_id = f"ownership-transfer:{digest[:12]}:{len(rows)}"
        stamped = datetime.now(UTC).isoformat()
        for r in rows:
            exp = _owner_tuple(r["expected_owner_type"], r["expected_owner_id"])
            new = _owner_tuple(r["new_owner_type"], r["new_owner_id"])
            returned = conn.execute(text(_TRANSFER_SQL), {
                "id": r["document_id"],
                "exp_person": exp[0], "exp_household": exp[1], "exp_organization": exp[2],
                "new_person": new[0], "new_household": new[1], "new_organization": new[2],
            }).first()
            if returned is None:
                raise RuntimeError(f"document {r['document_id']} did not transfer "
                                   "(ownership moved under the lock)")
            report["transferred"] += 1
            report["transfers"].append(
                {"document_id": r["document_id"],
                 "from": f"{r['expected_owner_type']} #{r['expected_owner_id']}",
                 "to": f"{r['new_owner_type']} #{r['new_owner_id']}"})
            write_audit_event(
                action="document.ownership_conflict_resolved", entity_type="document",
                entity_id=r["document_id"], actor_user_id=actor_user_id, request_id=request_id,
                metadata={
                    "document_id": r["document_id"],
                    "former_owner_type": r["expected_owner_type"],
                    "former_owner_id": r["expected_owner_id"],
                    "new_owner_type": r["new_owner_type"],
                    "new_owner_id": r["new_owner_id"],
                    "actor_user_id": actor_user_id,
                    "source_system": r["source_system"], "source_key": r["source_key"],
                    "evidence": r["evidence"], "reviewed_by": r["reviewed_by"],
                    "manifest_digest": digest, "manifest_sha256": csv_sha,
                    "manifest_version": sidecar["manifest_version"],
                    "applied_at": stamped, "tool": "scripts/apply_ownership_transfer.py",
                },
                conn=conn)
            report["audit_rows"] += 1

        # --- post-write invariants, all inside the transaction -------------------------------
        after = conn.execute(text(
            "select id, person_id, household_id, organization_id from documents "
            "where id = any(:ids) order by id"), {"ids": ids}).mappings().all()
        for doc in after:
            r = by_id[doc["id"]]
            want_owner = _owner_tuple(r["new_owner_type"], r["new_owner_id"])
            got = (doc["person_id"], doc["household_id"], doc["organization_id"])
            if got != want_owner:
                raise RuntimeError(f"document {doc['id']}: post-write owner {got} != "
                                   f"intended {want_owner}")
            if sum(1 for v in got if v is not None) != 1:
                raise RuntimeError(f"document {doc['id']}: more than one owner column is set")
        if report["transferred"] != len(rows):
            raise RuntimeError(f"transferred {report['transferred']} != manifest {len(rows)}")

        # guard 8 — every former owner lost exactly the documents this manifest moved, no more.
        for key, before in former_census.items():
            col = _OWNER_COLUMN[key[0]]
            moved = sum(1 for r in rows
                        if (r["expected_owner_type"], r["expected_owner_id"]) == key)
            now = conn.execute(text(f"select count(*) from documents where {col} = :i"),
                               {"i": key[1]}).scalar_one()
            if now != before - moved:
                raise RuntimeError(
                    f"{key[0]} #{key[1]}: owns {now} documents, expected {before - moved} "
                    "— a document outside the manifest changed")
        if conn.execute(text("select count(*) from documents")).scalar_one() != total_documents:
            raise RuntimeError("the documents row count changed; a row was created or deleted")

        report["committed"] = True
        out(f"  COMMITTED {report['transferred']} transfers, {report['audit_rows']} audit rows")
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
