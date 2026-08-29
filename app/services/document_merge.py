"""Canonical document merge PREVIEW  -  read-only, database-only (ADR-072 consolidation).

One canonical ``documents`` row per content hash is what ADR-072 already produces at ingest
(``document_sources.resolve_or_create_canonical``). Rows created before that, or by paths that
bypassed it, left duplicate content behind. This module reports what consolidating them WOULD
require. It executes nothing.

WHAT THIS MODULE WILL NEVER DO
  * write to the database  -  every connection is ``engine.connect()``, never ``engine.begin()``;
  * touch the filesystem, a storage backend, SharePoint, TaxDome or any connector;
  * run or re-run OCR, classification or extraction;
  * decide client identity. Drake is the authority for identity resolution
    (``source_contacts`` -> ``drake_identity`` -> ``drake_identity_match_candidates``). This layer
    merges DOCUMENTS by content hash and never reads or writes that chain. Where duplicates
    disagree about their owner, that is reported as conflict evidence for a human  -  provenance is
    not authority, and a document merge must not make an identity decision as a side effect.

SURVIVOR SELECTION IS MECHANICAL
  The proposed survivor is the LOWEST ``documents.id`` among the group's eligible rows  -  exactly
  what ``resolve_or_create_canonical`` resolves to today. Ownership deliberately does NOT influence
  it: letting an owned duplicate win would make canonicalization an implicit identity decision.
  Ownership is compared AFTERWARDS and reported as evidence.

ELIGIBILITY
  ``documents.status != 'deleted'``  -  the same predicate the ADR-072 resolver uses, and a real
  schema-backed state (``ck_documents_status`` admits 'deleted').

THE REGISTRY IS VERIFIED AGAINST THE LIVE SCHEMA
  ``_dependencies()`` reads every FK to ``documents.id`` from ``information_schema`` at run time and
  pairs it with a declared strategy. A reference the registry does not know about does not produce a
  warning  -  it BLOCKS the group, because an unknown dependency is exactly the case where a merge
  would silently lose data.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from app.db import engine

#: Eligibility predicate  -  identical to ``resolve_or_create_canonical``'s.
ELIGIBLE_SQL = "status <> 'deleted'"

SAFE, REVIEW, BLOCKED = "SAFE_AUTO_MERGE", "REVIEW_REQUIRED", "BLOCKED"
#: A hash group whose members belong to DIFFERENT owners. Identical content legitimately held by
#: two clients is not a duplicate to resolve - it is the same file reused. No retirement is ever
#: proposed for these, and they are counted apart from the merge population.
SHARED = "SHARED_CONTENT"

# --- reason taxonomy ------------------------------------------------------------------------------
# Every conflict, blocker and advisory the preview can emit carries a stable CODE, so the non-safe
# population can be counted by exact cause. Severity decides classification:
#   blocker  -> BLOCKED          conflict -> REVIEW_REQUIRED          advisory -> no effect
# An advisory is real, reportable evidence that does NOT make a merge unsafe (e.g. rows that simply
# need repointing). Advisories can therefore appear on SAFE_AUTO_MERGE groups.
BLOCKER, CONFLICT, ADVISORY = "blocker", "conflict", "advisory"
#: SHAPE describes what a hash group IS, not a problem with it. Shapes never gate a merge and never
#: appear as a primary reason, so blocker/conflict totals keep reconciling to the class counts.
SHAPE = "shape"

#: code -> (severity, human description). Ordered: PRIMARY reason selection walks this top-down, so
#: the listing order IS the precedence and the primary reason is deterministic.
REASONS: dict[str, tuple[str, str]] = {
    # --- blockers -------------------------------------------------------------------------------
    "unknown_dependency": (BLOCKER, "a reference to documents.id with no declared strategy"),
    # NOTE: the former ownership_* BLOCKERS are gone by construction. A merge partition is keyed on
    # the exact (person_id, household_id, organization_id) tuple, so every member of a partition has
    # the SAME owner and an ownership mismatch cannot arise inside one. Differing owners now split
    # the hash group into partitions and are described by the SHAPE codes below.
    # --- conflicts ------------------------------------------------------------------------------
    "ocr_conflict": (CONFLICT, "extracted-text state differs materially"),
    "classification_conflict": (CONFLICT, "document_classifications doc_type differs"),
    "fact_conflict": (CONFLICT, "same fact_type asserts different current values"),
    "category_conflict": (CONFLICT, "documents.category differs"),
    "document_classification_conflict": (CONFLICT, "documents.classification differs"),
    "version_chain_self_reference": (CONFLICT, "a version row links two members of this group"),
    # --- advisories (reported, never change the classification) ---------------------------------
    "ocr_equivalent_duplicate": (ADVISORY, "several equivalent OCR rows; one survives, rest redundant"),
    "classification_equivalent_duplicate": (ADVISORY, "several equivalent classification rows"),
    "facts_equivalent_duplicate": (ADVISORY, "the same current fact recorded on several members"),
    "document_sources_collision": (ADVISORY,
                                   "identical (source_system, source_uri) on several members; after "
                                   "reassignment they dedupe to one row, losing no provenance"),
    "document_relationships_redundant": (ADVISORY,
                                         "the same (entity_type, entity_id) on several members"),
    "document_relationships_multi_entity": (ADVISORY,
                                            "members relate to DIFFERENT entities; the survivor "
                                            "inherits both  -  additive, not contradictory"),
    "set_null_reassignment_required": (ADVISORY,
                                       "ON DELETE SET NULL references must be repointed, never nulled"),
    "no_action_reassignment_required": (ADVISORY,
                                        "ON DELETE NO ACTION references must be repointed before retire"),
    "soft_reference_present": (ADVISORY, "a document_id column carrying no FK (e.g. rm_document_status)"),
    # --- shapes (describe the hash group; never gate a merge) ------------------------------------
    "shared_content_cross_person": (SHAPE,
        "identical content held by DIFFERENT people - the same file reused, not a duplicate"),
    "shared_content_cross_household": (SHAPE,
        "identical content held by DIFFERENT households"),
    "shared_content_cross_organization": (SHAPE,
        "identical content held by DIFFERENT organizations"),
    "shared_content_mixed_dimensions": (SHAPE,
        "owners differ on more than one dimension (person / household / organization)"),
    "shared_content_unowned": (SHAPE,
        "an unowned copy sits alongside owned copies; ownership is never inferred from a hash"),
    "partial_merge_with_preserved_copies": (SHAPE,
        "the group holds BOTH owner-local duplicates that may merge AND copies belonging to other "
        "owners that must be preserved"),
    "single_owner_duplicate_group": (SHAPE,
        "every member has the same owner - an ordinary duplicate group"),
}

_BLOCKER_CODES = tuple(c for c, (sev, _) in REASONS.items() if sev == BLOCKER)
_CONFLICT_CODES = tuple(c for c, (sev, _) in REASONS.items() if sev == CONFLICT)
SHAPE_CODES = tuple(c for c, (sev, _) in REASONS.items() if sev == SHAPE)
#: Shapes that mean "content is shared across owners", i.e. rows that must be PRESERVED.
CROSS_OWNER_SHAPES = tuple(c for c in SHAPE_CODES if c.startswith("shared_content_"))


def _reason(code, *, detail=None, document_ids=None, **extra) -> dict:
    severity, description = REASONS[code]
    r = {"code": code, "severity": severity, "description": description}
    if detail:
        r["detail"] = detail
    if document_ids:
        r["document_ids"] = sorted(int(i) for i in document_ids)[:10]   # representative sample
    r.update(extra)
    return r


# --- dependency strategies ------------------------------------------------------------------------
# reassign       -  repoint document_id to the survivor. Nothing document-scoped can collide.
# singular       -  UNIQUE(document_id): at most one row may survive. Substantive values are COMPARED;
#                 equivalent rows are deduplicable, genuinely different ones need review.
# dedup_keyed    -  UNIQUE(document_id, <keys>): reassign, and where the survivor already holds the
#                 same key the duplicate's row is a redundant copy of the same statement.
# provenance     -  document_sources. Every DISTINCT (source_system, source_uri) tuple must survive;
#                 a collision after reassignment means the two rows assert the SAME source
#                 relationship, so it is deduplicated, never discarded.
# read_model     -  a disposable rm_* projection row: rebuilt from the event stream, never moved.
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

#: Every FK to documents.id, read from the LIVE catalog on each call  -  still true runtime
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

#: Soft references  -  a document_id column carrying no database FK. Verified by
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

def duplicate_hash_groups(conn, q, *, limit=None, shas=None) -> list[dict]:
    """SHA-256 groups holding more than one ELIGIBLE document row. ONE query for the whole corpus.

    ``shas`` restricts the SCAN to specific hashes. It narrows which groups are read; it changes
    nothing about how a group is analysed, so a group's verdict is identical either way. The
    executor uses it to revalidate one batch without re-scanning the whole corpus."""
    params: dict = {}
    where = f"{ELIGIBLE_SQL} AND sha256 IS NOT NULL"
    if shas is not None:
        where += " AND sha256 = ANY(:shas)"
        params["shas"] = sorted(set(shas))
    stmt = (f"SELECT sha256, count(*) AS n, array_agg(id ORDER BY id) AS ids "
            f"FROM documents WHERE {where} "
            f"GROUP BY sha256 HAVING count(*) > 1 ORDER BY min(id)")
    if limit:
        stmt += f" LIMIT {int(limit)}"
    return [{"sha256": r["sha256"], "row_count": int(r["n"]),
             "document_ids": [int(i) for i in r["ids"]]} for r in q.rows(conn, stmt, params)]


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
    # Which DIMENSION(S) actually disagree  -  a person mismatch and a household mismatch are
    # different problems and must be counted separately.
    dims = {}
    for dim in ("person_id", "household_id", "organization_id"):
        vals = {d[dim] for d in docs if d[dim] is not None}
        dims[dim] = {"distinct_values": len(vals),
                     "values": sorted(vals),
                     "unset_members": sum(1 for d in docs if d[dim] is None)}
    return {"owned_documents": owners, "distinct_owner_count": len(distinct),
            "unowned_count": len(docs) - len(owners), "dimensions": dims,
            "conflicting_dimensions": sorted(k for k, v in dims.items() if v["distinct_values"] > 1)}


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
    discards no provenance  -  the relationship is still recorded, once."""
    tuples = {(r["source_system"], r["source_uri"]) for r in rows}
    systems = sorted({r["source_system"] for r in rows})
    return {"rows": len(rows), "distinct_provenance_tuples": len(tuples),
            "source_systems": systems,
            "redundant_rows": len(rows) - len(tuples),
            "preserved_after_merge": len(tuples)}


