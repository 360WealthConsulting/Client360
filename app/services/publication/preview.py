"""Read-only publication preview — what the corpus WOULD be proposed as, if anyone published it.

This module runs SELECTs and returns counts. It writes nothing: no publication, no visibility flag,
no ownership change, no audit row. That is not a convention, it is the contract — the preview is how
a human decides, so it must be safe to run against production at any time, including by someone who
has not read the code.

BANDS, IN PRECEDENCE ORDER. A document falls in the first band that claims it:

1. ``cross_client_content_conflict``  Its content hash is attributed to more than one audience. The
   canonical resolver deduplicates by hash and fills only NULL ownership, so identical files
   submitted by two clients converge on one row with one owner — meaning at least one attribution in
   the group is wrong. Publishing on the wrong one hands a client another client's document, so the
   conflict outranks every other consideration including an otherwise-clean classification.

2. ``missing_audience_mapping``  No live audience can be resolved. A publication needs somebody to
   publish TO; a document whose owner row is gone or inactive has nobody, and that is a filing
   problem to fix rather than a disclosure decision to make.

3-5. The policy bands from :mod:`app.services.publication.policy` — ``proposed_client_visible``,
   ``proposed_staff_only``, ``review_required``.

The first two bands are properties of the DATA. The last three are properties of the TYPE. Keeping
them in one ordered list is what stops a well-classified 1040 with a contested owner from being
proposed client-visible on the strength of its classification alone.
"""
from __future__ import annotations

from sqlalchemy import text

from app.services.publication import policy

#: The ingestion sources this preview is scoped to by default.
DEFAULT_SOURCE_SYSTEMS = ("Drake", "TaxDome Drive")

CROSS_CLIENT_CONFLICT = "cross_client_content_conflict"
MISSING_AUDIENCE = "missing_audience_mapping"

#: Ordered. ``band_for`` returns the first that claims the row.
BANDS = (CROSS_CLIENT_CONFLICT, MISSING_AUDIENCE,
         policy.CLIENT_VISIBLE, policy.STAFF_ONLY, policy.REVIEW_REQUIRED)

#: One statement, because the preview must describe the corpus as it actually is rather than as a
#: chain of Python filters believes it to be.
#:
#: ``owned``   the "correctly owned" set: a resolvable owner, active status, not archived, not
#:             deleted. Matches the definition used in the vault visibility audit.
#: ``audience`` the audience a publication would address — person first, household second.
#: ``hash_span`` how many DISTINCT audiences share this row's content hash across the whole corpus.
_PREVIEW_SQL = """
WITH src AS (
    SELECT DISTINCT document_id, source_system
    FROM document_sources
    WHERE source_system = ANY(:source_systems)
),
owned AS (
    SELECT d.id, d.sha256, d.original_name, d.person_id, d.household_id,
           d.tax_year, s.source_system,
           c.doc_type,
           p.id AS person_row, p.active AS person_active, p.household_id AS person_household,
           h.id AS household_row
    FROM documents d
    JOIN src s ON s.document_id = d.id
    LEFT JOIN document_classifications c ON c.document_id = d.id
    LEFT JOIN people p ON p.id = d.person_id
    LEFT JOIN households h ON h.id = d.household_id
    WHERE (d.person_id IS NOT NULL OR d.household_id IS NOT NULL)
      AND d.status = 'active' AND d.archived IS FALSE AND d.deleted_at IS NULL
),
audienced AS (
    SELECT o.*,
           CASE
             WHEN o.person_row IS NOT NULL AND o.person_active IS TRUE THEN 'person:' || o.person_row
             WHEN o.household_row IS NOT NULL THEN 'household:' || o.household_row
             ELSE NULL
           END AS audience_key
    FROM owned o
),
spans AS (
    SELECT sha256, count(DISTINCT audience_key) AS audience_span
    FROM audienced GROUP BY sha256
)
SELECT a.id, a.sha256, a.original_name, a.doc_type, a.source_system,
       a.audience_key, sp.audience_span
FROM audienced a
JOIN spans sp ON sp.sha256 = a.sha256
"""


def band_for(row) -> str:
    """The band this corpus row falls in. Pure function of the row — no I/O, no state."""
    if (row["audience_span"] or 0) > 1:
        return CROSS_CLIENT_CONFLICT
    if not row["audience_key"]:
        return MISSING_AUDIENCE
    return policy.propose(document_type=row["doc_type"], original_name=row["original_name"])


def preview_rows(conn, *, source_systems=DEFAULT_SOURCE_SYSTEMS):
    """Yield ``(row, band)`` for every correctly owned document in the named sources. Read-only."""
    result = conn.execute(text(_PREVIEW_SQL), {"source_systems": list(source_systems)})
    for row in result.mappings():
        yield row, band_for(row)


def corpus_preview(conn, *, source_systems=DEFAULT_SOURCE_SYSTEMS) -> dict:
    """Band counts for the corpus, overall and per source system. Read-only.

    Returns ``{"total", "bands", "by_source", "conflict_groups"}``. ``conflict_groups`` counts
    distinct content hashes in the conflict band, which is the number of ATTRIBUTIONS a human has to
    resolve — a more useful unit of work than the document count.
    """
    bands = dict.fromkeys(BANDS, 0)
    by_source: dict[str, dict[str, int]] = {}
    conflict_hashes = set()
    total = 0

    for row, band in preview_rows(conn, source_systems=source_systems):
        total += 1
        bands[band] += 1
        source = row["source_system"]
        by_source.setdefault(source, dict.fromkeys(BANDS, 0))[band] += 1
        if band == CROSS_CLIENT_CONFLICT:
            conflict_hashes.add(row["sha256"])

    return {"total": total, "bands": bands, "by_source": by_source,
            "conflict_groups": len(conflict_hashes)}
