"""F3.4 / Epic 3 — Auditor Read/Export acceptance tests.

Read-only, capability-gated retrieval + deterministic export over the audit log
(F3.1), hash chain (F3.2), and evidence store (F3.3). Access control, determinism,
integrity reporting, redaction, pagination, and F3.1/F3.2/F3.3 preservation.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.db import audit_events, engine
from app.security.audit import write_audit_event
from app.security.audit_chain import HASH_VERSION
from app.security.audit_export import (
    EXPORT_SCHEMA_VERSION,
    build_export,
    read_audit_events,
    read_evidence,
    serialize_export,
    verify_integrity,
)
from app.security.evidence import compute_checksum, record_evidence
from app.security.models import Principal
from app.security.rbac import AuthorizationDenied

REPO_ROOT = Path(__file__).resolve().parents[1]


def _auditor() -> Principal:
    return Principal(1, "auditor@e.com", "Auditor", frozenset({"audit.read"}))


def _nobody() -> Principal:
    return Principal(2, "nobody@e.com", "Nobody", frozenset())


def _chain(n: int = 3):
    cid = f"f3-4-{uuid.uuid4().hex[:8]}"
    ids = [
        write_audit_event(action=f"f3_4.act{i}", entity_type="t", request_id=f"f3-4-{uuid.uuid4()}",
                          chain_id=cid, ip_address="1.2.3.4", user_agent="UA",
                          metadata={"ssn": "111", "note": "ok"})
        for i in range(n)
    ]
    return cid, ids


# --- authorization -----------------------------------------------------------

def test_authorized_reads_succeed_unauthorized_fail():
    cid, _ = _chain(1)
    auditor = _auditor()
    assert isinstance(read_audit_events(auditor, filters={"chain_id": cid}), list)
    for call in (
        lambda p: read_audit_events(p, filters={"chain_id": cid}),
        lambda p: read_evidence(p),
        lambda p: verify_integrity(p, chain_id=cid),
        lambda p: build_export(p, filters={"chain_id": cid}, chain_id=cid),
    ):
        with pytest.raises(AuthorizationDenied):
            call(_nobody())


# --- read + redaction + excluded fields --------------------------------------

def test_audit_read_is_reference_only_and_redacted():
    cid, _ = _chain(1)
    rows = read_audit_events(_auditor(), filters={"chain_id": cid})
    assert rows
    row = rows[0]
    assert row["metadata"] == {"ssn": "[REDACTED]", "note": "ok"}   # redaction preserved
    assert "ip_address" not in row and "user_agent" not in row       # excluded
    assert "entry_hash" in row and row["chained"] is True


def test_evidence_read_has_no_binary():
    audit_id = write_audit_event(action="f3_4.ev", entity_type="t", request_id=f"f3-4-{uuid.uuid4()}")
    rec = record_evidence(evidence_type="document_reference", source="taxdome",
                          checksum=compute_checksum(b"x"), reference="taxdome://d/1", audit_event_id=audit_id)
    rows = read_evidence(_auditor(), filters={"audit_event_id": audit_id})
    assert any(r["evidence_uid"] == rec.evidence_uid for r in rows)
    for r in rows:
        assert "reference" in r and "checksum" in r
        assert not any("content" in k or "binary" in k for k in r)   # no binary content


# --- integrity reporting (reuses F3.2) ---------------------------------------

def test_integrity_reporting_ok_and_failure():
    cid, _ = _chain(2)
    ok = verify_integrity(_auditor(), chain_id=cid)
    assert ok["ok"] is True and ok["checked"] == 2 and ok["head"] and "legacy_unchained_count" in ok

    # Forge an appended row (INSERT allowed) to trigger a failure. Use a unique
    # (but content-inconsistent) entry_hash to avoid the unique-index collision.
    prev = ok["head"]
    forged_hash = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex, will not recompute
    with engine.begin() as conn:
        forged_id = conn.execute(
            audit_events.insert().values(
                action="forged", entity_type="t", request_id="forge", outcome="success",
                metadata={}, chain_id=cid, prev_hash=prev, entry_hash=forged_hash, hash_version=HASH_VERSION,
            ).returning(audit_events.c.id)
        ).scalar_one()
    bad = verify_integrity(_auditor(), chain_id=cid)
    assert bad["ok"] is False and bad["first_failure_id"] == forged_id


def test_legacy_unchained_reported():
    with engine.begin() as conn:
        conn.execute(audit_events.insert().values(
            action="legacy", entity_type="t", request_id="legacy", outcome="success", metadata={}))
    assert verify_integrity(_auditor())["legacy_unchained_count"] >= 1


# --- deterministic export ----------------------------------------------------

def test_export_is_deterministic():
    cid, _ = _chain(3)
    kw = dict(filters={"chain_id": cid}, chain_id=cid, generated_at="2026-01-01T00:00:00Z")
    e1 = build_export(_auditor(), **kw)
    e2 = build_export(_auditor(), **kw)
    assert e1["export_schema_version"] == EXPORT_SCHEMA_VERSION
    assert e1["record_counts"]["audit_events"] == 3
    assert e1["integrity"]["ok"] is True
    assert serialize_export(e1) == serialize_export(e2)             # reproducible


# --- pagination / bounded ----------------------------------------------------

def test_pagination_is_bounded():
    cid, _ = _chain(3)
    page1 = read_audit_events(_auditor(), filters={"chain_id": cid}, limit=2, offset=0)
    page2 = read_audit_events(_auditor(), filters={"chain_id": cid}, limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 1
    # A huge limit is capped (no error, bounded).
    assert isinstance(read_audit_events(_auditor(), filters={"chain_id": cid}, limit=10_000), list)


# --- no mutation / no public API / F3.x preserved ----------------------------

def test_no_mutation_no_public_api():
    source = (REPO_ROOT / "app" / "security" / "audit_export.py").read_text()
    assert "APIRouter" not in source and "@router" not in source
    assert ".insert(" not in source and ".update(" not in source and ".delete(" not in source
    assert (REPO_ROOT / "docs" / "AUDIT_EXPORT.md").is_file()


def test_f3_1_2_3_preserved():
    from sqlalchemy import update

    from app.security.audit_chain import verify_chain
    cid, ids = _chain(1)
    assert verify_chain(cid).ok is True                              # F3.2
    with pytest.raises(Exception):  # noqa: B017 - F3.1 append-only
        with engine.begin() as conn:
            conn.execute(update(audit_events).where(audit_events.c.id == ids[0]).values(action="x"))