def _relationship_evidence(rows) -> dict:
    """document_relationships across the group.

    Members pointing at DIFFERENT entities is additive, not contradictory: the surviving document
    genuinely relates to both, and UNIQUE(document_id, entity_type, entity_id) admits both rows
    after reassignment. Members pointing at the SAME entity are one relationship recorded twice and
    dedupe on that constraint. Neither is a merge blocker; both are reported."""
    pairs = {(r["entity_type"], r["entity_id"]) for r in rows}
    return {"rows": len(rows), "distinct_entities": len(pairs),
            "redundant_rows": len(rows) - len(pairs),
            "entity_types": sorted({r["entity_type"] for r in rows})}


def _source_collisions(rows) -> int:
    """(source_system, source_uri) tuples asserted by more than one member. After reassignment these
    collide on uq_document_source_ref and dedupe to a single row  -  provenance is kept, not lost."""
    seen, collided = set(), set()
    for r in rows:
        key = (r["source_system"], r["source_uri"])
        (collided if key in seen else seen).add(key)
    return len(collided)


# --- per-group analysis --------------------------------------------------------------------------------

def _owner_key(doc) -> tuple:
    """The MERGE SCOPE key: the exact ownership tuple.

    Two documents may only be considered duplicates of each other when all three ownership
    dimensions match exactly. Anything else is a different scope: identical content held by a
    different person, household or organization is the same file reused, not a duplicate to
    resolve. An unowned document forms its own scope - a matching hash NEVER implies it belongs to
    the owner of another copy, because content does not establish identity (Drake does)."""
    return (doc.get("person_id"), doc.get("household_id"), doc.get("organization_id"))


