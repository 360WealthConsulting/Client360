#!/usr/bin/env python3
"""Batch 1 non-client exclusion — the guarded apply.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_nonclient_exclusion.py --manifest <csv> \\
        --expect-sha256 <sha> --expect-plan-digest <digest> --expect-rows 2990

    # writes, and only with every key turned at once
    python scripts/apply_nonclient_exclusion.py --manifest <csv> \\
        --expect-sha256 <sha> --expect-plan-digest <digest> --expect-rows 2990 \\
        --actor-user-id <id> --confirm APPLY-NONCLIENT-BATCH1-2990 --apply

THE MANIFEST IS THE INPUT, NOT A SUGGESTION
-------------------------------------------
The reviewed manifest is immutable: this script never rewrites it and never regenerates it. Every
expectation (`--expect-*`) is supplied by the operator from the approved review, never derived from
the file being checked — a file cannot vouch for itself. The manifest carries no ``applied`` column
and none is required; "has this already run?" is answered from the DATABASE (the sentinel is
self-identifying), which is the only place that can answer it truthfully.

WHY EVERY GUARD IS RE-EVALUATED
-------------------------------
The manifest proves what a human approved. It does not prove the corpus still looks that way. So the
plan is recomputed live and its digest compared, the rows are locked FOR UPDATE, and then EVERY row
is put back through ``document_nonclient_exclusion.eligibility`` inside the transaction — and its
answer must still name the same rule the manifest recorded. A document that gained an owner, was
renamed, or was re-proposed since review aborts the whole batch rather than being skipped: a partial
apply of a reviewed batch is a batch nobody reviewed.

WHAT THIS TOUCHES
-----------------
``review_status`` and one ``tags`` key, through ``exclude_document`` — nothing else. Ownership,
``status``, ``archived``, ``deleted_at``, ``sha256``, ``storage_uri``, ``document_sources`` and
``document_ocr`` are fingerprinted before and after and must be byte-identical, and every
non-target row in the table is fingerprinted too. No DELETE is issued anywhere, and the only file
this process ever writes is the rollback snapshot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BATCH_ID = "NONCLIENT-BATCH1"
SNAPSHOT_ROOT = REPO_ROOT / "var" / "nonclient_exclusion"

#: The reviewed composition. A manifest that does not match this exactly is not the reviewed batch.
EXPECTED_CENSUS = {
    "technical_extension": 2618,
    "web_asset_download": 369,
    "named_artifact": 3,
}


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def confirm_phrase(rows: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", BATCH_ID).strip("-").upper()
    return f"APPLY-{slug}-{rows}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path, *, expect_sha: str, expect_rows: int) -> list[dict]:
    """Read and structurally validate the reviewed manifest. Never modifies it."""
    from app.services import document_nonclient_exclusion as nx

    if not expect_sha:
        raise Abort("ABORT: --expect-sha256 is required")
    if expect_rows is None:
        raise Abort("ABORT: --expect-rows is required")
    if not path.is_file():
        raise Abort(f"ABORT: manifest not found: {path}")

    digest = sha256_of(path)
    if digest != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")

    with path.open(newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    if len(raw) != expect_rows:
        raise Abort(f"ABORT: manifest has {len(raw)} rows, approved {expect_rows}")

    rows, seen = [], set()
    for r in raw:
        try:
            did = int(r["document_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Abort(f"ABORT: unreadable document_id in manifest row: {r!r}") from exc
        if did in seen:
            raise Abort(f"ABORT: duplicate document_id {did} in manifest")
        seen.add(did)
        rule = (r.get("matched_rule") or "").strip()
        if rule not in nx.EXCLUDABLE_REASONS:
            raise Abort(f"ABORT: document {did} carries unapproved rule {rule!r}")
        if (r.get("proposed_classification") or "").strip() != nx.EXCLUDED_REVIEW_STATUS:
            raise Abort(f"ABORT: document {did} proposes an unexpected classification")
        if (r.get("current_owner_state") or "").strip() != "unowned":
            raise Abort(f"ABORT: document {did} was not unowned at review time")
        rows.append({"document_id": did, "matched_rule": rule,
                     "original_name": r.get("original_name") or ""})

    census = Counter(r["matched_rule"] for r in rows)
    if dict(census) != EXPECTED_CENSUS:
        raise Abort(f"ABORT: manifest census {dict(census)} != approved {EXPECTED_CENSUS}")
    return rows


# --- fingerprints: everything this batch must NOT change -----------------------------------------

_TARGET_FP = """
    select md5(string_agg(
        id::text||'|'||coalesce(person_id::text,'')||'|'||coalesce(household_id::text,'')||'|'||
        coalesce(organization_id::text,'')||'|'||coalesce(status,'')||'|'||archived::text||'|'||
        coalesce(deleted_at::text,'')||'|'||coalesce(sha256,'')||'|'||coalesce(storage_uri,'')||'|'||
        coalesce(storage_path,'')||'|'||coalesce(content_type,'')||'|'||coalesce(original_name,''),
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
    select id, original_name, review_status, tags, person_id, household_id, organization_id,
           status, archived, deleted_at
      from documents where id = any(:ids) order by id for update
"""


def _fingerprints(conn, ids):
    from sqlalchemy import text
    return {
        "target": conn.execute(text(_TARGET_FP), {"ids": ids}).scalar(),
        "non_target": conn.execute(text(_NON_TARGET_FP), {"ids": ids}).scalar(),
        "sources": conn.execute(text(_SOURCES_FP), {"ids": ids}).scalar(),
        "ocr": conn.execute(text(_OCR_FP), {"ids": ids}).scalar(),
    }


# --- snapshot ------------------------------------------------------------------------------------

SNAPSHOT_COLUMNS = ["document_id", "original_name", "matched_rule",
                    "prev_review_status", "prev_tags_json"]


def write_snapshot(locked: dict, rows: list[dict], root: Path) -> tuple[Path, str]:
    """Exact prior state for exactly these ids, written BEFORE any write. Returns (csv, sha256)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(root) / f"nonclient-apply-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rollback_snapshot_nonclient_exclusion.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["document_id"]):
            d = locked[r["document_id"]]
            w.writerow({
                "document_id": r["document_id"],
                "original_name": d["original_name"] or "",
                "matched_rule": r["matched_rule"],
                "prev_review_status": d["review_status"] or "",
                # exact prior tags, canonically ordered so the digest is reproducible
                "prev_tags_json": json.dumps(d["tags"], sort_keys=True, ensure_ascii=False),
            })
    snap_sha = sha256_of(path)
    (out / "manifest.json").write_text(json.dumps({
        "created_at": datetime.now(UTC).isoformat(),
        "batch_id": BATCH_ID,
        "rows": len(rows),
        "snapshot_sha256": snap_sha,
    }, indent=2) + "\n", encoding="utf-8")
    return path, snap_sha


# --- the run -------------------------------------------------------------------------------------

def run(manifest_path, *, expect_sha, expect_plan_digest, expect_rows,
        apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_root=SNAPSHOT_ROOT, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services import document_nonclient_exclusion as nx
    from scripts.preview_nonclient_exclusion import collect
    from scripts.preview_nonclient_exclusion import digest as plan_digest

    rows = load_manifest(Path(manifest_path), expect_sha=expect_sha, expect_rows=expect_rows)
    want = confirm_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id; a classification needs an actor")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    report = {"rows": len(rows), "validated": 0, "applied": 0, "audit_rows": 0,
              "snapshot": None, "snapshot_sha256": None, "committed": False,
              "confirm_phrase": want, "manifest_sha256": expect_sha,
              "plan_digest": expect_plan_digest, "failures": []}

    out(f"manifest: {manifest_path}")
    out(f"  batch={BATCH_ID} rows={len(rows)} census={dict(Counter(r['matched_rule'] for r in rows))}")
    out(f"  manifest sha256 verified: {expect_sha}")

    with engine.connect() as conn:
        if conn.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; the apply needs a writable session")
        live_plan, _ = collect(conn)
        live = plan_digest(live_plan)
        if live != expect_plan_digest:
            raise Abort("ABORT: the corpus has moved since this manifest was reviewed.\n"
                        f"  manifest plan digest {expect_plan_digest}\n"
                        f"  current  plan digest {live}\n"
                        "Re-run the preview and have the new plan reviewed.")
        out(f"  live plan digest verified: {live}")

    trans_conn = engine.connect()
    trans = trans_conn.begin()
    try:
        conn = trans_conn
        already = conn.execute(text(
            "select count(*) from documents where id = any(:ids) and review_status = :s"),
            {"ids": ids, "s": nx.EXCLUDED_REVIEW_STATUS}).scalar()
        if already:
            raise Abort(f"ABORT: {already} of these documents are already classified "
                        f"{nx.EXCLUDED_REVIEW_STATUS!r}; this batch has already been applied")

        locked = {r["id"]: dict(r) for r in conn.execute(
            text(_LOCK_SQL), {"ids": ids}).mappings()}
        if len(locked) != len(ids):
            missing = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(missing)} manifest documents no longer exist: {missing[:10]}")

        fp_before = _fingerprints(conn, ids)
        # How many rows OUTSIDE this manifest already carry the sentinel (an earlier batch, or a
        # manual classification). The invariant is that this number does not MOVE — asserting it is
        # zero would make every batch after the first one impossible.
        stray_before = conn.execute(text(
            "select count(*) from documents where review_status = :s and id <> all(:ids)"),
            {"ids": ids, "s": nx.EXCLUDED_REVIEW_STATUS}).scalar()

        # Revalidate ALL rows before ANY write.
        for did in ids:
            check = nx.eligibility(conn, did)
            if not check["eligible"]:
                report["failures"].append((did, check["reason_code"]))
            elif check["reason"] != by_id[did]["matched_rule"]:
                report["failures"].append(
                    (did, f"rule drift: manifest {by_id[did]['matched_rule']} "
                          f"!= current {check['reason']}"))
            else:
                report["validated"] += 1
        if report["failures"]:
            head = "; ".join(f"{d}:{why}" for d, why in report["failures"][:5])
            raise Abort(f"ABORT: {len(report['failures'])} of {len(ids)} rows no longer "
                        f"validate — {head}")
        out(f"  revalidated: {report['validated']}/{len(ids)} rows still eligible under the same rule")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written, no snapshot taken")
            trans.rollback()
            return report

        snap, snap_sha = write_snapshot(locked, rows, snapshot_root)
        report["snapshot"], report["snapshot_sha256"] = str(snap), snap_sha
        out(f"  rollback snapshot: {snap}")
        out(f"  snapshot sha256:   {snap_sha}")

        request_id = f"nonclient-batch1:{BATCH_ID}:{expect_sha[:12]}"
        for r in rows:
            result = nx.exclude_document(
                r["document_id"], reason=r["matched_rule"], actor_user_id=actor_user_id,
                request_id=request_id, conn=conn)
            if not result["excluded"]:
                raise RuntimeError(
                    f"document {r['document_id']} refused mid-batch: {result['outcome']}")
            report["applied"] += 1

        # --- post-write assertions, all inside the transaction -----------------------------------
        applied_ids = sorted(conn.execute(text(
            "select id from documents where id = any(:ids) and review_status = :s"),
            {"ids": ids, "s": nx.EXCLUDED_REVIEW_STATUS}).scalars())
        if applied_ids != ids:
            raise RuntimeError("post-write set equality failed")
        stray_after = conn.execute(text(
            "select count(*) from documents where review_status = :s and id <> all(:ids)"),
            {"ids": ids, "s": nx.EXCLUDED_REVIEW_STATUS}).scalar()
        if stray_after != stray_before:
            raise RuntimeError(
                f"sentinel count outside the manifest moved {stray_before} -> {stray_after}; "
                "this batch classified something it was not given")
        bad = conn.execute(text(
            "select count(*) from documents where id = any(:ids) and (person_id is not null "
            "or household_id is not null or organization_id is not null or archived "
            "or status = 'deleted' or deleted_at is not null)"), {"ids": ids}).scalar()
        if bad:
            raise RuntimeError(f"{bad} classified rows are owned, archived or deleted")

        fp_after = _fingerprints(conn, ids)
        for key in ("target", "non_target", "sources", "ocr"):
            if fp_after[key] != fp_before[key]:
                raise RuntimeError(f"{key} fingerprint changed — the batch touched more than "
                                   "review_status and tags")

        audit_rows = conn.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": request_id, "a": "document.nonclient_excluded"}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != len(rows):
            raise RuntimeError(f"{audit_rows} audit rows for {len(rows)} classifications")

        write_audit_event(
            action="document.nonclient_exclusion_batch_applied", entity_type="document_batch",
            entity_id=None, actor_user_id=actor_user_id, request_id=request_id,
            metadata={"batch_id": BATCH_ID, "rows": len(rows),
                      "manifest_sha256": expect_sha, "plan_digest": expect_plan_digest,
                      "snapshot_sha256": snap_sha, "census": dict(EXPECTED_CENSUS)},
            conn=conn)

        trans.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['applied']} classifications, {report['audit_rows']} audit rows")
        out("  post-write checks: set equality, no strays, ownership/archive/source/OCR "
            "fingerprints, non-target fingerprint — all OK")
    except BaseException:
        if not report["committed"]:
            trans.rollback()
        raise
    finally:
        trans_conn.close()

    receipt = Path(report["snapshot"]).parent / "apply_receipt.json"
    receipt.write_text(json.dumps({
        "applied_at": datetime.now(UTC).isoformat(), "batch_id": BATCH_ID,
        "rows_applied": report["applied"], "audit_rows": report["audit_rows"],
        "manifest_sha256": expect_sha, "plan_digest": expect_plan_digest,
        "snapshot_sha256": report["snapshot_sha256"], "actor_user_id": actor_user_id,
        "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply the reviewed Batch 1 non-client exclusion.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--expect-sha256", required=True)
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
        apply_changes=args.apply, confirm=args.confirm, actor_user_id=args.actor_user_id,
        snapshot_root=Path(args.snapshot_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
