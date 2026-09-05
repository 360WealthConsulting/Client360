"""Fail-closed backfill of RECORDED trust onto legacy Drake person_source_links. ALL-OR-NOTHING.

WHAT THIS WRITES, AND NOTHING ELSE
    person_source_links.trust_level / confirmation_source / evidence_method
                        / confirmed_by_user_id / confirmed_at   (NULL trust_level -> value)
    audit_events                                                (one 'drake.link_trust_backfilled')

``confirmed``, ``match_method``, ``match_score``, ``person_id`` and ``source_contact_id`` are NEVER
touched. No person, contact, household, document or Drake return is modified. This adds a recorded
provenance to a link; it does not change who is linked to whom.

WHY PREVIEW AND APPLY ARE SEPARATE COMMANDS, WITH THE PLAN PASSED BACK IN
    Deriving the plan from the database and then checking the database against it proves nothing — it
    always agrees. ``--preview`` writes a plan file and its digest; a human reads it and approves it;
    ``--apply`` then requires that digest, the row count and the per-level census to be supplied FROM
    OUTSIDE and to match. There are no defaults, and omitting any of them refuses the run. This is
    the same discipline as scripts/apply_owner_manifest.py, for the same reason.

WHY APPLY RE-DERIVES INSTEAD OF TRUSTING THE PLAN
    A plan is a snapshot of evidence, and evidence moves: a person can be merged away, a Drake return
    can be re-imported, a reviewer can approve something. Apply rebuilds the classification from the
    live database inside the transaction and compares it to the approved plan. ANY drift — a row that
    is no longer eligible, or eligible at a different level — aborts the whole run. A stale plan can
    never write.

WHICH MODES TOUCH ANYTHING
    ``--preview``   pure read. SELECTs only; writes the plan + digest to ``--out`` and nothing else.
    ``--dry-run``   pure read against the database. Runs every authorization and drift check needed
                    to prove an apply would be accepted, then RETURNS before the first side effect:
                    no UPDATE, no row locks taken by an attempted UPDATE, no rollback snapshot, no
                    audit event. Exits 0 when the plan is good.
    ``--apply``     the only mode that writes anything.

    Use ``--preview`` or ``--dry-run`` against production; neither modifies it. An earlier revision
    made ``--dry-run`` prove itself by running the UPDATEs and relying on the transaction rolling
    back — nothing persisted, but statements executed, locks were taken, and a rollback snapshot was
    left on disk for an apply that never happened. Rollback is a safety net, not a substitute for not
    writing.

WHAT IT WILL ACTUALLY DO TODAY
    Measured against production: 533 rows eligible (all ``identifier_verified``), 3,074 refused.
    ZERO ``human_approved``, because ``drake_identity_match_candidates`` holds 435 rows that are all
    still ``pending`` with no reviewer and no timestamp. That is not a defect in this script.

USAGE
    python scripts/apply_drake_trust_backfill.py --preview --out <dir>

    python scripts/apply_drake_trust_backfill.py --apply --plan <path> \\
        --expect-sha256 <hex> --expect-rows <n> \\
        --expect-identifier-verified <n> --expect-human-approved <n> \\
        --batch-id <slug> --actor-user-id <id> --confirm APPLY-DRAKE-TRUST-<BATCH-ID>-<ROWS>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, NOT the repository root, so `app` is not
# importable and the documented command dies at import time. Same bootstrap scripts/demo.py uses.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.db import engine  # noqa: E402
from app.security.audit import write_audit_event  # noqa: E402
from app.services.drake_trust_backfill import (  # noqa: E402
    apply_planned_row,
    build_plan,
    current_state,
)
from app.services.link_trust import HUMAN_APPROVED, IDENTIFIER_VERIFIED  # noqa: E402

SNAPSHOT_ROOT = Path(r"D:\Client360\Backups\DR")

PLAN_FILENAME = "drake_trust_backfill_plan.csv"
SNAPSHOT_FILENAME = "rollback_snapshot_drake_link_trust.csv"

PLAN_FIELDS = ["person_source_link_id", "person_id", "source_contact_id", "match_method",
               "confirmed", "trust_level", "confirmation_source", "evidence_method",
               "confirmed_by_user_id", "confirmed_at", "reason"]


def confirm_phrase(batch_id: str, rows: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(batch_id)).strip("-").upper()
    return f"APPLY-DRAKE-TRUST-{slug}-{rows}"


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_plan(plan, out_dir) -> tuple[Path, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / PLAN_FILENAME
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
        w.writeheader()
        for entry in sorted(plan["planned"], key=lambda e: e["person_source_link_id"]):
            row = {k: entry.get(k) for k in PLAN_FIELDS}
            row["confirmed_at"] = entry["confirmed_at"].isoformat() if entry["confirmed_at"] else ""
            row["confirmed_by_user_id"] = entry["confirmed_by_user_id"] or ""
            w.writerow(row)

    refusals = out / "drake_trust_backfill_refusals.csv"
    with refusals.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["person_source_link_id", "person_id",
                                           "source_contact_id", "match_method", "confirmed",
                                           "reason"])
        w.writeheader()
        w.writerows(sorted(plan["refused"], key=lambda e: e["person_source_link_id"]))

    digest = sha256_of(path)
    meta = {
        "created_at": datetime.now(UTC).isoformat(),
        "plan_sha256": digest,
        "planned_rows": plan["planned_rows"],
        "refused_rows": plan["refused_rows"],
        "census": plan["census"],
        "refusal_census": plan["refusal_census"],
    }
    (out / "manifest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return path, digest


def _load_plan(path, *, expect_sha, expect_rows, expect_census):
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"ABORT: plan not found at {p}")
    digest = sha256_of(p)
    if digest != expect_sha:
        raise SystemExit(f"ABORT: plan SHA256 {digest} != --expect-sha256 {expect_sha} — the plan "
                         "has changed since it was approved")
    with p.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if len(rows) != expect_rows:
        raise SystemExit(f"ABORT: plan holds {len(rows)} rows, --expect-rows says {expect_rows}")
    census = Counter(r["trust_level"] for r in rows)
    for level, want in expect_census.items():
        if census.get(level, 0) != want:
            raise SystemExit(f"ABORT: plan holds {census.get(level, 0)} {level} rows, "
                             f"expectation says {want}")
    return rows, digest


def _write_snapshot(before, rows, digest, batch_id, root) -> tuple[Path, str]:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(batch_id)).strip("-").lower()
    out = Path(root) / f"drake-trust-backfill-{slug}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / SNAPSHOT_FILENAME
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["person_source_link_id", "new_trust_level",
                    "prev_trust_level", "prev_confirmation_source", "prev_evidence_method",
                    "prev_confirmed_by_user_id", "prev_confirmed_at"])
        for r in sorted(rows, key=lambda x: int(x["person_source_link_id"])):
            link_id = int(r["person_source_link_id"])
            prev = before.get(link_id, {})
            w.writerow([link_id, r["trust_level"],
                        prev.get("trust_level") or "",
                        prev.get("confirmation_source") or "",
                        prev.get("evidence_method") or "",
                        prev.get("confirmed_by_user_id") if prev.get("confirmed_by_user_id")
                        is not None else "",
                        prev.get("confirmed_at").isoformat() if prev.get("confirmed_at") else ""])
    meta = {"created_at": datetime.now(UTC).isoformat(), "batch_id": batch_id,
            "plan_sha256": digest, "rows": len(rows), "snapshot_sha256": sha256_of(path)}
    (out / "manifest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return path, meta["snapshot_sha256"]


def _resolve_and_check_drift(conn, rows):
    """Re-derive the plan from ``conn`` and compare it to the approved rows.

    Returns ``(live_by_id, drift)``. Pure read — it runs the classifier and compares, and is used by
    BOTH the read-only validation pass and the write transaction so the two cannot diverge in what
    they consider drift.
    """
    live = build_plan(conn)
    live_by_id = {e["person_source_link_id"]: e for e in live["planned"]}

    drift = []
    for r in rows:
        link_id = int(r["person_source_link_id"])
        current = live_by_id.get(link_id)
        if current is None:
            drift.append({"person_source_link_id": link_id, "problem": "no longer eligible"})
        elif current["trust_level"] != r["trust_level"]:
            drift.append({"person_source_link_id": link_id,
                          "problem": f"evidence now supports {current['trust_level']}, "
                                     f"plan says {r['trust_level']}"})
    return live_by_id, drift


def run(*, preview=False, out_dir=None, plan_path=None, expect_sha=None, expect_rows=None,
        expect_census=None, apply_changes=False, confirm=None, batch_id=None, actor_user_id=None,
        snapshot_root=SNAPSHOT_ROOT, emit=print):
    if preview:
        with engine.connect() as conn:
            plan = build_plan(conn)
        path, digest = _write_plan(plan, out_dir)
        emit(f"plan:           {path}")
        emit(f"plan sha256:    {digest}")
        emit(f"planned rows:   {plan['planned_rows']}")
        emit(f"census:         {plan['census']}")
        emit(f"refused rows:   {plan['refused_rows']}")
        for reason, n in sorted(plan["refusal_census"].items(), key=lambda kv: -kv[1]):
            emit(f"    {reason:<42} {n:>6}")
        return {"plan_path": str(path), "plan_sha256": digest, **plan}

    rows, digest = _load_plan(plan_path, expect_sha=expect_sha, expect_rows=expect_rows,
                              expect_census=expect_census)
    want = confirm_phrase(batch_id, len(rows))
    if confirm != want:
        raise SystemExit(f"ABORT: --apply requires --confirm {want}")
    if actor_user_id is None:
        raise SystemExit("ABORT: --apply requires --actor-user-id; recording trust is a decision "
                         "and must name the operator")

    report = {"written": 0, "skipped": 0, "drift": [], "committed": False,
              "plan_sha256": digest, "batch_id": batch_id}

    # PASS 1 — VALIDATION, on a read-only connection.
    #
    # A dry run has to prove the apply WOULD be accepted, and it must do so without a single side
    # effect. An earlier revision proved it by running the UPDATEs and relying on the transaction
    # rolling back: nothing persisted, but the statements executed, took row locks, and the run also
    # left a rollback snapshot on disk for an apply that never happened. Rollback is a safety net,
    # not a substitute for not writing. Dry run now RETURNS from here, before the first side effect.
    with engine.connect() as conn:
        live_by_id, drift = _resolve_and_check_drift(conn, rows)
    report["drift"] = drift
    if drift:
        for d in drift[:20]:
            emit(f"    DRIFT {d['person_source_link_id']}: {d['problem']}")
        raise SystemExit(f"ABORT: {len(drift)} planned row(s) no longer match the live evidence; "
                         "nothing was written. Re-run --preview.")

    if not apply_changes:
        report["would_write"] = len(rows)
        emit(f"DRY RUN: {len(rows)} row(s) would be written; the plan matches the live evidence.")
        emit("DRY RUN: nothing was written — no UPDATE, no snapshot, no audit event.")
        emit("Re-run with --apply to commit.")
        return report

    # PASS 2 — THE WRITE. Drift is re-checked INSIDE the write transaction, because pass 1 ran on a
    # different connection and the evidence could have moved between the two.
    with engine.begin() as conn:
        live_by_id, drift = _resolve_and_check_drift(conn, rows)
        report["drift"] = drift
        if drift:
            for d in drift[:20]:
                emit(f"    DRIFT {d['person_source_link_id']}: {d['problem']}")
            raise SystemExit(f"ABORT: {len(drift)} planned row(s) drifted between validation and "
                             "apply; nothing was written. Re-run --preview.")

        link_ids = [int(r["person_source_link_id"]) for r in rows]
        before = current_state(conn, link_ids)
        snapshot_path, snapshot_digest = _write_snapshot(before, rows, digest, batch_id,
                                                         snapshot_root)

        for r in rows:
            entry = live_by_id[int(r["person_source_link_id"])]
            if apply_planned_row(conn, entry):
                report["written"] += 1
            else:
                report["skipped"] += 1

        write_audit_event(
            action="drake.link_trust_backfilled", entity_type="person_source_links",
            entity_id=None, actor_user_id=actor_user_id,
            request_id=f"drake-trust-backfill:{batch_id}:{digest[:12]}",
            metadata={"batch_id": batch_id, "plan_sha256": digest,
                      "snapshot_sha256": snapshot_digest, "rows": len(rows),
                      "written": report["written"], "skipped": report["skipped"],
                      "scope": "drake_link_trust_backfill"},
            conn=conn)
        report["committed"] = True

    report["snapshot"] = str(snapshot_path)
    report["snapshot_sha256"] = snapshot_digest
    emit(f"written:  {report['written']}")
    emit(f"skipped:  {report['skipped']}")
    emit(f"snapshot: {snapshot_path}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python scripts/apply_drake_trust_backfill.py")
    ap.add_argument("--preview", action="store_true", default=False)
    ap.add_argument("--out", default=None)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--expect-sha256", default=None)
    ap.add_argument("--expect-rows", type=int, default=None)
    ap.add_argument("--expect-identifier-verified", type=int, default=None)
    ap.add_argument("--expect-human-approved", type=int, default=None)
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    args = ap.parse_args(argv)

    if args.preview:
        if not args.out:
            raise SystemExit("ABORT: --preview requires --out")
        run(preview=True, out_dir=args.out)
        return 0

    if args.dry_run == args.apply:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    for flag, value in (("--plan", args.plan), ("--expect-sha256", args.expect_sha256),
                        ("--expect-rows", args.expect_rows), ("--batch-id", args.batch_id),
                        ("--expect-identifier-verified", args.expect_identifier_verified),
                        ("--expect-human-approved", args.expect_human_approved)):
        if value is None:
            raise SystemExit(f"ABORT: {flag} is required; it is what a human approved")

    run(plan_path=args.plan, expect_sha=args.expect_sha256, expect_rows=args.expect_rows,
        expect_census={IDENTIFIER_VERIFIED: args.expect_identifier_verified,
                       HUMAN_APPROVED: args.expect_human_approved},
        apply_changes=args.apply, confirm=args.confirm, batch_id=args.batch_id,
        actor_user_id=args.actor_user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
