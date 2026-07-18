"""E2.4 / F2.4 — Field-Level Security Foundation acceptance tests.

Wraps the existing redaction; includes an agreement test proving the abstraction
preserves ``redaction.redact_metadata`` behavior. No values ever appear in
results or events.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, select

from app.db import engine
from app.platform.outbox import outbox_events
from app.security.field_security import (
    DENIED,
    FIELD_MASKED,
    MASK_TOKEN,
    MASKED,
    OMITTED,
    VISIBLE,
    FieldAccessResult,
    FieldDescriptor,
    FieldSecurityContext,
    FieldSecurityService,
    SensitiveNameRedactionPolicy,
    UnknownFieldPolicyError,
    default_field_security_service,
    emit_field_security_event,
    get_field_policy,
    is_sensitive,
    list_field_policies,
    register_field_policy,
)
from app.security.redaction import redact_metadata

# --- descriptor / classification (reuses redaction.SENSITIVE) ----------------

def test_is_sensitive_reuses_existing_classification():
    for name in ("ssn", "tax_id", "auth_token", "password", "content", "body", "client_secret"):
        assert is_sensitive(name) is True
    for name in ("name", "email", "id", "status", "display_name"):
        assert is_sensitive(name) is False
    assert FieldDescriptor("ssn").sensitive is True
    assert FieldDescriptor("name").sensitive is False
    with pytest.raises(Exception):
        FieldDescriptor("")


# --- decisions / model -------------------------------------------------------

def test_result_carries_no_value():
    result = FieldAccessResult("ssn", MASKED, 3, "sensitive")
    d = result.to_dict()
    assert set(d) == {"field", "visibility", "user_id", "reason"}
    assert "value" not in d and result.visible is False


def test_policy_and_service_visibility():
    svc = default_field_security_service()
    ctx = FieldSecurityContext.system()
    assert svc.visibility(ctx, "ssn") == MASKED
    assert svc.visibility(ctx, "name") == VISIBLE
    assert svc.apply(ctx, "ssn", "123-45-6789") == (MASKED, MASK_TOKEN)
    assert svc.apply(ctx, "name", "Bob") == (VISIBLE, "Bob")


# --- agreement with existing redaction (required) ----------------------------

def test_redact_mapping_agrees_with_redact_metadata():
    svc = default_field_security_service()
    ctx = FieldSecurityContext.system()
    samples = [
        {"ssn": "123-45-6789", "name": "Bob", "auth_token": "abc", "note": "hi", "tax_id": "x"},
        {"password": "p", "content": "c", "body": "b", "id": 1, "status": "active"},
        {},
        None,
    ]
    for sample in samples:
        assert svc.redact_mapping(ctx, sample) == redact_metadata(sample)


# --- fail-closed / deterministic ---------------------------------------------

def test_fail_closed_on_policy_error():
    class BrokenPolicy:
        policy_name = "broken-e2-4"

        def evaluate(self, context, descriptor):
            raise RuntimeError("boom")

    svc = FieldSecurityService(BrokenPolicy())
    result = svc.evaluate(FieldSecurityContext.system(), "anything")
    assert result.visibility == DENIED
    # A field that cannot be evaluated is dropped, never exposed.
    assert svc.redact_mapping(FieldSecurityContext.system(), {"anything": "secret-value"}) == {}


def test_deterministic_masking():
    svc = default_field_security_service()
    ctx = FieldSecurityContext.system()
    first = svc.redact_mapping(ctx, {"ssn": "111", "name": "A"})
    second = svc.redact_mapping(ctx, {"ssn": "111", "name": "A"})
    assert first == second == {"ssn": MASK_TOKEN, "name": "A"}


def test_omitted_policy_drops_field():
    class OmitSensitive:
        policy_name = "omit-e2-4"

        def evaluate(self, context, descriptor):
            vis = OMITTED if descriptor.sensitive else VISIBLE
            return FieldAccessResult(descriptor.name, vis, context.user_id, "test")

    svc = FieldSecurityService(OmitSensitive())
    assert svc.redact_mapping(FieldSecurityContext.system(), {"ssn": "111", "name": "A"}) == {"name": "A"}


# --- registry / extension ----------------------------------------------------

def test_policy_registry():
    assert "sensitive-name" in list_field_policies()
    assert isinstance(get_field_policy("sensitive-name"), SensitiveNameRedactionPolicy)
    with pytest.raises(UnknownFieldPolicyError):
        get_field_policy("nope")

    class P:
        policy_name = "custom-e2-4"

        def evaluate(self, context, descriptor):
            return FieldAccessResult(descriptor.name, VISIBLE, None, "t")

    register_field_policy(P())
    assert get_field_policy("custom-e2-4").policy_name == "custom-e2-4"


def test_context_serialization():
    assert FieldSecurityContext.system().to_dict() == {"user_id": None, "provider": "sensitive-name"}


# --- events: reference-only, never the value ---------------------------------

def test_emit_field_security_event_never_leaks_value():
    result = FieldAccessResult("ssn", MASKED, 5, "sensitive")
    with engine.begin() as conn:
        event_id = emit_field_security_event(conn, result)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(outbox_events).where(outbox_events.c.event_id == event_id)
            ).mappings().first()
        assert row["name"] == FIELD_MASKED
        env = row["payload"]
        assert env["payload"] == {"user_id": 5, "field": "ssn", "visibility": MASKED}
        assert env["producer"] == "security.field"
        # No actual value could ever appear (none is passed); guard the shape.
        assert "123-45-6789" not in json.dumps(env)
    finally:
        with engine.begin() as conn:
            conn.execute(delete(outbox_events).where(outbox_events.c.event_id == event_id))


def test_visible_field_emits_no_event():
    assert emit_field_security_event(None, FieldAccessResult("name", VISIBLE, 1, "ok")) is None
