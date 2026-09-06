#!/usr/bin/env python3
"""Strict-safe ownership BATCH 4 — the guarded CONFLICT CLEANUP apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_strict_safe_ownership_batch4.py --manifest <csv> \\
        --manifest-json <json> --expect-sha256 <csv-sha> --expect-json-sha256 <json-sha> \\
        --expect-plan-digest <digest> --expect-rows 4

    # writes, and only with every key turned at once
    python scripts/apply_strict_safe_ownership_batch4.py --manifest <csv> \\
        --manifest-json <json> --expect-sha256 <csv-sha> --expect-json-sha256 <json-sha> \\
        --expect-plan-digest <digest> --expect-rows 4 \\
        --actor-user-id <id> --confirm APPLY-STRICT-SAFE-OWNERSHIP-4-4 --apply

WHAT THIS WRITES, AND WHY IT IS NOT resolve_document_ownership
---------------------------------------------------------------
One column, on four rows: ``documents.person_id`` set to NULL. Nothing else. ``organization_id`` is
already correct on every row and is never written; ``household_id`` must be NULL before and after.

Batches 1-3 route their writes through ``households.resolve_document_ownership`` because they ASSIGN
an unowned document, which is exactly what that service does. It cannot express this batch: it
requires a destination and only writes when all three ownership columns are NULL, and every row here
is doubly owned. So the UPDATE lives here, written so it can only clear the person id that was
reviewed, on a row that still looks exactly as reviewed:

    UPDATE documents SET person_id = NULL
     WHERE id = :id AND person_id = :former_person_id AND organization_id = :organization_id
       AND household_id IS NULL AND status <> 'deleted' AND deleted_at IS NULL AND archived = false

A row that has moved matches nothing, updates nothing, and aborts the batch.

DRIFT ABORTS EVERYTHING
-----------------------
The whole plan is recomputed live and its digest must equal the approved one; both manifests are
SHA-pinned and cross-checked against each other; the rows are locked ``FOR UPDATE`` with exact set
equality; and every clause of the rule — the person still inactive and nameless, the organization
still an active business, the canonical repair provenance still naming that person, the filename
still naming the organization — is re-proved under the lock by
``document_strict_safe_ownership_batch4.verify_row`` before the first write.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SNAPSHOT_ROOT = REPO_ROOT / "var" / "strict_safe_ownership_batch4"

#: The snapshot filename the Batch 4 rollback reads. Distinct from every earlier batch's.
SNAPSHOT_CSV = "rollback_snapshot_strict_safe_ownership_batch4.csv"

#: The approved row count, pinned here as well as on the command line.
EXPECTED_ROWS = 4

MANIFEST_REQUIRED_COLUMNS = ("document_id", "original_name", "former_person_id",
                             "former_person_name", "organization_id", "organization_name",
                             "review_status", "support_json")

SUPPORT_FIELDS = ("repaired_from_person_id", "filename_names_organization", "folder_source_id",
                  "folder_segment", "twin_document_id")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").upper()


def confirm_phrase(rows: int) -> str:
    from app.services.document_strict_safe_ownership_batch4 import BATCH_ID
    return f"APPLY-{_slug(BATCH_ID)}-{rows}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path, *, expect_sha: str, expect_rows: int) -> list[dict]:
    """Read and structurally validate the approved CSV manifest. Never modifies it."""
    if not expect_sha:
        raise Abort("ABORT: --expect-sha256 is required")
    if expect_rows is None:
        raise Abort("ABORT: --expect-rows is required")
    if expect_rows != EXPECTED_ROWS:
        raise Abort(f"ABORT: --expect-rows {expect_rows} != the approved {EXPECTED_ROWS}")
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
        for col in MANIFEST_REQUIRED_COLUMNS:
            if col not in r:
                raise Abort(f"ABORT: manifest is missing the {col!r} column")
        try:
            did = int(r["document_id"])
            former_person_id = int(r["former_person_id"])
            organization_id = int(r["organization_id"])
            support = json.loads(r["support_json"])
        except (TypeError, ValueError) as exc:
            raise Abort(f"ABORT: unreadable manifest row: {r!r}") from exc
        if did in seen:
            raise Abort(f"ABORT: duplicate document_id {did} in manifest")
        seen.add(did)
        if not isinstance(support, dict) or set(support) != set(SUPPORT_FIELDS):
            raise Abort(f"ABORT: document {did} support keys {sorted(support)} are not "
                        f"{sorted(SUPPORT_FIELDS)}")
        if support.get("repaired_from_person_id") != former_person_id:
            raise Abort(f"ABORT: document {did} repair provenance names "
                        f"{support.get('repaired_from_person_id')}, not {former_person_id}")
        if support.get("filename_names_organization") is not True:
            raise Abort(f"ABORT: document {did} does not claim filename organization evidence")
        if not support.get("folder_segment"):
            raise Abort(f"ABORT: document {did} carries no folder evidence")
        rows.append({
            "document_id": did,
            "original_name": r["original_name"] or "",
            "former_person_id": former_person_id,
            "former_person_name": (r.get("former_person_name") or "").strip(),
            "organization_id": organization_id,
            "organization_name": (r.get("organization_name") or "").strip(),
            "review_status": (r.get("review_status") or "").strip(),
            "support": support,
            **{k: support[k] for k in SUPPORT_FIELDS},
        })

    # NOTE: the batch is deliberately NOT constrained to a single business. Every row is verified
    # independently against its OWN organization's canonical-repair provenance under the lock, so a
    # second repaired business is no less safe than the first — and refusing one would make the
    # manifest impossible to regenerate the moment another repair lands. How many businesses a given
    # manifest covers is a REVIEWED FACT, recorded in the json census (``distinct_organizations``)
    # and pinned by the plan digest, not a structural rule.
    return rows


def verify_json_manifest(json_path, *, expect_json_sha, expect_plan_digest, rows) -> dict:
    """Pin the JSON manifest's SHA and cross-check it describes the SAME batch as the CSV."""
    from app.services.document_strict_safe_ownership_batch4 import BATCH_ID, PLAN_FIELDS

    path = Path(json_path)
    if not path.is_file():
        raise Abort(f"ABORT: json manifest not found: {path}")
    digest = sha256_of(path)
    if digest != expect_json_sha:
        raise Abort(f"ABORT: json manifest SHA256 {digest} != approved {expect_json_sha}")

    meta = json.loads(path.read_text(encoding="utf-8"))
    if meta.get("batch_id") != BATCH_ID:
        raise Abort(f"ABORT: json manifest is for batch {meta.get('batch_id')!r}, not {BATCH_ID!r}")
    if meta.get("plan_digest") != expect_plan_digest:
        raise Abort(f"ABORT: json manifest plan_digest {meta.get('plan_digest')} != "
                    f"--expect-plan-digest {expect_plan_digest}")
    if meta.get("confirmation_phrase") != confirm_phrase(len(rows)):
        raise Abort(f"ABORT: json manifest confirmation_phrase {meta.get('confirmation_phrase')!r} "
                    f"!= {confirm_phrase(len(rows))!r}")
    if meta.get("rows") != len(rows):
        raise Abort(f"ABORT: json manifest says {meta.get('rows')} rows, csv has {len(rows)}")

    csv_view = [{k: r[k] for k in PLAN_FIELDS}
                for r in sorted(rows, key=lambda r: r["document_id"])]
    json_view = [{k: r.get(k) for k in PLAN_FIELDS}
                 for r in sorted(meta.get("plan") or [], key=lambda r: r["document_id"])]
    if csv_view != json_view:
        raise Abort("ABORT: the csv and json manifests do not describe the same rows")
    return meta


