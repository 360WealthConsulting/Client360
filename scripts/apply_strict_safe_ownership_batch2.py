#!/usr/bin/env python3
"""Strict-safe ownership BATCH 2 — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_strict_safe_ownership_batch2.py --manifest <csv> \\
        --expect-sha256 <sha> --expect-plan-digest <digest> --expect-rows 52

    # writes, and only with every key turned at once
    python scripts/apply_strict_safe_ownership_batch2.py --manifest <csv> \\
        --expect-sha256 <sha> --expect-plan-digest <digest> --expect-rows 52 \\
        --actor-user-id <id> --confirm APPLY-STRICT-SAFE-OWNERSHIP-2-52 --apply

THIS IS NOT THE BATCH 1 SCRIPT
------------------------------
``scripts/apply_strict_safe_ownership.py`` applies the frozen 541-row Batch 1 plan and is untouched
by this file. Batch 2 is a separately reviewed population with a DIFFERENT rule (see
``app.services.document_strict_safe_ownership_batch2``), a different digest and a different
confirmation phrase, so the two cannot be run against each other's manifests even by accident.

The safety architecture is deliberately identical, because it is the part that has been exercised in
production: manifest SHA pin, expected rows / people / composition, a live plan digest pin, one
transaction, ``FOR UPDATE`` on exactly the manifest ids, exact set equality, full revalidation under
the lock, a rollback snapshot written before the first write, assignment only through the canonical
``resolve_document_ownership(..., conn=conn)``, and before/after fingerprints proving nothing but
``documents.person_id`` moved.

CORROBORATORS ARE NAMED, NOT COUNTED
------------------------------------
Every Batch 2 row carries exactly ONE contact corroborator and the manifest names it —
``ADDRESS`` / ``EMAIL`` / ``PHONE`` — rather than carrying a count. That is on purpose: Batch 1's
``corroborator_count`` means "two or three independent contact signals", and a Batch 2 row is not
that. The folder and filename evidence that makes a Batch 2 row safe is checked against live state
below, but it is never folded into a corroborator count, because it is filing evidence and not
contact evidence.

DRIFT ABORTS EVERYTHING
-----------------------
The manifest proves what a human approved; it cannot prove the corpus still looks that way. The whole
Batch 2 plan is recomputed live and its digest must equal the approved one, every manifest row is
re-checked under the lock against that live plan — person, corroborator, folder AND source — and any
drift raises before the first ownership write. There is no skip and no partial: a subset of a
reviewed batch is a batch nobody reviewed.
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

SNAPSHOT_ROOT = REPO_ROOT / "var" / "strict_safe_ownership_batch2"

#: The snapshot filename ``scripts/rollback_strict_safe_ownership.py`` reads. Batch 2 writes the same
#: file, with the same prior-state columns, under its own root — so the existing, already-exercised
#: rollback tool reverts a Batch 2 apply with no change to that tool. (Its confirmation phrase still
#: names batch 1; the phrase is a typo-guard, and the snapshot itself is what bounds the revert.)
SNAPSHOT_CSV = "rollback_snapshot_strict_safe_ownership.csv"

#: The approved row count. Pinned here as well as on the command line so a mistyped --expect-rows
#: cannot quietly widen or narrow the reviewed batch.
EXPECTED_ROWS = 52

#: The approved number of distinct people.
EXPECTED_DISTINCT_PEOPLE = 39

#: The approved composition: {corroborator name: rows}. A manifest that does not match is not the
#: reviewed batch, whatever its row count says.
EXPECTED_COMPOSITION = {"ADDRESS": 49, "EMAIL": 1, "PHONE": 2}

#: Every Batch 2 row carries exactly one CONTACT corroborator. Folder and filename evidence are
#: independent ownership signals and are deliberately not added to this number.
INDEPENDENT_DOCUMENT_CORROBORATOR_COUNT = 1

MANIFEST_REQUIRED_COLUMNS = ("document_id", "person_id", "person_name", "corroborator",
                             "matching_folder", "source_id", "source_external_id")


class Abort(SystemExit):
    """A gate refused. Always raised before any write."""


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").upper()


def confirm_phrase(rows: int) -> str:
    from app.services.document_strict_safe_ownership_batch2 import BATCH_ID
    return f"APPLY-{_slug(BATCH_ID)}-{rows}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path, *, expect_sha: str, expect_rows: int) -> list[dict]:
    """Read and structurally validate the approved manifest. Never modifies it."""
    from app.services.document_strict_safe_ownership_batch2 import (
        CORROBORATOR_KINDS,
        PERMANENT_EXCLUDED_DOCUMENT_IDS,
    )

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
            did, pid = int(r["document_id"]), int(r["person_id"])
            source_id = int(r["source_id"])
        except (TypeError, ValueError) as exc:
            raise Abort(f"ABORT: unreadable manifest row: {r!r}") from exc
        if did in seen:
            raise Abort(f"ABORT: duplicate document_id {did} in manifest")
        seen.add(did)
        if did in PERMANENT_EXCLUDED_DOCUMENT_IDS:
            raise Abort(f"ABORT: document {did} is permanently excluded from this batch "
                        "(conflicting available SharePoint path evidence)")
        corroborator = (r.get("corroborator") or "").strip().upper()
        if corroborator not in CORROBORATOR_KINDS:
            raise Abort(f"ABORT: document {did} carries corroborator {corroborator!r}, "
                        f"not one of {list(CORROBORATOR_KINDS)}")
        matching_folder = (r.get("matching_folder") or "").strip()
        if not matching_folder:
            raise Abort(f"ABORT: document {did} carries no matching_folder; Batch 2 requires "
                        "source-path evidence")
        rows.append({"document_id": did, "person_id": pid,
                     "person_name": (r.get("person_name") or "").strip(),
                     "corroborator": corroborator,
                     "matching_folder": matching_folder,
                     "source_id": source_id,
                     "source_external_id": (r.get("source_external_id") or "").strip()})

    composition = dict(Counter(r["corroborator"] for r in rows))
    if composition != EXPECTED_COMPOSITION:
        raise Abort(f"ABORT: composition {composition} != approved {EXPECTED_COMPOSITION}")
    people = {r["person_id"] for r in rows}
    if len(people) != EXPECTED_DISTINCT_PEOPLE:
        raise Abort(f"ABORT: {len(people)} distinct people, approved {EXPECTED_DISTINCT_PEOPLE}")
    return rows


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
           deleted_at, review_status, tags
      from documents where id = any(:ids) order by id for update
"""


