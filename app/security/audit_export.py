"""Auditor Read/Export (F3.4 / Epic 3).

A controlled, **read-only** auditor surface for inspecting and exporting audit
records (F3.1), their tamper-evidence status (F3.2), and evidence references
(F3.3). It preserves the immutability of the underlying stores — it only issues
SELECTs and introduces **no** mutation path.

Authorization reuses the existing capability model: every operation requires the
``audit.read`` capability (via the F2.2 Authorization Foundation). Unauthorized
callers are rejected; the surface cannot create/update/delete or reach unrelated
administrative operations.

Scope (F3.4): read audit events, read evidence, integrity verification status
(via the existing F3.2 verifier — no second verifier), and a deterministic,
versioned JSON export. **No** public/unauthenticated export, mutation, external
storage, SIEM, retention, PDF, or binary export (out of scope).

Privacy/minimization: exports carry only fields needed for audit review, preserve
existing metadata redaction (metadata is redacted at write and re-redacted on
export), and deliberately **exclude** `ip_address` and `user_agent` (client
identifiers not required for content review). No binary document content is ever
exported (evidence is reference + checksum only).
"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.db import audit_events, engine, metadata
from app.security.audit_chain import GENESIS_PREV_HASH, verify_chain
from app.security.rbac import AuthorizationContext, default_authorization_service
from app.security.redaction import redact_metadata

EXPORT_SCHEMA_VERSION = 1
AUDIT_READ_CAPABILITY = "audit.read"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

# Reference-only audit fields (ip_address / user_agent deliberately excluded).
_AUDIT_FIELDS = (
    "id", "actor_user_id", "action", "entity_type", "entity_id", "outcome",
    "request_id", "occurred_at", "prev_hash", "entry_hash", "hash_version", "chain_id",
)
_EVIDENCE_FIELDS = (
    "id", "evidence_uid", "evidence_type", "classification", "source", "checksum",
    "reference", "provenance", "audit_event_id", "created_by", "created_at",
)


def _require_audit_read(principal) -> None:
    """Authorize via the existing capability model (raises AuthorizationDenied)."""
    default_authorization_service().require(
        AuthorizationContext.from_principal(principal), AUDIT_READ_CAPABILITY
    )


def _bounded(limit: int) -> int:
    return min(max(int(limit), 1), MAX_LIMIT)


def _evidence_table():
    from sqlalchemy import Table

    table = metadata.tables.get("evidence")
    if table is None:
        table = Table("evidence", metadata, autoload_with=engine)
    return table


def _audit_filter_clauses(filters: dict):
    clauses = []
    f = filters or {}
    mapping = {
        "actor_user_id": audit_events.c.actor_user_id,
        "action": audit_events.c.action,
        "entity_type": audit_events.c.entity_type,
        "entity_id": audit_events.c.entity_id,
        "outcome": audit_events.c.outcome,
        "request_id": audit_events.c.request_id,
        "chain_id": audit_events.c.chain_id,
    }
    for key, column in mapping.items():
        if f.get(key) is not None:
            clauses.append(column == f[key])
    if f.get("start") is not None:
        clauses.append(audit_events.c.occurred_at >= f["start"])
    if f.get("end") is not None:
        clauses.append(audit_events.c.occurred_at <= f["end"])
    return clauses


def _audit_record(row) -> dict:
    record = {field: row[field] for field in _AUDIT_FIELDS}
    record["metadata"] = redact_metadata(row["metadata"])  # preserve/enforce redaction
    record["chained"] = row["entry_hash"] is not None
    return record


def read_audit_events(principal, *, filters: dict | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0, conn=None) -> list[dict]:
    """Read-only, bounded audit-event retrieval (reference-only, redacted)."""
    _require_audit_read(principal)
    query = (
        select(audit_events).where(*_audit_filter_clauses(filters))
        .order_by(audit_events.c.id).limit(_bounded(limit)).offset(max(int(offset), 0))
    )

    def _do(c):
        return [_audit_record(r) for r in c.execute(query).mappings().all()]

    if conn is not None:
        return _do(conn)
    with engine.connect() as connection:
        return _do(connection)


def _evidence_filter_clauses(evidence, filters: dict):
    clauses = []
    f = filters or {}
    mapping = {
        "evidence_type": evidence.c.evidence_type,
        "classification": evidence.c.classification,
        "source": evidence.c.source,
        "audit_event_id": evidence.c.audit_event_id,
    }
    for key, column in mapping.items():
        if f.get(key) is not None:
            clauses.append(column == f[key])
    return clauses


def read_evidence(principal, *, filters: dict | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0, conn=None) -> list[dict]:
    """Read-only, bounded evidence retrieval (reference-only; no binary content)."""
    _require_audit_read(principal)
    evidence = _evidence_table()
    query = (
        select(evidence).where(*_evidence_filter_clauses(evidence, filters))
        .order_by(evidence.c.id).limit(_bounded(limit)).offset(max(int(offset), 0))
    )

    def _do(c):
        out = []
        for r in c.execute(query).mappings().all():
            rec = {field: r[field] for field in _EVIDENCE_FIELDS}
            rec["evidence_metadata"] = redact_metadata(r["evidence_metadata"])
            out.append(rec)
        return out

    if conn is not None:
        return _do(conn)
    with engine.connect() as connection:
        return _do(connection)


def _legacy_unchained_count(c) -> int:
    return c.execute(
        select(func.count()).select_from(audit_events).where(audit_events.c.entry_hash.is_(None))
    ).scalar_one()


def _chain_head(c, chain_id: str):
    return c.execute(
        select(audit_events.c.entry_hash)
        .where(audit_events.c.chain_id == chain_id, audit_events.c.entry_hash.isnot(None))
        .order_by(audit_events.c.id.desc()).limit(1)
    ).scalar()


def verify_integrity(principal, *, chain_id: str = "default", from_id: int | None = None, conn=None) -> dict:
    """Integrity status for a chain via the existing F3.2 verifier (no second verifier)."""
    _require_audit_read(principal)

    def _do(c):
        result = verify_chain(chain_id, from_id=from_id, conn=c)
        return {
            "chain_id": chain_id,
            "ok": result.ok,
            "checked": result.checked,
            "first_failure_id": result.first_failure_id,
            "reason": result.reason,
            "checkpoint": from_id,
            "genesis": GENESIS_PREV_HASH if from_id is None else None,
            "head": _chain_head(c, chain_id),
            "legacy_unchained_count": _legacy_unchained_count(c),
        }

    if conn is not None:
        return _do(conn)
    with engine.connect() as connection:
        return _do(connection)


def build_export(
    principal, *, filters: dict | None = None, chain_id: str = "default",
    generated_at: str | None = None, limit: int = MAX_LIMIT, conn=None,
) -> dict:
    """Assemble a deterministic, versioned auditor export.

    Deterministic for the same data, filters, chain_id, and (caller-supplied)
    generated_at. No binary content.
    """
    _require_audit_read(principal)
    filters = dict(filters or {})

    def _do(c):
        audit = read_audit_events(principal, filters=filters, limit=limit, conn=c)
        evid = read_evidence(principal, filters=filters, limit=limit, conn=c)
        integrity = verify_integrity(principal, chain_id=chain_id, from_id=filters.get("from_id"), conn=c)
        legacy = sum(1 for r in audit if not r["chained"])
        return {
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "generated_at": generated_at,
            "chain_id": chain_id,
            "filters": filters,
            "record_counts": {
                "audit_events": len(audit),
                "evidence": len(evid),
                "legacy_unchained": legacy,
            },
            "integrity": integrity,
            "audit_events": audit,
            "evidence": evid,
        }

    if conn is not None:
        return _do(conn)
    with engine.connect() as connection:
        return _do(connection)


def serialize_export(export: dict) -> str:
    """Deterministic, stable-ordered JSON serialization of an export."""
    return json.dumps(export, sort_keys=True, separators=(",", ":"), default=str)
