"""E2.3 / F2.3 — Object-Level Security Foundation acceptance tests.

Wraps the existing record-scope enforcement; asserts the abstraction agrees with
``record_in_scope``. No field-level security / tenant isolation is exercised.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import engine, record_assignments, users
from app.platform.outbox import outbox_events
from app.security.authorization import record_in_scope
from app.security.models import Principal
from app.security.object_security import (
    OBJECT_ACCESS_DENIED,
    ObjectAccessDenied,
    ObjectAccessResult,
    ObjectRef,
    ObjectSecurityContext,
    RecordScopePolicy,
    UnknownObjectPolicyError,
    default_object_security_service,
    emit_object_access_event,
    get_object_policy,
    list_object_policies,
    register_object_policy,
    resolve_assignments,
    resolve_owners,
)


def _user() -> int:
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"obj-{s}@e.com", normalized_email=f"obj-{s}@e.com",
            display_name=f"Obj {s}", auth_subject=f"obj-{s}", status="active",
        ).returning(users.c.id)).scalar_one()


def _unique_entity_id() -> int:
    return uuid.uuid4().int % 2_000_000_000


def _assign(user_id: int, entity_type: str, entity_id: int, assignment_type: str = "owner") -> None:
    with engine.begin() as c:
        c.execute(record_assignments.insert().values(
            user_id=user_id, entity_type=entity_type, entity_id=entity_id,
            assignment_type=assignment_type,
        ))


# --- models ------------------------------------------------------------------

def test_objectref_validation_and_serialization():
    ref = ObjectRef("person", 42)
    assert ref.ref == "person:42"
    assert ref.to_dict() == {"entity_type": "person", "entity_id": 42}
    with pytest.raises(Exception):
        ObjectRef("", 1)
    with pytest.raises(Exception):
        ObjectRef("person", "not-int")  # type: ignore[arg-type]


def test_result_model():
    granted = ObjectAccessResult(True, "person", 1, False, 7, "ok")
    denied = ObjectAccessResult(False, "person", 1, True, 7, "no")
    assert bool(granted) is True and bool(denied) is False
    assert granted.ref == "person:1"
    assert denied.to_dict()["write"] is True


# --- object access evaluation (preserves enforcement) ------------------------

def test_access_requires_assignment_and_agrees_with_record_in_scope():
    uid = _user()
    principal = Principal(uid, f"u{uid}@e.com", "U", frozenset())  # no bypass
    ref = ObjectRef("person", _unique_entity_id())
    svc = default_object_security_service()
    ctx = ObjectSecurityContext.for_principal(principal)

    # No assignment yet -> denied, and agrees with record_in_scope.
    assert svc.can_access(ctx, ref) is False
    assert svc.can_access(ctx, ref) == record_in_scope(principal, ref.entity_type, ref.entity_id)

    # After an active assignment -> granted.
    _assign(uid, ref.entity_type, ref.entity_id)
    assert svc.can_access(ctx, ref) is True
    assert svc.can_access(ctx, ref) == record_in_scope(principal, ref.entity_type, ref.entity_id)


def test_bypass_capability_grants_without_assignment():
    uid = _user()
    privileged = Principal(uid, f"u{uid}@e.com", "U", frozenset({"record.read_all"}))
    ref = ObjectRef("person", _unique_entity_id())  # no assignment
    svc = default_object_security_service()
    assert svc.can_access(ObjectSecurityContext.for_principal(privileged), ref) is True


def test_require_raises_when_denied():
    uid = _user()
    principal = Principal(uid, f"u{uid}@e.com", "U", frozenset())
    ref = ObjectRef("household", _unique_entity_id())
    svc = default_object_security_service()
    with pytest.raises(ObjectAccessDenied):
        svc.require(ObjectSecurityContext.for_principal(principal), ref)


# --- ownership / assignment resolution ---------------------------------------

def test_resolve_assignments_and_owners():
    uid = _user()
    entity_id = _unique_entity_id()
    _assign(uid, "person", entity_id, assignment_type="advisor")
    rows = resolve_assignments("person", entity_id)
    assert any(r["user_id"] == uid and r["assignment_type"] == "advisor" for r in rows)
    assert uid in resolve_owners("person", entity_id)
    assert resolve_owners("person", _unique_entity_id()) == frozenset()


# --- policy registry / extension point ---------------------------------------

def test_object_policy_registry():
    assert "record-scope" in list_object_policies()
    assert isinstance(get_object_policy("record-scope"), RecordScopePolicy)
    with pytest.raises(UnknownObjectPolicyError):
        get_object_policy("nope")

    class AllowAll:
        policy_name = "allow-all-e2-3"

        def evaluate(self, context, obj, write):
            return ObjectAccessResult(True, obj.entity_type, obj.entity_id, write, context.principal.user_id, "test")

    register_object_policy(AllowAll())
    assert get_object_policy("allow-all-e2-3").policy_name == "allow-all-e2-3"


def test_context_serialization():
    principal = Principal(9, "p@e.com", "P", frozenset())
    ctx = ObjectSecurityContext.for_principal(principal)
    assert ctx.to_dict() == {"user_id": 9, "provider": "record-scope", "has_connection": False}


# --- object-security events (F1.3/F1.4) --------------------------------------

def test_emit_object_access_event_is_reference_only():
    result = ObjectAccessResult(False, "person", 42, True, 5, "denied")
    with engine.begin() as conn:
        event_id = emit_object_access_event(conn, result)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(outbox_events).where(outbox_events.c.event_id == event_id)
            ).mappings().first()
        assert row["name"] == OBJECT_ACCESS_DENIED
        env = row["payload"]
        assert env["payload"] == {
            "user_id": 5, "entity_type": "person", "entity_id": 42, "write": True, "allowed": False,
        }
        assert env["subject_ref"] == "person:42"
        assert env["producer"] == "security.object"
    finally:
        with engine.begin() as conn:
            conn.execute(delete(outbox_events).where(outbox_events.c.event_id == event_id))