# --- fingerprints: everything this batch must NOT change -----------------------------------------
#
# ``person_id`` is deliberately absent from the target fingerprint — it is the one column this batch
# writes. Everything else about the target rows, and the ownership of every OTHER document, must be
# identical before and after.

_TARGET_FP = """
    select md5(string_agg(
        id::text||'|'||coalesce(household_id::text,'')||'|'||coalesce(organization_id::text,'')||'|'||
        coalesce(status,'')||'|'||archived::text||'|'||coalesce(deleted_at::text,'')||'|'||
        coalesce(sha256,'')||'|'||coalesce(storage_uri,'')||'|'||coalesce(storage_path,'')||'|'||
        coalesce(original_name,'')||'|'||coalesce(review_status,'')||'|'||coalesce(tags::text,''),
        E'\n' order by id))
      from documents where id = any(:ids)
"""

_NON_TARGET_FP = """
    select md5(string_agg(
        id::text||'|'||coalesce(person_id::text,'')||'|'||coalesce(household_id::text,'')||'|'||
        coalesce(organization_id::text,'')||'|'||coalesce(review_status,''),
        E'\n' order by id))
      from documents where id <> all(:ids)
"""

_ENTITY_FP = """
    select md5(
        coalesce((select string_agg(id::text||'|'||coalesce(full_name,'')||'|'||active::text,
                                    E'\n' order by id) from people where id = any(:people)), '')
        || '::' ||
        coalesce((select string_agg(id::text||'|'||coalesce(name,'')||'|'||active::text||'|'||
                                    coalesce(entity_type,'')||'|'||coalesce(details::text,''),
                                    E'\n' order by id)
                    from relationship_entities where id = any(:orgs)), ''))
"""

