#!/usr/bin/env python3
"""Strict-safe ownership BATCH 5 — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_strict_safe_ownership_batch5.py --manifest <csv>

    # writes, and only with every key turned at once
    python scripts/apply_strict_safe_ownership_batch5.py --manifest <csv> \\
        --actor-user-id <id> --confirm APPLY-STRICT-SAFE-OWNERSHIP-BATCH5-55 --apply

WHERE THE OWNERSHIP RULES LIVE
------------------------------
Not here. Every assignment goes through ``households.resolve_document_ownership(..., conn=...)``,
the canonical single-document write path, so this batch gets the same atomic ``WHERE all-NULL AND
NOT permanent-reject`` re-check and the same ``document.ownership_resolved`` audit event as the
admin workflow. Passing ``conn`` is what makes that possible in a batch: without it the service
opens its own transaction per document and a partial apply becomes possible. The frozen contract
lives in ``document_strict_safe_ownership_batch5``. This script contributes transaction policy,
locking and refusals, and nothing else.

THE MANIFEST IS THE ONLY SOURCE OF ROWS
---------------------------------------
Batch 1 recomputed its plan and applied what came back. Batch 5 does not: it applies the 55 frozen
rows or it applies nothing. ``build_plan`` is recomputed and its digest must still equal the
approved one, and every manifest row must still appear in it naming the same person — but the plan
can only ever REMOVE a row from consideration, never add one. A document that qualifies today and
was not reviewed cannot reach a write through this script.

WHAT IT WRITES
--------------
``documents.person_id`` for exactly the manifest rows, plus one audit event each, inside ONE
transaction. household_id and organization_id stay NULL, review_status stays ``not_required``, tags
are untouched, and no proposal fact, classification, person, household, source row or OCR row is
written. Every one of those claims is fingerprinted before and after and re-checked before commit.

DRIFT ABORTS EVERYTHING
-----------------------
The manifest proves what a human approved; it cannot prove the corpus still looks that way. So each
row is locked FOR UPDATE and re-proved under the lock against all four frozen fingerprints. Any
drift raises before the first ownership write. There is no skip, no partial, no "apply the ones that
still pass" — a subset of a reviewed batch is a batch nobody reviewed.
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

SNAPSHOT_ROOT = REPO_ROOT / "var" / "strict_safe_ownership_batch5"
SNAPSHOT_CSV = "rollback_snapshot_strict_safe_ownership_batch5.csv"


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


# --- fingerprints: everything this batch must NOT change -----------------------------------------

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

_PROPOSAL_FP = """
    select md5(string_agg(
        id::text||'|'||document_id::text||'|'||version::text||'|'||is_current::text||'|'||
        coalesce(fact_value::text,''), E'\n' order by id))
      from document_facts where fact_type = 'owner_proposal'
"""

_CLASSIFICATION_FP = """
    select md5(string_agg(
        id::text||'|'||document_id::text||'|'||coalesce(doc_type,'')||'|'||
        coalesce(confidence::text,'')||'|'||coalesce(classifier_version,''),
        E'\n' order by id))
      from document_classifications
"""

_PEOPLE_FP = """
    select md5(string_agg(
        id::text||'|'||coalesce(first_name,'')||'|'||coalesce(last_name,'')||'|'||
        coalesce(full_name,'')||'|'||coalesce(normalized_email,'')||'|'||
        coalesce(normalized_phone,'')||'|'||coalesce(household_id::text,'')||'|'||active::text,
        E'\n' order by id))
      from people where id = any(:pids)
"""

_SOURCES_FP = """
    select md5(string_agg(
        document_id::text||'|'||coalesce(source_system,'')||'|'||coalesce(source_uri,'')||'|'||
        coalesce(source_external_id,'')||'|'||coalesce(source_hash,'')||'|'||
        coalesce(available::text,''), E'\n' order by document_id, id))
      from document_sources where document_id = any(:ids)
"""

_OCR_FP = """
    select md5(string_agg(
        document_id::text||'|'||coalesce(status,'')||'|'||coalesce(char_count::text,'')||'|'||
        coalesce(source_hash,''), E'\n' order by document_id, id))
      from document_ocr where document_id = any(:ids)
"""

_LOCK_SQL = """
    select id, original_name, person_id, household_id, organization_id, status, archived,
           deleted_at, review_status, tags, sha256
      from documents where id = any(:ids) order by id for update
