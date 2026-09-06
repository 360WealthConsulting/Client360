"""Fail-closed apply for an APPROVED Drake carry-over ownership manifest. ALL-OR-NOTHING.

WHAT THIS WRITES, AND NOTHING ELSE
    documents.person_id / household_id / organization_id   (exactly one per row, NULL -> value)
    audit_events                                           (one 'document.ownership_resolved' per row)

No proposal, classification, naming, tax year, category, OCR, source ref or contact_type is touched,
and no file is moved.

WHY THE OWNERSHIP UPDATE IS NOT WRITTEN HERE
    ``households.resolve_document_ownership(..., conn=...)`` is the canonical single-document write
    path and already owns the rules: the atomic ``WHERE all-NULL AND NOT permanent-reject`` recheck
    inside the UPDATE, and the audit event. Passing ``conn`` enlists it in THIS script's transaction
    so nothing commits per row — a batch that half-applies is a batch nobody reviewed. A batch script
    must never restate ownership semantics; it may only decide WHICH documents to hand it.

WHY THE EXPECTATIONS ARE REQUIRED ARGUMENTS
    The digest, the row count and the per-type census are what a human APPROVED. Deriving them from
    the manifest and then checking the manifest against them always agrees and proves nothing, so
    they are supplied from outside the file and the file must match. There are no defaults.

RE-VALIDATION AT APPLY TIME
    A manifest is a snapshot of evidence, and evidence drifts. Under the row lock every rule is
    re-run from scratch via ``drake_sibling_ownership.evaluate`` — client id, source availability,
    sibling agreement, contradiction state, owner eligibility, all-NULL ownership — and the recorded
    owner and evidence digest must still match. ANY failing row aborts the whole run. There is no
    partial apply and no "skip the bad ones".

USAGE
    python scripts/apply_drake_sibling_ownership.py --manifest <path> --batch-id <slug> \\
        --expect-sha256 <hex> --expect-rows <n> --expect-person <n> --expect-household <n> \\
        --expect-organization <n> --dry-run

    ... --apply --confirm APPLY-<BATCH-ID>-<ROWS> --actor-user-id <id>
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.db import documents, engine  # noqa: E402
from app.services.document_owner_proposal import build_match_indexes  # noqa: E402
from app.services.drake_sibling_ownership import evaluate  # noqa: E402
from app.services.households import resolve_document_ownership  # noqa: E402

OWNER_TYPES = ("person", "household", "organization")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def confirm_phrase(batch_id, rows):
    return f"APPLY-{batch_id}-{rows}"


def load_manifest(path, *, expect_sha, expect_rows, expect_census):
    if not expect_sha:
        raise SystemExit("ABORT: --expect-sha256 is required")
    if expect_rows is None:
        raise SystemExit("ABORT: --expect-rows is required")
    for k, v in expect_census.items():
        if v is None:
            raise SystemExit(f"ABORT: --expect-{k} is required")
    if sum(expect_census.values()) != expect_rows:
        raise SystemExit(f"ABORT: census {expect_census} sums to {sum(expect_census.values())}, "
                         f"not --expect-rows {expect_rows}")
    digest = sha256_of(path)
    if digest != expect_sha:
        raise SystemExit(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")
    with open(path, newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    if len(raw) != expect_rows:
        raise SystemExit(f"ABORT: manifest has {len(raw)} rows, approved {expect_rows}")
    rows = []
    for r in raw:
        if (r.get("applied") or "").strip().upper() != "NO":
            raise SystemExit("ABORT: a manifest row is not applied=NO")
        if r["owner_type"] not in OWNER_TYPES:
            raise SystemExit(f"ABORT: unknown owner_type {r['owner_type']!r}")
        rows.append({"document_id": int(r["document_id"]), "owner_type": r["owner_type"],
                     "owner_id": int(r["owner_id"]), "client_id": r["drake_client_id"],
                     "evidence_digest": r["evidence_digest"]})
    census = Counter(r["owner_type"] for r in rows)
    if {k: census.get(k, 0) for k in expect_census} != expect_census:
        raise SystemExit(f"ABORT: census {dict(census)} != approved {expect_census}")
    dupes = [d for d, n in Counter(r["document_id"] for r in rows).items() if n > 1]
    if dupes:
        raise SystemExit(f"ABORT: duplicate document_id rows: {sorted(dupes)[:10]}")
    return rows, digest


def snapshot(rows, out_dir, conn):
    """Prior ownership for every target, written BEFORE the first write. Rollback's only input."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "rollback_snapshot_drake_sibling_ownership.csv")
    ids = [r["document_id"] for r in rows]
    prior = {r["id"]: r for r in conn.execute(
        select(documents.c.id, documents.c.original_name, documents.c.person_id,
               documents.c.household_id, documents.c.organization_id)
        .where(documents.c.id.in_(ids))).mappings()}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["document_id", "original_name", "prior_person_id", "prior_household_id",
                    "prior_organization_id", "drake_client_id", "destination_owner_type",
                    "destination_owner_id", "evidence_digest"])
        for r in rows:
            p = prior[r["document_id"]]
            w.writerow([r["document_id"], p["original_name"], p["person_id"] or "",
                        p["household_id"] or "", p["organization_id"] or "", r["client_id"],
                        r["owner_type"], r["owner_id"], r["evidence_digest"]])
    return path


