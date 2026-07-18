"""E2.2 / F2.2 — Authorization (RBAC) Foundation acceptance tests.

Wraps the existing capability-based RBAC; asserts the abstraction agrees with the
existing ``Principal.can`` enforcement. No object/field/record-level security is
exercised (later features).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import (
    capabilities,
    engine,
    role_capabilities,
    roles,
    user_roles,
    users,
)
from app.platform.outbox import outbox_events
from app.security.models import Principal
from app.security.rbac import (
    AUTHZ_DENIED,
    AuthorizationContext,
    AuthorizationDenied,
    AuthorizationResult,
    CapabilityPolicy,
    Permission,
    UnknownPolicyError,
    authorization_context,
    default_authorization_service,
    emit_authorization_event,
    get_policy,
    list_policies,
    register_policy,
    resolve_capabilities,
    resolve_roles,
)


def _seed_user_with_capability():
    s = uuid.uuid4().hex[:8]
    cap_code, role_code = f"e2_2.cap.{s}", f"e2_2-role-{s}"
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"az-{s}@e.com", normalized_email=f"az-{s}@e.com",
            display_name=f"AZ {s}", auth_subject=f"az-{s}", status="active",
        ).returning(users.c.id)).scalar_one()
        cap_id = c.execute(capabilities.insert().values(
            code=cap_code, description="test cap", sensitive=False,
        ).returning(capabilities.c.id)).scalar_one()
        role_id = c.execute(roles.insert().values(
            code=role_code, name=f"Role {s}", active=True,
        ).returning(roles.c.id)).scalar_one()
        c.execute(role_capabilities.insert().values(role_id=role_id, capability_id=cap_id))
        c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
    return uid, cap_code, role_code


# --- models ------------------------------------------------------------------

def test_permission_validation():
    assert Permission("a.read").code == "a.read"
    with pytest.raises(Exception):
        Permission("")


def test_context_and_result_serialization():
    principal = Principal(3, "u@e.com", "U", frozenset({"a.read"}))
    ctx = AuthorizationContext.from_principal(principal, roles=("advisor",))
    d = ctx.to_dict()
    assert d["user_id"] == 3 and d["capabilities"] == ["a.read"] and d["roles"] == ["advisor"]

    granted = AuthorizationResult.granted("a.read", 3)
    denied = AuthorizationResult.denied("b.write", 3)
    assert bool(granted) is True and bool(denied) is False
    assert granted.to_dict()["allowed"] is True and denied.to_dict()["permission"] == "b.write"


# --- policy & service --------------------------------------------------------

def test_capability_policy_and_service():
    principal = Principal(1, "u@e.com", "U", frozenset({"a.read"}))
    ctx = AuthorizationContext.from_principal(principal)
    svc = default_authorization_service()

    assert svc.is_permitted(ctx, "a.read") is True
    assert svc.is_permitted(ctx, "z.none") is False
    assert svc.authorize(ctx, "a.read").reason == "capability granted"
    svc.require(ctx, "a.read")  # no raise
    with pytest.raises(AuthorizationDenied):
        svc.require(ctx, "z.none")


def test_service_agrees_with_principal_can():
    """The abstraction must not change enforcement semantics."""
    caps = frozenset({"a.read", "b.write"})
    principal = Principal(2, "u@e.com", "U", caps)
    ctx = AuthorizationContext.from_principal(principal)
    svc = default_authorization_service()
    for code in ("a.read", "b.write", "c.delete", "x.admin"):
        assert svc.is_permitted(ctx, code) == principal.can(code)


def test_policy_registry_extension_point():
    assert "capability" in list_policies()
    assert isinstance(get_policy("capability"), CapabilityPolicy)
    with pytest.raises(UnknownPolicyError):
        get_policy("nope")

    class AllowAllPolicy:
        policy_name = "allow-all-e2-2"

        def evaluate(self, context, permission):
            return AuthorizationResult.granted(permission.code, context.user_id)

    register_policy(AllowAllPolicy())
    assert get_policy("allow-all-e2-2").policy_name == "allow-all-e2-2"


# --- role & capability resolution (reuses existing query) --------------------

def test_resolve_capabilities_and_roles():
    uid, cap_code, role_code = _seed_user_with_capability()
    assert cap_code in resolve_capabilities(uid)
    assert role_code in resolve_roles(uid)
    assert resolve_capabilities(-1) == frozenset()


def test_authorization_context_enriches_roles():
    uid, cap_code, role_code = _seed_user_with_capability()
    principal = Principal(uid, "az@e.com", "AZ", frozenset({cap_code}))
    ctx = authorization_context(principal, include_roles=True)
    assert cap_code in ctx.capabilities
    assert role_code in ctx.roles


# --- authorization events (F1.3/F1.4) ----------------------------------------

def test_emit_authorization_event_is_reference_only():
    result = AuthorizationResult.denied("x.write", user_id=5)
    with engine.begin() as conn:
        event_id = emit_authorization_event(conn, result)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(outbox_events).where(outbox_events.c.event_id == event_id)
            ).mappings().first()
        assert row["name"] == AUTHZ_DENIED
        env = row["payload"]
        assert env["payload"] == {"user_id": 5, "permission": "x.write", "allowed": False}
        assert env["subject_ref"] == "user:5"
        assert env["producer"] == "security.authorization"
    finally:
        with engine.begin() as conn:
            conn.execute(delete(outbox_events).where(outbox_events.c.event_id == event_id))
