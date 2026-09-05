#!/usr/bin/env python3
"""Non-client exclusion — PREVIEW ONLY in this revision.

    python scripts/preview_nonclient_exclusion.py --out-dir <dir>

The preview forces the session read-only at the libpq level BEFORE ``app.db`` is imported, so no
code path in the process can write, and it asserts that before running a single query.

There is deliberately NO --apply here. The deferral lane earned its apply path by first proving the
plan against production; this lane is at the same stage, and a batch that classifies 3,002 rows
should not gain a write switch until its manifest has been reviewed. The manifest and its digest are
the artefact that review consumes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _engine():
    """The app engine, forced read-only at the libpq level.

    Set before ``app.db`` is imported so the option applies to the connection it opens, not just to
    ours — a preview must not be able to write through any path in the process.
    """
    os.environ["PGOPTIONS"] = "-c default_transaction_read_only=on"
    from app.db import engine
    return engine


#: Candidate scope. Deliberately WIDER than the allow-list: it selects every live unowned document
#: whose route is UNSUPPORTED plus the three named artifacts, and the authoritative per-row check
#: then refuses everything the allow-list does not name. Reading a superset and refusing is how the
#: preview can report what it REJECTED, which is the number that proves the rule is not too broad.
_CANDIDATE_SQL = """
    SELECT d.id,
           d.original_name,
           d.content_type,
           d.person_id, d.household_id, d.organization_id,
           d.status, d.archived, d.deleted_at, d.review_status,
           coalesce(d.tags->>'source_system', '') AS source_system,
           coalesce((SELECT max(s.source_path) FROM document_sources s
                      WHERE s.document_id = d.id), '') AS source_path,
           coalesce(f.fact_value::jsonb->>'route', '(no proposal)') AS route
      FROM documents d
      LEFT JOIN document_facts f
             ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
     WHERE d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
       AND d.person_id IS NULL AND d.household_id IS NULL AND d.organization_id IS NULL
       AND NOT (d.id = ANY(:rejects))
       AND (coalesce(f.fact_value::jsonb->>'route','') = :required_route
            OR d.id = ANY(:named_ids))
     ORDER BY d.id
"""

_FIELDS = ["document_id", "original_name", "source_system", "source_path", "content_type",
           "route", "matched_rule", "proposed_classification", "current_owner_state",
           "current_review_status"]


def collect(conn):
    """(plan, rejected) — every row verified through the service's own eligibility check."""
    from sqlalchemy import text

    from app.services import document_nonclient_exclusion as nx

    rows = conn.execute(text(_CANDIDATE_SQL), {
        "rejects": sorted(nx.PERMANENT_REJECT_DOCUMENT_IDS),
        "required_route": nx.REQUIRED_ROUTE,
        "named_ids": sorted(nx.APPROVED_ARTIFACT_DOCUMENTS),
    }).mappings().all()

    plan, rejected = [], Counter()
    for r in rows:
        check = nx.eligibility(conn, r["id"])
        if not check["eligible"]:
            rejected[check["reason_code"]] += 1
            continue
        owner = "unowned" if (r["person_id"] is None and r["household_id"] is None
                              and r["organization_id"] is None) else "OWNED"
        plan.append({
            "document_id": int(r["id"]),
            "original_name": r["original_name"] or "",
            "source_system": r["source_system"],
            "source_path": r["source_path"],
            "content_type": r["content_type"] or "",
            "route": r["route"],
            "matched_rule": check["reason"],
            "proposed_classification": nx.EXCLUDED_REVIEW_STATUS,
            "current_owner_state": owner,
            "current_review_status": r["review_status"] or "",
        })
    return plan, rejected


def digest(plan) -> str:
    """Content digest over the PLAN — stable across formatting and ordering noise."""
    payload = json.dumps(
        [[r["document_id"], r["matched_rule"]] for r in sorted(plan, key=lambda x: x["document_id"])],
        separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_manifest(plan, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "nonclient_exclusion_manifest.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS, lineterminator="\n")
        w.writeheader()
        for row in sorted(plan, key=lambda r: r["document_id"]):
            w.writerow(row)
    d = digest(plan)
    json_path = out_dir / "nonclient_exclusion_manifest.json"
    json_path.write_text(json.dumps(
        {"rows": len(plan), "plan_digest_sha256": d,
         "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()},
        indent=2) + "\n", encoding="utf-8")
    return csv_path, json_path, d


def main(argv=None) -> int:
    from sqlalchemy import text

    ap = argparse.ArgumentParser(description="Preview the non-client exclusion batch (read-only).")
    ap.add_argument("--out-dir")
    args = ap.parse_args(argv)

    engine = _engine()
    with engine.connect() as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on", \
            "refusing to preview on a writable session"
        plan, rejected = collect(conn)

    reasons = Counter(r["matched_rule"] for r in plan)
    print("=" * 70)
    print("NON-CLIENT EXCLUSION — PREVIEW (nothing written)")
    print("=" * 70)
    print(f"  candidates to classify      {len(plan)}")
    for reason, n in reasons.most_common():
        print(f"      {reason:<26} {n}")
    if rejected:
        print("  examined but REFUSED (fail-closed):")
        for code, n in rejected.most_common():
            print(f"      {code:<26} {n}")
    owned = sum(1 for r in plan if r["current_owner_state"] != "unowned")
    print(f"  owned rows in plan          {owned}   (must be 0)")
    print(f"  plan digest (sha256)        {digest(plan)}")

    if args.out_dir:
        csv_path, json_path, d = write_manifest(plan, Path(args.out_dir))
        for path in (csv_path, json_path):
            print(f"  {hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        print(f"  manifest dir                {Path(args.out_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
