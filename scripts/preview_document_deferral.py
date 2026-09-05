"""Deferred-ownership backfill — PREVIEW by default, apply only when asked twice.

    python scripts/preview_document_deferral.py                     # preview, writes nothing
    python scripts/preview_document_deferral.py --out-dir DIR       # preview + manifest + SHA256
    python scripts/preview_document_deferral.py --apply --manifest M --confirm   # writes

PREVIEW IS THE DEFAULT AND WRITES NOTHING. It opens a read-only transaction, enumerates the
documents this implementation may defer, and emits a deterministic manifest plus its SHA-256. The
manifest is the unit of review: apply refuses unless the manifest still hashes to the value recorded
in its own header AND every row in it is still eligible, so a plan reviewed on Monday cannot be
applied on Friday against a corpus that has moved underneath it.

ELIGIBILITY IS NOT RESTATED HERE. Every row is checked through
``document_deferral.eligibility`` — the same function the write uses — so this tool cannot drift
into a second, more generous definition of what may be parked. In particular ``UNSUPPORTED`` is not
eligible in this implementation and this script has no flag to make it so.

Apply is deliberately awkward: it needs ``--apply``, a ``--manifest`` whose digest still matches, and
``--confirm``. It is included so the lane is complete and reviewable, not because it should be run
now.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST_COLUMNS = ("document_id", "reason", "route", "original_name", "source_system")


def _engine(read_only: bool):
    """The app engine, forced read-only at the libpq level for preview.

    Set before ``app.db`` is imported so the option applies to the connection it opens, not just to
    ours — a preview must not be able to write through any path in the process.
    """
    if read_only:
        os.environ["PGOPTIONS"] = "-c default_transaction_read_only=on"
    from app.db import engine
    return engine


def _candidate_sql() -> text:
    """Unowned, live, non-reject documents whose CURRENT proposal route is deferrable.

    The route filter is expressed as a join to the current ``owner_proposal`` fact plus a NULL case
    for documents that have none, so the two eligible classes (``NO_MATCH`` and "no proposal") are
    one query. The authoritative check still runs per row afterwards.
    """
    return text("""
        SELECT d.id,
               d.original_name,
               d.tags ->> 'source_system' AS source_system,
               f.fact_value::jsonb ->> 'route' AS route
          FROM documents d
          LEFT JOIN document_facts f
                 ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
         WHERE d.person_id IS NULL AND d.household_id IS NULL AND d.organization_id IS NULL
           AND d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
           AND d.review_status IS DISTINCT FROM :deferred
           AND NOT (d.id = ANY(:rejects))
           AND (f.fact_value IS NULL OR f.fact_value::jsonb ->> 'route' = ANY(:routes))
         ORDER BY d.id
    """)


def collect(conn, *, limit=None) -> tuple[list[dict], Counter]:
    """Rows this run would defer, each verified through the service's own eligibility check."""
    from app.services import document_deferral as dd

    routes = sorted(r for r in dd.ROUTE_REASON if r is not None)
    rows = conn.execute(_candidate_sql(), {
        "deferred": dd.DEFERRED_REVIEW_STATUS,
        "rejects": sorted(dd.PERMANENT_REJECT_DOCUMENT_IDS),
        "routes": routes,
    }).mappings().all()

    plan, skipped = [], Counter()
    for row in rows:
        check = dd.eligibility(conn, row["id"])
        if not check["eligible"]:
            skipped[check["reason_code"]] += 1
            continue
        plan.append({
            "document_id": int(row["id"]),
            "reason": check["reason"],
            "route": row["route"] or "",
            "original_name": row["original_name"] or "",
            "source_system": row["source_system"] or "",
        })
        if limit and len(plan) >= limit:
            break
    return plan, skipped


def _digest(plan: list[dict]) -> str:
    """Content digest over the PLAN, not the file — stable across formatting and ordering noise."""
    payload = json.dumps(
        [[r["document_id"], r["reason"], r["route"]] for r in sorted(plan, key=lambda r: r["document_id"])],
        separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_manifest(plan: list[dict], out_dir: Path) -> tuple[Path, Path, str]:
    from app.services import document_deferral as dd

    out_dir.mkdir(parents=True, exist_ok=True)
    digest = _digest(plan)
    csv_path = out_dir / "deferral_manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(plan, key=lambda r: r["document_id"]))
    header = {
        "generated_at": datetime.now(UTC).isoformat(),
        "rows": len(plan),
        "plan_digest_sha256": digest,
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "eligible_routes": sorted(str(r) for r in dd.ROUTE_REASON),
        "reasons": dict(Counter(r["reason"] for r in plan)),
    }
    json_path = out_dir / "deferral_manifest.json"
    json_path.write_text(json.dumps(header, indent=2) + "\n", encoding="utf-8")
    return csv_path, json_path, digest


def preview(args) -> int:
    engine = _engine(read_only=True)
    with engine.connect() as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on", \
            "refusing to preview on a writable session"
        plan, skipped = collect(conn, limit=args.limit)

    reasons = Counter(r["reason"] for r in plan)
    print("=" * 68)
    print("DEFERRED-OWNERSHIP BACKFILL — PREVIEW (nothing written)")
    print("=" * 68)
    print(f"  eligible documents          {len(plan)}")
    for reason, n in reasons.most_common():
        print(f"      {reason:<24} {n}")
    if skipped:
        print("  examined but not eligible:")
        for code, n in skipped.most_common():
            print(f"      {code:<24} {n}")
    print(f"  plan digest (sha256)        {_digest(plan)}")

    if args.out_dir:
        csv_path, json_path, digest = write_manifest(plan, Path(args.out_dir))
        for path in (csv_path, json_path):
            print(f"  {hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        print(f"  manifest dir                {Path(args.out_dir).resolve()}")
    return 0


def apply(args) -> int:
    """Write the plan. Refuses without --confirm and a manifest whose digest still matches."""
    if not args.confirm:
        print("REFUSED: --apply requires --confirm.")
        return 2
    if not args.manifest:
        print("REFUSED: --apply requires --manifest <deferral_manifest.json>.")
        return 2

    header = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    engine = _engine(read_only=False)
    from app.services import document_deferral as dd

    with engine.connect() as conn:
        plan, _ = collect(conn)
    if _digest(plan) != header.get("plan_digest_sha256"):
        print("REFUSED: the corpus has moved since this manifest was generated.\n"
              f"  manifest digest {header.get('plan_digest_sha256')}\n"
              f"  current digest  {_digest(plan)}\n"
              "Re-run the preview and have the new plan reviewed.")
        return 1

    summary = Counter()
    with engine.begin() as conn:
        for row in plan:
            result = dd.defer_document(row["document_id"], reason=row["reason"],
                                       actor_user_id=args.actor_user_id,
                                       request_id=f"deferral-backfill-{header['plan_digest_sha256'][:12]}",
                                       conn=conn)
            summary[result["outcome"]] += 1
    for outcome, n in summary.most_common():
        print(f"  {outcome:<24} {n}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="preview_document_deferral",
                                description="Preview (default) or apply the deferred-ownership backfill.")
    p.add_argument("--out-dir", default=None, help="write the manifest + digests here")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--apply", action="store_true", help="WRITE. Requires --manifest and --confirm.")
    p.add_argument("--manifest", default=None)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--actor-user-id", type=int, default=None)
    args = p.parse_args(argv)
    return apply(args) if args.apply else preview(args)


if __name__ == "__main__":
    raise SystemExit(main())