def run(manifest_path, *, batch_id, expect_sha, expect_rows, expect_census, apply_changes=False,
        confirm=None, actor_user_id=None, out_dir=None, log=print):
    rows, digest = load_manifest(manifest_path, expect_sha=expect_sha, expect_rows=expect_rows,
                                 expect_census=expect_census)
    want = confirm_phrase(batch_id, len(rows))
    if apply_changes and confirm != want:
        raise SystemExit(f"ABORT: --apply requires --confirm {want}")

    out_dir = out_dir or os.path.join("var", "drake_sibling_ownership",
                                      f"{batch_id}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}")
    report = {"batch_id": batch_id, "manifest_sha256": digest, "rows": len(rows), "applied": 0,
              "audit_rows": 0, "failures": [], "confirm_phrase": want, "committed": False}

    with engine.begin() as conn:
        trans = conn.begin_nested()
        idx = build_match_indexes(conn)
        ids = [r["document_id"] for r in rows]
        conn.execute(select(documents.c.id).where(documents.c.id.in_(ids)).with_for_update()).all()
        log(f"  locked {len(ids)} rows")

        for r in rows:
            v = evaluate(conn, r["document_id"], idx, exclude_firm=True)
            if not v["eligible"]:
                report["failures"].append({"document_id": r["document_id"], "why": v["reasons"]})
                continue
            if list(v["candidate"]) != [r["owner_type"], r["owner_id"]]:
                report["failures"].append({"document_id": r["document_id"],
                                           "why": [f"owner drift: now {v['candidate']}"]})
                continue
            if v["client_id"] != r["client_id"]:
                report["failures"].append({"document_id": r["document_id"],
                                           "why": [f"client id drift: now {v['client_id']}"]})
        if report["failures"]:
            trans.rollback()
            log(f"  ABORT: {len(report['failures'])} row(s) failed re-validation; nothing written")
            for f in report["failures"][:20]:
                log(f"    doc={f['document_id']} {f['why']}")
            return report

        snap = snapshot(rows, out_dir, conn)
        log(f"  rollback snapshot: {snap}")

        for r in rows:
            kwargs = {f"{r['owner_type']}_id": r["owner_id"]}
            res = resolve_document_ownership(r["document_id"], actor_user_id=actor_user_id,
                                             request_id=f"{batch_id}:{digest[:12]}",
                                             conn=conn, **kwargs)
            if not res.get("assigned"):
                report["failures"].append({"document_id": r["document_id"],
                                           "why": [f"write refused: {res.get('reason')}"]})
                break
            report["applied"] += 1
            report["audit_rows"] += 1

        if report["failures"]:
            trans.rollback()
            report["applied"] = report["audit_rows"] = 0
            log("  ABORT during write; entire batch rolled back")
            return report
        if not apply_changes:
            trans.rollback()
            log(f"  DRY RUN — would assign {report['rows']} document(s) and write "
                f"{report['audit_rows']} audit rows. Nothing committed.")
            report["audit_rows"] = 0
            return report
        trans.commit()
        report["committed"] = True

    receipt = {**report, "applied_at": dt.datetime.now(dt.UTC).isoformat(), "snapshot": snap,
               "manifest": manifest_path, "actor_user_id": actor_user_id}
    with open(os.path.join(out_dir, "apply_receipt.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, default=str)
    log(f"  COMMITTED {report['applied']} row(s), {report['audit_rows']} audit rows")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="apply_drake_sibling_ownership")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--expect-sha256", required=True)
    ap.add_argument("--expect-rows", required=True, type=int)
    ap.add_argument("--expect-person", required=True, type=int)
    ap.add_argument("--expect-household", required=True, type=int)
    ap.add_argument("--expect-organization", required=True, type=int)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    a = ap.parse_args(argv)
    if a.apply and a.dry_run:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    r = run(a.manifest, batch_id=a.batch_id, expect_sha=a.expect_sha256, expect_rows=a.expect_rows,
            expect_census={"person": a.expect_person, "household": a.expect_household,
                           "organization": a.expect_organization},
            apply_changes=a.apply, confirm=a.confirm, actor_user_id=a.actor_user_id, out_dir=a.out)
    print(json.dumps(r, indent=2, default=str))
    return 1 if r["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