def _owner_label(key) -> dict:
    person, household, organization = key
    return {"person_id": person, "household_id": household, "organization_id": organization,
            "unowned": person is None and household is None and organization is None}


def _group_shape(partitions) -> str:
    """What this hash group IS. Purely descriptive; never gates a merge."""
    owned = [p for p in partitions if not p["owner"]["unowned"]]
    mergeable = [p for p in partitions if p["mergeable"]]
    if len(partitions) == 1:
        return "single_owner_duplicate_group"
    if mergeable and len(partitions) > len(mergeable):
        return "partial_merge_with_preserved_copies"
    dims = [d for d in ("person_id", "household_id", "organization_id")
            if len({p["owner"][d] for p in owned if p["owner"][d] is not None}) > 1]
    if len(dims) > 1:
        return "shared_content_mixed_dimensions"
    if dims == ["person_id"]:
        return "shared_content_cross_person"
    if dims == ["household_id"]:
        return "shared_content_cross_household"
    if dims == ["organization_id"]:
        return "shared_content_cross_organization"
    return "shared_content_unowned"


def _analyze_partition(sha, key, member_docs, deps, pre, counts) -> dict:
    """One ownership-scoped merge candidate. Every member here has the SAME owner."""
    ids = [d["id"] for d in member_docs]
    survivor = min(ids)                    # lowest eligible id WITHIN the partition. No owner bias:
    duplicates = sorted(i for i in ids if i != survivor)   # the owner is identical for all of them.
    mergeable = len(ids) > 1

    def _rows(bucket):
        return [r for i in ids for r in pre[bucket].get(i, [])]

    scoped = {k: {did: n for did, n in per.items() if did in set(ids)}
              for k, per in counts.items()}
    scoped = {k: v for k, v in scoped.items() if v}
    unknown = sorted({d["table"] for d in deps if d["strategy"] is None
                      and scoped.get(f"{d['table']}.{d['column']}")})

    ocr = _singular_conflict(_rows("ocr"), ("status", "char_count"))
    classification = _singular_conflict(_rows("classifications"), ("doc_type",))
    facts = _fact_conflicts(_rows("facts"))
    provenance = _provenance(_rows("sources"))
    relationships = _relationship_evidence(_rows("relationships"))
    source_collisions = _source_collisions(_rows("sources"))
    version_self_refs = [r for i in ids for r in pre["version_self_reference"].get(i, [])
                         if r["previous_document_id"] in set(ids)]

    distinct_categories = {d["category"] for d in member_docs if d["category"]}
    distinct_classification = {d["classification"] for d in member_docs if d["classification"]}

    reassignments = {}
    for d in deps:
        k = f"{d['table']}.{d['column']}"
        n = sum(v for did, v in scoped.get(k, {}).items() if did != survivor)
        if n:
            reassignments[k] = {"rows": n, "strategy": d["strategy"],
                                "delete_rule": d["delete_rule"]}

    reasons: list[dict] = []
    if mergeable:
        if unknown:
            reasons.append(_reason("unknown_dependency", detail=f"tables: {', '.join(unknown)}",
                                   document_ids=ids, tables=unknown))
        if ocr["conflict"]:
            reasons.append(_reason("ocr_conflict", document_ids=ocr.get("document_ids"),
                                   distinct_values=ocr["distinct_values"]))
        if classification["conflict"]:
            reasons.append(_reason("classification_conflict",
                                   document_ids=classification.get("document_ids"),
                                   distinct_values=classification["distinct_values"]))
        if facts["conflict"]:
            reasons.append(_reason("fact_conflict", document_ids=ids,
                                   fact_types=facts["conflicting_fact_types"]))
        if len(distinct_categories) > 1:
            reasons.append(_reason("category_conflict", document_ids=ids,
                                   values=sorted(distinct_categories)))
        if len(distinct_classification) > 1:
            reasons.append(_reason("document_classification_conflict", document_ids=ids,
                                   values=sorted(distinct_classification)))
        if version_self_refs:
            reasons.append(_reason("version_chain_self_reference", document_ids=ids,
                                   rows=version_self_refs))
        if ocr["rows"] > 1 and not ocr["conflict"]:
            reasons.append(_reason("ocr_equivalent_duplicate", document_ids=ocr.get("document_ids"),
                                   rows=ocr["rows"]))
        if classification["rows"] > 1 and not classification["conflict"]:
            reasons.append(_reason("classification_equivalent_duplicate",
                                   document_ids=classification.get("document_ids"),
                                   rows=classification["rows"]))
        if facts["redundant_rows"]:
            reasons.append(_reason("facts_equivalent_duplicate", document_ids=ids,
                                   rows=facts["redundant_rows"]))
        if source_collisions:
            reasons.append(_reason("document_sources_collision", document_ids=ids,
                                   colliding_tuples=source_collisions))
        if relationships["redundant_rows"]:
            reasons.append(_reason("document_relationships_redundant", document_ids=ids,
                                   rows=relationships["redundant_rows"]))
        if relationships["distinct_entities"] > 1:
            reasons.append(_reason("document_relationships_multi_entity", document_ids=ids,
                                   distinct_entities=relationships["distinct_entities"]))
        for rule, code in (("SET NULL", "set_null_reassignment_required"),
                           ("NO ACTION", "no_action_reassignment_required"),
                           ("SOFT", "soft_reference_present")):
            rows = sum(v["rows"] for v in reassignments.values() if v["delete_rule"] == rule)
            if rows:
                reasons.append(_reason(code, document_ids=ids, rows=rows))

    blockers = [r for r in reasons if r["severity"] == BLOCKER]
    conflicts = [r for r in reasons if r["severity"] == CONFLICT]
    codes = {r["code"] for r in reasons}
    primary = next((c for c in REASONS if c in codes and REASONS[c][0] not in (ADVISORY, SHAPE)),
                   None)
    if not mergeable:
        result = None                       # a single-document scope is not a merge candidate
    else:
        result = BLOCKED if blockers else (REVIEW if conflicts else SAFE)

    return {
        "sha256": sha,
        "owner": _owner_label(key),
        "mergeable": mergeable,
        "classification": result,
        "proposed_survivor": survivor if mergeable else None,
        "member_document_ids": sorted(ids),
        "member_count": len(ids),
        "duplicate_document_ids": duplicates if mergeable else [],
        "excess_rows": len(duplicates) if mergeable else 0,
        "rows_preserved": 0 if mergeable else len(ids),
        "dependent_row_counts": {k: dict(sorted(v.items())) for k, v in sorted(scoped.items())},
        "reassignments_required": reassignments if mergeable else {},
        "total_reassignments": sum(v["rows"] for v in reassignments.values()) if mergeable else 0,
        "provenance": provenance,
        "ocr": ocr, "classification_rows": classification, "facts": facts,
        "relationships": relationships, "source_collisions": source_collisions,
        "version_self_references": version_self_refs,
        "reasons": reasons, "reason_codes": sorted(codes), "primary_reason": primary,
        "conflicts": conflicts, "blockers": blockers,
        "advisories": [r for r in reasons if r["severity"] == ADVISORY],
    }