"""


def _fingerprints(conn, ids, pids):
    from sqlalchemy import text
    out = {}
    for key, sql in (("target", _TARGET_FP), ("non_target", _NON_TARGET_FP),
                     ("sources", _SOURCES_FP), ("ocr", _OCR_FP)):
        out[key] = conn.execute(text(sql), {"ids": ids}).scalar()
    for key, sql in (("proposals", _PROPOSAL_FP), ("classifications", _CLASSIFICATION_FP)):
        out[key] = conn.execute(text(sql)).scalar()
    out["people"] = conn.execute(text(_PEOPLE_FP), {"pids": pids}).scalar()
    return out


#: The domain tables this batch must leave untouched, checked before and after inside the txn.
UNCHANGED_FINGERPRINTS = ("non_target", "sources", "ocr", "proposals", "classifications", "people")


# --- rollback artifact ---------------------------------------------------------------------------

SNAPSHOT_COLUMNS = ["document_id", "original_name",
                    "prior_person_id", "prior_household_id", "prior_organization_id",
                    "prior_review_status", "prior_tags_json",
                    "post_person_id", "post_household_id", "post_organization_id",
                    "post_review_status", "post_tags_json",
                    "assigned_person_id", "assigned_person_name",
                    "ownership_audit_id", "corroborator_count",
                    "document_fingerprint", "proposal_fingerprint",
                    "classification_fingerprint", "target_person_fingerprint"]


def write_rollback_artifact(root, rows, pre, post, audit_ids, *, request_id, manifest_sha):
    """The POPULATED rollback record: pre-image, post-image, audit id, authorising fingerprints.

    Written after the writes and BEFORE the commit, so it can carry the post-image and the audit
    event ids the writes produced. ``committed`` is false in the sidecar until the apply succeeds
    and drops a receipt beside it — a snapshot whose transaction rolled back must never be
    replayable as a rollback.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(root) / f"strict-safe-ownership-batch5-apply-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / SNAPSHOT_CSV
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["document_id"]):
            did = r["document_id"]
            a, b = pre[did], post[did]
            w.writerow({
                "document_id": did,
                "original_name": a["original_name"] or "",
                "prior_person_id": "" if a["person_id"] is None else a["person_id"],
                "prior_household_id": "" if a["household_id"] is None else a["household_id"],
                "prior_organization_id": "" if a["organization_id"] is None else a["organization_id"],
                "prior_review_status": a["review_status"] or "",
                "prior_tags_json": json.dumps(a["tags"], sort_keys=True, ensure_ascii=False),
                "post_person_id": "" if b["person_id"] is None else b["person_id"],
                "post_household_id": "" if b["household_id"] is None else b["household_id"],
                "post_organization_id": "" if b["organization_id"] is None else b["organization_id"],
                "post_review_status": b["review_status"] or "",
                "post_tags_json": json.dumps(b["tags"], sort_keys=True, ensure_ascii=False),
                "assigned_person_id": r["person_id"],
                "assigned_person_name": r["person_name"],
                "ownership_audit_id": audit_ids.get(did, ""),
                "corroborator_count": r["corroborator_count"],
                "document_fingerprint": r["document_fingerprint"],
                "proposal_fingerprint": r["proposal_fingerprint"],
                "classification_fingerprint": r["classification_fingerprint"],
                "target_person_fingerprint": r["target_person_fingerprint"],
            })
    from app.services.document_strict_safe_ownership_batch5 import BATCH_ID, sha256_of
    snap_sha = sha256_of(path)
    (out / "manifest.json").write_text(json.dumps({
        "created_at": datetime.now(UTC).isoformat(),
        "batch_id": BATCH_ID,
        "rows": len(rows),
        "snapshot_sha256": snap_sha,
        "manifest_sha256": manifest_sha,
        "request_id": request_id,
        "committed": False,
    }, indent=2) + "\n", encoding="utf-8")
    return path, snap_sha


# --- the run -------------------------------------------------------------------------------------

