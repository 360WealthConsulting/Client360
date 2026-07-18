"""F3.1 / Epic 3 — Append-only Audit Log acceptance tests.

Formalizes the canonical append-only guarantee for `audit_events` (already
enforced by the `audit_events_immutable` DB trigger from migration c410f4a1b2c3).
No hash-chain, integrity verification, or evidence-store behavior is exercised
(deferred to F3.2/F3.3/F3.4).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from app.db import audit_events, engine
from app.security.audit import write_audit_event
from app.security.audit_foundation import (
    AuditContext,
    DbAuditSink,
    SecurityAuditService,
    SecurityEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _new_audit() -> int:
    return write_audit_event(
        action="f3_1.test.created",
        entity_type="test_entity",
        request_id=f"f3-1-{uuid.uuid4()}",
        outcome="success",
        metadata={"note": "append-only test"},
    )


def test_audit_creation_succeeds():
    audit_id = _new_audit()
    assert isinstance(audit_id, int)
    with engine.connect() as conn:
        row = conn.execute(
            select(audit_events).where(audit_events.c.id == audit_id)
        ).mappings().first()
    assert row is not None and row["action"] == "f3_1.test.created"


def test_update_of_committed_record_is_rejected():
    audit_id = _new_audit()
    with pytest.raises(Exception):  # noqa: B017 - DB trigger raises; any DB error is a pass
        with engine.begin() as conn:
            conn.execute(
                update(audit_events).where(audit_events.c.id == audit_id).values(action="tampered")
            )
    # Row is unchanged after the rejected update.
    with engine.connect() as conn:
        action = conn.execute(
            select(audit_events.c.action).where(audit_events.c.id == audit_id)
        ).scalar_one()
    assert action == "f3_1.test.created"


def test_delete_of_committed_record_is_rejected():
    audit_id = _new_audit()
    with pytest.raises(Exception):  # noqa: B017
        with engine.begin() as conn:
            conn.execute(delete(audit_events).where(audit_events.c.id == audit_id))
    with engine.connect() as conn:
        still_there = conn.execute(
            select(audit_events.c.id).where(audit_events.c.id == audit_id)
        ).scalar_one_or_none()
    assert still_there == audit_id


def test_f2_5_db_sink_still_writes_append_only():
    """F2.5 compatibility: the DbAuditSink append path is unchanged."""
    event = SecurityEvent(
        action="object.access_denied", entity_type="test_entity", entity_id="7", outcome="denied",
        attributes={"ssn": "111"},
    )
    result = SecurityAuditService(sinks=[DbAuditSink()]).record(event, AuditContext(request_id=f"f3-1-{uuid.uuid4()}"))
    assert result.recorded and result.audit_id is not None
    with engine.connect() as conn:
        row = conn.execute(
            select(audit_events).where(audit_events.c.id == result.audit_id)
        ).mappings().first()
    assert row["action"] == "object.access_denied"
    # And that written record is itself immutable.
    with pytest.raises(Exception):  # noqa: B017
        with engine.begin() as conn:
            conn.execute(
                update(audit_events).where(audit_events.c.id == result.audit_id).values(outcome="ok")
            )


def test_sole_write_path_is_insert_only():
    """`write_audit_event` is the canonical persistence path and is INSERT-only."""
    source = (REPO_ROOT / "app" / "security" / "audit.py").read_text()
    assert "audit_events.insert()" in source
    assert "audit_events.update(" not in source and "audit_events.delete(" not in source


def test_doc_present():
    assert (REPO_ROOT / "docs" / "AUDIT_LOG.md").is_file()
