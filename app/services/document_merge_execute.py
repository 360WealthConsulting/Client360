"""Guarded executor for canonical document consolidation (ADR-072).

WHAT THIS DOES
    Retires ownership-scoped duplicate document ROWS and repoints their dependent rows onto the
    partition survivor. It is a DATABASE consolidation only: no file is deleted, moved, renamed or
    re-read, no filesystem is scanned, no external system is contacted.

THE BOUNDARY
    The unit of execution is an OWNERSHIP PARTITION, never a SHA group. A partition's key is the
    exact (person_id, household_id, organization_id) tuple, so every member of a partition has the
    same owner and a cross-owner retirement is unrepresentable rather than merely forbidden. This
    module never computes ownership or classification itself: it consumes document_merge.preview()
    and refuses anything that preview did not certify SAFE_AUTO_MERGE.

    Content NEVER establishes identity. A matching sha256 is not evidence that two documents belong
    to the same client - that is Drake's authority. SHARED_CONTENT, REVIEW_REQUIRED and BLOCKED
    partitions have no executable path at all.

HOW A RUN IS SHAPED
    plan()   read-only. Produces an execution plan with a precondition FINGERPRINT per partition.
    apply()  re-derives the preview inside the write transaction, AFTER locking the rows, and
             refuses any partition whose fingerprint moved. Default is dry-run; a real write needs
             an explicit apply flag plus matching expected counts.

WHAT "RETIRED" MEANS
    The established soft-delete of app/services/document_platform: documents.status becomes
    'deleted', deleted_at is stamped, a document_events row records 'merged_into_canonical', and a
    hash-chained audit event is appended. No schema change; no new lifecycle state.

    This choice is deliberate: document_merge.ELIGIBLE_SQL is "status <> 'deleted'", so a retired
    row leaves the eligible population and the preview stops proposing it - idempotency falls out
    of the existing model instead of needing a new column or a migration.

    THE MERGE IS NOT REVERSIBLE BY document_platform.restore().
    Restoring the document ROW is not restoring the merge. By the time a row is retired its
    dependent rows have been repointed onto the survivor and rows that duplicated information the
    survivor already held have been DELETED. The pre-merge dependency graph no longer exists as
    live rows, and restore() does not rebuild it - it would return an active document stripped of
    the dependents it used to own.

    What makes a merge reconstructable is the evidence captured in phase 1 BEFORE any write, and
    recorded in both the run result and the audit event: the original status/deleted_at and
    ownership of every participating document, every dependency action with original row ids and
    original reference values, the complete original row of everything deleted as redundant, and
    the provenance tuples as they stood. That is forensic evidence for a manual reconstruction.
    THERE IS NO ROLLBACK EXECUTOR, and this module does not pretend to be one.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import text

from app.db import engine
from app.security.audit import write_audit_event
from app.services.document_merge import (
    BLOCKED,
    REVIEW,
    SAFE,
    SHARED,
    preview,
)

#: Partitions per write transaction. A failure rolls back exactly one batch, never a partial one.
DEFAULT_BATCH_SIZE = 50

#: Only this classification is executable. Listed positively: anything absent cannot run.
EXECUTABLE_CLASSIFICATIONS = frozenset({SAFE})

#: Never executable, spelled out so the refusal is greppable and testable.
NON_EXECUTABLE_CLASSIFICATIONS = frozenset({REVIEW, BLOCKED, SHARED})

#: Final run status. It is derived from what actually happened to every PLANNED partition, not
#: from whether a batch raised: a run that applied some partitions and refused others is PARTIAL
#: even though no exception was ever thrown. Getting this wrong is what let a production run of
#: 1,068 applied / 931 refused print "APPLIED" and exit 0.
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_DRY_RUN = "DRY_RUN"

#: Process exit code per status. Guard/usage errors keep their own codes in the CLI.
EXIT_CODES = {STATUS_SUCCESS: 0, STATUS_DRY_RUN: 0, STATUS_PARTIAL: 3, STATUS_FAILED: 4}

AUDIT_ACTION = "document.merge.partition_applied"
AUDIT_ENTITY = "document_merge_partition"


class MergeExecutionError(RuntimeError):
    """Refusal. Raised BEFORE any write whenever a guard does not hold."""


class StalePlanError(MergeExecutionError):
    """The database moved since the plan was generated. The plan is rejected, never adapted."""


def _now():
    return datetime.now(UTC)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def partition_fingerprint(part: dict, group: dict) -> str:
    """Everything that must not move between plan and apply.

    Covers ownership tuple, sha, survivor, duplicates, classification, eligibility rule and the
    exact dependency shape. If ANY of these changed, the plan is stale and is refused."""
    return hashlib.sha256(_canonical({
        "sha256": group["sha256"],
        "owner": [part["owner"]["person_id"], part["owner"]["household_id"],
                  part["owner"]["organization_id"]],
        "survivor": part["proposed_survivor"],
        "duplicates": sorted(part["duplicate_document_ids"]),
        "classification": part["classification"],
        "reason_codes": sorted(part["reason_codes"]),
        "dependent_row_counts": part["dependent_row_counts"],
        "member_document_ids": sorted(part["member_document_ids"]),
    }).encode()).hexdigest()


def _partition_plan(part: dict, group: dict) -> dict:
    owner = part["owner"]
    return {
        "sha256": group["sha256"],
        "owner": {"person_id": owner["person_id"], "household_id": owner["household_id"],
                  "organization_id": owner["organization_id"], "unowned": owner["unowned"]},
        "survivor_document_id": part["proposed_survivor"],
        "duplicate_document_ids": sorted(part["duplicate_document_ids"]),
        "classification": part["classification"],
        "rows_to_retire": part["excess_rows"],
        "dependency_actions": {k: dict(sorted(v.items()))
                               for k, v in sorted(part["dependent_row_counts"].items())},
        "reassignments_required": {k: dict(v) for k, v in
                                   sorted(part["reassignments_required"].items())},
        "provenance_tuples_to_preserve": part["provenance"]["distinct_provenance_tuples"],
        "provenance_rows_seen": part["provenance"]["rows"],
        "reason_codes": sorted(part["reason_codes"]),
        "fingerprint": partition_fingerprint(part, group),
    }


def plan(*, limit=None, report=None) -> dict:
    """READ-ONLY execution plan. Performs ZERO writes.

    Only SAFE_AUTO_MERGE partitions are planned; everything else is counted as refused so the
    totals reconcile against the preview an operator already reviewed."""
    report = report or preview(limit=limit)
    plans, refused = [], defaultdict(int)
    for g in report["groups"]:
        for part in g["partitions"]:
            if not part["mergeable"]:
                continue
            if part["classification"] in EXECUTABLE_CLASSIFICATIONS:
                plans.append(_partition_plan(part, g))
            else:
                refused[part["classification"]] += 1
        if g["classification"] == SHARED:
            refused[SHARED] += 1

    plans.sort(key=lambda p: (p["sha256"], p["survivor_document_id"]))
    by_table: dict[str, int] = defaultdict(int)
    for p in plans:
        for k, info in p["reassignments_required"].items():
            by_table[k] += info["rows"]
    return {
        "plan_version": 1,
        "generated_from": "document_merge.preview()",
        "read_only": True,
        "wrote_anything": False,
        "survivor_rule": report["survivor_rule"],
        "eligibility": report["eligibility"],
        "safe_partitions": len(plans),
        "rows_to_retire": sum(p["rows_to_retire"] for p in plans),
        "proposed_reassignments": sum(by_table.values()),
        "reassignments_by_table": dict(sorted(by_table.items())),
        "provenance_tuples_to_preserve": sum(p["provenance_tuples_to_preserve"] for p in plans),
        "refused_partitions": {k: refused[k] for k in sorted(refused)},
        "preview_totals": {
            "physical_sha_groups": report["physical_sha_groups"],
            "physical_sha_document_rows": report["physical_sha_document_rows"],
            "ownership_scoped_merge_groups": report["ownership_scoped_merge_groups"],
            "rows_eligible_for_retirement": report["rows_eligible_for_retirement"],
            "merge_partitions_safe": report["merge_partitions_safe"],
            "merge_partitions_review_required": report["merge_partitions_review_required"],
            "merge_partitions_blocked": report["merge_partitions_blocked"],
            "shared_content_groups": report["shared_content_groups"],
        },
        "partitions": plans,
    }


# --- revalidation ------------------------------------------------------------------------------

def _lock_documents(conn, ids) -> list[int]:
    """Lock every participating row in ASCENDING id order (deterministic, deadlock-avoiding).

    FOR UPDATE on documents only. No filesystem or network resource is held across this lock."""
    ordered = sorted(set(int(i) for i in ids))
    if not ordered:
        return []
    rows = conn.execute(text("SELECT id FROM documents WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"),
                        {"ids": ordered}).scalars().all()
    return [int(i) for i in rows]


def _live_partitions(conn, shas) -> dict[tuple, tuple[dict, dict]]:
    """Re-derive the preview INSIDE the caller's transaction, keyed by (sha, owner tuple).

    This is the same analyze_group()/partition code the operator reviewed - never a second
    implementation of the ownership or classification rules."""
    live = preview(conn=conn, shas=list(shas))
    out = {}
    for g in live["groups"]:
        for part in g["partitions"]:
            key = (g["sha256"], part["owner"]["person_id"], part["owner"]["household_id"],
                   part["owner"]["organization_id"])
            out[key] = (part, g)
    return out


def _revalidate(conn, planned: dict) -> dict:
    """Return the live partition for one plan entry, or raise. Runs AFTER the rows are locked."""
    o = planned["owner"]
    key = (planned["sha256"], o["person_id"], o["household_id"], o["organization_id"])
    live = _live_partitions(conn, [planned["sha256"]])
    if key not in live:
        raise StalePlanError(
            f"{planned['sha256'][:12]}: the ownership partition no longer exists (owner tuple, "
            f"sha, eligibility or membership changed since the plan was generated)")
    part, group = live[key]
    if not part["mergeable"]:
        raise StalePlanError(f"{planned['sha256'][:12]}: partition is no longer mergeable")
    if part["classification"] not in EXECUTABLE_CLASSIFICATIONS:
        raise StalePlanError(
            f"{planned['sha256'][:12]}: classification is now {part['classification']}, "
            f"not {SAFE}")
    live_fp = partition_fingerprint(part, group)
    if live_fp != planned["fingerprint"]:
        raise StalePlanError(
            f"{planned['sha256'][:12]}: precondition fingerprint mismatch - the ownership tuple, "
            f"sha, eligibility, survivor, duplicate ids, classification or dependency structure "
            f"changed since the plan was generated")
    if part["proposed_survivor"] != planned["survivor_document_id"]:
        raise StalePlanError(f"{planned['sha256'][:12]}: survivor moved")   # unreachable via fp
    return part


def _assert_no_unknown_dependency(conn, part, ids) -> None:
    """An FK to documents.id with no declared strategy, carrying rows, stops this partition."""
    from app.services.document_merge import _dependencies
    unknown = []
    for d in _dependencies(conn):
        if d["strategy"] is not None:
            continue
        n = conn.execute(
            text(f"SELECT count(*) FROM {d['table']} WHERE {d['column']} = ANY(:ids)"),
            {"ids": ids}).scalar_one()
        if n:
            unknown.append(f"{d['table']}.{d['column']}")
    if unknown:
        raise MergeExecutionError(
            f"unknown dependency with live rows: {', '.join(sorted(unknown))} - refusing to mutate")




# --- dependency policy -------------------------------------------------------------------------
# Exactly the policy preview() already certifies - deliberately not broader.
#
# A duplicate's dependent rows are repointed to the survivor. Where a UNIQUE constraint means the
# survivor already carries the SAME information, the duplicate's row is redundant and is removed
# instead; preview() classifies precisely these as advisories ("after reassignment they dedupe to
# one row, losing no provenance"). Any OTHER unique collision is uncertified, so the partition is
# refused before a single row is touched rather than guessed at.

#: table -> (unique key columns beside document_id, optional predicate restricting the constraint).
_DEDUP_KEYS = {
    "document_sources": (("source_system", "source_uri"), None),
    "document_facts": (("fact_type", "fact_value"), "is_current"),
    "document_relationships": (("entity_type", "entity_id"), None),
}

#: UNIQUE(document_id): one row per document. A duplicate's row is dropped ONLY when it is
#: byte-identical to the survivor's on every column except id/document_id/timestamps - see
#: _plan_singular. Anything else is refused, so a dropped row is always trivially reconstructible.
_SINGULAR = ("document_ocr", "document_classifications", "rm_document_status")

#: Columns holding DOCUMENT-DERIVED CONTENT: extracted body text and values lifted out of the
#: document itself. These never enter the run result or the hash-chained audit metadata. They do
#: not need to: a row is only ever deleted when an IDENTICAL twin is retained, so the content is
#: recoverable from that twin, and the evidence records which row it is plus a hash proving the
#: two matched at execution time. Only DELETED rows carry row content at all - a repointed row is
#: recorded as (row id, original reference value) and never includes any column value.
_CONTENT_COLUMNS = {
    "document_ocr": ("text", "last_error"),
    "document_facts": ("fact_value",),
}

#: Columns excluded when comparing two singular rows for identity: the surrogate key, the FK being
#: repointed, and every column DEFAULTED to a clock read. The clock columns are derived from the
#: live catalog rather than guessed by name - two rows inserted a microsecond apart are expected to
#: differ there, and hardcoding a name list silently misses columns like classified_at.
_IDENTITY_EXEMPT = frozenset({"id", "document_id"})


def _columns(conn, table) -> list[str]:
    return [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t "
        "ORDER BY ordinal_position"), {"t": table})]


def _identity_columns(conn, table) -> list[str]:
    """Columns that must match for two rows to count as the SAME information.

    Excludes the surrogate key, the repointed FK, and any column whose DEFAULT is a clock read
    (now(), CURRENT_TIMESTAMP, ...) - those record when the row was written, not what it says."""
    rows = conn.execute(text(
        "SELECT column_name, coalesce(column_default, '') AS d FROM information_schema.columns "
        "WHERE table_name = :t ORDER BY ordinal_position"), {"t": table}).mappings().all()
    return [r["column_name"] for r in rows
            if r["column_name"] not in _IDENTITY_EXEMPT
            and "now()" not in r["d"].lower()
            and "current_timestamp" not in r["d"].lower()]


def _jsonable(value):
    """Coerce a captured column value to a JSON-safe primitive WITHOUT losing it.

    The pre-merge evidence is stored as JSON (in the saved plan and in the audit chain), so
    Decimal, datetime, date, UUID and bytes must be represented losslessly as text rather than
    dropped - a forensic reconstruction has to be able to read the original value back."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return str(value)                       # exact decimal text, never a lossy float
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _jsonable_row(row) -> dict:
    return {k: _jsonable(v) for k, v in dict(row).items()}