def _fingerprints(conn, ids):
    from sqlalchemy import text
    return {k: conn.execute(text(sql), {"ids": ids}).scalar() for k, sql in (
        ("target", _TARGET_FP), ("non_target", _NON_TARGET_FP),
        ("sources", _SOURCES_FP), ("ocr", _OCR_FP))}


# --- snapshot ------------------------------------------------------------------------------------

SNAPSHOT_COLUMNS = ["document_id", "original_name", "prior_person_id", "prior_household_id",
                    "prior_organization_id", "prior_review_status", "prior_tags_json",
                    "destination_person_id", "destination_person_name", "corroborator",
                    "independent_document_corroborator_count", "matching_folder", "source_id",
                    "source_external_id", "evidence_json"]


def write_snapshot(locked, rows, live_by_id, root: Path) -> tuple[Path, str]:
    """Exact prior state for exactly these ids, written BEFORE any write."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(root) / f"strict-safe-ownership-batch2-apply-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / SNAPSHOT_CSV
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["document_id"]):
            d = locked[r["document_id"]]
            live = live_by_id.get(r["document_id"], {})
            w.writerow({
                "document_id": r["document_id"],
                "original_name": d["original_name"] or "",
                "prior_person_id": "" if d["person_id"] is None else d["person_id"],
                "prior_household_id": "" if d["household_id"] is None else d["household_id"],
                "prior_organization_id": "" if d["organization_id"] is None else d["organization_id"],
                "prior_review_status": d["review_status"] or "",
                "prior_tags_json": json.dumps(d["tags"], sort_keys=True, ensure_ascii=False),
                "destination_person_id": r["person_id"],
                "destination_person_name": r["person_name"],
                "corroborator": r["corroborator"],
                "independent_document_corroborator_count":
                    INDEPENDENT_DOCUMENT_CORROBORATOR_COUNT,
                "matching_folder": r["matching_folder"],
                "source_id": r["source_id"],
                "source_external_id": r["source_external_id"],
                "evidence_json": json.dumps(live.get("evidence", []), ensure_ascii=False),
            })
    snap_sha = sha256_of(path)
    (out / "manifest.json").write_text(json.dumps({
        "created_at": datetime.now(UTC).isoformat(),
        "batch_id": "STRICT-SAFE-OWNERSHIP-2",
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
    from app.services import document_strict_safe_ownership_batch2 as b2
    from app.services.households import resolve_document_ownership

    rows = load_manifest(Path(manifest_path), expect_sha=expect_sha, expect_rows=expect_rows)
    want = confirm_phrase(len(rows))
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise Abort("ABORT: --apply requires --actor-user-id; ownership needs an actor")

    ids = sorted(r["document_id"] for r in rows)
    by_id = {r["document_id"]: r for r in rows}
    report = {"rows": len(rows), "validated": 0, "applied": 0, "audit_rows": 0,
              "snapshot": None, "snapshot_sha256": None, "committed": False,
              "confirm_phrase": want, "manifest_sha256": expect_sha,
              "plan_digest": expect_plan_digest, "failures": []}

    out(f"manifest: {manifest_path}")
    out(f"  rows={len(rows)} people={len({r['person_id'] for r in rows})} "
        f"composition={dict(Counter(r['corroborator'] for r in rows))}")
    out(f"  manifest sha256 verified: {expect_sha}")

    trans_conn = engine.connect()
    trans = trans_conn.begin()
    try:
        conn = trans_conn
        if conn.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; the apply needs a writable session")

        # 1. Recompute the whole Batch 2 plan live and require digest equality.
        live_plan = b2.build_plan(conn)
        live_digest = b2.plan_digest(live_plan)
        if live_digest != expect_plan_digest:
            raise Abort("ABORT: the Batch 2 plan has moved since it was approved.\n"
                        f"  approved digest {expect_plan_digest}\n"
                        f"  current  digest {live_digest}\n"
                        f"  approved rows {len(rows)}, current plan rows {len(live_plan)}\n"
                        "Re-run the selection and have the new plan reviewed.")
        out(f"  live plan digest verified: {live_digest} ({len(live_plan)} rows)")
        live_by_id = {r["document_id"]: r for r in live_plan}

        # 2. Lock exactly the manifest ids, and require exact set equality.
        locked = {r["id"]: dict(r) for r in conn.execute(text(_LOCK_SQL), {"ids": ids}).mappings()}
        if sorted(locked) != ids:
            missing = sorted(set(ids) - set(locked))
            raise Abort(f"ABORT: {len(missing)} manifest documents did not lock: {missing[:10]}")
        out(f"  locked {len(locked)} rows FOR UPDATE (exact set equality)")

        fp_before = _fingerprints(conn, ids)

        # 3. Revalidate EVERY row under the lock, before ANY write. Document state, the Batch 2 rule
        #    (via the live plan), the proposed person, the named corroborator AND the filing source.
        rejects = set(b2.PERMANENT_REJECT_DOCUMENT_IDS)
        for did in ids:
            d, want_row, live = locked[did], by_id[did], live_by_id.get(did)
            why = None
            if d["person_id"] is not None or d["household_id"] is not None \
                    or d["organization_id"] is not None:
                why = "already owned"
            elif d["archived"]:
                why = "archived"
            elif d["status"] == "deleted" or d["deleted_at"] is not None:
                why = "deleted"
            elif (d["review_status"] or "") != "not_required":
                why = f"review_status is {d['review_status']!r}"
            elif did in rejects:
                why = "permanent reject"
            elif did in b2.PERMANENT_EXCLUDED_DOCUMENT_IDS:
                why = "permanently excluded from batch 2"
            elif live is None:
                why = "no longer in the batch 2 plan (proposal/evidence/path drift)"
            elif live["person_id"] != want_row["person_id"]:
                why = f"proposed person drifted {want_row['person_id']} -> {live['person_id']}"
            elif live["corroborator"] != want_row["corroborator"]:
                why = (f"corroborator drifted {want_row['corroborator']} -> "
                       f"{live['corroborator']}")
            elif live["source_id"] != want_row["source_id"]:
                why = f"filing source drifted {want_row['source_id']} -> {live['source_id']}"
            elif live["matching_folder"] != want_row["matching_folder"]:
                why = (f"matching folder drifted {want_row['matching_folder']!r} -> "
                       f"{live['matching_folder']!r}")
            elif want_row["source_external_id"] and \
                    live["source_external_id"] != want_row["source_external_id"]:
                why = "source external id drifted"
            if why:
                report["failures"].append((did, why))
            else:
                report["validated"] += 1
        if report["failures"]:
            head = "; ".join(f"{d}:{w}" for d, w in report["failures"][:5])
            raise Abort(f"ABORT: {len(report['failures'])} of {len(ids)} rows no longer validate "
                        f"— {head}")
        out(f"  revalidated under lock: {report['validated']}/{len(ids)}")

        if not apply_changes:
            out("  DRY RUN — every gate passed; nothing written, no snapshot taken")
            trans.rollback()
            return report

        # 4. Snapshot BEFORE the first write.
        snap, snap_sha = write_snapshot(locked, rows, live_by_id, Path(snapshot_root))
        report["snapshot"], report["snapshot_sha256"] = str(snap), snap_sha
        out(f"  rollback snapshot: {snap}")
        out(f"  snapshot sha256:   {snap_sha}")

        # 5. Assign, through the canonical service, in this transaction.
        request_id = f"strict-safe-ownership-batch2:{b2.BATCH_ID}:{expect_sha[:12]}"
        for r in rows:
            result = resolve_document_ownership(
                r["document_id"], person_id=r["person_id"], actor_user_id=actor_user_id,
                request_id=request_id, conn=conn)
            if not result.get("assigned"):
                raise RuntimeError(f"document {r['document_id']} was not assigned: "
                                   f"{result.get('reason')}")
            report["applied"] += 1

        # 6. Post-write invariants, all inside the transaction.
        actual = dict(conn.execute(text(
            "select id, person_id from documents where id = any(:ids)"), {"ids": ids}).all())
        wrong = [did for did in ids if actual.get(did) != by_id[did]["person_id"]]
        if wrong:
            raise RuntimeError(
                f"{len(wrong)} documents do not carry their approved person_id: {wrong[:10]}")
        bad = conn.execute(text(
            "select count(*) from documents where id = any(:ids) and (household_id is not null "
            "or organization_id is not null or coalesce(review_status,'') <> 'not_required')"),
            {"ids": ids}).scalar()
        if bad:
            raise RuntimeError(f"{bad} targets gained a household/organization or lost not_required")

        fp_after = _fingerprints(conn, ids)
        for key in ("target", "non_target", "sources", "ocr"):
            if fp_after[key] != fp_before[key]:
                raise RuntimeError(f"{key} fingerprint changed — the batch touched more than "
                                   "documents.person_id")

        audit_rows = conn.execute(text(
            "select count(*) from audit_events where request_id = :r and action = :a"),
            {"r": request_id, "a": "document.ownership_resolved"}).scalar()
        report["audit_rows"] = audit_rows
        if audit_rows != len(rows):
            raise RuntimeError(f"{audit_rows} audit rows for {len(rows)} assignments")

        trans.commit()
        report["committed"] = True
        out(f"  COMMITTED {report['applied']} assignments, {report['audit_rows']} audit rows")
        out("  post-write checks: person_id equality, household/organization still NULL, "
            "review_status/tags/provenance/sources/OCR and non-target fingerprints all OK")
    except BaseException:
        if not report["committed"]:
            trans.rollback()
        raise
    finally:
        trans_conn.close()

    receipt = Path(report["snapshot"]).parent / "apply_receipt.json"
    receipt.write_text(json.dumps({
        "applied_at": datetime.now(UTC).isoformat(), "batch_id": "STRICT-SAFE-OWNERSHIP-2",
        "manifest_sha256": expect_sha, "plan_digest": expect_plan_digest,
        "snapshot": report["snapshot"], "snapshot_sha256": report["snapshot_sha256"],
        "rows_applied": report["applied"], "audit_rows": report["audit_rows"],
        "actor_user_id": actor_user_id, "request_id": request_id,
        "confirm_phrase": want, "committed": True,
    }, indent=2) + "\n", encoding="utf-8")
    out(f"  receipt: {receipt}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Apply the approved strict-safe ownership BATCH 2 manifest.")
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
