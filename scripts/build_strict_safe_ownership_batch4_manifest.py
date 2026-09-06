#!/usr/bin/env python3
"""Freeze the strict-safe ownership BATCH 4 manifest. READ-ONLY against the database.

    python scripts/build_strict_safe_ownership_batch4_manifest.py

Rebuilds the plan from live state, writes the CSV and JSON a human reviews, and prints the three
values the apply script will demand back: the CSV SHA256, the JSON SHA256 and the plan digest.

This script reads the database inside ``SET TRANSACTION READ ONLY`` and writes nothing but local
report files. It is the only way a Batch 4 manifest should be produced, because a manifest typed by
hand cannot be shown to match the rule.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_ROOT = REPO_ROOT / "reports"

CSV_NAME = "strict_safe_ownership_batch4_manifest.csv"
JSON_NAME = "strict_safe_ownership_batch4_manifest.json"

#: Columns carried in the CSV, in order. ``support_json`` keeps the evidence a reviewer needs
#: without turning the flat file into a nested one.
CSV_COLUMNS = ("document_id", "original_name", "former_person_id", "former_person_name",
               "organization_id", "organization_name", "review_status", "support_json")

#: The evidence fields that travel inside support_json.
SUPPORT_FIELDS = ("repaired_from_person_id", "filename_names_organization", "folder_source_id",
                  "folder_segment", "twin_document_id")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text_lf(path: Path, body: str) -> None:
    """Write UTF-8 text with LF newlines, on every platform.

    Both manifests go through this one function, and the guarantee it makes is why the apply's SHA
    pin is portable at all. ``Path.write_text`` translates ``\\n`` to ``\\r\\n`` on Windows, which is
    how the JSON manifest came to be CRLF on disk while git stored it as LF — the same reviewed file
    then hashed one way on Windows and another on Linux, and the gate could not be satisfied on both.
    Encoding the bytes ourselves takes the platform out of the answer.

    ``.gitattributes`` marks these artifacts ``-text`` so a checkout cannot undo it. The two defences
    are deliberately independent: the generator makes the bytes right, and git is prevented from
    rewriting them afterwards. Neither alone is enough — a file copied by hand bypasses git, and a
    file regenerated on another platform bypasses .gitattributes.
    """
    path.write_bytes(body.encode("utf-8"))


def manifest_bytes(plan) -> tuple[str, str, str]:
    """The exact CSV and JSON text for a plan, plus its digest. Pure: no clock, no filesystem.

    Deterministic by construction, which is the point: a frozen manifest is hashed and that hash is
    a gate, so the artifact has to be a function of the REVIEWED PLAN and nothing else. It carries no
    generation timestamp for exactly that reason — a wall clock would give every regeneration a
    different hash and make "regenerate and compare" impossible to use as a check. When the batch was
    applied belongs in the apply receipt, which already records ``applied_at``; it is not part of
    what was approved.
    """
    from app.services.document_strict_safe_ownership_batch4 import (
        BATCH_ID,
        PLAN_FIELDS,
        plan_census,
        plan_digest,
    )

    digest = plan_digest(plan)
    ordered = sorted(plan, key=lambda r: r["document_id"])

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in ordered:
        writer.writerow({
            **{k: row[k] for k in CSV_COLUMNS if k != "support_json"},
            "support_json": json.dumps({k: row[k] for k in SUPPORT_FIELDS},
                                       sort_keys=True, ensure_ascii=False),
        })
    csv_text = buffer.getvalue()

    json_text = json.dumps({
        "batch_id": BATCH_ID,
        "confirmation_phrase": f"APPLY-{BATCH_ID}-{len(ordered)}",
        "rollback_phrase": f"ROLLBACK-{BATCH_ID}-{len(ordered)}",
        "rows": len(ordered),
        "census": plan_census(ordered),
        "target_state": {"person_id": None, "household_id": None,
                         "organization_id": "unchanged (already correct)"},
        "plan_digest": digest,
        "plan": [{k: r[k] for k in PLAN_FIELDS} for r in ordered],
    }, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return csv_text, json_text, digest


def write_manifest(plan, out_dir: Path) -> dict:
    from app.services.document_strict_safe_ownership_batch4 import plan_census

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_text, json_text, digest = manifest_bytes(plan)
    csv_path, json_path = out_dir / CSV_NAME, out_dir / JSON_NAME
    write_text_lf(csv_path, csv_text)
    write_text_lf(json_path, json_text)

    return {"csv": csv_path, "json": json_path, "csv_sha256": sha256_of(csv_path),
            "json_sha256": sha256_of(json_path), "plan_digest": digest,
            "rows": len(plan), "census": plan_census(plan)}


def main(argv=None) -> int:
    from sqlalchemy import text

    from app.db import engine
    from app.services.document_strict_safe_ownership_batch4 import build_plan, plan_digest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)

    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        plan = build_plan(connection)

    digest = plan_digest(plan)
    out_dir = Path(args.output_dir) if args.output_dir else \
        REPORT_ROOT / f"strict-safe-ownership-batch4-{digest[:12]}"
    result = write_manifest(plan, out_dir)

    print(f"report directory : {out_dir}")
    print(f"rows             : {result['rows']}")
    print(f"census           : {result['census']}")
    print(f"document ids     : {[r['document_id'] for r in plan]}")
    print(f"CSV_SHA256       : {result['csv_sha256']}")
    print(f"JSON_SHA256      : {result['json_sha256']}")
    print(f"PLAN_DIGEST      : {result['plan_digest']}")
    print("NOTHING WAS WRITTEN to the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
