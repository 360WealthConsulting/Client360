"""F3.2 / Epic 3 — Audit hash-chain integrity acceptance tests.

Deterministic hashing, chain population, integrity verification, tamper/linkage
detection, checkpoint verification, legacy-unchained handling, and F3.1/F2.5
preservation. Each chain test uses a unique chain_id for isolation (append-only
rows cannot be cleaned up).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.db import audit_events, engine
from app.security.audit import write_audit_event
from app.security.audit_chain import (
    GENESIS_PREV_HASH,
    HASH_VERSION,
    compute_entry_hash,
    content_from_fields,
    verify_chain,
)
from app.security.audit_foundation import (
    AuditContext,
    DbAuditSink,
    SecurityAuditService,
    SecurityEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cid() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


def _write(chain_id: str, action: str = "f3_2.test") -> int:
    return write_audit_event(
        action=action, entity_type="test_entity", request_id=f"f3-2-{uuid.uuid4()}",
        outcome="success", metadata={"note": "chain"}, chain_id=chain_id,
    )


def _row(audit_id: int) -> dict:
    with engine.connect() as conn:
        return conn.execute(select(audit_events).where(audit_events.c.id == audit_id)).mappings().first()


# --- deterministic hashing ---------------------------------------------------

def test_hash_is_deterministic():
    content = content_from_fields(
        actor_user_id=1, action="a", entity_type="t", entity_id="9", outcome="success",
        request_id="r", ip_address=None, user_agent=None, metadata={"k": "v"},
    )
    h1 = compute_entry_hash(content, prev_hash=GENESIS_PREV_HASH, chain_id="c")
    h2 = compute_entry_hash(content, prev_hash=GENESIS_PREV_HASH, chain_id="c")
    assert h1 == h2 and len(h1) == 64
    # Different prev_hash / content changes the hash.
    assert compute_entry_hash(content, prev_hash="x" * 64, chain_id="c") != h1
    content2 = {**content, "action": "b"}
    assert compute_entry_hash(content2, prev_hash=GENESIS_PREV_HASH, chain_id="c") != h1


# --- chain population --------------------------------------------------------

def test_new_records_are_chained():
    cid = _cid()
    id1 = _write(cid)
    id2 = _write(cid)
    r1, r2 = _row(id1), _row(id2)
    assert r1["prev_hash"] == GENESIS_PREV_HASH          # genesis
    assert r1["entry_hash"] and r1["hash_version"] == HASH_VERSION and r1["chain_id"] == cid
    assert r2["prev_hash"] == r1["entry_hash"]           # linked to previous


# --- verification ------------------------------------------------------------

def test_verify_intact_chain():
    cid = _cid()
    for _ in range(3):
        _write(cid)
    result = verify_chain(cid)
    assert result.ok is True and result.checked == 3 and result.first_failure_id is None


def test_verify_detects_forged_entry():
    cid = _cid()
    _write(cid)
    prev = _row(_write(cid))["entry_hash"]
    # Append a forged row (INSERT is allowed; append-only blocks only UPDATE/DELETE)
    # with a WRONG entry_hash — its content will not recompute to it. Use a unique
    # 64-hex value (not a constant) so the append-only, un-cleanable row cannot
    # collide with uq_audit_events_entry_hash on a repeat run (mirrors F3.4).
    forged_hash = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex, will not recompute
    with engine.begin() as conn:
        forged_id = conn.execute(
            audit_events.insert().values(
                action="forged", entity_type="t", request_id="forge", outcome="success",
                metadata={}, chain_id=cid, prev_hash=prev, entry_hash=forged_hash, hash_version=HASH_VERSION,
            ).returning(audit_events.c.id)
        ).scalar_one()
    result = verify_chain(cid)
    assert result.ok is False and result.first_failure_id == forged_id
    assert "mismatch" in result.reason


def test_verify_detects_broken_linkage():
    cid = _cid()
    _write(cid)
    # A row whose OWN hash is self-consistent but whose prev_hash is wrong.
    content = content_from_fields(
        actor_user_id=None, action="linkbreak", entity_type="t", entity_id=None,
        outcome="success", request_id="lb", ip_address=None, user_agent=None, metadata={},
    )
    wrong_prev = "a" * 64
    self_hash = compute_entry_hash(content, prev_hash=wrong_prev, chain_id=cid)
    with engine.begin() as conn:
        bad_id = conn.execute(
            audit_events.insert().values(
                action="linkbreak", entity_type="t", request_id="lb", outcome="success",
                metadata={}, chain_id=cid, prev_hash=wrong_prev, entry_hash=self_hash, hash_version=HASH_VERSION,
            ).returning(audit_events.c.id)
        ).scalar_one()
    result = verify_chain(cid)
    assert result.ok is False and result.first_failure_id == bad_id and "linkage" in result.reason


def test_verify_from_checkpoint():
    cid = _cid()
    ids = [_write(cid) for _ in range(3)]
    result = verify_chain(cid, from_id=ids[1])       # trust the checkpoint anchor
    assert result.ok is True and result.checked == 2


# --- legacy / append-only / F2.5 ---------------------------------------------

def test_legacy_unchained_row_is_readable_and_excluded():
    # Simulate a pre-migration row: NULL hash columns.
    with engine.begin() as conn:
        legacy_id = conn.execute(
            audit_events.insert().values(
                action="legacy", entity_type="t", request_id="legacy", outcome="success", metadata={},
            ).returning(audit_events.c.id)
        ).scalar_one()
    row = _row(legacy_id)
    assert row["entry_hash"] is None and row["chain_id"] is None   # unchained but valid/readable
    # An isolated chain still verifies (legacy row excluded by entry_hash IS NOT NULL).
    cid = _cid(); _write(cid)
    assert verify_chain(cid).ok is True


def test_append_only_preserved():
    cid = _cid()
    audit_id = _write(cid)
    with pytest.raises(Exception):  # noqa: B017 - append-only trigger
        with engine.begin() as conn:
            conn.execute(update(audit_events).where(audit_events.c.id == audit_id).values(action="tampered"))


def test_f2_5_dbsink_records_are_chained():
    event = SecurityEvent(action="object.access_denied", entity_type="t", entity_id="1", outcome="denied")
    result = SecurityAuditService(sinks=[DbAuditSink()]).record(event, AuditContext(request_id=f"f3-2-{uuid.uuid4()}"))
    assert result.audit_id is not None
    assert _row(result.audit_id)["entry_hash"] is not None    # chained on the default chain


def test_doc_present():
    assert (REPO_ROOT / "docs" / "AUDIT_INTEGRITY.md").is_file()
