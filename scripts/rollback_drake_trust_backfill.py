"""Rollback a committed Drake link-trust backfill. ALL-OR-NOTHING.

Restores ``trust_level`` / ``confirmation_source`` / ``evidence_method`` / ``confirmed_by_user_id`` /
``confirmed_at`` on exactly the links named in a rollback snapshot, to the values recorded there —
which for a backfill is NULL across the board, since the apply only ever moves a row from "nothing
recorded" to "recorded". Nothing else is touched: ``confirmed``, ``match_method``, ``match_score``,
``person_id`` and ``source_contact_id`` are left exactly as they are.

DRIFT IS REFUSED, NOT OVERWRITTEN. If a link's current trust_level is not the value the apply wrote,
somebody has changed it since — a reviewer recording a real decision, most likely — and restoring it
to NULL would silently destroy that. Any such row aborts the whole run.

USAGE
    python scripts/rollback_drake_trust_backfill.py --snapshot <dir-or-csv> --dry-run
    python scripts/rollback_drake_trust_backfill.py --snapshot <dir-or-csv> --apply \\
        --actor-user-id <id> --confirm ROLLBACK-DRAKE-TRUST-<BATCH-ID>-<ROWS>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.security.audit import write_audit_event  # noqa: E402

SNAPSHOT_FILENAME = "rollback_snapshot_drake_link_trust.csv"


def confirm_phrase(batch_id: str, rows: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(batch_id)).strip("-").upper()
    return f"ROLLBACK-DRAKE-TRUST-{slug}-{rows}"


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_snapshot(snapshot):
    p = Path(snapshot)
    csv_path = p if p.is_file() else p / SNAPSHOT_FILENAME
    meta_path = csv_path.parent / "manifest.json"
    if not csv_path.is_file():
        raise SystemExit(f"ABORT: snapshot csv not found at {csv_path}")
    digest = sha256_of(csv_path)
    meta = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        recorded = meta.get("snapshot_sha256")
        if recorded and recorded != digest:
            raise SystemExit(f"ABORT: snapshot SHA256 {digest} != recorded {recorded} — the "
                             "snapshot has been modified since the apply")
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("ABORT: snapshot is empty")
    return rows, csv_path, digest, meta


_RESTORE_SQL = text("""
    UPDATE person_source_links
       SET trust_level          = :prev_trust_level,
           confirmation_source  = :prev_confirmation_source,
           evidence_method      = :prev_evidence_method,
           confirmed_by_user_id = :prev_confirmed_by_user_id,
           confirmed_at         = :prev_confirmed_at
     WHERE id = :link_id
       AND trust_level IS NOT DISTINCT FROM :expected_now
""")


def run(snapshot, *, apply_changes=False, confirm=None, actor_user_id=None, emit=print):
    rows, csv_path, digest, meta = load_snapshot(snapshot)
    batch_id = meta.get("batch_id", "unknown")
    want = confirm_phrase(batch_id, len(rows))
    if apply_changes and confirm != want:
        raise SystemExit(f"ABORT: --apply requires --confirm {want}")
    if apply_changes and actor_user_id is None:
        raise SystemExit("ABORT: --apply requires --actor-user-id")

    emit(f"snapshot: {csv_path}")
    emit(f"rows:     {len(rows)}")

    report = {"restored": 0, "drift": [], "committed": False,
              "snapshot_sha256": digest, "batch_id": batch_id}

    with engine.begin() as conn:
        link_ids = [int(r["person_source_link_id"]) for r in rows]
        live = {r["id"]: r["trust_level"] for r in conn.execute(text(
            "SELECT id, trust_level FROM person_source_links WHERE id = ANY(:ids)"),
            {"ids": link_ids}).mappings()}

        for r in rows:
            link_id = int(r["person_source_link_id"])
            if link_id not in live:
                report["drift"].append({"person_source_link_id": link_id, "problem": "link is gone"})
            elif live[link_id] != r["new_trust_level"]:
                report["drift"].append({
                    "person_source_link_id": link_id,
                    "problem": f"trust_level is now {live[link_id]!r}, apply wrote "
                               f"{r['new_trust_level']!r} — refusing to discard a later decision"})

        if report["drift"]:
            for d in report["drift"][:20]:
                emit(f"    DRIFT {d['person_source_link_id']}: {d['problem']}")
            raise SystemExit(f"ABORT: {len(report['drift'])} row(s) drifted since the apply; "
                             "nothing was restored.")

        for r in rows:
            restored = conn.execute(_RESTORE_SQL, {
                "link_id": int(r["person_source_link_id"]),
                "expected_now": r["new_trust_level"],
                "prev_trust_level": r["prev_trust_level"] or None,
                "prev_confirmation_source": r["prev_confirmation_source"] or None,
                "prev_evidence_method": r["prev_evidence_method"] or None,
                "prev_confirmed_by_user_id": int(r["prev_confirmed_by_user_id"])
                if r["prev_confirmed_by_user_id"] else None,
                "prev_confirmed_at": r["prev_confirmed_at"] or None,
            })
            report["restored"] += restored.rowcount

        if not apply_changes:
            raise SystemExit("ABORT: dry run complete; nothing committed "
                             "(re-run with --apply to commit)")

        write_audit_event(
            action="drake.link_trust_backfill_rolled_back", entity_type="person_source_links",
            entity_id=None, actor_user_id=actor_user_id,
            request_id=f"drake-trust-rollback:{batch_id}:{digest[:12]}",
            metadata={"batch_id": batch_id, "snapshot_sha256": digest,
                      "restored": report["restored"], "rows": len(rows),
                      "scope": "drake_link_trust_backfill_rollback",
                      "rolled_back_at": datetime.now(UTC).isoformat()},
            conn=conn)
        report["committed"] = True

    emit(f"restored: {report['restored']}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python scripts/rollback_drake_trust_backfill.py")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--actor-user-id", type=int, default=None)
    args = ap.parse_args(argv)
    if args.dry_run == args.apply:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    run(args.snapshot, apply_changes=args.apply, confirm=args.confirm,
        actor_user_id=args.actor_user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
