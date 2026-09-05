"""Identity-keyed upsert for Drake client returns.

Extracted from ``scripts/import_drake_all_years.py`` so the upsert has a seam that can be tested
without a CSV on disk, and so the positional key can be retired in ONE place. The script keeps the
CSV parsing and file discovery; this module owns what reaches the table.

WHAT CHANGED, AND WHY IT MATTERS
--------------------------------
Before: ``ON CONFLICT (tax_year, source_row_number) DO UPDATE`` — the conflict target was the row's
POSITION in the export. A Drake re-export that inserted, deleted or re-sorted one row shifted every
row after it, and each shifted row overwrote a DIFFERENT taxpayer's return: AGI, filing status,
acknowledgements, and both identifier hashes.

Now: ``ON CONFLICT (return_identity_key) DO UPDATE``, where the key is derived from the return's own
content by ``app.services.drake_return_identity``. ``source_row_number`` is still written, as
provenance — it records where in the export the row was found — but it decides nothing.

QUARANTINED ROWS ARE SKIPPED, NOT GUESSED AT
--------------------------------------------
A row whose identity cannot be established — no usable taxpayer identifier, or an identity tuple
claimed by several rows in the same export — is NOT written at all. It is counted and returned in the
summary for staff attention.

Skipping is the fail-closed choice, and it is also the only idempotent one. Writing such a row would
require inventing a conflict target for it, and every available candidate (position, name, or
"whatever is already there") is precisely the class of guess that produced the original defect. A
skipped row leaves any existing database row untouched, so a malformed export can never mutate a
return that was previously imported correctly.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.services.drake_return_identity import (
    AMBIGUOUS_COLLISION,
    NO_TAXPAYER_IDENTIFIER,
    assign_identities,
    is_identified,
)

#: Written on insert and refreshed on update. ``return_identity_key`` and ``identity_status`` are set
#: on insert only — an identified row's key IS its conflict target, so it cannot change, and its
#: status is 'identified' by construction.
_MUTABLE_COLUMNS = (
    "source_row_number",
    "taxpayer_identifier_hash", "spouse_identifier_hash",
    "taxpayer_first_name", "taxpayer_last_name", "taxpayer_normalized_name", "taxpayer_dob",
    "spouse_first_name", "spouse_last_name", "spouse_normalized_name", "spouse_dob",
    "filing_status", "return_type", "preparer_code",
    "agi", "preparer_fee",
    "prepare_date", "review_date", "approved_date", "complete_date",
    "federal_product", "federal_ack_date", "federal_ack_code",
    "state_product", "state_ack_date", "state_ack_code",
    "source_updated_at",
)

_INSERT_COLUMNS = ("tax_year", "return_identity_key", "identity_status", *_MUTABLE_COLUMNS)

_VALUES = ", ".join(
    "CAST(:raw_data AS JSONB)" if column == "raw_data" else f":{column}"
    for column in _INSERT_COLUMNS
)

_UPSERT = text(f"""
INSERT INTO drake_client_returns ({", ".join(_INSERT_COLUMNS)}, raw_data)
VALUES ({_VALUES}, CAST(:raw_data AS JSONB))
-- The uniqueness is a PARTIAL index (quarantined rows carry a NULL key and must be allowed to
-- coexist), so the conflict target has to restate that index's predicate verbatim. Without the
-- WHERE clause Postgres cannot match the target and rejects the statement outright.
ON CONFLICT (return_identity_key) WHERE return_identity_key IS NOT NULL DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in _MUTABLE_COLUMNS)},
    raw_data = EXCLUDED.raw_data
RETURNING (xmax = 0) AS inserted
""")


def new_summary() -> dict[str, Any]:
    return {
        "rows_read": 0,
        "identified": 0,
        "inserted": 0,
        "updated": 0,
        "quarantined": 0,
        "quarantined_no_taxpayer_identifier": 0,
        "quarantined_ambiguous_collision": 0,
        "quarantined_rows": [],
    }


def upsert_return_rows(conn, rows, *, summary=None) -> dict[str, Any]:
    """Upsert one export batch by stable identity. Returns a summary.

    ``rows`` are dicts of column values, already parsed — every key in :data:`_INSERT_COLUMNS` plus
    ``raw_data``. Identity assignment is batch-scoped because a collision is only visible with the
    whole export in hand, so pass ONE export (one year's file) per call.
    """
    summary = summary if summary is not None else new_summary()

    for row in assign_identities(rows):
        summary["rows_read"] += 1

        if not is_identified(row):
            summary["quarantined"] += 1
            status = row["identity_status"]
            if status == NO_TAXPAYER_IDENTIFIER:
                summary["quarantined_no_taxpayer_identifier"] += 1
            elif status == AMBIGUOUS_COLLISION:
                summary["quarantined_ambiguous_collision"] += 1
            # Content-free: enough to find the row in the export, never the identifiers themselves.
            summary["quarantined_rows"].append({
                "tax_year": row.get("tax_year"),
                "source_row_number": row.get("source_row_number"),
                "return_type": row.get("return_type"),
                "identity_status": status,
            })
            continue

        params = {column: row.get(column) for column in _INSERT_COLUMNS}
        params["raw_data"] = row.get("raw_data") if isinstance(row.get("raw_data"), str) \
            else json.dumps(row.get("raw_data") or {}, ensure_ascii=False)

        inserted = conn.execute(_UPSERT, params).scalar_one()
        summary["identified"] += 1
        summary["inserted" if inserted else "updated"] += 1

    return summary
