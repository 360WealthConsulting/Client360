"""Bounded recovery for the 2026-08-29 TaxDome merge-retirement incident.

WHAT HAPPENED
    The merge executor soft-deleted a set of duplicate documents. Before the TaxDome sync fix
    (4af537b) was deployed, a routine sync unconditionally forced status='active' and
    deleted_at=NULL onto every row it matched, resurrecting documents the merge had retired. They
    then re-entered the merge plan as fresh candidates.

WHAT THIS DOES
    Restores the lifecycle state the ORIGINAL merge created - and nothing else - for documents
    that are PROVABLY part of that one incident.

        status      -> 'deleted'
        deleted_at  -> the original merge instant

    document_merge_execute._retire stamps deleted_at and the merged_into_canonical event from a
    SINGLE `now`, so the surviving event's occurred_at IS the original deleted_at. Nothing is
    reconstructed by guesswork.

WHAT IT NEVER DOES
    It does not touch the surviving canonical document, ownership, provenance, storage columns,
    dependencies, or any file. It does not rewrite or delete the original merge event. It does not
    run TaxDome sync. It creates no document rows and deletes none.

NOT A GENERIC TOOL
    This is deliberately NOT "soft-delete every active document with a historic merge event". The
    incident run id and time window are REQUIRED arguments with no defaults, and every one of the
    thirteen guards below must hold. Anything ambiguous is refused, never repaired.

EVIDENCE
    The hash-chained audit written by the merge (action document.merge.partition_applied) is the
    primary authority: it records the run id, the survivor, the retired ids, the ownership tuple
    and the content hash. The abbreviated sha in the event note is NEVER parsed - the database
    holds the full value.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import text

from app.db import engine
from app.security.audit import write_audit_event
from app.services.document_merge_execute import AUDIT_ACTION as MERGE_AUDIT_ACTION
from app.services.document_merge_execute import (
    EXIT_CODES,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
)

#: The lifecycle event the merge executor stamps on every document it retires.
MERGE_RETIREMENT_EVENT = "merged_into_canonical"

#: The new event this recovery records. A genuine present-tense lifecycle event - never a
#: back-dated fabrication, and never a rewrite of the original merge event.
RECOVERY_EVENT = "merge_retirement_restored"

RECOVERY_AUDIT_ACTION = "document.merge.retirement_recovered"
RECOVERY_AUDIT_ENTITY = "document_merge_retirement"

DEFAULT_BATCH_SIZE = 200

#: Dependent tables that must be EMPTY for a row to be restorable. The incident population carries
#: exactly one document_events row (the merge event) and nothing else; anything more means the row
#: has been used since it was resurrected, and restoring it could strand that work.
_MUST_BE_EMPTY = ("document_ocr", "document_sources", "document_facts",
                  "document_classifications", "tax_document_links", "document_versions")


class RecoveryError(RuntimeError):
    """A refusal. Raised BEFORE any mutation."""


class StaleStateError(RecoveryError):
    """The row moved between planning and apply. Refused, never adapted."""


def _now():
    return datetime.now(UTC)


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def row_fingerprint(candidate) -> str:
    """Everything that must not move between plan and apply for one document."""
    return hashlib.sha256(_canonical_json({
        "document_id": candidate["document_id"],
        "status": candidate["status"],
        "deleted_at": candidate["deleted_at"],
        "updated_at": candidate["updated_at"],
        "sha256": candidate["sha256"],
        "owner": candidate["owner"],
        "survivor_document_id": candidate["survivor_document_id"],
        "merge_event_id": candidate["merge_event_id"],
        "merge_event_at": candidate["merge_event_at"],
        "incident_run_id": candidate["incident_run_id"],
    }).encode()).hexdigest()


# --- the thirteen eligibility guards -------------------------------------------------------------
# Fail-closed: a candidate is eligible only when EVERY guard passes. Each failure records a reason
# code so a refusal is always explainable, and no guard is ever inferred from another.

GUARDS = (
    "status_is_active",                 # 1
    "deleted_at_is_null",               # 2
    "source_system_is_taxdome",         # 3
    "exactly_one_merge_event",          # 4
    "event_transition_active_to_deleted",   # 5
    "event_belongs_to_incident_run",    # 6
    "event_inside_incident_window",     # 7
    "updated_after_merge_event",        # 8
    "survivor_exists",                  # 9
    "survivor_is_a_different_document",  # 10
    "survivor_sha256_matches",          # 11
    "ownership_partition_invariant_holds",  # 12
    "no_unexpected_dependencies",       # 13
)


def _merge_audit_for(conn, document_id, run_id):
    """The hash-chained merge audit that retired this document, from the incident run.

    This is the AUTHORITY for the survivor, the ownership tuple and the content hash. Returning
    None means there is no audit evidence, and the candidate is refused - the abbreviated sha in
    the event note is never parsed as a substitute."""
    row = conn.execute(text(
        "SELECT id, metadata::text AS meta FROM audit_events "
        "WHERE action = :a AND metadata->>'run_id' = :run "
        "  AND (metadata::jsonb -> 'retired_document_ids') "
        "      @> to_jsonb(CAST(:did AS integer)) "
        "ORDER BY id LIMIT 2"),
        {"a": MERGE_AUDIT_ACTION, "run": run_id, "did": document_id}).mappings().all()
    if len(row) != 1:
        return None
    return json.loads(row[0]["meta"])


def _dependency_counts(conn, document_id) -> dict[str, int]:
    parts = " UNION ALL ".join(
        f"SELECT '{t}' AS tbl, count(*) AS n FROM {t} WHERE document_id = :did"
        for t in _MUST_BE_EMPTY)
    rows = conn.execute(text(parts), {"did": document_id}).mappings().all()
    out = {r["tbl"]: int(r["n"]) for r in rows}
    out["document_events"] = conn.execute(text(
        "SELECT count(*) FROM document_events WHERE document_id = :did"),
        {"did": document_id}).scalar_one()
    return out


def evaluate(conn, document_id, *, run_id, window_start, window_end,
             source_system="TaxDome Drive") -> dict:
    """Run every guard against ONE document. Read-only. Returns the candidate with its verdict."""
    failures: list[str] = []
    doc = conn.execute(text(
        "SELECT id, status, deleted_at, updated_at, sha256, person_id, household_id,"
        " organization_id, tags->>'source_system' AS source_system"
        " FROM documents WHERE id = :i"), {"i": document_id}).mappings().first()
    if doc is None:
        return {"document_id": document_id, "eligible": False,
                "failed_guards": ["document_missing"], "survivor_document_id": None}

    if doc["status"] != "active":
        failures.append("status_is_active")
    if doc["deleted_at"] is not None:
        failures.append("deleted_at_is_null")
    if doc["source_system"] != source_system:
        failures.append("source_system_is_taxdome")

    events = conn.execute(text(
        "SELECT id, from_status, to_status, occurred_at FROM document_events"
        " WHERE document_id = :i AND event_type = :ev ORDER BY id"),
        {"i": document_id, "ev": MERGE_RETIREMENT_EVENT}).mappings().all()
    if len(events) != 1:
        failures.append("exactly_one_merge_event")
    event = events[0] if len(events) == 1 else None

    if event is not None:
        if not (event["from_status"] == "active" and event["to_status"] == "deleted"):
            failures.append("event_transition_active_to_deleted")
        if not (window_start <= event["occurred_at"] <= window_end):
            failures.append("event_inside_incident_window")
        if doc["updated_at"] is None or doc["updated_at"] <= event["occurred_at"]:
            failures.append("updated_after_merge_event")

    audit = _merge_audit_for(conn, document_id, run_id)
    if audit is None:
        failures.append("event_belongs_to_incident_run")
    survivor = None
    if audit is not None:
        survivor_id = audit.get("survivor_document_id")
        survivor = conn.execute(text(
            "SELECT id, status, sha256, person_id, household_id, organization_id"
            " FROM documents WHERE id = :i"), {"i": survivor_id}).mappings().first()
        if survivor is None:
            failures.append("survivor_exists")
        else:
            if survivor["id"] == document_id:
                failures.append("survivor_is_a_different_document")
            # Full hash from the database on BOTH sides - never the abbreviated note value.
            if not doc["sha256"] or survivor["sha256"] != doc["sha256"]:
                failures.append("survivor_sha256_matches")
            # The original merge safety invariant: identical exact ownership tuple.
            if (survivor["person_id"], survivor["household_id"], survivor["organization_id"]) != \
                    (doc["person_id"], doc["household_id"], doc["organization_id"]):
                failures.append("ownership_partition_invariant_holds")

    deps = _dependency_counts(conn, document_id)
    unexpected = {t: n for t, n in deps.items()
                  if (n != 0 if t != "document_events" else n != 1)}
    if unexpected:
        failures.append("no_unexpected_dependencies")

    candidate = {
        "document_id": document_id,
        "status": doc["status"],
        "deleted_at": doc["deleted_at"].isoformat() if doc["deleted_at"] else None,
        "updated_at": doc["updated_at"].isoformat() if doc["updated_at"] else None,
        "sha256": doc["sha256"],
        "owner": [doc["person_id"], doc["household_id"], doc["organization_id"]],
        "source_system": doc["source_system"],
        "survivor_document_id": (survivor["id"] if survivor is not None
                                 else (audit or {}).get("survivor_document_id")),
        "merge_event_id": event["id"] if event else None,
        "merge_event_at": event["occurred_at"].isoformat() if event else None,
        # The ORIGINAL deleted_at: _retire stamps deleted_at and this event from one `now`.
        "restore_deleted_at": event["occurred_at"].isoformat() if event else None,
        "incident_run_id": run_id,
        "dependency_counts": dict(sorted(deps.items())),
        "unexpected_dependencies": dict(sorted(unexpected.items())),
        "eligible": not failures,
        "failed_guards": sorted(set(failures)),
    }
    candidate["fingerprint"] = row_fingerprint(candidate)
    return candidate


# --- PLAN: read-only ------------------------------------------------------------------------------

def _incident_population(conn, *, run_id, source_system) -> list[int]:
    """Documents this incident could possibly concern: retired by the incident run per the AUDIT.

    Starting from the audit rather than from "active rows with a merge event" is what keeps this
    bounded to one incident instead of becoming a generic sweep."""
    rows = conn.execute(text(
        "SELECT DISTINCT jsonb_array_elements_text("
        "         (metadata::jsonb -> 'retired_document_ids'))::int AS did "
        "FROM audit_events WHERE action = :a AND metadata->>'run_id' = :run"),
        {"a": MERGE_AUDIT_ACTION, "run": run_id}).scalars().all()
    return sorted(int(r) for r in rows)


def plan(*, run_id, window_start, window_end, source_system="TaxDome Drive", conn=None) -> dict:
    """Read-only recovery plan. Performs ZERO writes and touches no file.

    ``run_id``, ``window_start`` and ``window_end`` are REQUIRED - there are no defaults, so this
    cannot be pointed at the whole corpus by omission."""
    if not run_id or window_start is None or window_end is None:
        raise RecoveryError(
            "recovery requires an explicit incident run id and time window; it is deliberately "
            "not a generic 'restore every merge-retired document' operation")
    close = conn is None
    conn = conn or engine.connect()
    try:
        ids = _incident_population(conn, run_id=run_id, source_system=source_system)
        candidates = [evaluate(conn, i, run_id=run_id, window_start=window_start,
                               window_end=window_end, source_system=source_system) for i in ids]
    finally:
        if close:
            conn.close()

    eligible = [c for c in candidates if c["eligible"]]
    refused = [c for c in candidates if not c["eligible"]]
    by_reason: dict[str, int] = defaultdict(int)
    for c in refused:
        for g in c["failed_guards"]:
            by_reason[g] += 1

    doc = {
        "plan_version": 1,
        "read_only": True,
        "wrote_anything": False,
        "incident_run_id": run_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "source_system": source_system,
        "guards": list(GUARDS),
        "population_from_audit": len(ids),
        "eligible_count": len(eligible),
        "refused_count": len(refused),
        "refusals_by_guard": dict(sorted(by_reason.items())),
        "candidates": candidates,
        "eligible": eligible,
    }
    doc["plan_fingerprint"] = hashlib.sha256(_canonical_json(
        [[c["document_id"], c["fingerprint"]] for c in eligible]).encode()).hexdigest()
    return doc


# --- APPLY: the only code that writes -------------------------------------------------------------

def _restore(conn, candidate, recovery_run, incident_run) -> dict:
    """Restore ONE document's merge lifecycle. Writes three things and nothing else."""
    did = candidate["document_id"]
    restore_at = candidate["restore_deleted_at"]

    # WRITE 1: the lifecycle restoration. status + deleted_at ONLY - storage columns, ownership,
    # provenance, sha and every dependency are left exactly as they are.
    n = conn.execute(text(
        "UPDATE documents SET status = 'deleted', deleted_at = :at, updated_at = :now "
        "WHERE id = :i AND status = 'active' AND deleted_at IS NULL"),
        {"at": restore_at, "now": _now(), "i": did}).rowcount or 0
    if n != 1:
        raise StaleStateError(f"document {did}: no longer active with a null deleted_at")

    # WRITE 2: a NEW present-tense lifecycle event. The original merged_into_canonical event is
    # neither rewritten nor removed.
    conn.execute(text(
        "INSERT INTO document_events (document_id, event_type, from_status, to_status, note,"
        " occurred_at) VALUES (:d, :ev, 'active', 'deleted', :note, :now)"),
        {"d": did, "ev": RECOVERY_EVENT,
         "note": f"merge retirement restored after TaxDome resurrection "
                 f"(incident run {incident_run}, recovery {recovery_run})", "now": _now()})

    evidence = {
        "recovery_run_id": recovery_run,
        "incident_run_id": incident_run,
        "document_id": did,
        "survivor_document_id": candidate["survivor_document_id"],
        "restored_status": "deleted",
        "restored_deleted_at": restore_at,
        "observed_status_before": candidate["status"],
        "observed_deleted_at_before": candidate["deleted_at"],
        "observed_updated_at_before": candidate["updated_at"],
        "merge_event_id": candidate["merge_event_id"],
        "merge_event_at": candidate["merge_event_at"],
        "deleted_at_source": "merge event occurred_at (the same instant _retire stamped)",
        "owner": candidate["owner"],
        "sha256": candidate["sha256"],
        "guards_passed": list(GUARDS),
        "fingerprint": candidate["fingerprint"],
    }
    # WRITE 3: the existing hash-chained audit, on the caller's connection so it commits or rolls
    # back with the restoration. Identifiers and digests only - no OCR text, fact or body content.
    write_audit_event(action=RECOVERY_AUDIT_ACTION, entity_type=RECOVERY_AUDIT_ENTITY,
                      entity_id=str(did), outcome="success", request_id=recovery_run,
                      metadata=evidence, conn=conn)
    return evidence


