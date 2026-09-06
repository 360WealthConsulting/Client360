"""READ-ONLY preview for Drake client-id carry-over ownership. Produces a manifest; writes no
ownership, ever.

The manifest is the immutable input the apply is approved against: a human approves its SHA256, its
row count and its per-owner-type census, and ``apply_drake_sibling_ownership.py`` refuses anything
that does not match those externally supplied numbers. Deriving the expectations from the file would
prove nothing — it always agrees with itself.

Every row records not just the decision but the evidence behind it, including the contradictions that
fired BEFORE firm-signal exclusion and the exact firm identities that were removed, so a reviewer can
see what the tuning did rather than take it on trust.

USAGE
    python scripts/preview_drake_sibling_ownership.py --document-ids 121824,121826 --out DIR
    python scripts/preview_drake_sibling_ownership.py --since-first-sync --out DIR
    python scripts/preview_drake_sibling_ownership.py --strict --since-first-sync --out DIR
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.db import (  # noqa: E402
    documents,
    engine,
    households,
    metadata,
    people,
    relationship_entities,
)
from app.services.document_owner_proposal import build_match_indexes  # noqa: E402
from app.services.drake_sibling_ownership import evaluate  # noqa: E402

document_sources = metadata.tables["document_sources"]
FIRST_SYNC_AT = "2026-09-05 22:53:00-04"

COLUMNS = ["document_id", "drake_client_id", "owner_type", "owner_id", "owner_name",
           "sibling_document_ids", "sibling_owner_tuple", "engine_status", "engine_proposed_owner",
           "contradictions_before_firm_exclusion", "firm_signals_excluded",
           "contradictions_remaining", "prior_person_id", "prior_household_id",
           "prior_organization_id", "extract_method", "text_len", "evidence_digest", "applied"]


def _owner_name(conn, otype, oid):
    tbl = {"person": people, "household": households, "organization": relationship_entities}[otype]
    col = tbl.c.full_name if otype == "person" else tbl.c.name
    return conn.execute(select(col).where(tbl.c.id == oid)).scalar()


def _digest(row: dict) -> str:
    """A stable hash of the EVIDENCE, not of the row's presentation. Lets the apply prove the
    evidence behind a decision has not changed since a human looked at it."""
    payload = json.dumps({k: row[k] for k in (
        "document_id", "drake_client_id", "owner_type", "owner_id", "sibling_document_ids",
        "sibling_owner_tuple", "contradictions_remaining")}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def candidates(conn, *, document_ids=None, since_first_sync=False):
    stmt = (select(documents.c.id)
            .select_from(documents.join(document_sources,
                                        document_sources.c.document_id == documents.c.id))
            .where(document_sources.c.source_system == "Drake",
                   documents.c.status != "deleted", documents.c.archived.is_(False),
                   documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                   documents.c.organization_id.is_(None)))
    if document_ids:
        stmt = stmt.where(documents.c.id.in_(tuple(document_ids)))
    if since_first_sync:
        stmt = stmt.where(documents.c.created_at >= text(f"timestamptz '{FIRST_SYNC_AT}'"))
    return [r[0] for r in conn.execute(stmt.distinct().order_by(documents.c.id))]


def build(*, document_ids=None, since_first_sync=False, exclude_firm=True, out_dir=None):
    rows, rejected = [], []
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        idx = build_match_indexes(conn)
        for did in candidates(conn, document_ids=document_ids, since_first_sync=since_first_sync):
            v = evaluate(conn, did, idx, exclude_firm=exclude_firm)
            if not v["eligible"]:
                rejected.append({"document_id": did, "reasons": v["reasons"],
                                 "candidate": v["candidate"],
                                 "contradictions": v["contradictions_tuned"] if exclude_firm
                                 else v["contradictions_strict"]})
                continue
            otype, oid = v["candidate"]
            row = {
                "document_id": did, "drake_client_id": v["client_id"],
                "owner_type": otype, "owner_id": oid,
                "owner_name": _owner_name(conn, otype, oid),
                "sibling_document_ids": ";".join(str(s) for s in v["siblings"]),
                "sibling_owner_tuple": json.dumps(v["sibling_tuples"][0]),
                "engine_status": v["engine"]["confidence"],
                "engine_proposed_owner": json.dumps([v["engine"]["type"], v["engine"]["id"]]),
                "contradictions_before_firm_exclusion": ";".join(v["contradictions_strict"]),
                "firm_signals_excluded": json.dumps(v["firm_excluded"]),
                "contradictions_remaining": ";".join(v["contradictions_tuned"]),
                "prior_person_id": "", "prior_household_id": "", "prior_organization_id": "",
                "extract_method": v["extract_method"], "text_len": v["text_len"],
                "applied": "NO",
            }
            row["evidence_digest"] = _digest(row)
            rows.append(row)

    census = {t: sum(1 for r in rows if r["owner_type"] == t)
              for t in ("person", "household", "organization")}
    result = {"rows": rows, "rejected": rejected, "census": census,
              "policy": "tuned_firm_exclusion" if exclude_firm else "strict"}
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "drake_sibling_ownership_manifest.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        with open(path, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        meta = {"created_at": dt.datetime.now(dt.UTC).isoformat(), "manifest": path,
                "manifest_sha256": sha, "rows": len(rows), "census": census,
                "policy": result["policy"], "rejected": len(rejected)}
        with open(os.path.join(out_dir, "preview_meta.json"), "w", encoding="utf-8") as fh:
            json.dump({**meta, "rejected_detail": rejected}, fh, indent=2, default=str)
        result.update(meta)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="preview_drake_sibling_ownership")
    ap.add_argument("--document-ids", default=None,
                    help="comma-separated document ids; default is every unowned Drake document")
    ap.add_argument("--since-first-sync", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="do NOT exclude verified firm staff/self signals from contradictions")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    ids = [int(x) for x in a.document_ids.split(",")] if a.document_ids else None
    r = build(document_ids=ids, since_first_sync=a.since_first_sync,
              exclude_firm=not a.strict, out_dir=a.out)
    print(f"policy            : {r['policy']}")
    print(f"eligible rows     : {len(r['rows'])}")
    print(f"census            : {r['census']}")
    print(f"rejected          : {len(r['rejected'])}")
    if r.get("manifest"):
        print(f"manifest          : {r['manifest']}")
        print(f"manifest_sha256   : {r['manifest_sha256']}")
    for x in r["rejected"]:
        print(f"  REJECT doc={x['document_id']} reasons={x['reasons']} "
              f"candidate={x['candidate']} contradictions={x['contradictions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