_LOCK_SQL = """
    select id, original_name, person_id, household_id, organization_id, status, archived,
           deleted_at, review_status, tags
      from documents where id = any(:ids) order by id for update
"""

_CLEAR_SQL = """
    update documents
       set person_id = null
     where id = :id
       and person_id = :former_person_id
       and organization_id = :organization_id
       and household_id is null
       and status <> 'deleted' and deleted_at is null and archived = false
    returning id
"""


def _fingerprints(conn, ids, people, orgs):
    from sqlalchemy import text
    return {
        "target": conn.execute(text(_TARGET_FP), {"ids": ids}).scalar(),
        "non_target": conn.execute(text(_NON_TARGET_FP), {"ids": ids}).scalar(),
        "entities": conn.execute(text(_ENTITY_FP), {"people": people, "orgs": orgs}).scalar(),
    }


# --- snapshot ------------------------------------------------------------------------------------

SNAPSHOT_COLUMNS = ["document_id", "original_name", "prior_person_id", "prior_household_id",
                    "prior_organization_id", "prior_review_status", "prior_tags_json",
                    "former_person_id", "former_person_name", "organization_id",
                    "organization_name", "support_json"]


def write_snapshot(locked, rows, root: Path) -> tuple[Path, str]:
    """Exact prior state for exactly these ids, written BEFORE any write."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(root) / f"strict-safe-ownership-batch4-apply-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / SNAPSHOT_CSV
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["document_id"]):
            d = locked[r["document_id"]]
            w.writerow({
                "document_id": r["document_id"],
                "original_name": d["original_name"] or "",
                "prior_person_id": "" if d["person_id"] is None else d["person_id"],
                "prior_household_id": "" if d["household_id"] is None else d["household_id"],
                "prior_organization_id": "" if d["organization_id"] is None
                                         else d["organization_id"],
                "prior_review_status": d["review_status"] or "",
                "prior_tags_json": json.dumps(d["tags"], sort_keys=True, ensure_ascii=False),
                "former_person_id": r["former_person_id"],
                "former_person_name": r["former_person_name"],
                "organization_id": r["organization_id"],
                "organization_name": r["organization_name"],
                "support_json": json.dumps(r["support"], sort_keys=True, ensure_ascii=False),
            })
    snap_sha = sha256_of(path)
    (out / "manifest.json").write_text(json.dumps({
        "created_at": datetime.now(UTC).isoformat(),
        "batch_id": "STRICT-SAFE-OWNERSHIP-4",
        "rows": len(rows),
        "snapshot_sha256": snap_sha,
    }, indent=2) + "\n", encoding="utf-8")
    return path, snap_sha


# --- the run -------------------------------------------------------------------------------------

def run(manifest_path, *, expect_sha, expect_plan_digest, expect_rows,
        manifest_json, expect_json_sha, apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_root=SNAPSHOT_ROOT, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services import document_strict_safe_ownership_batch4 as b4

    rows = load_manifest(Path(manifest_path), expect_sha=expect_sha, expect_rows=expect_rows)
    if manifest_json is None or expect_json_sha is None:
        raise Abort("ABORT: --manifest-json and --expect-json-sha256 are both required")
    verify_json_manifest(manifest_json, expect_json_sha=expect_json_sha,
                         expect_plan_digest=expect_plan_digest, rows=rows)

    want = confirm_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id; an ownership change needs an actor")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    people = sorted({r["former_person_id"] for r in rows})
    orgs = sorted({r["organization_id"] for r in rows})
    report = {"rows": len(rows), "validated": 0, "cleared": 0, "audit_rows": 0,
              "snapshot": None, "snapshot_sha256": None, "committed": False,
              "confirm_phrase": want, "manifest_sha256": expect_sha,
              "json_sha256": expect_json_sha, "plan_digest": expect_plan_digest, "failures": []}

    out(f"manifest: {manifest_path}")
    out(f"  rows={len(rows)} former_person={people} organization={orgs}")
    out(f"  csv sha256 verified:  {expect_sha}")
    out(f"  json sha256 verified: {expect_json_sha}")

    trans_conn = engine.connect()
    trans = trans_conn.begin()
    try:
        conn = trans_conn
        if conn.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; the apply needs a writable session")

        # 1. Recompute the whole plan live and require digest equality.
        live_plan = b4.build_plan(conn)
        live_digest = b4.plan_digest(live_plan)
        if live_digest != expect_plan_digest:
            raise Abort("ABORT: the Batch 4 plan has moved since it was approved.\n"
                        f"  approved digest {expect_plan_digest}\n"
                        f"  current  digest {live_digest}\n"
                        f"  approved rows {len(rows)}, current plan rows {len(live_plan)}\n"
                        "Re-run the selection and have the new plan reviewed.")
        out(f"  live plan digest verified: {live_digest} ({len(live_plan)} rows)")
        live_by_id = {r["document_id"]: r for r in live_plan}
        if sorted(live_by_id) != ids:
            raise Abort(f"ABORT: the live plan is {sorted(live_by_id)}, the manifest is {ids}")

        # 2. Lock exactly the manifest ids, and require exact set equality.
        locked = {r["id"]: dict(r) for r in conn.execute(text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != ids:
            missing = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(missing)} manifest documents did not lock: {missing}")
        out(f"  locked {len(locked)} rows FOR UPDATE (exact set equality)")

        fp_before = _fingerprints(conn, ids, people, orgs)

        # 3. Revalidate EVERY row under the lock, before ANY write.
        for did in ids:
            want_row = by_id[did]
            live = live_by_id.get(did)
            why = None
            if live is None:
                why = "no longer in the batch 4 plan"
            elif live["former_person_id"] != want_row["former_person_id"]:
                why = (f"former person drifted {want_row['former_person_id']} -> "
                       f"{live['former_person_id']}")
            elif live["organization_id"] != want_row["organization_id"]:
                why = (f"organization drifted {want_row['organization_id']} -> "
                       f"{live['organization_id']}")
            elif live["folder_source_id"] != want_row["folder_source_id"]:
                why = f"folder evidence drifted to source {live['folder_source_id']}"
            elif live["twin_document_id"] != want_row["twin_document_id"]:
                why = "same-filename corroboration changed"
            else:
                why = b4.verify_row(conn, want_row)
            if why:
                report["failures"].append((did, why))
            else:
                report["validated"] += 1
        if report["failures"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["failures"][:5])
            raise Abort(f"ABORT: {len(report['failures'])} of {len(ids)} rows no longer validate "
                        f"— {head}")
        out(f"  revalidated under lock: {report['validated']}/{len(ids)} "
            "(document state, plan, person, organization, repair provenance, filename, folder)")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written, no snapshot taken")
            trans.rollback()
            return report

        # 4. Snapshot BEFORE the first write.
        snap, snap_sha = write_snapshot(locked, rows, Path(snapshot_root))
        report["snapshot"], report["snapshot_sha256"] = str(snap), snap_sha
        out(f"  rollback snapshot: {snap}")
        out(f"  snapshot sha256:   {snap_sha}")

        # 5. Clear the stale person scope. One column, one statement per row, re-checked in the
        #    statement itself so a row that moved between the lock and now matches nothing.
        request_id = f"strict-safe-ownership-batch4:{b4.BATCH_ID}:{expect_sha[:12]}"
        for r in rows:
            updated = conn.execute(text(_CLEAR_SQL), {
                "id": r["document_id"], "former_person_id": r["former_person_id"],
                "organization_id": r["organization_id"]}).first()
            if updated is None:
                raise RuntimeError(f"document {r['document_id']} did not clear "
                                   "(ownership moved under the lock)")
            report["cleared"] += 1
            write_audit_event(
                action="document.ownership_conflict_resolved", entity_type="document",
                entity_id=r["document_id"], actor_user_id=actor_user_id, request_id=request_id,
                metadata={"document_id": r["document_id"], "batch_id": b4.BATCH_ID,
                          "removed_person_id": r["former_person_id"],
                          "retained_organization_id": r["organization_id"],
                          "reason": "canonical_type_repair: the person record is the retired "
                                    "business-as-person shell of this organization",
                          "repaired_from_person_id": r["repaired_from_person_id"],
                          "folder_segment": r["folder_segment"],
                          "twin_document_id": r["twin_document_id"],
                          "manifest_sha256": expect_sha},
                conn=conn)

        # 6. Post-write invariants, all inside the transaction.
        after = conn.execute(text(
            "select id, person_id, household_id, organization_id from documents "
            "where id = any(:ids) order by id"), {"ids": ids}).mappings().all()
        for row in after:
            expected_org = by_id[row["id"]]["organization_id"]
            if row["person_id"] is not None:
                raise RuntimeError(f"document {row['id']} still carries a person_id")
            if row["household_id"] is not None:
                raise RuntimeError(f"document {row['id']} gained a household_id")
            if row["organization_id"] != expected_org:
                raise RuntimeError(f"document {row['id']} organization changed to "
                                   f"{row['organization_id']}, expected {expected_org}")

        fp_after = _fingerprints(conn, ids, people, orgs)
        for key in ("target", "non_target", "entities"):
            if fp_after[key] != fp_before[key]:
                raise RuntimeError(f"{key} fingerprint changed — the batch touched more than "
                                   "documents.person_id on its own rows")

        audit_rows = conn.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": request_id, "a": "document.ownership_conflict_resolved"}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != len(rows):
            raise RuntimeError(f"{audit_rows} audit rows for {len(rows)} changes")

        trans.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['cleared']} conflict resolutions, "
            f"{report['audit_rows']} audit rows")
        out("  post-write checks: person_id NULL, household_id still NULL, organization unchanged, "
            "target/non-target/entity fingerprints all OK")
    except BaseException:
        if not report["committed"]:
            trans.rollback()
        raise
    finally:
        trans_conn.close()

    receipt = Path(report["snapshot"]).parent / "apply_receipt.json"
    receipt.write_text(json.dumps({
        "applied_at": datetime.now(UTC).isoformat(), "batch_id": "STRICT-SAFE-OWNERSHIP-4",
        "manifest_sha256": expect_sha, "json_manifest_sha256": expect_json_sha,
        "plan_digest": expect_plan_digest, "snapshot": report["snapshot"],
        "snapshot_sha256": report["snapshot_sha256"], "rows_cleared": report["cleared"],
        "audit_rows": report["audit_rows"], "actor_user_id": actor_user_id,
        "request_id": request_id, "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Apply the approved strict-safe ownership BATCH 4 conflict cleanup.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--manifest-json", required=True)
    ap.add_argument("--expect-sha256", required=True)
    ap.add_argument("--expect-json-sha256", required=True)
    ap.add_argument("--expect-plan-digest", required=True)
    ap.add_argument("--expect-rows", type=int, required=True)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    ap.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT))
    args = ap.parse_args(argv)
    run(args.manifest, expect_sha=args.expect_sha256,
        expect_plan_digest=args.expect_plan_digest, expect_rows=args.expect_rows,
        manifest_json=args.manifest_json, expect_json_sha=args.expect_json_sha256,
        apply_changes=args.apply, confirm=args.confirm, actor_user_id=args.actor_user_id,
        snapshot_root=Path(args.snapshot_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