def _digest(value) -> dict:
    """A durable, non-revealing representation of one content value."""
    if value is None:
        return {"sha256": None, "length": 0, "is_null": True}
    raw = value if isinstance(value, str) else str(value)
    return {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "length": len(raw),
            "is_null": False}


def _redact_content(table, row) -> tuple[dict, dict]:
    """Split one captured row into (columns safe to record, digests of the content columns).

    The content values themselves are DISCARDED here and never reach the caller, so they cannot
    reach the run artifact or the audit chain."""
    content = _CONTENT_COLUMNS.get(table, ())
    safe = {k: v for k, v in row.items() if k not in content and not k.startswith("_")}
    digests = {k: _digest(row.get(k)) for k in content if k in row}
    return safe, digests


def _deleted_record(table, row, retained_row) -> dict:
    """The rollback evidence for one row deleted as redundant.

    Everything needed to reconstruct it: its own primary key, the document it pointed at, every
    non-content column verbatim, the id of the retained twin holding the identical content, and a
    hash of both sides proving they matched when the merge ran."""
    safe, digests = _redact_content(table, row)
    _, retained_digests = _redact_content(table, retained_row or {})
    matches = all(digests[k]["sha256"] == retained_digests.get(k, {}).get("sha256")
                  for k in digests) if retained_row is not None else None
    return {
        "row_id": row["id"],
        "original_document_id": row.get("document_id"),
        "original_row": safe,                       # non-content columns, verbatim
        "content_columns_omitted": sorted(_CONTENT_COLUMNS.get(table, ())),
        "content_digest": digests,                  # sha256 + length, never the value
        "identical_to_retained_row_id": (retained_row or {}).get("id"),
        "retained_content_digest": {k: retained_digests.get(k) for k in digests},
        "content_matches_retained": matches,
    }