def apply(*, plan_doc=None, apply_writes=False, batch_size=DEFAULT_BATCH_SIZE,
          expected_eligible=None, expected_plan_fingerprint=None,
          run_id=None, window_start=None, window_end=None,
          source_system="TaxDome Drive") -> dict:
    """Restore eligible documents. DRY-RUN unless ``apply_writes`` is True.

    A dry run re-evaluates every candidate inside a transaction and stops before the first write;
    it issues no UPDATE, INSERT or audit write of any kind.

    Immediately before each mutation the candidate is RE-EVALUATED against live state and its
    fingerprint compared. Anything that moved since planning is refused, never adapted.

    Batches commit independently. A later failure - including KeyboardInterrupt - is reported as
    PARTIAL with the committed batch numbers, never as success."""
    recovery_run = f"dmrec-{uuid.uuid4().hex[:12]}"
    plan_doc = plan_doc or plan(run_id=run_id, window_start=window_start,
                                window_end=window_end, source_system=source_system)
    incident_run = plan_doc["incident_run_id"]

    if apply_writes:
        if expected_eligible is None or expected_plan_fingerprint is None:
            raise RecoveryError(
                "apply requires --expected-eligible and --expected-plan-fingerprint from the plan "
                "you reviewed, so a population that moved cannot be written blind")
        actual = (plan_doc["eligible_count"], plan_doc["plan_fingerprint"])
        if actual != (expected_eligible, expected_plan_fingerprint):
            raise RecoveryError(
                f"expectation mismatch - refusing to write. expected eligible={expected_eligible} "
                f"fingerprint={str(expected_plan_fingerprint)[:16]}...; plan has "
                f"eligible={actual[0]} fingerprint={actual[1][:16]}...")

    targets = plan_doc["eligible"]
    batches = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
    restored, refused, batch_reports = [], [], []
    committed_batches, failure = [], None
    ws = datetime.fromisoformat(plan_doc["window_start"])
    we = datetime.fromisoformat(plan_doc["window_end"])

    for n, batch in enumerate(batches, start=1):
        done, errors = [], []
        try:
            with engine.begin() as conn:
                for candidate in batch:
                    # STALE-STATE GUARD: re-run every guard against live state, immediately before
                    # the mutation and inside the write transaction.
                    live = evaluate(conn, candidate["document_id"], run_id=incident_run,
                                    window_start=ws, window_end=we,
                                    source_system=plan_doc["source_system"])
                    if not live["eligible"]:
                        errors.append({"document_id": candidate["document_id"],
                                       "refused": "StaleStateError",
                                       "detail": "no longer eligible: "
                                                 + ", ".join(live["failed_guards"])})
                        continue
                    if live["fingerprint"] != candidate["fingerprint"]:
                        errors.append({"document_id": candidate["document_id"],
                                       "refused": "StaleStateError",
                                       "detail": "state changed since the plan was generated"})
                        continue
                    if apply_writes:
                        done.append(_restore(conn, live, recovery_run, incident_run))
        except (Exception, KeyboardInterrupt) as exc:   # noqa: BLE001 - recorded, then reported
            failure = {"batch": n, "rows_in_batch": len(batch),
                       "rows_failed": len(batch) - len(errors),
                       "error": type(exc).__name__, "detail": str(exc) or type(exc).__name__}
            refused.extend(errors)
            batch_reports.append({"batch": n, "rows": len(batch), "restored": 0,
                                  "refused": len(errors), "committed": False,
                                  "error": f"{type(exc).__name__}: {exc}"})
            break
        if apply_writes:
            committed_batches.append(n)
        restored.extend(done)
        refused.extend(errors)
        batch_reports.append({"batch": n, "rows": len(batch), "restored": len(done),
                              "refused": len(errors), "committed": bool(apply_writes)})

    planned_n = len(targets)
    restored_n, refused_n = len(restored), len(refused)
    failed_n = failure["rows_failed"] if failure else 0
    not_attempted = max(0, planned_n - restored_n - refused_n - failed_n)

    if not apply_writes:
        status = STATUS_DRY_RUN
    elif restored_n == planned_n and refused_n == 0 and failed_n == 0 and not_attempted == 0:
        status = STATUS_SUCCESS
    elif restored_n > 0:
        status = STATUS_PARTIAL
    else:
        status = STATUS_FAILED

    return {
        "recovery_run_id": recovery_run,
        "incident_run_id": incident_run,
        "status": status,
        "exit_code": EXIT_CODES[status],
        "dry_run": not apply_writes,
        "wrote_anything": bool(apply_writes and restored),
        "partial_apply": status == STATUS_PARTIAL,
        "failed_batch": failure,
        "committed_batches": committed_batches,
        "batch_size": batch_size,
        "batches": batch_reports,
        "documents_planned": planned_n,
        "documents_restored": restored_n,
        "documents_refused": refused_n,
        "documents_failed": failed_n,
        "documents_not_attempted": not_attempted,
        "filesystem_mutations": 0,      # structurally: this module never touches a file
        "restored": restored,
        "refused": refused,
        "plan_fingerprint": plan_doc["plan_fingerprint"],
        "plan_totals": {"population_from_audit": plan_doc["population_from_audit"],
                        "eligible_count": plan_doc["eligible_count"],
                        "refused_count": plan_doc["refused_count"],
                        "refusals_by_guard": plan_doc["refusals_by_guard"]},
    }
