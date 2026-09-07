#!/usr/bin/env python3
"""READ-ONLY canonical filing preview. Produces the artifacts both phases are authorized against.

    python scripts/preview_canonical_filing.py --out var/canonical_filing

Opens a ``SET TRANSACTION READ ONLY`` transaction, verifies that it really is read-only, builds the
deployed filing preview, applies the canonical ``CLIENT > SERVICE_LINE > TAX_YEAR`` contract, and
writes four artifacts plus their SHA256s:

* ``canonical_filing_preview.csv``   — one row per live document, with its first-fail reason
* ``phase_a_folder_manifest.json``   — the folders Phase A would create
* ``phase_b_filing_manifest.json``   — the document→folder assignments Phase B would make
* ``canonical_filing_summary.json``  — the census, which must reconcile against the corpus total

The transaction is rolled back, never committed. This script has no ``--apply`` and no write path.
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

CSV_NAME = "canonical_filing_preview.csv"
PHASE_A_NAME = "phase_a_folder_manifest.json"
PHASE_B_NAME = "phase_b_filing_manifest.json"
SUMMARY_NAME = "canonical_filing_summary.json"

CSV_COLUMNS = (
    "document_id", "status", "reason", "source_system",
    "owner_scope_type", "owner_scope_id", "owner_source_label", "owner_folder_label",
    "owner_label_sanitized", "service_code", "service_label", "service_source",
    "tax_year", "tax_year_confidence", "tax_year_source",
    "folder_segments", "folder_path", "folder_code",
    "proposed_document_type", "source_provenance", "taxdome_unsorted",
    "proposed_display_name", "display_name_source", "display_name_quality",
    "raw_filename_fallback", "original_name",
    "derivation_rule", "derivation_status", "derivation_reason",
    "backing_document_count", "backing_digest", "contradiction_veto",
)


def _write_json(path: Path, payload) -> str:
    from app.services.filing_manifest import sha256_of
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8", newline="\n")
    return sha256_of(path)


def _csv_row(row) -> dict:
    derivation = row.get("derivation") or {}
    return {
        "document_id": row["document_id"], "status": row["status"], "reason": row["reason"] or "",
        "source_system": row["source_system"],
        "owner_scope_type": row["owner_scope_type"] or "",
        "owner_scope_id": row["owner_scope_id"] if row["owner_scope_id"] is not None else "",
        "owner_source_label": row["owner_source_label"] or "",
        "owner_folder_label": row["owner_folder_label"] or "",
        "owner_label_sanitized": int(bool(row["owner_label_sanitized"])),
        "service_code": row["service_code"] or "", "service_label": row["service_label"] or "",
        "service_source": row["service_source"] or "",
        "tax_year": row["tax_year"] if row["tax_year"] is not None else "",
        "tax_year_confidence": row["tax_year_confidence"] or "",
        "tax_year_source": row["tax_year_source"] or "",
        "folder_segments": json.dumps(row["folder_segments"], ensure_ascii=False),
        "folder_path": row["folder_path"], "folder_code": row["folder_code"] or "",
        "proposed_document_type": row["proposed_document_type"],
        "source_provenance": row["source_provenance"],
        "taxdome_unsorted": int(bool(row["taxdome_unsorted"])),
        "proposed_display_name": row["proposed_display_name"],
        "display_name_source": row["display_name_source"],
        "display_name_quality": row["display_name_quality"],
        "raw_filename_fallback": int(bool(row["raw_filename_fallback"])),
        "original_name": row["original_name"],
        "derivation_rule": derivation.get("derivation_rule") or "",
        "derivation_status": derivation.get("status") or "",
        "derivation_reason": derivation.get("reason") or "",
        "backing_document_count": derivation.get("backing_document_count") or "",
        "backing_digest": derivation.get("backing_digest") or "",
        "contradiction_veto": int(bool(derivation.get("contradiction_veto"))),
    }


def run(out_dir: Path, *, min_backing_documents=None) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.services.canonical_filing import build_rows, summarize
    from app.services.canonical_filing_phases import (
        build_phase_a_manifest,
        build_phase_b_manifest,
    )
    from app.services.document_filing_preview import build_preview, corpus_totals
    from app.services.filing_labels import visible_label_collisions
    from app.services.filing_manifest import sha256_of
    from app.services.taxdome_service_derivation import MIN_BACKING_DOCUMENTS

    minimum = MIN_BACKING_DOCUMENTS if min_backing_documents is None else int(min_backing_documents)

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        read_only = connection.execute(text("SHOW transaction_read_only")).scalar()
        if read_only != "on":
            raise SystemExit(f"ABORT: transaction is not read only (got {read_only!r})")
        alembic = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        totals = corpus_totals(connection)
        proposals = build_preview(connection)
        # Live filing state, read in the SAME read-only snapshot as the proposals, so the plan's
        # eligibility and its classification can never be from two different moments.
        filed_document_ids = [r[0] for r in connection.execute(text(
            "select id from documents where folder_id is not null order by id"))]
        transaction.rollback()

    rows, profiles = build_rows(proposals, min_backing_documents=minimum)
    phase_a = build_phase_a_manifest(rows)
    phase_b = build_phase_b_manifest(rows, phase_a, filed_document_ids=filed_document_ids)
    census = summarize(rows)

    owners = {(r["owner_scope_type"], r["owner_scope_id"]): r["owner_source_label"]
              for r in rows if r["owner_scope_type"] and r["owner_source_label"]}
    collisions = visible_label_collisions(owners)

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / CSV_NAME
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row(row))
    csv_sha = sha256_of(csv_path)

    phase_a_sha = _write_json(out_dir / PHASE_A_NAME, phase_a)
    phase_b_sha = _write_json(out_dir / PHASE_B_NAME, phase_b)

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "alembic_version": alembic,
        "transaction": "READ ONLY (verified)",
        "min_backing_documents": minimum,
        "corpus_totals": totals,
        "census": census,
        "owner_profiles": {
            "owners_with_a_profile": len(profiles),
            "single_service_owners": sum(
                1 for p in profiles.values() if p["single_service_code"]),
            "single_service_owners_meeting_minimum": sum(
                1 for p in profiles.values() if p["single_service_code"] and p["meets_minimum"]),
            "multi_service_owners": sum(
                1 for p in profiles.values() if not p["single_service_code"]),
        },
        "visible_label_collisions": {k: [list(o) for o in v] for k, v in collisions.items()},
        "visible_label_collision_count": len(collisions),
        "artifacts": {
            CSV_NAME: csv_sha,
            PHASE_A_NAME: phase_a_sha,
            PHASE_B_NAME: phase_b_sha,
        },
        "phase_a": {k: phase_a[k] for k in
                    ("phase", "folder_count", "census", "destinations", "owners",
                     "folder_manifest_digest", "batch_id", "confirm_phrase")},
        "phase_b": {k: phase_b[k] for k in
                    ("phase", "document_count", "destinations", "owners", "by_service",
                     "by_service_source", "derived_assignments", "auto_file_safe_count",
                     "already_filed_count", "naming_hold_count", "reconciliation",
                     "assignment_digest", "folder_manifest_digest", "batch_id",
                     "confirm_phrase")},
        "documents_already_filed": len(filed_document_ids),
    }
    summary_sha = _write_json(out_dir / SUMMARY_NAME, summary)
    summary["artifacts"][SUMMARY_NAME] = summary_sha
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="READ-ONLY canonical filing preview.")
    parser.add_argument("--out", default="var/canonical_filing", help="artifact directory")
    parser.add_argument("--min-backing-documents", type=int, default=None,
                        help="override the TaxDome owner-profile minimum (default 5)")
    args = parser.parse_args(argv)

    summary = run(Path(args.out), min_backing_documents=args.min_backing_documents)
    census = summary["census"]
    print(f"transaction        : {summary['transaction']}")
    print(f"alembic            : {summary['alembic_version']}")
    print(f"rows               : {census['total_rows']}")
    print(f"buckets            : {census['buckets']}")
    print(f"first-fail reasons : {census['first_fail_reasons']}")
    print(f"AUTO_FILE_SAFE     : {census['auto_file_safe']}")
    print(f"  by source        : {census['by_source']}")
    print(f"  by service       : {census['by_service']}")
    print(f"  by service source: {census['by_service_source']}")
    print(f"  destinations     : {census['destinations']}  owners: {census['owners']}")
    print(f"  depth census     : {census['depth_census']}")
    print(f"  raw-name fallback: {census['raw_filename_fallback']}")
    print(f"phase A            : {summary['phase_a']['folder_count']} folders  "
          f"{summary['phase_a']['confirm_phrase']}")
    print(f"phase B            : {summary['phase_b']['document_count']} documents  "
          f"{summary['phase_b']['confirm_phrase']}")
    reconciliation = summary["phase_b"]["reconciliation"]
    print(f"  eligibility      : {reconciliation['auto_file_safe']} AUTO_FILE_SAFE = "
          f"{reconciliation['already_filed']} already filed + "
          f"{reconciliation['naming_hold']} naming hold + "
          f"{reconciliation['planned']} planned"
          f"{'' if reconciliation['reconciles'] else '  *** DOES NOT RECONCILE ***'}")
    print(f"label collisions   : {summary['visible_label_collision_count']}")
    for name, digest in sorted(summary["artifacts"].items()):
        print(f"  {digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
