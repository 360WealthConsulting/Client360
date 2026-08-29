"""Canonical document merge PREVIEW — read-only, database-only (ADR-072 consolidation).

One canonical ``documents`` row per content hash is what ADR-072 already produces at ingest
(``document_sources.resolve_or_create_canonical``). Rows created before that, or by paths that
bypassed it, left duplicate content behind. This module reports what consolidating them WOULD
require. It executes nothing.

WHAT THIS MODULE WILL NEVER DO
  * write to the database — every connection is ``engine.connect()``, never ``engine.begin()``;
  * touch the filesystem, a storage backend, SharePoint, TaxDome or any connector;
  * run or re-run OCR, classification or extraction;
  * decide client identity. Drake is the authority for identity resolution
    (``source_contacts`` → ``drake_identity`` → ``drake_identity_match_candidates``). This layer
    merges DOCUMENTS by content hash and never reads or writes that chain. Where duplicates
    disagree about their owner, that is reported as conflict evidence for a human — provenance is
    not authority, and a document merge must not make an identity decision as a side effect.

SURVIVOR SELECTION IS MECHANICAL
  The proposed survivor is the LOWEST ``documents.id`` among the group's eligible rows — exactly
  what ``resolve_or_create_canonical`` resolves to today. Ownership deliberately does NOT influence
  it: letting an owned duplicate win would make canonicalization an implicit identity decision.
  Ownership is compared AFTERWARDS and reported as evidence.

ELIGIBILITY
  ``documents.status != 'deleted'`` — the same predicate the ADR-072 resolver uses, and a real
  schema-backed state (``ck_documents_status`` admits 'deleted').

THE REGISTRY IS VERIFIED AGAINST THE LIVE SCHEMA
  ``_dependencies()`` reads every FK to ``documents.id`` from ``information_schema`` at run time and
  pairs it with a declared strategy. A reference the registry does not know about does not produce a
  warning — it BLOCKS the group, because an unknown dependency is exactly the case where a merge
  would silently lose data.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from app.db import engine

#: Eligibility predicate — identical to ``resolve_or_create_canonical``'s.
ELIGIBLE_SQL = "status <> 'deleted'"

SAFE, REVIEW, BLOCKED = "SAFE_AUTO_MERGE", "REVIEW_REQUIRED", "BLOCKED"

# --- dependency strategies ------------------------------------------------------------------------
# reassign      — repoint document_id to the survivor. Nothing document-scoped can collide.
# singular      — UNIQUE(document_id): at most one row may survive. Substantive values are COMPARED;
#                 equivalent rows are deduplicable, genuinely different ones need review.
# dedup_keyed   — UNIQUE(document_id, <keys>): reassign, and where the survivor already holds the
#                 same key the duplicate's row is a redundant copy of the same statement.
# provenance    — document_sources. Every DISTINCT (source_system, source_uri) tuple must survive;
#                 a collision after reassignment means the two rows assert the SAME source
#                 relationship, so it is deduplicated, never discarded.
# read_model    — a disposable rm_* projection row: rebuilt from the event stream, never moved.
_STRATEGY: dict[str, str] = {
    "document_sources": "provenance",
    "document_ocr": "singular",
    "document_classifications": "singular",
    "document_facts": "dedup_keyed",
    "document_relationships": "dedup_keyed",
    "document_events": "reassign",
    "document_versions": "reassign",
    "tax_document_links": "reassign",
    "tax_document_classifications": "reassign",
    "tax_checklist_items": "reassign",
    "portal_message_attachments": "reassign",
    "portal_document_requests": "reassign",
    "benefit_document_links": "reassign",
    "benefit_obligations": "reassign",
    "benefit_retirement_plan_details": "reassign",
    "payroll_document_links": "reassign",
    "payroll_issues": "reassign",
    "campaign_documents": "reassign",
    "communication_attachments": "reassign",
    "insurance_requirements": "reassign",
    "invoices": "reassign",
    "meetings": "reassign",
    "operational_tasks": "reassign",
    "exceptions": "reassign",
    "rm_document_status": "read_model",
}

#: Every FK to documents.id, read from the LIVE catalog on each call — still true runtime
#: introspection, but via pg_catalog rather than information_schema. The information_schema view is
#: a four-way join over catalog views and cost 1.28s PER CALL; this is 0.007s and returns exactly the
#: same rows (asserted by tests/test_document_merge_preview.py). No caching: a schema change is seen
#: immediately, which is the property an unknown-dependency BLOCK depends on.
_FK_SQL = """
SELECT c.relname AS table_name, a.attname AS column_name,
       CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE'   WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT' END AS delete_rule