def _rows_of(conn, table, column, ids) -> list[dict]:
    """Every full row of ``table`` whose ``column`` points at one of ``ids``. Read-only."""
    if not ids:
        return []
    return [_jsonable_row(r) for r in conn.execute(text(
        f"SELECT * FROM {table} WHERE {column} = ANY(:ids) ORDER BY id"),
        {"ids": list(ids)}).mappings()]


def _unique_key_sets(conn, table) -> list[tuple[list[str], str | None]]:
    """UNIQUE constraints/indexes on ``table`` that involve document_id, from the live catalog."""
    rows = conn.execute(text("""
        SELECT array_agg(a.attname ORDER BY k.ord) AS cols,
               pg_get_expr(i.indpred, i.indrelid) AS predicate
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
        WHERE c.relname = :t AND i.indisunique
        GROUP BY i.indexrelid, i.indpred, i.indrelid
    """), {"t": table}).mappings().all()
    return [(list(r["cols"]), r["predicate"]) for r in rows if "document_id" in r["cols"]]


def _assert_no_uncertified_collision(conn, table, column, survivor, dup_ids) -> None:
    """Refuse if repointing would break a UNIQUE constraint with no certified rule.

    document_versions(document_id, version_number) is the live example: two copies both at version
    1 cannot both move to the survivor, and inventing a renumbering rule would broaden policy
    beyond what preview certifies. Fail closed instead."""
    if column != "document_id":
        return
    certified = set(_DEDUP_KEYS.get(table, ((), None))[0]) | {"document_id"}
    for cols, predicate in _unique_key_sets(conn, table):
        if table in _SINGULAR or set(cols) <= certified:
            continue
        others = [c for c in cols if c != "document_id"]
        if not others:
            continue
        sel = ", ".join(others)
        where = "document_id = ANY(:ids)" + (f" AND ({predicate})" if predicate else "")
        n = conn.execute(text(
            f"SELECT count(*) FROM (SELECT {sel} FROM {table} WHERE {where} "
            f"GROUP BY {sel} HAVING count(*) > 1) x"),
            {"ids": [survivor, *dup_ids]}).scalar_one()
        if n:
            raise MergeExecutionError(
                f"uncertified unique collision on {table}({', '.join(cols)}): repointing would "
                f"produce {n} duplicate key(s). preview() certifies no rule for this - refusing "
                f"to mutate this partition")


