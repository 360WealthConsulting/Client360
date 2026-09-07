#!/usr/bin/env python3
"""PHASE R PREVIEW — READ-ONLY. Plan the reconciliation of the legacy filing tree.

    python scripts/preview_canonical_reconciliation.py --out var/canonical_reconciliation

Opens a ``SET TRANSACTION READ ONLY`` transaction, verifies it, reads the live legacy folders and
their document assignments, re-runs the canonical contract against current production, and writes a
reconciliation manifest plus its census. Rolls back; never commits. There is no ``--apply``.

The manifest names, for every legacy folder and every already-filed document, the canonical identity
it implies and the single explicit action proposed for it. Nothing is implicit: a document is either
already canonical, moves, or is left for review.
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

MANIFEST_NAME = "phase_r_reconciliation_manifest.json"
FOLDERS_CSV = "phase_r_folders.csv"
DOCUMENTS_CSV = "phase_r_documents.csv"
SUMMARY_NAME = "phase_r_summary.json"

FOLDER_COLUMNS = ("folder_id", "folder_code", "legacy_kind", "canonical_kind", "owner_scope_type",
                  "owner_scope_id", "service_code", "tax_year", "proposed_action", "reason")
DOCUMENT_COLUMNS = ("document_id", "existing_folder_id", "existing_folder_code", "legacy_kind",
                    "legacy_service_code", "legacy_tax_year", "canonical_folder_code",
                    "canonical_service_code", "canonical_tax_year", "classification",
                    "proposed_action", "reason")


def _write_json(path: Path, payload) -> str:
    from app.services.filing_manifest import sha256_of
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8", newline="\n")
    return sha256_of(path)


def _write_csv(path: Path, columns, rows) -> str:
    from app.services.filing_manifest import sha256_of
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
    return sha256_of(path)


def run(out_dir: Path) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.services.canonical_filing import AUTO_FILE_SAFE, build_rows
    from app.services.canonical_filing_reconcile import (
        MOVE_DOCUMENT,
        NOOP_ALREADY_CANONICAL,
        classify_document,
        plan_folders,
        summarize,
    )
    from app.services.document_filing_preview import build_preview
    from app.services.filing_manifest import digest_of

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        read_only = connection.execute(text("SHOW transaction_read_only")).scalar()
        if read_only != "on":
            raise SystemExit(f"ABORT: transaction is not read only (got {read_only!r})")
        alembic = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        has_identity = connection.execute(text(
            "select count(*) from information_schema.columns where table_schema='public' "
            "and table_name='document_folders' and column_name='owner_scope_type'")).scalar() == 1
        identity_select = ("owner_scope_type" if has_identity else "null::text as owner_scope_type")
        folders = [dict(r) for r in connection.execute(text(
            f"select id, code, parent_folder_id, {identity_select} "
            "from document_folders order by id")).mappings()]
        assignments = [dict(r) for r in connection.execute(text(
            "select d.id as document_id, d.folder_id, f.code as folder_code "
            "  from documents d join document_folders f on f.id = d.folder_id "
            " where d.status <> 'deleted' order by d.id")).mappings()]
        proposals = build_preview(connection)
        transaction.rollback()

    rows, _profiles = build_rows(proposals)
    by_id = {int(r["document_id"]): r for r in rows}

    folder_plan = plan_folders(folders)
    records = [classify_document(a, by_id.get(int(a["document_id"]))) for a in assignments]
    census = summarize(folder_plan, records)

    out_dir.mkdir(parents=True, exist_ok=True)
    folder_rows = [{
        "folder_id": f["folder_id"], "folder_code": f["folder_code"],
        "legacy_kind": (f["legacy_semantics"] or {}).get("folder_kind", ""),
        "canonical_kind": (f["canonical_identity"] or {}).get("folder_kind", ""),
        "owner_scope_type": (f["canonical_identity"] or {}).get("owner_scope_type", ""),
        "owner_scope_id": (f["canonical_identity"] or {}).get("owner_scope_id", ""),
        "service_code": (f["canonical_identity"] or {}).get("service_code") or "",
        "tax_year": (f["canonical_identity"] or {}).get("tax_year") or "",
        "proposed_action": f["proposed_action"], "reason": f["reason"],
    } for f in folder_plan["folders"]]
    document_rows = [{
        "document_id": r["document_id"], "existing_folder_id": r["existing_folder_id"],
        "existing_folder_code": r["existing_folder_code"],
        "legacy_kind": (r["legacy_semantics"] or {}).get("folder_kind", ""),
        "legacy_service_code": (r["legacy_semantics"] or {}).get("service_code") or "",
        "legacy_tax_year": (r["legacy_semantics"] or {}).get("tax_year") or "",
        "canonical_folder_code": (r["canonical_identity"] or {}).get("folder_code", ""),
        "canonical_service_code": (r["canonical_identity"] or {}).get("service_code", ""),
        "canonical_tax_year": (r["canonical_identity"] or {}).get("tax_year", ""),
        "classification": r["classification"], "proposed_action": r["proposed_action"],
        "reason": r["reason"],
    } for r in records]

    folders_sha = _write_csv(out_dir / FOLDERS_CSV, FOLDER_COLUMNS, folder_rows)
    documents_sha = _write_csv(out_dir / DOCUMENTS_CSV, DOCUMENT_COLUMNS, document_rows)

    manifest = {
        "phase": "PHASE_R_RECONCILIATION",
        "folder_actions": folder_plan["action_counts"],
        "identity_collisions": folder_plan["collisions"],
        "document_classifications": census["documents"]["classifications"],
        "document_actions": census["documents"]["actions"],
        "folders": folder_rows,
        "documents": document_rows,
    }
    manifest["reconciliation_digest"] = digest_of(
        {"folders": [{k: f[k] for k in FOLDER_COLUMNS} for f in folder_rows],
         "documents": [{k: d[k] for k in DOCUMENT_COLUMNS} for d in document_rows]})
    manifest_sha = _write_json(out_dir / MANIFEST_NAME, manifest)

    auto = [r for r in rows if r["status"] == AUTO_FILE_SAFE]
    filed_ids = {int(a["document_id"]) for a in assignments}
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "alembic_version": alembic,
        "cf01_applied": has_identity,
        "transaction": "READ ONLY (verified)",
        "legacy_folders_total": len(folders),
        "legacy_documents_filed": len(assignments),
        "census": census,
        "canonical_auto_file_safe": len(auto),
        "canonical_already_equivalent": sum(
            1 for r in records if r["proposed_action"] == NOOP_ALREADY_CANONICAL),
        "canonical_legacy_needs_reconciliation": sum(
            1 for r in records if r["proposed_action"] == MOVE_DOCUMENT),
        "canonical_currently_unfiled": sum(
            1 for r in auto if int(r["document_id"]) not in filed_ids),
        "reconciliation_digest": manifest["reconciliation_digest"],
        "artifacts": {FOLDERS_CSV: folders_sha, DOCUMENTS_CSV: documents_sha,
                      MANIFEST_NAME: manifest_sha},
    }
    summary["artifacts"][SUMMARY_NAME] = _write_json(out_dir / SUMMARY_NAME, summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PHASE R preview — READ ONLY.")
    parser.add_argument("--out", default="var/canonical_reconciliation")
    args = parser.parse_args(argv)
    summary = run(Path(args.out))
    print(f"transaction            : {summary['transaction']}")
    print(f"alembic                : {summary['alembic_version']}  cf01={summary['cf01_applied']}")
    print(f"legacy folders         : {summary['legacy_folders_total']}")
    print(f"legacy filed documents : {summary['legacy_documents_filed']}")
    print(f"folder actions         : {summary['census']['folders']['actions']}")
    print(f"identity collisions    : {summary['census']['folders']['identity_collisions']}")
    print(f"doc classifications    : {summary['census']['documents']['classifications']}")
    print(f"doc actions            : {summary['census']['documents']['actions']}")
    print(f"canonical AUTO_FILE_SAFE          : {summary['canonical_auto_file_safe']}")
    print(f"  already equivalent              : {summary['canonical_already_equivalent']}")
    print(f"  legacy needs reconciliation     : {summary['canonical_legacy_needs_reconciliation']}")
    print(f"  currently unfiled               : {summary['canonical_currently_unfiled']}")
    for name, digest in sorted(summary["artifacts"].items()):
        print(f"  {digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