def analyze_group(group, deps, pre) -> dict:
    """One physical SHA group, PARTITIONED BY OWNERSHIP SCOPE. Issues NO query.

    A hash group is no longer a single merge decision. It is partitioned on the exact ownership
    tuple, and each partition is evaluated independently. Copies belonging to different owners are
    never proposed for retirement merely because their content matches - that would let a content
    hash decide client identity, which is Drake's authority, not this layer's."""
    ids = group["document_ids"]
    docs = [pre["documents"][i] for i in sorted(ids) if i in pre["documents"]]

    counts = {key: {did: n for did, n in per.items() if did in set(ids)}
              for key, per in pre["dependency_counts"].items()}
    counts = {k: v for k, v in counts.items() if v}

    buckets: dict[tuple, list] = defaultdict(list)
    for d in docs:
        buckets[_owner_key(d)].append(d)
    partitions = [_analyze_partition(group["sha256"], key, members, deps, pre, counts)
                  for key, members in sorted(buckets.items(), key=lambda kv: min(
                      d["id"] for d in kv[1]))]

    mergeable = [p for p in partitions if p["mergeable"]]
    ownership = _ownership_evidence(docs)
    shape = _group_shape(partitions)

    if not mergeable:
        classification = SHARED
    elif any(p["classification"] == BLOCKED for p in mergeable):
        classification = BLOCKED
    elif any(p["classification"] == REVIEW for p in mergeable):
        classification = REVIEW
    else:
        classification = SAFE

    all_sources = [r for i in ids for r in pre["sources"].get(i, [])]

    # Convenience view for the common single-scope case: when exactly ONE partition can merge, its
    # decision IS the group's decision. With several merge partitions there is no single survivor,
    # and proposed_survivor is None deliberately - a caller must read `partitions`.
    solo = mergeable[0] if len(mergeable) == 1 else None
    aggregate_reassignments: dict[str, dict] = {}
    for p in mergeable:
        for k, info in p["reassignments_required"].items():
            acc = aggregate_reassignments.setdefault(
                k, {"rows": 0, "strategy": info["strategy"], "delete_rule": info["delete_rule"]})
            acc["rows"] += info["rows"]

    return {
        "sha256": group["sha256"],
        "classification": classification,
        "shape": shape,
        "row_count": len(docs),
        "member_document_ids": sorted(d["id"] for d in docs),
        "partitions": partitions,
        "merge_partition_count": len(mergeable),
        "preserved_partition_count": len(partitions) - len(mergeable),
        "excess_rows": sum(p["excess_rows"] for p in mergeable),
        "rows_preserved_cross_owner": sum(p["rows_preserved"] for p in partitions),
        "total_reassignments": sum(p["total_reassignments"] for p in mergeable),
        "provenance": _provenance(all_sources),
        "ownership": ownership,
        "proposed_survivor": solo["proposed_survivor"] if solo else None,
        "duplicate_document_ids": sorted(i for p in mergeable for i in p["duplicate_document_ids"]),
        "survivor_rule": "lowest documents.id within the ownership-scoped partition",
        "dependent_row_counts": (solo["dependent_row_counts"] if solo
                                 else {k: dict(sorted(v.items()))
                                       for k, v in sorted(counts.items())}),
        "reassignments_required": aggregate_reassignments,
        "ocr": solo["ocr"] if solo else _singular_conflict(
            [r for i in ids for r in pre["ocr"].get(i, [])], ("status", "char_count")),
        "classification_rows": solo["classification_rows"] if solo else _singular_conflict(
            [r for i in ids for r in pre["classifications"].get(i, [])], ("doc_type",)),
        "facts": solo["facts"] if solo else _fact_conflicts(
            [r for i in ids for r in pre["facts"].get(i, [])]),
        "relationships": solo["relationships"] if solo else _relationship_evidence(
            [r for i in ids for r in pre["relationships"].get(i, [])]),
        "source_collisions": solo["source_collisions"] if solo else _source_collisions(all_sources),
        "version_self_references": solo["version_self_references"] if solo else [],
        "reason_codes": sorted({c for p in partitions for c in p["reason_codes"]} | {shape}),
        "primary_reason": next((p["primary_reason"] for p in mergeable if p["primary_reason"]),
                               None),
        "conflicts": [r for p in mergeable for r in p["conflicts"]],
        "blockers": [r for p in mergeable for r in p["blockers"]],
        "advisories": [r for p in partitions for r in p["advisories"]],
        "reasons": [r for p in partitions for r in p["reasons"]],
    }