# --- PHASE 1: plan the mutations. Issues SELECTs only ------------------------------------------
# Everything below computes what apply WOULD do and captures the PRE-MERGE state while doing it.
# No statement here mutates anything, which is what makes a dry run genuinely read-only and what
# makes the recorded evidence a true snapshot taken before the first write.

def _identity_of(row, columns) -> tuple:
    return tuple(row.get(c) for c in columns)


def _plan_singular(conn, table, survivor, dup_ids) -> dict:
    """UNIQUE(document_id): survivor keeps one row; a duplicate's row is dropped only if identical.

    If the survivor has no row, exactly one is PROMOTED (lowest id) so nothing is lost. Any
    remaining duplicate row that is NOT byte-identical to the kept row is uncertified information,
    and the partition is refused rather than silently discarding it."""
    cols = _identity_columns(conn, table)
    survivor_rows = _rows_of(conn, table, "document_id", [survivor])
    dup_rows = _rows_of(conn, table, "document_id", dup_ids)
    if not dup_rows:
        return {"promote": None, "delete": [], "kept": None}

    promote = None
    kept = survivor_rows[0] if survivor_rows else None
    remaining = list(dup_rows)
    if kept is None:
        promote = remaining.pop(0)
        kept = promote                      # this row is repointed, so its content is RETAINED

    for r in remaining:
        if _identity_of(r, cols) != _identity_of(kept, cols):
            differing = sorted(c for c in cols if r.get(c) != kept.get(c))
            raise MergeExecutionError(
                f"{table} row {r['id']} on document {r['document_id']} differs from the row kept "
                f"on the survivor (columns: {', '.join(differing)}). preview() certifies no rule "
                f"for discarding distinct content - refusing to mutate this partition")
    return {"promote": promote, "delete": remaining, "kept": kept}


