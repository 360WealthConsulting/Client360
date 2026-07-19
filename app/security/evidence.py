"""Evidence Write-once Store (F3.3 / Epic 3).

A canonical, immutable, **reference-only** store for regulatory/operational
evidence associated with Client360 workflows. Evidence records are append-only
(write-once) — enforced at the database level by the ``evidence_immutable``
trigger (same pattern as ``audit_events_immutable``, F3.1) — so creation metadata,
checksum, and provenance cannot be altered once written.

Scope (F3.3): a canonical ``evidence`` table, an internal persistence service, and
a retrieval service, with optional linkage to audit events. **No binary document
content is stored** — only a reference (URI/pointer), a caller-supplied checksum,
and reference metadata. **No public API**, external storage, SIEM, retention, or
auditor export (deferred / out of scope).

Reference-only contract: ``reference`` and ``evidence_metadata`` must carry
references, never secrets, PII, SSNs, tax-return data, or binary content
(Constitution §9).
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import Table, select


def _evidence_table() -> Table:
    """The reflected ``evidence`` table (reflected on first use if needed)."""
    from app.db import engine, metadata

    table = metadata.tables.get("evidence")
    if table is None:
        table = Table("evidence", metadata, autoload_with=engine)
    return table


def compute_checksum(data: bytes) -> str:
    """SHA-256 hex of caller-held content — a convenience so callers can record a
    checksum without the store ever seeing the content."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class EvidenceRecord:
    id: int
    evidence_uid: str
    evidence_type: str
    classification: str
    source: str
    checksum: str | None
    reference: str | None
    evidence_metadata: dict
    provenance: str | None
    audit_event_id: int | None
    created_by: int | None
    created_at: object

    def to_dict(self) -> dict:
        data = asdict(self)
        data["created_at"] = str(self.created_at) if self.created_at is not None else None
        return data


def _to_record(row) -> EvidenceRecord:
    m = dict(row)
    return EvidenceRecord(
        id=m["id"], evidence_uid=m["evidence_uid"], evidence_type=m["evidence_type"],
        classification=m["classification"], source=m["source"], checksum=m["checksum"],
        reference=m["reference"], evidence_metadata=m["evidence_metadata"],
        provenance=m["provenance"], audit_event_id=m["audit_event_id"],
        created_by=m["created_by"], created_at=m["created_at"],
    )


def record_evidence(
    *, evidence_type: str, source: str, checksum: str | None = None,
    classification: str = "operational", reference: str | None = None,
    metadata: dict | None = None, provenance: str | None = None,
    audit_event_id: int | None = None, created_by: int | None = None,
    evidence_uid: str | None = None, conn=None,
) -> EvidenceRecord:
    """Create an immutable evidence record. References only — never binary content.

    ``evidence_uid`` may be supplied for deterministic correlation/idempotency (e.g.
    workflow outcomes); when omitted a random uid is generated (existing behavior).
    """
    evidence = _evidence_table()
    values = {
        "evidence_uid": evidence_uid or str(uuid.uuid4()),
        "evidence_type": evidence_type,
        "classification": classification,
        "source": source,
        "checksum": checksum,
        "reference": reference,
        "evidence_metadata": metadata or {},
        "provenance": provenance,
        "audit_event_id": audit_event_id,
        "created_by": created_by,
    }

    def _do(connection) -> EvidenceRecord:
        row = connection.execute(
            evidence.insert().values(**values).returning(*evidence.c)
        ).mappings().first()
        return _to_record(row)

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.begin() as connection:
        return _do(connection)


def get_evidence(*, evidence_uid: str | None = None, evidence_id: int | None = None, conn=None) -> EvidenceRecord | None:
    """Retrieve a single evidence record by uid or id."""
    evidence = _evidence_table()
    if evidence_uid is not None:
        where = evidence.c.evidence_uid == evidence_uid
    elif evidence_id is not None:
        where = evidence.c.id == evidence_id
    else:
        raise ValueError("evidence_uid or evidence_id is required")
    query = select(evidence).where(where)

    def _do(connection) -> EvidenceRecord | None:
        row = connection.execute(query).mappings().first()
        return _to_record(row) if row is not None else None

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.connect() as connection:
        return _do(connection)


def list_evidence_for_audit(audit_event_id: int, *, conn=None) -> list[EvidenceRecord]:
    """Retrieve all evidence linked to an audit event."""
    evidence = _evidence_table()
    query = select(evidence).where(evidence.c.audit_event_id == audit_event_id).order_by(evidence.c.id)

    def _do(connection) -> list[EvidenceRecord]:
        return [_to_record(row) for row in connection.execute(query).mappings().all()]

    if conn is not None:
        return _do(conn)
    from app.db import engine

    with engine.connect() as connection:
        return _do(connection)
