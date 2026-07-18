"""F3.3 / Epic 3 — Evidence write-once store acceptance tests.

Creation, retrieval, audit linkage, immutability (update/delete rejected),
checksum immutability, reference-only, and F3.1/F3.2 preservation.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, text, update

from app.db import engine, metadata
from app.security.audit import write_audit_event
from app.security.evidence import (
    EvidenceRecord,
    compute_checksum,
    get_evidence,
    list_evidence_for_audit,
    record_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _evidence():
    return metadata.tables["evidence"]


def _record(**kw) -> EvidenceRecord:
    defaults = dict(evidence_type="document_reference", source="taxdome",
                    checksum=compute_checksum(b"content"), reference="taxdome://doc/1")
    defaults.update(kw)
    return record_evidence(**defaults)


# --- creation / retrieval ----------------------------------------------------

def test_evidence_creation_succeeds():
    rec = _record(classification="regulatory", provenance="workflow:X",
                  metadata={"document_ref": "abc"})
    assert isinstance(rec, EvidenceRecord) and rec.id and rec.evidence_uid
    assert rec.evidence_type == "document_reference" and rec.classification == "regulatory"
    assert rec.source == "taxdome" and rec.checksum == compute_checksum(b"content")
    assert rec.reference == "taxdome://doc/1" and rec.provenance == "workflow:X"
    assert rec.evidence_metadata == {"document_ref": "abc"}


def test_retrieval_by_uid_and_id():
    rec = _record()
    assert get_evidence(evidence_uid=rec.evidence_uid).id == rec.id
    assert get_evidence(evidence_id=rec.id).evidence_uid == rec.evidence_uid
    assert get_evidence(evidence_uid="nope-" + uuid.uuid4().hex) is None
    with pytest.raises(ValueError):
        get_evidence()


def test_compute_checksum_is_sha256():
    assert compute_checksum(b"content") == compute_checksum(b"content")
    assert len(compute_checksum(b"x")) == 64
    assert compute_checksum(b"a") != compute_checksum(b"b")


# --- audit linkage -----------------------------------------------------------

def test_audit_linkage():
    audit_id = write_audit_event(action="f3_3.evidence.linked", entity_type="t",
                                 request_id=f"f3-3-{uuid.uuid4()}")
    rec = _record(audit_event_id=audit_id)
    linked = list_evidence_for_audit(audit_id)
    assert rec.evidence_uid in {e.evidence_uid for e in linked}
    assert get_evidence(evidence_id=rec.id).audit_event_id == audit_id


# --- write-once --------------------------------------------------------------

def test_update_is_rejected():
    rec = _record()
    with pytest.raises(Exception):  # noqa: B017 - evidence_immutable trigger
        with engine.begin() as conn:
            conn.execute(update(_evidence()).where(_evidence().c.id == rec.id).values(source="tampered"))
    assert get_evidence(evidence_id=rec.id).source == "taxdome"  # unchanged


def test_delete_is_rejected():
    rec = _record()
    with pytest.raises(Exception):  # noqa: B017
        with engine.begin() as conn:
            conn.execute(delete(_evidence()).where(_evidence().c.id == rec.id))
    assert get_evidence(evidence_id=rec.id) is not None  # persists


def test_checksum_is_immutable():
    rec = _record(checksum=compute_checksum(b"original"))
    with pytest.raises(Exception):  # noqa: B017
        with engine.begin() as conn:
            conn.execute(
                update(_evidence()).where(_evidence().c.id == rec.id).values(checksum=compute_checksum(b"tampered"))
            )
    assert get_evidence(evidence_id=rec.id).checksum == compute_checksum(b"original")


def test_trigger_present():
    with engine.connect() as conn:
        assert conn.execute(
            text("select tgname from pg_trigger where tgname='evidence_immutable'")
        ).scalar() == "evidence_immutable"


# --- F3.1 / F3.2 preserved ---------------------------------------------------

def test_f3_1_and_f3_2_preserved():
    from app.security.audit_chain import verify_chain
    cid = f"f3-3-{uuid.uuid4().hex[:8]}"
    audit_id = write_audit_event(action="f3_3.preserve", entity_type="t",
                                 request_id=f"f3-3-{uuid.uuid4()}", chain_id=cid)
    # F3.2 chain intact
    assert verify_chain(cid).ok is True
    # F3.1 append-only still enforced on audit_events
    from app.db import audit_events
    with pytest.raises(Exception):  # noqa: B017
        with engine.begin() as conn:
            conn.execute(update(audit_events).where(audit_events.c.id == audit_id).values(action="x"))


def test_no_public_api_and_doc_present():
    source = (REPO_ROOT / "app" / "security" / "evidence.py").read_text()
    assert "APIRouter" not in source and "@router" not in source
    assert (REPO_ROOT / "docs" / "EVIDENCE.md").is_file()