# --- the preview -----------------------------------------------------------------------------------------

def preview(*, limit=None, batch_size=DEFAULT_BATCH_SIZE, conn=None, shas=None) -> dict:
    """READ-ONLY preview of canonical document consolidation. Performs ZERO writes.

    Query cost scales with (dependency tables x id batches), NOT with the number of duplicate
    groups: every dependent table is read once per batch across the WHOLE duplicate-document id
    set, and each group is then analysed in pure Python. A corpus with thousands of groups issues
    tens of statements, not tens of thousands.

    Deterministic: the same database state yields the same report, so the preview is idempotent.

    ``conn`` runs every read on the CALLER's connection, so a caller inside a transaction sees its
    own uncommitted state - this is how the executor revalidates after locking. ``shas`` restricts
    the scan. Neither changes how a group is partitioned or classified; both are plumbing. This
    function still performs ZERO writes on whichever connection it is given."""
    import contextlib
    import time

    q = _QueryCounter()
    started = time.monotonic()
    with (contextlib.nullcontext(conn) if conn is not None else engine.connect()) as conn:
        deps = _dependencies(conn)
        groups = duplicate_hash_groups(conn, q, limit=limit, shas=shas)
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
            "relationships": _prefetch_by_document(conn, q,
                "SELECT document_id, entity_type, entity_id FROM document_relationships "
                "WHERE document_id = ANY(:ids)", all_ids, batch_size),
            "sources": _prefetch_by_document(conn, q,
                "SELECT document_id, source_system, source_uri, source_path, source_external_id "
                "FROM document_sources WHERE document_id = ANY(:ids)", all_ids, batch_size),
            "version_self_reference": _prefetch_version_self_reference(conn, q, all_ids, batch_size),
        } if all_ids else {k: {} for k in ("documents", "dependency_counts", "ocr",
                                           "classifications", "facts", "relationships", "sources",
                                           "version_self_reference")}

        analyzed = [analyze_group(g, deps, pre) for g in groups]   # pure Python, no queries
    elapsed = round(time.monotonic() - started, 3)

    by_class = {SAFE: 0, REVIEW: 0, BLOCKED: 0, SHARED: 0}
    for g in analyzed:
        by_class[g["classification"]] += 1
    partitions = [p for g in analyzed for p in g["partitions"]]
    merge_partitions = [p for p in partitions if p["mergeable"]]
    partition_class = {SAFE: 0, REVIEW: 0, BLOCKED: 0}
    for p in merge_partitions:
        partition_class[p["classification"]] += 1
    shapes: dict[str, int] = defaultdict(int)
    for g in analyzed:
        shapes[g["shape"]] += 1

    dependent_totals: dict[str, int] = defaultdict(int)
    for g in analyzed:
        for key, info in g["reassignments_required"].items():
            dependent_totals[key] += info["rows"]

    # --- reason aggregation --------------------------------------------------------------------
    # (a) groups CONTAINING each reason  -  a group may carry several, so these overlap and their
    #     sum deliberately exceeds the group count.
    # (b) PRIMARY reason  -  mutually exclusive, one per non-safe group, so the blocker totals
    #     reconcile exactly to blocked_groups and the conflict totals to review_required_groups.
    containing: dict[str, int] = defaultdict(int)
    primary_counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[int]] = {}
    for g in analyzed:
        for code in g["reason_codes"]:
            containing[code] += 1
        for r in g["reasons"]:
            if r["code"] not in examples and r.get("document_ids"):
                examples[r["code"]] = r["document_ids"][:5]
        if g["primary_reason"]:
            primary_counts[g["primary_reason"]] += 1

    def _rows(codes):
        return [{"code": c, "severity": REASONS[c][0], "description": REASONS[c][1],
                 "groups_containing": containing.get(c, 0),
                 "groups_primary": primary_counts.get(c, 0),
                 "example_document_ids": examples.get(c, [])}
                for c in codes if containing.get(c)]

    blocked_primary = sum(primary_counts.get(c, 0) for c in _BLOCKER_CODES)
    review_primary = sum(primary_counts.get(c, 0) for c in _CONFLICT_CODES)
    physical_rows = sum(g["row_count"] for g in analyzed)
    eligible = sum(g["excess_rows"] for g in analyzed)
    cross_owner_preserved = sum(g["rows_preserved_cross_owner"] for g in analyzed)

    reason_report = {
        "blockers": _rows(_BLOCKER_CODES),
        "conflicts": _rows(_CONFLICT_CODES),
        "advisories": _rows([c for c, (sev, _) in REASONS.items() if sev == ADVISORY]),
        "primary_totals": {"blocked": blocked_primary, "review_required": review_primary},
        "reconciles": (blocked_primary == by_class[BLOCKED]
                       and review_primary == by_class[REVIEW]),
        "unreported_codes": sorted(set(containing) - set(REASONS)),
    }

    return {
        "read_only": True,
        "wrote_anything": False,
        "survivor_rule": "lowest documents.id among eligible rows WITHIN one ownership-scoped "
                         "partition (ADR-072 resolution order); ownership never influences "
                         "selection",
        "eligibility": ELIGIBLE_SQL,
        "dependencies_checked": len(deps),
        "unregistered_dependencies": sorted({d["table"] for d in deps if d["strategy"] is None}),
        # --- PHYSICAL CONTENT POPULATION ------------------------------------------------------
        # Hash groups exactly as they sit in the table. These describe CONTENT, not merge work.
        # physical_sha_excess_rows is what a naive global-SHA merge would have retired; it is
        # reported for contrast only and is NEVER actionable. The actionable number is
        # rows_eligible_for_retirement, which is always <= it.
        "physical_sha_groups": len(analyzed),
        "physical_sha_document_rows": physical_rows,
        "physical_sha_excess_rows": physical_rows - len(analyzed),
        # --- OWNERSHIP-SCOPED MERGE POPULATION ------------------------------------------------
        # What may actually merge. A hash group splits into one partition per distinct ownership
        # tuple; only partitions holding 2+ documents are merge candidates. These three reconcile:
        #   physical_sha_document_rows
        #     = ownership_scoped_merge_groups (one survivor each)
        #     + rows_eligible_for_retirement
        #     + cross_owner_rows_preserved
        "ownership_partitions": len(partitions),
        "ownership_scoped_merge_groups": len(merge_partitions),
        "rows_eligible_for_retirement": eligible,
        # Every row NOT proposed for retirement: partition survivors PLUS cross-owner copies.
        "rows_preserved": physical_rows - eligible,
        # The subset preserved specifically because no other row shares their ownership scope.
        "cross_owner_rows_preserved": cross_owner_preserved,
        # Group-level classes (a group is SHARED_CONTENT when no partition can merge).
        "safe_auto_merge_groups": by_class[SAFE],
        "review_required_groups": by_class[REVIEW],
        "blocked_groups": by_class[BLOCKED],
        "shared_content_groups": by_class[SHARED],
        # Partition-level classes - the population an executor would ever act on.
        "merge_partitions_safe": partition_class[SAFE],
        "merge_partitions_review_required": partition_class[REVIEW],
        "merge_partitions_blocked": partition_class[BLOCKED],
        "groups_by_shape": dict(sorted(shapes.items())),
        # --- DEPENDENCY / PROVENANCE ----------------------------------------------------------
        # "proposed": no executor exists, so nothing has been or will be retired by this run.
        "total_proposed_reassignments": sum(g["total_reassignments"] for g in analyzed),
        "proposed_retirement_rows": eligible,
        "provenance_tuples_preserved": sum(g["provenance"]["preserved_after_merge"] for g in analyzed),
        "provenance_rows_seen": sum(g["provenance"]["rows"] for g in analyzed),
        "reassignments_by_table": dict(sorted(dependent_totals.items())),
        "reasons": reason_report,
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


# --- blocker diagnostics -------------------------------------------------------------------------
# Read-only enrichment of the BLOCKED population so ownership blockers can be reviewed without
# hand-querying ids. It changes NO classification: it re-reads the same preview() result and adds
# human-readable labels, provenance and shape evidence. Every read is batched, exactly as preview().

#: How many members/owners/sources a TEXT rendering shows before truncating. Full data is always
#: present in the returned structure (and therefore in --output-json / --output-csv).
DEFAULT_SAMPLE = 5

#: What blocked_details() can be filtered on: the group SHAPE codes (which describe how the copies
#: are spread across owners) plus every partition-level BLOCKER. Ownership can no longer BLOCK a
#: merge - the partition key IS the ownership tuple - so cross-owner cases surface as shapes.
DETAIL_REASON_CODES = tuple(sorted(
    set(SHAPE_CODES) | {c for c, (sev, _) in REASONS.items() if sev == BLOCKER}))


def _prefetch_labels(conn, q, table, name_expr, ids, batch) -> dict[int, str]:
    """id -> display label for an owner table. Empty when there are no ids."""
    out: dict[int, str] = {}
    ids = [i for i in ids if i is not None]
    if not ids:
        return out
    for chunk in _in_batches(sorted(set(ids)), batch):
        for r in q.rows(conn, f"SELECT id, {name_expr} AS label FROM {table} WHERE id = ANY(:ids)",
                        {"ids": chunk}):
            out[int(r["id"])] = r["label"]
    return out


def _ownership_shape(members) -> dict:
    """FACTUAL evidence about why several owners appear. It deliberately draws no conclusion.

    Deciding whether a group is one generic file ingested per client, or competing assignments for
    a client-specific document, needs human judgement about the content. Filenames alone are not
    evidence, so this reports counts and lets the reviewer decide."""
    owned = [m for m in members if m["person_id"] or m["household_id"] or m["organization_id"]]
    owner_keys = [(m["person_id"], m["household_id"], m["organization_id"]) for m in owned]
    per_owner: dict[tuple, int] = defaultdict(int)
    for k in owner_keys:
        per_owner[k] += 1
    uris = {s["source_uri"] for m in members for s in m["sources"] if s["source_uri"]}
    systems = {s["source_system"] for m in members for s in m["sources"]}
    max_per_owner = max(per_owner.values(), default=0)

    notes = []
    if owned and len(per_owner) == len(owned) and max_per_owner == 1:
        notes.append("every owned member has a DISTINCT owner and appears once - consistent with "
                     "one shared/generic file ingested separately per client (needs confirmation)")
    if max_per_owner > 1:
        notes.append(f"at least one owner holds {max_per_owner} members - consistent with competing "
                     "assignments for the same client (needs confirmation)")
    if len(members) - len(owned):
        notes.append(f"{len(members) - len(owned)} member(s) carry no owner at all")
    if len(uris) == 1 and len(members) > 1:
        notes.append("all members share ONE source_uri - the same stored location was recorded more "
                     "than once")
    if len(systems) > 1:
        notes.append(f"members arrived from {len(systems)} different source systems")

    return {
        "member_count": len(members),
        "owned_members": len(owned),
        "unowned_members": len(members) - len(owned),
        "distinct_owners": len(per_owner),
        "max_members_per_owner": max_per_owner,
        "every_member_a_distinct_owner": bool(owned) and len(per_owner) == len(owned)
                                         and max_per_owner == 1,
        "any_owner_with_multiple_members": max_per_owner > 1,
        "distinct_source_uris": len(uris),
        "distinct_source_systems": sorted(systems),
        "evidence_notes": notes,          # observations only - never a verdict
    }


def blocked_details(*, reasons=None, limit=None, batch_size=DEFAULT_BATCH_SIZE,
                    report=None) -> dict:
    """READ-ONLY detail for every BLOCKED duplicate group. Performs ZERO writes.

    ``reasons`` filters by primary reason code (default: all ownership blockers). ``report`` reuses
    an existing preview() result instead of recomputing it. Classification is never recalculated -
    the groups and their verdicts come straight from preview()."""
    report = report or preview(limit=limit, batch_size=batch_size)
    wanted = tuple(reasons) if reasons else None
    groups = [g for g in report["groups"]
              if g["classification"] in (BLOCKED, SHARED)
              and (wanted is None
                   or not {g["primary_reason"], g["shape"]}.isdisjoint(wanted))]

    doc_ids = sorted({i for g in groups for i in g["member_document_ids"]})
    q = _QueryCounter()
    with engine.connect() as conn:
        docs = _prefetch_documents(conn, q, doc_ids, batch_size)
        sources = _prefetch_by_document(conn, q,
            "SELECT document_id, source_system, source_uri, source_path, source_external_id "
            "FROM document_sources WHERE document_id = ANY(:ids)", doc_ids, batch_size)
        people_labels = _prefetch_labels(conn, q, "people",
            "COALESCE(NULLIF(full_name, ''), NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''), "
            "'person ' || id::text)", [d.get("person_id") for d in docs.values()], batch_size)
        household_labels = _prefetch_labels(conn, q, "households",
            "COALESCE(NULLIF(name, ''), 'household ' || id::text)",
            [d.get("household_id") for d in docs.values()], batch_size)
        org_labels = _prefetch_labels(conn, q, "relationship_entities",
            "COALESCE(NULLIF(name, ''), 'organization ' || id::text)",
            [d.get("organization_id") for d in docs.values()], batch_size)

    detailed = []
    for g in groups:
        ids = list(g["member_document_ids"])
        retirable = set(g["duplicate_document_ids"])
        survivors = {p["proposed_survivor"] for p in g["partitions"] if p["mergeable"]}
        members = []
        for did in ids:
            d = docs.get(did, {})
            members.append({
                "document_id": did,
                "is_survivor": did in survivors,
                # Preserved = kept exactly as-is because no other copy shares its ownership scope.
                "proposed_for_retirement": did in retirable,
                "preserved": did not in retirable,
                "original_name": d.get("original_name"),
                "category": d.get("category"),
                "classification": d.get("classification"),
                "subcategory": d.get("subcategory"),
                "status": d.get("status"),
                "person_id": d.get("person_id"),
                "person_name": people_labels.get(d.get("person_id")),
                "household_id": d.get("household_id"),
                "household_name": household_labels.get(d.get("household_id")),
                "organization_id": d.get("organization_id"),
                "organization_name": org_labels.get(d.get("organization_id")),
                "sources": [{"source_system": s["source_system"], "source_uri": s["source_uri"],
                             "source_path": s["source_path"],
                             "source_external_id": s["source_external_id"]}
                            for s in sources.get(did, [])],
            })
        source_rows = sum(len(m["sources"]) for m in members)
        detailed.append({
            "sha256": g["sha256"],
            "classification": g["classification"],
            "shape": g["shape"],
            "primary_reason": g["primary_reason"],
            "reason_codes": g["reason_codes"],
            "proposed_survivor": g["proposed_survivor"],
            "merge_partition_count": g["merge_partition_count"],
            "preserved_partition_count": g["preserved_partition_count"],
            "rows_preserved_cross_owner": g["rows_preserved_cross_owner"],
            "member_document_ids": ids,
            "member_count": len(ids),
            "excess_rows": g["excess_rows"],
            "source_record_count": source_rows,
            "conflicting_dimensions": g["ownership"]["conflicting_dimensions"],
            "ownership_shape": _ownership_shape(members),
            "members": members,
        })

    detailed.sort(key=lambda x: (-x["member_count"], x["sha256"]))
    return {
        "read_only": True,
        "wrote_anything": False,
        "filter_reasons": list(wanted) if wanted else sorted(DETAIL_REASON_CODES),
        "classification_source": "preview() - verdicts are reused, never recomputed",
        "summary": _blocked_summary(detailed, report),
        "groups": detailed,
        "instrumentation": {"sql_query_count": q.count,
                            "blocked_groups_detailed": len(detailed),
                            "documents_read": len(doc_ids),
                            "batch_size": batch_size},
    }


def _blocked_summary(detailed, report) -> dict:
    by_reason: dict[str, dict[str, int]] = defaultdict(
        lambda: {"groups": 0, "document_rows": 0, "excess_rows": 0})
    systems, owner_distribution = set(), defaultdict(int)
    for g in detailed:
        b = by_reason[g["primary_reason"] or g["shape"] or "unknown"]
        b["groups"] += 1
        b["document_rows"] += g["member_count"]
        b["excess_rows"] += g["excess_rows"]
        systems.update(g["ownership_shape"]["distinct_source_systems"])
        owner_distribution[g["ownership_shape"]["distinct_owners"]] += 1
    largest = [{"sha256": g["sha256"], "member_count": g["member_count"],
                "primary_reason": g["primary_reason"] or g["shape"],
                "distinct_owners": g["ownership_shape"]["distinct_owners"]}
               for g in detailed[:20]]
    return {
        "blocked_groups_total": report["blocked_groups"],
        "shared_content_groups_total": report["shared_content_groups"],
        "groups_in_this_report": len(detailed),
        "rows_preserved_cross_owner": sum(g["rows_preserved_cross_owner"] for g in detailed),
        "by_classification": {
            k: sum(1 for g in detailed if g["classification"] == k) for k in (BLOCKED, SHARED)},
        "by_shape": {k: sum(1 for g in detailed if g["shape"] == k)
                     for k in sorted({g["shape"] for g in detailed})},
        "by_reason": {k: dict(v) for k, v in sorted(by_reason.items())},
        "groups_with_more_than_2_members": sum(1 for g in detailed if g["member_count"] > 2),
        "groups_with_more_than_10_members": sum(1 for g in detailed if g["member_count"] > 10),
        "groups_with_more_than_100_members": sum(1 for g in detailed if g["member_count"] > 100),
        "largest_20_groups": largest,
        "distinct_source_systems": sorted(systems),
        "distinct_owners_per_group": dict(sorted(owner_distribution.items())),
    }