def _plan_dedupe(conn, table, survivor, dup_ids) -> list[dict]:
    """Duplicate-owned rows the survivor (or an earlier duplicate) already carries, in full.

    Keeps exactly one row per key across {survivor} + duplicates, preferring the survivor's and
    otherwise the lowest id, so the repoint that follows cannot collide."""
    keys, predicate = _DEDUP_KEYS[table]
    match = " AND ".join(f"r.{c} IS NOT DISTINCT FROM d.{c}" for c in keys)
    pred_d = f" AND ({predicate})".replace("(", "(d.", 1) if predicate else ""
    pred_r = f" AND ({predicate})".replace("(", "(r.", 1) if predicate else ""
    rows = conn.execute(text(
        f"SELECT d.*, (SELECT r.id FROM {table} r WHERE {match}{pred_r}"
        f"   AND (r.document_id = :survivor"
        f"        OR (r.document_id = ANY(:dups) AND r.id < d.id))"
        f"   ORDER BY (r.document_id = :survivor) DESC, r.id LIMIT 1) AS _retained_id "
        f"FROM {table} d WHERE d.document_id = ANY(:dups){pred_d} AND EXISTS ("
        f"  SELECT 1 FROM {table} r WHERE {match}{pred_r}"
        f"  AND (r.document_id = :survivor"
        f"       OR (r.document_id = ANY(:dups) AND r.id < d.id))) ORDER BY d.id"),
        {"dups": dup_ids, "survivor": survivor}).mappings()
    return [_jsonable_row(r) for r in rows]


