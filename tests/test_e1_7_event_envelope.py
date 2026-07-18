"""E1.7 / F1.4 — Event envelope & schema versioning acceptance tests.

Covers serialization, deserialization, validation, version compatibility, and
integration with the F1.3 transactional outbox (without changing its guarantees).
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.db import engine
from app.platform import (
    SCHEMA_VERSION,
    Envelope,
    EnvelopeError,
    clear_subscribers,
    dispatch_pending,
    is_envelope,
    new_event,
    publish,
    publish_event,
    subscribe,
    upgrade_envelope,
)
from app.platform.outbox import outbox_events, outbox_processed_events


@pytest.fixture(autouse=True)
def _clean_outbox():
    def _wipe():
        with engine.begin() as conn:
            conn.execute(delete(outbox_processed_events))
            conn.execute(delete(outbox_events))
    clear_subscribers()
    _wipe()
    yield
    clear_subscribers()
    _wipe()


# --- envelope construction & validation --------------------------------------

def test_new_event_defaults_and_validation():
    env = new_event("AccountFunded", {"account_ref": "account:1"})
    assert env.event_type == "AccountFunded"
    assert env.schema_version == SCHEMA_VERSION
    assert env.event_id and isinstance(env.event_id, str)
    assert env.occurred_at  # ISO timestamp default
    assert env.payload == {"account_ref": "account:1"}
    assert env.metadata == {}


def test_validation_rejects_bad_input():
    with pytest.raises(EnvelopeError):
        Envelope(event_type="").validate()
    with pytest.raises(EnvelopeError):
        Envelope(event_type="X", payload=["not", "a", "dict"]).validate()  # type: ignore[arg-type]
    with pytest.raises(EnvelopeError):
        Envelope(event_type="X", schema_version=999).validate()


# --- serialization / deserialization -----------------------------------------

def test_dict_roundtrip_is_stable():
    env = new_event(
        "OpportunityOpened", {"opportunity_ref": "opp:9"},
        correlation_id="c1", causation_id="c0", subject_ref="opp:9",
        producer="crm.pipeline", metadata={"trace": "t1"},
    )
    restored = Envelope.from_dict(env.to_dict())
    assert restored == env


def test_json_roundtrip():
    env = new_event("DocumentReceived", {"document_ref": "doc:3"})
    restored = Envelope.from_json(env.to_json())
    assert restored.event_id == env.event_id
    assert restored.event_type == "DocumentReceived"
    assert restored.payload == {"document_ref": "doc:3"}


# --- version compatibility ---------------------------------------------------

def test_missing_version_defaults_to_current():
    data = {"event_type": "Legacy", "event_id": "e-1", "payload": {}}
    env = Envelope.from_dict(data)
    assert env.schema_version == SCHEMA_VERSION


def test_future_version_is_rejected():
    with pytest.raises(EnvelopeError):
        upgrade_envelope({"schema_version": SCHEMA_VERSION + 1})
    with pytest.raises(EnvelopeError):
        Envelope.from_dict({"event_type": "X", "event_id": "e", "schema_version": SCHEMA_VERSION + 1})


def test_unknown_fields_are_preserved_not_dropped():
    data = {
        "event_type": "X", "event_id": "e", "schema_version": SCHEMA_VERSION,
        "future_field": "keep-me",
    }
    env = Envelope.from_dict(data)
    assert env.metadata["_unknown"]["future_field"] == "keep-me"


def test_is_envelope_discriminates_from_bare_payload():
    assert is_envelope(new_event("X").to_dict()) is True
    assert is_envelope({"account": 7}) is False  # bare F1.3 payload


# --- outbox integration (guarantees unchanged) -------------------------------

def test_publish_event_serializes_into_outbox():
    env = new_event("AccountOpened", {"account_ref": "account:5"}, subject_ref="account:5")
    with engine.begin() as conn:
        event_id = publish_event(conn, env)
    assert event_id == env.event_id
    with engine.connect() as conn:
        row = conn.execute(
            select(outbox_events).where(outbox_events.c.event_id == env.event_id)
        ).mappings().first()
    assert row["name"] == "AccountOpened"          # transport name mirrors event_type
    assert row["status"] == "pending"
    assert is_envelope(row["payload"])              # full envelope stored in payload


def test_dispatch_delivers_envelope_to_handler():
    received = []
    subscribe("AccountOpened", lambda env: received.append(env))
    env = new_event("AccountOpened", {"account_ref": "account:5"}, correlation_id="corr-1")
    with engine.begin() as conn:
        publish_event(conn, env)

    summary = dispatch_pending()

    assert summary["dispatched"] == 1
    assert len(received) == 1
    delivered = received[0]
    assert isinstance(delivered, Envelope)
    assert delivered.event_id == env.event_id
    assert delivered.event_type == "AccountOpened"
    assert delivered.correlation_id == "corr-1"
    assert delivered.payload == {"account_ref": "account:5"}


def test_legacy_bare_publish_still_delivers_dict():
    """F1.3 bare publish path is unchanged (no envelope) — regression guard."""
    seen = []
    subscribe("legacy.bare", lambda view: seen.append(view))
    with engine.begin() as conn:
        event_id = publish(conn, "legacy.bare", {"x": 1})

    dispatch_pending()

    assert seen == [{"event_id": event_id, "name": "legacy.bare", "payload": {"x": 1}}]
