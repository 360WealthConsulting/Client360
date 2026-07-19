"""Workflow audit & evidence reconciliation (F4.7 / Epic 4, ADR-016).

Completes the workflow audit/evidence model: every **material workflow outcome**
produces a tamper-evident audit record (F3.1/ADR-015) and a write-once evidence
record (F3.3), linked together and traceable to the underlying domain record and the
responsible actor/process. This module reuses the established platform infrastructure
(``write_audit_event`` and ``record_evidence``) — it does **not** invent a new audit
or evidence mechanism.

Direction (ADR-016): audit and evidence **observe and document** workflow activity;
they **never drive** workflow execution. ``record_workflow_evidence`` only reads and
writes to the evidence store; it never changes workflow state.

Determinism & idempotency: an evidence record's uid is derived deterministically from
its audit event id (``wf:<outcome>:<audit_event_id>``). Because each material outcome
writes exactly one audit event, and idempotent operations (SLA escalation, automation)
write their audit event only once, re-recording is a no-op — the evidence store is
write-once and the uid is unique. Retries never create duplicate evidence.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.security.evidence import (
    EvidenceRecord,
    _evidence_table,
    _to_record,
    get_evidence,
    record_evidence,
)

#: Evidence source/type for workflow outcomes.
WORKFLOW_EVIDENCE_SOURCE = "workflow"
WORKFLOW_EVIDENCE_TYPE = "workflow_outcome"


def workflow_evidence_uid(outcome: str, audit_event_id: int) -> str:
    """Deterministic evidence uid for a workflow outcome (idempotency key)."""
    return f"wf:{outcome}:{audit_event_id}"


def record_workflow_evidence(
    *, outcome: str, workflow_instance_id: int, audit_event_id: int | None,
    step_id: int | None = None, actor_user_id: int | None = None,
    references: dict | None = None, conn=None,
) -> EvidenceRecord | None:
    """Record ONE write-once evidence entry for a material workflow outcome.

    Linked to its audit event (traceability). Deterministic + idempotent (a repeated
    call for the same audit event returns the existing record). Never changes workflow
    state. Returns ``None`` when there is no audit anchor.
    """
    if audit_event_id is None:
        return None
    uid = workflow_evidence_uid(outcome, audit_event_id)
    existing = get_evidence(evidence_uid=uid, conn=conn)
    if existing is not None:
        return existing  # write-once / idempotent
    metadata = {"workflow_instance_id": workflow_instance_id, "outcome": outcome}
    if step_id is not None:
        metadata["workflow_step_id"] = step_id
    if references:
        metadata.update(references)  # references only (ids/labels), never PII
    reference = f"workflow_instance:{workflow_instance_id}"
    if step_id is not None:
        reference += f"/step:{step_id}"
    return record_evidence(
        evidence_type=WORKFLOW_EVIDENCE_TYPE,
        source=WORKFLOW_EVIDENCE_SOURCE,
        classification="operational",
        reference=reference,
        metadata=metadata,
        provenance=f"workflow.{outcome}",
        audit_event_id=audit_event_id,
        created_by=actor_user_id,
        evidence_uid=uid,
        conn=conn,
    )


def list_workflow_evidence(workflow_instance_id: int, *, limit: int = 200, conn=None) -> list[EvidenceRecord]:
    """Retrieve the write-once evidence records for a workflow instance (and its steps).

    Read-only, bounded, reference-only. Matches the instance reference and any step
    references (``workflow_instance:<id>`` and ``workflow_instance:<id>/…``).
    """
    evidence = _evidence_table()
    base = f"workflow_instance:{workflow_instance_id}"
    query = (
        select(evidence)
        .where(or_(evidence.c.reference == base, evidence.c.reference.like(base + "/%")))
        .order_by(evidence.c.id).limit(max(int(limit), 1))
    )

    def _do(c) -> list[EvidenceRecord]:
        return [_to_record(row) for row in c.execute(query).mappings().all()]

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.connect() as connection:
        return _do(connection)
