#!/usr/bin/env python3
"""Document filing preview — READ-ONLY report. There is no apply mode and no write path.

    python scripts/preview_document_filing.py
    python scripts/preview_document_filing.py --person-id 3824 --show-rows 20
    python scripts/preview_document_filing.py --source SharePoint --filing-status AUTO_FILE_SAFE

WHAT IT PRODUCES
----------------
A timestamped report directory containing the per-document proposals (CSV and JSON), the census, and
the TaxDome / SharePoint taxonomy summaries that show what hierarchy the corpus actually supports.

READ-ONLY BY CONSTRUCTION, NOT BY CONVENTION
--------------------------------------------
The database transaction issues ``SET TRANSACTION READ ONLY`` before it reads a single row, so a
write attempted anywhere beneath this script fails at the server rather than being caught by review.
There is no ``--apply`` flag to forget to omit, no mutation branch, and nothing here creates a folder
row, sets ``documents.folder_id``, moves a file, renames a document, or touches ownership.

FILTERS NARROW *WHICH* DOCUMENTS ARE EVALUATED, NEVER *HOW*
------------------------------------------------------------
``--person-id`` and friends select rows; they do not feed the filing rules. A filtered run and a full
run produce identical proposals for the documents they share, which is what makes a small run a
trustworthy rehearsal for the whole corpus.
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

REPORT_ROOT = REPO_ROOT / "reports"

#: Columns that hold structured values. They are JSON-encoded in the CSV so the file stays flat and
#: machine-readable without losing the evidence.
_JSON_COLUMNS = ("proposed_folder_segments", "tax_year_evidence", "reasons", "conflicts",
                 "evidence")


def _report_dir(output_dir=None) -> Path:
    if output_dir:
        return Path(output_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return REPORT_ROOT / f"document-filing-preview-{stamp}"


def write_reports(proposals, summary, taxonomies, out_dir: Path) -> dict[str, Path]:
    """Write the CSV/JSON reports deterministically. Local filesystem only."""
    from app.services.document_filing_preview import PROPOSAL_FIELDS

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    csv_path = out_dir / "document_filing_preview.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(PROPOSAL_FIELDS), lineterminator="\n")
        writer.writeheader()
        for proposal in sorted(proposals, key=lambda p: p["document_id"]):
            row = {}
            for field in PROPOSAL_FIELDS:
                value = proposal.get(field)
                if field in _JSON_COLUMNS:
                    value = json.dumps(value, sort_keys=True, ensure_ascii=False)
                row[field] = "" if value is None else value
            writer.writerow(row)
    paths["csv"] = csv_path

    json_path = out_dir / "document_filing_preview.json"
    # Rows are built in PROPOSAL_FIELDS order and NOT re-sorted by key: the JSON reads in the same
    # order as the CSV columns. Determinism comes from the fixed field tuple and the id sort, so
    # nothing here depends on dict iteration luck.
    ordered = [{field: proposal.get(field) for field in PROPOSAL_FIELDS}
               for proposal in sorted(proposals, key=lambda p: p["document_id"])]
    json_path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    paths["json"] = json_path

    summary_path = out_dir / "document_filing_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    paths["summary"] = summary_path

    for name, census in sorted(taxonomies.items()):
        path = out_dir / f"{name}_taxonomy_summary.json"
        path.write_text(json.dumps(census, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        paths[name] = path
    return paths


def run(*, document_ids=None, person_id=None, household_id=None, organization_id=None,
        source=None, filing_status=None, limit=None, output_dir=None, with_ocr_text=False,
        skip_taxonomy=False, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.services.document_filing_preview import (
        build_preview,
        corpus_totals,
        sharepoint_taxonomy,
        summarize,
        taxdome_taxonomy,
    )

    connection = engine.connect()
    try:
        # The whole run happens inside ONE explicitly read-only transaction.
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        assert connection.execute(text("show transaction_read_only")).scalar() == "on"
        out("transaction: READ ONLY (verified)")

        totals = corpus_totals(connection)
        proposals = build_preview(connection, document_ids=document_ids, person_id=person_id,
                                  household_id=household_id, organization_id=organization_id,
                                  source=source, limit=limit, with_ocr_text=with_ocr_text)
        if filing_status:
            proposals = [p for p in proposals if p["filing_status"] == filing_status]
        summary = summarize(proposals, totals=totals)
        taxonomies = {}
        if not skip_taxonomy:
            taxonomies = {"taxdome": taxdome_taxonomy(connection),
                          "sharepoint": sharepoint_taxonomy(connection)}
        transaction.rollback()
    finally:
        connection.close()

    out_dir = _report_dir(output_dir)
    paths = write_reports(proposals, summary, taxonomies, out_dir)

    census = summary["census"]
    out(f"report directory: {out_dir}")
    # The two censuses are printed under separate headings ON PURPOSE. They answer different
    # questions and they do not have the same totals: a document owned by both a person and a
    # household is OWNED in the database and a CONFLICT for filing, and both are true.
    if summary.get("database_ownership"):
        out("RAW DATABASE OWNERSHIP (all active documents, straight from the columns):")
        for key, value in summary["database_ownership"].items():
            out(f"  {key}={value}")
    out("FILING PREVIEW (evaluated documents):")
    for key in ("TOTAL_DOCUMENTS", "TOTAL_ACTIVE_DOCUMENTS", "EVALUATED", "AUTO_FILE_SAFE",
                "REVIEW_REQUIRED", "UNRESOLVED", "FILING_SCOPE_RESOLVED", "FILING_SCOPE_CONFLICT",
                "FILING_SCOPE_UNRESOLVED", "FILING_SCOPE_RESOLVED_AUTO_FILE_SAFE",
                "FILING_SCOPE_RESOLVED_REVIEW_REQUIRED", "FILING_SCOPE_RESOLVED_UNRESOLVED",
                "EXCLUDED_NONCLIENT"):
        out(f"  {key}={census[key]}")
    out("NOTHING WAS WRITTEN to the database. Every value above is a proposal.")
    return {"proposals": proposals, "summary": summary, "taxonomies": taxonomies,
            "paths": {k: str(v) for k, v in paths.items()}, "report_dir": str(out_dir)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="READ-ONLY document filing preview. No apply mode exists.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--document-id", type=int, action="append", dest="document_ids")
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument("--household-id", type=int, default=None)
    parser.add_argument("--organization-id", type=int, default=None)
    parser.add_argument("--source", default=None, help="e.g. SharePoint, 'TaxDome Drive'")
    parser.add_argument("--filing-status", default=None,
                        choices=["AUTO_FILE_SAFE", "REVIEW_REQUIRED", "UNRESOLVED"])
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--with-ocr-text", action="store_true", default=False,
                        help="classify using stored OCR text as well as the filename (slower)")
    parser.add_argument("--skip-taxonomy", action="store_true", default=False)
    parser.add_argument("--show-rows", type=int, default=0)
    args = parser.parse_args(argv)

    result = run(document_ids=args.document_ids, person_id=args.person_id,
                 household_id=args.household_id, organization_id=args.organization_id,
                 source=args.source, filing_status=args.filing_status, limit=args.limit,
                 output_dir=args.output_dir, with_ocr_text=args.with_ocr_text,
                 skip_taxonomy=args.skip_taxonomy)

    if args.show_rows:
        print(f"\n{'id':>8} {'status':<16} {'conf':<5} destination")
        for proposal in result["proposals"][:args.show_rows]:
            print(f"{proposal['document_id']:>8} {proposal['filing_status']:<16} "
                  f"{proposal['filing_confidence']:<5} "
                  f"{proposal['proposed_folder_path'] or '-'}")
            detail = "; ".join(proposal["reasons"] + proposal["conflicts"])
            if detail:
                print(f"{'':>8} └─ {detail[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