def _plan_dependency(conn, table, column, survivor, dup_ids) -> dict:
    """The exact rows apply would touch in one dependent COLUMN, captured before any change."""
    delete_rows, promote, deleted_records = [], None, []
    if table in _SINGULAR and column == "document_id":
        p = _plan_singular(conn, table, survivor, dup_ids)
        promote, delete_rows = p["promote"], p["delete"]
        repoint_rows = [promote] if promote else []
        deleted_records = [_deleted_record(table, r, p["kept"]) for r in delete_rows]
    else:
        if table in _DEDUP_KEYS and column == "document_id":
            delete_rows = _plan_dedupe(conn, table, survivor, dup_ids)
            by_id = {r["id"]: r for r in _rows_of(conn, table, "document_id",
                                                  [survivor, *dup_ids])}
            deleted_records = [_deleted_record(table, r, by_id.get(r.get("_retained_id")))
                               for r in delete_rows]
        doomed = {r["id"] for r in delete_rows}
        repoint_rows = [r for r in _rows_of(conn, table, column, dup_ids)
                        if r["id"] not in doomed]
    return {
        "table": table,
        "column": column,
        # A repointed row records only its key and the reference it moved from - never a value.
        "repoint": [{"row_id": r["id"], "original_value": r[column]} for r in repoint_rows],
        "delete": deleted_records,
        "promoted_row_id": promote["id"] if promote else None,
    }


def _prepare_partition(conn, planned) -> dict:
    """Validate, lock, and compute every mutation WITHOUT performing one. SELECTs only.

    Returns the prepared mutation set, which doubles as the PRE-MERGE snapshot recorded in the run
    result and the audit event."""
    from app.services.document_merge import _dependencies

    survivor = planned["survivor_document_id"]
    dup_ids = list(planned["duplicate_document_ids"])
    ids = sorted([survivor, *dup_ids])

    _lock_documents(conn, ids)                      # deterministic id order, documents only
    part = _revalidate(conn, planned)               # same preview code, transaction-visible
    _assert_no_unknown_dependency(conn, part, ids)

    deps = [d for d in _dependencies(conn) if d["table"] != "documents"]
    for d in deps:
        _assert_no_uncertified_collision(conn, d["table"], d["column"], survivor, dup_ids)

    actions = []
    for d in sorted(deps, key=lambda x: (x["table"], x["column"])):
        a = _plan_dependency(conn, d["table"], d["column"], survivor, dup_ids)
        if a["repoint"] or a["delete"]:
            actions.append(a)

    documents_before = _rows_of(conn, "documents", "id", ids)
    provenance_before = [_jsonable_row(r) for r in conn.execute(text(
        "SELECT document_id, source_system, source_uri FROM document_sources "
        "WHERE document_id = ANY(:ids) ORDER BY document_id, source_system, source_uri"),
        {"ids": ids}).mappings()]

    return {
        "sha256": planned["sha256"],
        "owner": planned["owner"],
        "survivor_document_id": survivor,
        "retired_document_ids": dup_ids,
        "classification_at_execution": part["classification"],
        "fingerprint": planned["fingerprint"],
        "dependency_actions": actions,
        "pre_merge_documents": [
            {"document_id": r["id"], "status": r["status"], "deleted_at": r["deleted_at"],
             "person_id": r["person_id"], "household_id": r["household_id"],
             "organization_id": r["organization_id"], "sha256": r["sha256"],
             "storage_path": r["storage_path"], "storage_uri": r["storage_uri"],
             "stored_name": r["stored_name"]}
            for r in documents_before],
        "pre_merge_provenance_tuples": [
            {"document_id": r["document_id"], "source_system": r["source_system"],
             "source_uri": r["source_uri"]} for r in provenance_before],
        "rows_to_repoint": sum(len(a["repoint"]) for a in actions),
        "rows_to_delete": sum(len(a["delete"]) for a in actions),
        "rows_to_retire": len(dup_ids),
    }


# --- PHASE 2: perform the mutations. The ONLY code in this module that writes -------------------

