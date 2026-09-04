"""Generic rollback for a committed owner-manifest apply. ALL-OR-NOTHING.

Restores exactly the ownership fields the apply changed, for exactly the documents in that apply's
rollback snapshot, to the values recorded there. Nothing else is touched: status, deleted_at,
storage, proposals, source availability and OCR are never written, and no file is moved.

DRIFT IS FATAL, NOT SOMETHING TO WORK AROUND
    Every document must currently hold EXACTLY the owner the apply assigned. If one has since been
    re-owned, cleared, deleted or reassigned by a person, this aborts the whole run rather than
    overwrite that decision.

USAGE
    python scripts/rollback_owner_manifest.py --snapshot <dir-or-csv> --dry-run
    python scripts/rollback_owner_manifest.py --snapshot <dir-or-csv> --apply \\
        --confirm ROLLBACK-<BATCH-ID>-<ROWS>
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

# Running this file directly puts scripts/ on sys.path, NOT the repository root, so `app` is not
# importable and the documented command dies at import time. Same bootstrap scripts/demo.py uses.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import and_, text  # noqa: E402

from app.db import documents, engine  # noqa: E402
from app.security.audit import write_audit_event  # noqa: E402

_OWNER_COLUMN = {"person": "person_id", "household": "household_id",
                 "organization": "organization_id"}


def confirm_phrase(batch_id: str, rows: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(batch_id or "unknown")).strip("-").upper()
    return f"ROLLBACK-{slug}-{rows}"


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_snapshot(snapshot):
    """Resolve the snapshot csv and verify it still matches the digest recorded at apply time."""
    p = Path(snapshot)
    csv_path = p if p.is_file() else p / "rollback_snapshot_owner_assignments.csv"
    meta_path = csv_path.parent / "manifest.json"
    if not csv_path.exists():
        raise SystemExit(f"ABORT: snapshot csv not found at {csv_path}")
    digest = sha256_of(csv_path)
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        recorded = meta.get("snapshot_sha256")
        if recorded and recorded != digest:
            raise SystemExit(f"ABORT: snapshot SHA256 {digest} != recorded {recorded} — the "
                             "snapshot has been modified since the apply")
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("ABORT: snapshot is empty")
    return rows, csv_path, digest, meta


def run(snapshot, *, apply_changes=False, confirm=None, actor_user_id=None, out=print):
    rows, csv_path, digest, meta = load_snapshot(snapshot)
    batch_id = meta.get("batch_id") or "unknown"
    want = confirm_phrase(batch_id, len(rows))
    if apply_changes and confirm != want:
        raise SystemExit(f"ABORT: --apply requires --confirm {want}")

    out(f"snapshot: {csv_path}")
    out(f"  batch={batch_id}  sha256={digest}  rows={len(rows)}")
    ids = sorted(int(r["document_id"]) for r in rows)
    report = {"restored": 0, "drift": [], "committed": False, "snapshot_sha256": digest,
              "batch_id": batch_id, "confirm_phrase": want}

    conn = engine.connect()
    trans = conn.begin()
    try:
        cur = {r["id"]: dict(r) for r in conn.execute(text("""
            select id, status, deleted_at, person_id, household_id, organization_id
              from documents where id = any(:i) order by id for update"""),
            {"i": ids}).mappings()}
        for r in rows:
            did = int(r["document_id"])
            d = cur.get(did)
            col = _OWNER_COLUMN[r["owner_type"]]
            expected = int(r["owner_id"])
            if d is None:
                report["drift"].append((did, "document no longer exists"))
                continue
            if d[col] != expected:
                report["drift"].append((did, f"{col} is {d[col]!r}, expected {expected}"))
            others = [c for c in ("person_id", "household_id", "organization_id")
                      if c != col and d[c] is not None]
            if others:
                report["drift"].append((did, f"unexpected owner columns set: {others}"))
        if report["drift"]:
            for did, why in report["drift"][:20]:
                out(f"    DRIFT #{did}: {why}")
            raise RuntimeError(f"{len(report['drift'])} document(s) drifted since the apply — "
                               "refusing to overwrite a later decision")

        for r in rows:
            did = int(r["document_id"])
            col = _OWNER_COLUMN[r["owner_type"]]
            prev = r.get(f"prev_{col}") or None
            value = int(prev) if prev not in (None, "") else None
            n = conn.execute(documents.update().where(and_(
                documents.c.id == did, documents.c[col] == int(r["owner_id"]),
            )).values(**{col: value})).rowcount
            if n != 1:
                raise RuntimeError(f"document {did}: restore matched {n} rows")
            report["restored"] += n
            write_audit_event(
                action="document.ownership_rolled_back", entity_type="document", entity_id=did,
                actor_user_id=actor_user_id,
                request_id=f"owner-manifest-rollback:{batch_id}:{digest[:12]}",
                metadata={"document_id": did, "restored_column": col, "restored_value": value,
                          "reverted_owner_id": int(r["owner_id"]), "batch_id": batch_id,
                          "snapshot_sha256": digest, "scope": "owner_manifest_rollback"},
                conn=conn)
        if report["restored"] != len(rows):
            raise RuntimeError(f"restored {report['restored']} != {len(rows)}")

        if apply_changes:
            trans.commit()
            report["committed"] = True
            out(f"  COMMITTED rollback of {report['restored']} documents")
        else:
            trans.rollback()
            out(f"  DRY RUN: rolled back. Would restore {report['restored']} documents.")
    except Exception:
        if not report["committed"]:
            trans.rollback()
            out("  ROLLED BACK — no change persisted")
        raise
    finally:
        conn.close()

    if report["committed"]:
        receipt = csv_path.parent / "rollback_receipt.json"
        receipt.write_text(json.dumps({
            "rolled_back_at": datetime.now(UTC).isoformat(), "batch_id": batch_id,
            "snapshot": str(csv_path), "snapshot_sha256": digest,
            "documents_restored": report["restored"], "actor_user_id": actor_user_id},
            indent=1), encoding="utf-8")
        out(f"  receipt: {receipt}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    report = run(args.snapshot, apply_changes=args.apply, confirm=args.confirm,
                 actor_user_id=args.actor_user_id)
    return 0 if (report["committed"] or not args.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())