FROM pg_constraint con
JOIN pg_class c  ON c.oid = con.conrelid
JOIN pg_class rc ON rc.oid = con.confrelid
JOIN unnest(con.conkey)  WITH ORDINALITY AS k(attnum, ord)  ON true
JOIN unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = k.ord
JOIN pg_attribute a  ON a.attrelid  = con.conrelid  AND a.attnum  = k.attnum
JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = fk.attnum
WHERE con.contype = 'f' AND rc.relname = 'documents' AND fa.attname = 'id'
  AND c.relname <> 'documents'
ORDER BY c.relname, a.attname
"""

#: Soft references — a document_id column carrying no database FK. Verified by
#: tests/test_document_merge_preview.py against the live schema.
_SOFT_REFERENCES = (("rm_document_status", "document_id"),)


def _dependencies(conn) -> list[dict]:
    """Every reference to documents.id, read from the LIVE schema and paired with its strategy.

    ``strategy=None`` marks a reference the registry does not know about; the caller BLOCKS on it."""
    deps = [{"table": t, "column": c, "delete_rule": rule, "strategy": _STRATEGY.get(t)}
            for t, c, rule in conn.execute(text(_FK_SQL)).fetchall()]
    known = {(d["table"], d["column"]) for d in deps}
    for t, c in _SOFT_REFERENCES:
        if (t, c) not in known and _table_exists(conn, t):
            deps.append({"table": t, "column": c, "delete_rule": "SOFT", "strategy": _STRATEGY.get(t)})
    return deps


def _table_exists(conn, table) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


#: Ids per IN-list. Postgres handles a large ANY() array fine; the cap keeps parameter size and
#: planner cost predictable, and makes the query count a function of CORPUS size, not group count.
DEFAULT_BATCH_SIZE = 5000


class _QueryCounter:
    """Counts every statement the preview issues, so the report can prove its own scaling."""

    __slots__ = ("count",)

    def __init__(self):
        self.count = 0

    def rows(self, conn, sql, params=None):
        self.count += 1
        return conn.execute(text(sql), params or {}).mappings().all()


def _in_batches(ids, size):
    ids = list(ids)
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


# --- duplicate discovery ---------------------------------------------------------------------

def duplicate_hash_groups(conn, q, *, limit=None) -> list[dict]:
    """SHA-256 groups holding more than one ELIGIBLE document row. ONE query for the whole corpus."""
    stmt = (f"SELECT sha256, count(*) AS n, array_agg(id ORDER BY id) AS ids "
            f"FROM documents WHERE {ELIGIBLE_SQL} AND sha256 IS NOT NULL "
            f"GROUP BY sha256 HAVING count(*) > 1 ORDER BY min(id)")
    if limit:
        stmt += f" LIMIT {int(limit)}"
    return [{"sha256": r["sha256"], "row_count": int(r["n"]),
             "document_ids": [int(i) for i in r["ids"]]} for r in q.rows(conn, stmt)]


# --- batched prefetch -------------------------------------------------------------------------
# Every read below covers the WHOLE duplicate-document id set in ceil(n / batch) statements, so the
# query count scales with (dependency tables x batches), never with the number of duplicate groups.
# analyze_group() then runs as pure Python over these dicts and issues no query at all.

def _prefetch_dependency_counts(conn, q, deps, ids, batch) -> dict[str, dict[int, int]]:
    """Rows per ``table.column`` per document id. Each FK COLUMN is queried separately, so a table
    with two document FKs (document_versions) keeps them distinguishable."""
    out: dict[str, dict[int, int]] = {}
    for d in deps:
        key = f"{d['table']}.{d['column']}"
        acc: dict[int, int] = {}
        for chunk in _in_batches(ids, batch):
            for r in q.rows(conn,
                            f"SELECT {d['column']} AS did, count(*) AS n FROM {d['table']} "
                            f"WHERE {d['column']} = ANY(:ids) GROUP BY {d['column']}",
                            {"ids": chunk}):
                acc[int(r["did"])] = acc.get(int(r["did"]), 0) + int(r["n"])
        if acc:
            out[key] = acc
    return out


def _prefetch_documents(conn, q, ids, batch) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for chunk in _in_batches(ids, batch):
        for r in q.rows(conn,
                        "SELECT id, status, person_id, household_id, organization_id, category, "
                        "       classification, subcategory, original_name, created_at "
                        "FROM documents WHERE id = ANY(:ids)", {"ids": chunk}):
            out[int(r["id"])] = dict(r)
    return out


def _prefetch_by_document(conn, q, sql, ids, batch) -> dict[int, list[dict]]:
    """Any per-document detail read, bucketed by document_id."""
    out: dict[int, list[dict]] = defaultdict(list)
    for chunk in _in_batches(ids, batch):
        for r in q.rows(conn, sql, {"ids": chunk}):
            out[int(r["document_id"])].append(dict(r))
    return out


def _prefetch_version_self_reference(conn, q, ids, batch) -> dict[int, list[dict]]:
    """document_versions rows whose BOTH document FKs fall inside the duplicate set.

    If such a row's two documents collapse onto the same survivor, the version chain becomes
    self-referential (previous_document_id == document_id). The executor must handle that; the
    preview records the evidence per group."""
    out: dict[int, list[dict]] = defaultdict(list)
    id_set = set(ids)
    for chunk in _in_batches(ids, batch):
        for r in q.rows(conn,
                        "SELECT id, document_id, previous_document_id FROM document_versions "
                        "WHERE document_id = ANY(:ids) AND previous_document_id IS NOT NULL",
                        {"ids": chunk}):
            if int(r["previous_document_id"]) in id_set:
                out[int(r["document_id"])].append(
                    {"version_id": int(r["id"]),
                     "previous_document_id": int(r["previous_document_id"])})
    return out


# --- conflict detection (pure Python over prefetched rows) --------------------------------------

def _ownership_evidence(docs) -> dict:
    """Ownership across the group. Reported, never used to choose the survivor."""
    owners = {}
    for d in docs:
        owner = (d["person_id"], d["household_id"], d["organization_id"])
        if any(v is not None for v in owner):
            owners[d["id"]] = {"person_id": d["person_id"], "household_id": d["household_id"],
                               "organization_id": d["organization_id"]}
    distinct = {tuple(sorted(v.items())) for v in owners.values()}
    return {"owned_documents": owners, "distinct_owner_count": len(distinct),
            "unowned_count": len(docs) - len(owners)}


def _singular_conflict(rows, compare_columns) -> dict:
    """UNIQUE(document_id) tables: at most one row can survive, so compare SUBSTANCE.

    Rows whose compared values are equal are redundant copies of the same statement and are
    deduplicable. Rows that genuinely disagree need a human."""
    if len(rows) <= 1:
        return {"rows": len(rows), "conflict": False, "distinct_values": len(rows)}
    seen = {tuple(r[c] for c in compare_columns) for r in rows}
    return {"rows": len(rows), "conflict": len(seen) > 1, "distinct_values": len(seen),
            "document_ids": sorted(int(r["document_id"]) for r in rows)}


def _fact_conflicts(rows) -> dict:
    """document_facts: the same fact_type asserting DIFFERENT current values is a real conflict;
    the same (fact_type, fact_value) on several members is one statement recorded twice."""
    by_type = defaultdict(set)
    for r in rows:
        by_type[r["fact_type"]].add(r["fact_value"])
    conflicting = sorted(t for t, vals in by_type.items() if len(vals) > 1)
    redundant = len(rows) - sum(len(v) for v in by_type.values())
    return {"rows": len(rows), "conflicting_fact_types": conflicting,
            "conflict": bool(conflicting), "redundant_rows": max(redundant, 0)}


def _provenance(rows) -> dict:
    """Every DISTINCT (source_system, source_uri) must survive consolidation.

    Two rows asserting the SAME tuple are the same source relationship recorded on two document
    rows; after reassignment they collide on uq_document_source_ref and are deduplicated. That
    discards no provenance — the relationship is still recorded, once."""
    tuples = {(r["source_system"], r["source_uri"]) for r in rows}
    systems = sorted({r["source_system"] for r in rows})
    return {"rows": len(rows), "distinct_provenance_tuples": len(tuples),
            "source_systems": systems,
            "redundant_rows": len(rows) - len(tuples),
            "preserved_after_merge": len(tuples)}


# --- per-group analysis --------------------------------------------------------------------------------

def analyze_group(group, deps, pre) -> dict:
    """Full analysis of ONE duplicate hash group. Issues NO query — everything is prefetched."""
    ids = group["document_ids"]
    docs = [pre["documents"][i] for i in sorted(ids) if i in pre["documents"]]
    survivor = min(d["id"] for d in docs)          # mechanical: lowest eligible id. No owner bump.
    duplicates = [d["id"] for d in docs if d["id"] != survivor]

    counts = {key: {did: n for did, n in per.items() if did in set(ids)}
              for key, per in pre["dependency_counts"].items()}
    counts = {k: v for k, v in counts.items() if v}
    unknown = sorted({d["table"] for d in deps if d["strategy"] is None
                      and counts.get(f"{d['table']}.{d['column']}")})

    def _rows(bucket):
        return [r for i in ids for r in pre[bucket].get(i, [])]

    ownership = _ownership_evidence(docs)
    ocr = _singular_conflict(_rows("ocr"), ("status", "char_count"))
    classification = _singular_conflict(_rows("classifications"), ("doc_type",))
    facts = _fact_conflicts(_rows("facts"))
    provenance = _provenance(_rows("sources"))
    version_self_refs = _rows("version_self_reference")

    distinct_categories = {d["category"] for d in docs if d["category"]}
    distinct_classification = {d["classification"] for d in docs if d["classification"]}

    conflicts, blockers = [], []
    if unknown:
        blockers.append({"kind": "unknown_dependency", "tables": unknown,
                         "detail": "a reference to documents.id with no declared strategy — a merge "
                                   "could silently lose these rows"})
    if ownership["distinct_owner_count"] > 1:
        blockers.append({"kind": "conflicting_ownership",
                         "detail": "duplicates name DIFFERENT owners; resolving that is an identity "
                                   "decision and belongs to the governed person-merge path, not here",
                         "owners": ownership["owned_documents"]})
    elif ownership["distinct_owner_count"] == 1 and ownership["unowned_count"]:
        owned_ids = sorted(ownership["owned_documents"])
        if survivor not in owned_ids:
            conflicts.append({"kind": "ownership_on_non_survivor",
                              "detail": "only a non-survivor carries an owner; consolidating changes "
                                        "which row holds it — a human must confirm",
                              "owned_document_ids": owned_ids})
    if ocr["conflict"]:
        conflicts.append({"kind": "ocr_conflict", "detail": "extracted-text state differs materially",
                          "distinct_values": ocr["distinct_values"]})
    if classification["conflict"]:
        conflicts.append({"kind": "classification_conflict", "detail": "doc_type differs",
                          "distinct_values": classification["distinct_values"]})
    if facts["conflict"]:
        conflicts.append({"kind": "fact_conflict", "detail": "same fact_type, different current values",
                          "fact_types": facts["conflicting_fact_types"]})
    if len(distinct_categories) > 1:
        conflicts.append({"kind": "category_conflict", "values": sorted(distinct_categories)})
    if len(distinct_classification) > 1:
        conflicts.append({"kind": "document_classification_conflict",
                          "values": sorted(distinct_classification)})
    if version_self_refs:
        conflicts.append({"kind": "version_chain_self_reference",
                          "detail": "a document_versions row links two members of this group; after "
                                    "consolidation previous_document_id would equal document_id",
                          "rows": version_self_refs})

    classification_result = BLOCKED if blockers else (REVIEW if conflicts else SAFE)

    # Reassignments the executor WOULD have to perform. SET NULL references are included
    # deliberately: leaving them to a cascade would null a live reference instead of preserving it.
    reassignments = {}
    for d in deps:
        key = f"{d['table']}.{d['column']}"
        per_doc = counts.get(key, {})
        n = sum(v for did, v in per_doc.items() if did != survivor)
        if n:
            reassignments[key] = {"rows": n, "strategy": d["strategy"],
                                  "delete_rule": d["delete_rule"]}

    return {
        "sha256": group["sha256"],
        "classification": classification_result,
        "proposed_survivor": survivor,
        "survivor_rule": "lowest documents.id among eligible rows (ADR-072 resolution order)",
        "duplicate_document_ids": duplicates,
        "row_count": len(docs),
        "excess_rows": len(duplicates),
        "dependent_row_counts": {k: dict(sorted(v.items())) for k, v in sorted(counts.items())},
        "reassignments_required": reassignments,
        "total_reassignments": sum(v["rows"] for v in reassignments.values()),
        "provenance": provenance,
        "ownership": ownership,
        "ocr": ocr,
        "classification_rows": classification,
        "facts": facts,
        "version_self_references": version_self_refs,
        "conflicts": conflicts,
        "blockers": blockers,
    }


# --- the preview -----------------------------------------------------------------------------------------

def preview(*, limit=None, batch_size=DEFAULT_BATCH_SIZE) -> dict:
    """READ-ONLY preview of canonical document consolidation. Performs ZERO writes.

    Query cost scales with (dependency tables x id batches), NOT with the number of duplicate
    groups: every dependent table is read once per batch across the WHOLE duplicate-document id
    set, and each group is then analysed in pure Python. A corpus with thousands of groups issues
    tens of statements, not tens of thousands.

    Deterministic: the same database state yields the same report, so the preview is idempotent."""
    import time

    q = _QueryCounter()
    started = time.monotonic()
    with engine.connect() as conn:
        deps = _dependencies(conn)
        groups = duplicate_hash_groups(conn, q, limit=limit)
        all_ids = sorted({i for g in groups for i in g["document_ids"]})

        pre = {
            "documents": _prefetch_documents(conn, q, all_ids, batch_size),
            "dependency_counts": _prefetch_dependency_counts(conn, q, deps, all_ids, batch_size),
            "ocr": _prefetch_by_document(conn, q,
                "SELECT document_id, status, char_count FROM document_ocr "
                "WHERE document_id = ANY(:ids)", all_ids, batch_size),
            "classifications": _prefetch_by_document(conn, q,
                "SELECT document_id, doc_type FROM document_classifications "
                "WHERE document_id = ANY(:ids)", all_ids, batch_size),
            "facts": _prefetch_by_document(conn, q,
                "SELECT document_id, fact_type, fact_value FROM document_facts "
                "WHERE document_id = ANY(:ids) AND is_current", all_ids, batch_size),
            "sources": _prefetch_by_document(conn, q,
                "SELECT document_id, source_system, source_uri, source_path, source_external_id "
                "FROM document_sources WHERE document_id = ANY(:ids)", all_ids, batch_size),
            "version_self_reference": _prefetch_version_self_reference(conn, q, all_ids, batch_size),
        } if all_ids else {k: {} for k in ("documents", "dependency_counts", "ocr",
                                           "classifications", "facts", "sources",
                                           "version_self_reference")}

        analyzed = [analyze_group(g, deps, pre) for g in groups]   # pure Python, no queries
    elapsed = round(time.monotonic() - started, 3)

    by_class = {SAFE: 0, REVIEW: 0, BLOCKED: 0}
    for g in analyzed:
        by_class[g["classification"]] += 1

    dependent_totals: dict[str, int] = defaultdict(int)
    for g in analyzed:
        for key, info in g["reassignments_required"].items():
            dependent_totals[key] += info["rows"]

    return {
        "read_only": True,
        "wrote_anything": False,
        "survivor_rule": "lowest documents.id among eligible rows (ADR-072 resolution order); "
                         "ownership never influences selection",
        "eligibility": ELIGIBLE_SQL,
        "dependencies_checked": len(deps),
        "unregistered_dependencies": sorted({d["table"] for d in deps if d["strategy"] is None}),
        "total_duplicate_groups": len(analyzed),
        "total_document_rows_in_groups": sum(g["row_count"] for g in analyzed),
        "excess_duplicate_rows": sum(g["excess_rows"] for g in analyzed),
        "safe_auto_merge_groups": by_class[SAFE],
        "review_required_groups": by_class[REVIEW],
        "blocked_groups": by_class[BLOCKED],
        "total_proposed_reassignments": sum(g["total_reassignments"] for g in analyzed),
        "total_rows_eventually_retired": sum(g["excess_rows"] for g in analyzed),
        "provenance_tuples_preserved": sum(g["provenance"]["preserved_after_merge"] for g in analyzed),
        "provenance_rows_seen": sum(g["provenance"]["rows"] for g in analyzed),
        "reassignments_by_table": dict(sorted(dependent_totals.items())),
        "instrumentation": {
            "sql_query_count": q.count,
            "duplicate_groups_processed": len(analyzed),
            "duplicate_document_rows_processed": len(all_ids),
            "elapsed_seconds": elapsed,
            "batch_size": batch_size,
            "id_batches": (len(all_ids) + batch_size - 1) // batch_size if all_ids else 0,
        },
        "groups": analyzed,
    }