def _execute_prepared(conn, prepared, run_id, actor_user_id, request_id) -> dict:
    """Apply one prepared partition. Every statement below is driven by explicit row ids captured
    in phase 1, so nothing is re-derived here and a dry run can simply not call this."""
    survivor = prepared["survivor_document_id"]
    dup_ids = prepared["retired_document_ids"]
    reassigned, deleted = {}, {}

    for a in prepared["dependency_actions"]:
        table, column = a["table"], a["column"]
        doomed = [d["row_id"] for d in a["delete"]]
        if doomed:                                                   # WRITE 1: redundant rows
            conn.execute(text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": doomed})
            deleted[f"{table}.{column}"] = len(doomed)
        moving = [r["row_id"] for r in a["repoint"]]
        if moving:                                                   # WRITE 2: repoint
            conn.execute(text(f"UPDATE {table} SET {column} = :s WHERE id = ANY(:ids)"),
                         {"s": survivor, "ids": moving})
            reassigned[f"{table}.{column}"] = len(moving)

    now = _now()
    retired = conn.execute(text(                                     # WRITE 3: the retirement
        "UPDATE documents SET status = 'deleted', deleted_at = :now, updated_at = :now "
        "WHERE id = ANY(:dups) AND status <> 'deleted'"),
        {"now": now, "dups": dup_ids}).rowcount or 0
    for did in dup_ids:                                              # WRITE 4: lifecycle record
        conn.execute(text(
            "INSERT INTO document_events (document_id, event_type, from_status, to_status, note,"
            " occurred_at) VALUES (:d, 'merged_into_canonical', 'active', 'deleted', :note, :now)"),
            {"d": did, "note": f"consolidated into document {survivor} "
                               f"(sha {prepared['sha256'][:12]}, run {run_id})", "now": now})

    result = {**prepared,
              "run_id": run_id,
              "rows_retired": retired,
              "reassigned_by_table": dict(sorted(reassigned.items())),
              "deleted_by_table": dict(sorted(deleted.items()))}
    # WRITE 5: audit on the SAME connection, so it commits or rolls back atomically with the merge
    # it describes. A rolled-back batch therefore leaves no success record. (app/security/audit.py)
    write_audit_event(
        action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
        entity_id=f"{prepared['sha256']}:{survivor}",
        actor_user_id=actor_user_id, request_id=request_id, outcome="success",
        metadata=result, conn=conn)
    return result


# --- the run -----------------------------------------------------------------------------------

def _check_expectations(plan_doc, *, expected_safe_partitions, expected_retirement_rows) -> None:
    """Both counts must be supplied and must match the CURRENT plan. Checked before any write."""
    if expected_safe_partitions is None or expected_retirement_rows is None:
        raise MergeExecutionError(
            "apply requires --expected-safe-partitions and --expected-retirement-rows; supply the "
            "figures from the preview you reviewed so a corpus that moved cannot be written blind")
    actual = (plan_doc["safe_partitions"], plan_doc["rows_to_retire"])
    if actual != (expected_safe_partitions, expected_retirement_rows):
        raise MergeExecutionError(
            f"expectation mismatch - refusing to write. expected safe_partitions="
            f"{expected_safe_partitions} rows_to_retire={expected_retirement_rows}; current plan "
            f"has safe_partitions={actual[0]} rows_to_retire={actual[1]}. The corpus changed since "
            f"the preview was reviewed: re-review before applying")


def apply(*, plan_doc=None, apply_writes=False, batch_size=DEFAULT_BATCH_SIZE,
          expected_safe_partitions=None, expected_retirement_rows=None,
          actor_user_id=None, request_id=None, limit=None) -> dict:
    """Execute SAFE_AUTO_MERGE ownership partitions. DRY-RUN unless ``apply_writes`` is True.

    A dry run issues NO mutation statement of any kind - not an UPDATE, DELETE, INSERT or audit
    write, and no write that is later rolled back. It runs phase 1 only: it locks, revalidates,
    checks fingerprints and collisions, and computes the exact rows apply would touch. Rollback is
    deliberately not relied upon, because a write-then-rollback still advances sequences and fires
    triggers, which is observably different from never writing.

    BATCHES COMMIT INDEPENDENTLY. If a later batch fails, earlier batches are already durable; the
    result reports ``partial_apply`` with the committed batch numbers. It is never silently
    presented as if nothing happened."""
    run_id = f"dmx-{uuid.uuid4().hex[:12]}"
    request_id = request_id or run_id
    plan_doc = plan_doc or plan(limit=limit)
    if apply_writes:
        _check_expectations(plan_doc,
                            expected_safe_partitions=expected_safe_partitions,
                            expected_retirement_rows=expected_retirement_rows)

    partitions = plan_doc["partitions"]
    batches = [partitions[i:i + batch_size] for i in range(0, len(partitions), batch_size)]
    prepared_all, applied, refused, batch_reports = [], [], [], []
    committed_batches, failure = [], None

    for n, batch in enumerate(batches, start=1):
        results, errors, prepared_batch = [], [], []
        try:
            with engine.begin() as conn:
                for planned in batch:
                    try:
                        prepared = _prepare_partition(conn, planned)      # SELECTs only
                    except MergeExecutionError as exc:
                        # A refusal is per-partition and never poisons the batch: every guard runs
                        # before this partition's first mutation.
                        errors.append({"sha256": planned["sha256"],
                                       "survivor_document_id": planned["survivor_document_id"],
                                       "refused": type(exc).__name__, "detail": str(exc)})
                        continue
                    prepared_batch.append(prepared)
                    if apply_writes:
                        results.append(_execute_prepared(conn, prepared, run_id, actor_user_id,
                                                         request_id))
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - recorded, then reported
            # KeyboardInterrupt is caught DELIBERATELY. Production lost a run to Ctrl-C and the
            # operator needed to know which batches had already committed; an unhandled traceback
            # discards exactly that. It is recorded as a failure and surfaces as PARTIAL/FAILED,
            # never as success.
            # Everything in this batch rolled back, so nothing here counts as applied. The
            # partitions that were not already refused are FAILED.
            failure = {"batch": n, "partitions_in_batch": len(batch),
                       "partitions_failed": len(batch) - len(errors),
                       "error": type(exc).__name__, "detail": str(exc) or type(exc).__name__}
            refused.extend(errors)              # refusals inside the failing batch are still real
            batch_reports.append({"batch": n, "partitions": len(batch), "applied": 0,
                                  "refused": len(errors), "committed": False,
                                  "error": f"{type(exc).__name__}: {exc}"})
            break
        if apply_writes:
            committed_batches.append(n)
        prepared_all.extend(prepared_batch)
        applied.extend(results)
        refused.extend(errors)
        batch_reports.append({"batch": n, "partitions": len(batch),
                              "applied": len(results), "refused": len(errors),
                              "committed": bool(apply_writes)})

    reassigned_totals: dict[str, int] = defaultdict(int)
    deleted_totals: dict[str, int] = defaultdict(int)
    for r in applied:
        for k, v in r["reassigned_by_table"].items():
            reassigned_totals[k] += v
        for k, v in r["deleted_by_table"].items():
            deleted_totals[k] += v

    # A dry run reports what WOULD happen, straight off the prepared (read-only) snapshot.
    would_reassign: dict[str, int] = defaultdict(int)
    would_delete: dict[str, int] = defaultdict(int)
    for pr in prepared_all:
        for a in pr["dependency_actions"]:
            if a["repoint"]:
                would_reassign[f"{a['table']}.{a['column']}"] += len(a["repoint"])
            if a["delete"]:
                would_delete[f"{a['table']}.{a['column']}"] += len(a["delete"])

    # --- final status -------------------------------------------------------------------------
    # Derived from every PLANNED partition, so a refusal counts even when nothing raised.
    planned_n, applied_n, refused_n = len(partitions), len(applied), len(refused)
    failed_n = failure["partitions_failed"] if failure else 0
    not_attempted = max(0, planned_n - applied_n - refused_n - failed_n)

    if not apply_writes:
        status = STATUS_DRY_RUN
    elif applied_n == planned_n and refused_n == 0 and failed_n == 0 and not_attempted == 0:
        status = STATUS_SUCCESS
    elif applied_n > 0:
        status = STATUS_PARTIAL
    else:
        status = STATUS_FAILED

    return {
        "run_id": run_id,
        "status": status,
        "exit_code": EXIT_CODES[status],
        "dry_run": not apply_writes,
        "wrote_anything": bool(apply_writes and applied),
        "partial_apply": status == STATUS_PARTIAL,
        "failed_batch": failure,
        "committed_batches": committed_batches,
        "retirement_mechanism": "documents.status='deleted' + deleted_at, document_events "
                                "'merged_into_canonical', hash-chained audit event. The document "
                                "ROW is soft and restorable, but the merge as a whole is NOT "
                                "undone by document_platform.restore(): dependent rows were "
                                "repointed and redundant ones deleted. Reconstructing the "
                                "pre-merge graph needs the pre_merge_* evidence recorded here.",
        "batch_size": batch_size,
        "batches": batch_reports,
        "partitions_planned": len(partitions),
        "partitions_prepared": len(prepared_all),
        "partitions_applied": applied_n,
        "partitions_refused": refused_n,
        "partitions_failed": failed_n,
        "partitions_not_attempted": not_attempted,
        "planned_retirement_rows": sum(p["rows_to_retire"] for p in partitions),
        "rows_retired": sum(r["rows_retired"] for r in applied),
        "rows_committed": sum(r["rows_retired"] for r in applied),
        "reassignments_total": sum(v for r in applied
                                   for v in r["reassigned_by_table"].values()),
        "reassigned_by_table": dict(sorted(reassigned_totals.items())),
        "deleted_by_table": dict(sorted(deleted_totals.items())),
        "would_reassign_by_table": dict(sorted(would_reassign.items())),
        "would_delete_by_table": dict(sorted(would_delete.items())),
        "would_retire_rows": sum(pr["rows_to_retire"] for pr in prepared_all),
        "applied": applied,
        "prepared": prepared_all,
        "refused": refused,
        "plan_totals": {k: plan_doc[k] for k in
                        ("safe_partitions", "rows_to_retire", "proposed_reassignments")},
    }