def run(manifest_path, *, expect_sha=None, expect_plan_digest=None, expect_rows=None,
        expect_composition=None, expect_people=None,
        apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_root=SNAPSHOT_ROOT, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.services import document_strict_safe_ownership as sso
    from app.services import document_strict_safe_ownership_batch5 as b5
    from app.services.households import resolve_document_ownership

    expect_plan_digest = b5.FROZEN_PLAN_DIGEST if expect_plan_digest is None else expect_plan_digest

    try:
        rows = b5.load_manifest(manifest_path, expect_sha=expect_sha, expect_rows=expect_rows,
                                expect_composition=expect_composition, expect_people=expect_people)
    except b5.ManifestError as exc:
        raise Abort(f"ABORT: {exc}") from exc

    manifest_sha = b5.sha256_of(Path(manifest_path))
    want = b5.confirm_phrase("APPLY", len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id; ownership needs an actor")

    ids = sorted(r["document_id"] for r in rows)
    pids = sorted({r["person_id"] for r in rows})
    by_id = {r["document_id"]: r for r in rows}
    report = {"rows": len(rows), "validated": 0, "applied": 0, "audit_rows": 0,
              "snapshot": None, "snapshot_sha256": None, "committed": False,
              "confirm_phrase": want, "manifest_sha256": manifest_sha,
              "plan_digest": expect_plan_digest, "failures": []}

    out(f"manifest: {manifest_path}")
    out(f"  rows={len(rows)} people={len(pids)} "
        f"composition={ {c: sum(1 for r in rows if r['corroborator_count'] == c) for c in sorted({r['corroborator_count'] for r in rows})} }")
    out(f"  manifest sha256 verified: {manifest_sha}")

    trans_conn = engine.connect()
    trans = trans_conn.begin()
    try:
        conn = trans_conn
        if conn.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; the apply needs a writable session")

        # 1. Recompute the canonical plan. A GATE — never a source of rows.
        live_plan = sso.build_plan(conn)
        live_digest = sso.plan_digest(live_plan)
        if live_digest != expect_plan_digest:
            raise Abort("ABORT: the strict-safe plan has moved since it was approved.\n"
                        f"  approved digest {expect_plan_digest}\n"
                        f"  current  digest {live_digest}\n"
                        f"  approved rows {len(rows)}, current plan rows {len(live_plan)}\n"
                        "Re-run the selection and have the new plan reviewed.")
        # Plan membership is checked per row in step 3, AFTER the frozen-state checks, so an
        # operator is told "already owned by person 412" rather than the downstream consequence
        # "no longer in the plan". Both abort; only one of them says what actually happened.
        live_by_id = {r["document_id"]: r for r in live_plan}
        out(f"  live plan digest verified: {live_digest} ({len(live_plan)} rows)")

        # 2. Lock exactly the manifest ids, and require exact set equality.
        locked = {r["id"]: dict(r) for r in conn.execute(text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != ids:
            gone = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(gone)} manifest documents did not lock: {gone[:10]}")
        out(f"  locked {len(locked)} rows FOR UPDATE (exact set equality)")

        fp_before = _fingerprints(conn, ids, pids)

        # 3. Re-prove EVERY row under the lock against all four frozen fingerprints.
        live_state = {r["id"]: dict(r) for r in conn.execute(
            text(b5.LIVE_STATE_SQL), {"ids": ids}).mappings()}
        persons = {r["id"]: dict(r) for r in conn.execute(
            text(b5.PERSON_STATE_SQL), {"ids": pids}).mappings()}
        for did in ids:
            w = by_id[did]
            why = b5.verify_row(w, live_state.get(did), persons.get(w["person_id"]))
            if why is None and did not in live_by_id:
                why = "no longer in the canonical strict-safe plan"
            elif why is None and live_by_id[did]["person_id"] != w["person_id"]:
                why = (f"plan proposes person {live_by_id[did]['person_id']}, "
                       f"manifest says {w['person_id']}")
            if why:
                report["failures"].append((did, why))
            else:
                report["validated"] += 1
        if report["failures"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["failures"][:5])
            raise Abort(f"ABORT: {len(report['failures'])} of {len(ids)} rows no longer validate "
                        f"— {head}")
        out(f"  re-proved under lock: {report['validated']}/{len(ids)} "
            "(document, proposal, classification and target-person fingerprints)")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written, no snapshot taken")
            trans.rollback()
            return report

        pre = {did: dict(locked[did]) for did in ids}

        # 4. Assign, through the canonical service, in this transaction.
        request_id = f"strict-safe-ownership-batch5:{manifest_sha[:12]}"
        for r in rows:
            result = resolve_document_ownership(
                r["document_id"], person_id=r["person_id"], actor_user_id=actor_user_id,
                request_id=request_id, conn=conn)
            if not result.get("assigned"):
                raise RuntimeError(f"document {r['document_id']} was not assigned: "
                                   f"{result.get('reason')}")
            report["applied"] += 1

        # 5. Post-write invariants, all inside the transaction.
        post = {r["id"]: dict(r) for r in conn.execute(text(
            "select id, person_id, household_id, organization_id, review_status, tags "
            "from documents where id = any(:ids)"), {"ids": ids}).mappings()}
        wrong = [d for d in ids if post[d]["person_id"] != by_id[d]["person_id"]]
        if wrong:
            raise RuntimeError(
                f"{len(wrong)} documents do not carry their approved person_id: {wrong[:10]}")
        for did in ids:
            if post[did]["household_id"] is not None or post[did]["organization_id"] is not None:
                raise RuntimeError(f"document {did} gained a household/organization scope")
            if (post[did]["review_status"] or "") != (pre[did]["review_status"] or ""):
                raise RuntimeError(f"document {did} review_status changed — deferral clause fired")
            if json.dumps(post[did]["tags"], sort_keys=True, ensure_ascii=False) \
                    != json.dumps(pre[did]["tags"], sort_keys=True, ensure_ascii=False):
                raise RuntimeError(f"document {did} tags changed — deferral clause fired")

        fp_after = _fingerprints(conn, ids, pids)
        for key in UNCHANGED_FINGERPRINTS:
            if fp_after[key] != fp_before[key]:
                raise RuntimeError(f"{key} fingerprint changed — the batch touched more than "
                                   "documents.person_id")

        # audit_events.entity_id is varchar; key the map by int so it joins to document ids.
        audit = {int(eid): aid for eid, aid in conn.execute(text(
            "select entity_id, max(id) from audit_events where request_id = :r and action = :a "
            "group by entity_id"), {"r": request_id, "a": "document.ownership_resolved"}).all()}
        report["audit_rows"] = len(audit)
        if len(audit) != len(rows):
            raise RuntimeError(f"{len(audit)} audit rows for {len(rows)} assignments")

        # 6. Populated rollback artifact, hashed BEFORE the commit.
        snap, snap_sha = write_rollback_artifact(
            Path(snapshot_root), rows, pre, post, audit,
            request_id=request_id, manifest_sha=manifest_sha)
        report["snapshot"], report["snapshot_sha256"] = str(snap), snap_sha
        out(f"  rollback artifact: {snap}")
        out(f"  artifact sha256:   {snap_sha} (hashed before commit)")

        trans.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['applied']} assignments, {report['audit_rows']} audit rows")
        out("  post-write checks: person_id equality, household/organization still NULL, "
            "review_status/tags unchanged, proposals/classifications/people/sources/OCR and "
            "non-target fingerprints all OK")
    except BaseException:
        if not report["committed"]:
            trans.rollback()
        raise
    finally:
        trans_conn.close()

    # The receipt is what makes the snapshot replayable: written only after a real commit.
    from app.services.document_strict_safe_ownership_batch5 import BATCH_ID
    snap_dir = Path(report["snapshot"]).parent
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    meta["committed"] = True
    (snap_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    receipt = snap_dir / "apply_receipt.json"
    receipt.write_text(json.dumps({
        "applied_at": datetime.now(UTC).isoformat(), "batch_id": BATCH_ID,
        "manifest_sha256": manifest_sha, "plan_digest": expect_plan_digest,
        "snapshot": report["snapshot"], "snapshot_sha256": report["snapshot_sha256"],
        "rows_applied": report["applied"], "audit_rows": report["audit_rows"],
        "actor_user_id": actor_user_id, "request_id": request_id,
        "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    from app.services import document_strict_safe_ownership_batch5 as b5
    ap = argparse.ArgumentParser(
        description="Apply the frozen strict-safe ownership BATCH 5 manifest (55 rows).")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--expect-sha256", default=b5.FROZEN_MANIFEST_SHA256)
    ap.add_argument("--expect-plan-digest", default=b5.FROZEN_PLAN_DIGEST)
    ap.add_argument("--expect-rows", type=int, default=b5.EXPECTED_ROWS)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--apply", action="store_true", default=False,
                    help="WRITE. Without it this script reads and validates only.")
    ap.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT))
    args = ap.parse_args(argv)
    run(args.manifest, expect_sha=args.expect_sha256,
        expect_plan_digest=args.expect_plan_digest, expect_rows=args.expect_rows,
        apply_changes=args.apply, confirm=args.confirm, actor_user_id=args.actor_user_id,
        snapshot_root=Path(args.snapshot_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
