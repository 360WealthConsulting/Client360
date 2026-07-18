"""E2.5 / F2.5 — Security Audit & Policy Event Foundation acceptance tests.

Wraps the two existing mechanisms (DB audit + outbox security events); includes
agreement tests proving both are preserved. No sensitive values reach any sink.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, select

from app.db import audit_events, engine
from app.platform.outbox import outbox_events
from app.security.audit_foundation import (
    AUTHENTICATION,
    AUTHORIZATION,
    FIELD,
    GENERIC,
    OBJECT,
    AuditContext,
    AuditResult,
    DbAuditSink,
    OutboxAuditSink,
    RecordAllPolicy,
    SecurityAuditService,
    SecurityEvent,
    UnknownSinkError,
    category_for_action,
    default_security_audit_service,
    for_workflow_template,
    get_sink,
    list_sinks,
    register_sink,
)
from app.security.redaction import redact_metadata

# --- taxonomy ----------------------------------------------------------------

def test_category_for_action():
    assert category_for_action("identity.authenticated") == AUTHENTICATION
    assert category_for_action("authorization.denied") == AUTHORIZATION
    assert category_for_action("object.access_granted") == OBJECT
    assert category_for_action("field.masked") == FIELD
    assert category_for_action("something.else") == GENERIC


# --- event model -------------------------------------------------------------

def test_event_derives_category_and_validates():
    ev = SecurityEvent(action="authorization.denied", actor_user_id=5)
    assert ev.category == AUTHORIZATION
    assert SecurityEvent.from_dict(ev.to_dict()) == ev
    with pytest.raises(Exception):
        SecurityEvent(action="")


def test_scrubbed_attributes_agree_with_redact_metadata():
    ev = SecurityEvent(action="object.access_denied", attributes={"ssn": "111", "reason": "x", "token": "t"})
    assert ev.scrubbed_attributes() == redact_metadata({"ssn": "111", "reason": "x", "token": "t"})


def test_envelope_is_deterministic_and_scrubbed():
    ev = SecurityEvent(action="field.masked", actor_user_id=7, subject_ref="user:7",
                       attributes={"ssn": "secret-value"})
    env1 = ev.to_envelope(event_id="fixed-1", occurred_at="2026-01-01T00:00:00+00:00")
    env2 = ev.to_envelope(event_id="fixed-1", occurred_at="2026-01-01T00:00:00+00:00")
    assert env1.to_dict() == env2.to_dict()                 # deterministic
    assert env1.producer == "security.field"
    assert env1.payload["ssn"] == "[REDACTED]"              # scrubbed
    assert "secret-value" not in json.dumps(env1.to_dict())


# --- outbox sink (F1.3/F1.4) -------------------------------------------------

def test_outbox_sink_publishes_reference_only():
    svc = default_security_audit_service()
    assert svc.sink_names == ["outbox"]
    ev = SecurityEvent(action="identity.authentication_failed", actor_user_id=9,
                       subject_ref="user:9", outcome="failure",
                       attributes={"password": "hunter2", "ip_hint": "x"})
    with engine.begin() as conn:
        result = svc.record(ev, AuditContext(request_id="r-1"), conn=conn)
    try:
        assert result.recorded and result.sinks == ("outbox",)
        with engine.connect() as conn:
            row = conn.execute(
                select(outbox_events).where(outbox_events.c.event_id == result.event_id)
            ).mappings().first()
        assert row["name"] == "identity.authentication_failed"
        env = row["payload"]
        assert env["producer"] == "security.authentication"
        assert env["payload"]["password"] == "[REDACTED]"   # scrubbed
        assert "hunter2" not in json.dumps(env)
    finally:
        with engine.begin() as conn:
            conn.execute(delete(outbox_events).where(outbox_events.c.event_id == result.event_id))


# --- db sink agreement (preserves existing audit behavior) -------------------

def test_db_sink_agrees_with_write_audit_event():
    ev = SecurityEvent(action="object.access_denied", actor_user_id=None,
                       entity_type="person", entity_id="42", outcome="denied",
                       attributes={"ssn": "111", "note": "ok"})
    ctx = AuditContext(request_id="req-db-1", ip_address="10.0.0.1", user_agent="pytest")
    result = SecurityAuditService(sinks=[DbAuditSink()]).record(ev, ctx)
    assert result.recorded and result.sinks == ("db",) and result.audit_id is not None
    with engine.connect() as conn:
        row = conn.execute(
            select(audit_events).where(audit_events.c.id == result.audit_id)
        ).mappings().first()
    assert row["action"] == "object.access_denied"
    assert row["entity_type"] == "person" and row["entity_id"] == "42"
    assert row["outcome"] == "denied" and row["request_id"] == "req-db-1"
    # Metadata redacted identically to the existing DB audit path.
    assert row["metadata"] == redact_metadata({"ssn": "111", "note": "ok"})


# --- result / registry / policy ----------------------------------------------

def test_audit_result_model():
    r = AuditResult(recorded=True, sinks=("outbox",), event_id="e", audit_id=1)
    assert r.to_dict()["recorded"] is True


def test_sink_registry_and_policy():
    assert set(list_sinks()) >= {"outbox", "db"}
    assert isinstance(get_sink("outbox"), OutboxAuditSink)
    with pytest.raises(UnknownSinkError):
        get_sink("nope")

    class NullSink:
        sink_name = "null-e2-5"

        def record(self, event, context, *, conn=None):
            return {"sink": self.sink_name}

    register_sink(NullSink())
    assert "null-e2-5" in list_sinks()

    class DropAll:
        policy_name = "drop-all"

        def should_record(self, event):
            return False

    svc = SecurityAuditService(sinks=[get_sink("null-e2-5")], policy=DropAll())
    assert svc.record(SecurityEvent(action="identity.authenticated")).recorded is False
    assert isinstance(RecordAllPolicy(), RecordAllPolicy)


# --- workflow registry integration (F1.5) ------------------------------------

def test_for_workflow_template_validates_against_registry():
    ev = for_workflow_template("object.access_granted", "TAXOPS-SOP-01", actor_user_id=1)
    assert ev.template_id == "TAXOPS-SOP-01"
    env = ev.to_envelope(event_id="x", occurred_at="2026-01-01T00:00:00+00:00")
    assert env.metadata["template_id"] == "TAXOPS-SOP-01"
    with pytest.raises(Exception):
        for_workflow_template("object.access_granted", "NOPE-SOP-999")


# --- audit context -----------------------------------------------------------

def test_audit_context_from_request():
    import types

    request = types.SimpleNamespace(
        state=types.SimpleNamespace(request_id="rq-1", principal=types.SimpleNamespace(user_id=3)),
        client=types.SimpleNamespace(host="1.2.3.4"),
        headers={"user-agent": "UA"},
    )
    ctx = AuditContext.from_request(request)
    assert ctx.request_id == "rq-1" and ctx.actor_user_id == 3
    assert ctx.ip_address == "1.2.3.4" and ctx.user_agent == "UA"
