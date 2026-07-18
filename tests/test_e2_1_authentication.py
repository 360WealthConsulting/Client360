"""E2.1 / F2.1 — Authentication Foundation acceptance tests.

Exercises the provider-agnostic authentication layer that wraps the existing
session authentication. No authorization is asserted (that is F2.2+).
"""
from __future__ import annotations

import json
import types
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import engine, users
from app.platform.outbox import outbox_events
from app.security.authentication import (
    AUTH_AUTHENTICATED,
    AuthenticatedIdentity,
    AuthenticationContext,
    SessionAuthenticationService,
    UnknownProviderError,
    authenticate_token,
    current_context,
    emit_authentication_event,
    get_provider,
    list_providers,
    register_provider,
    resolve_identity,
)
from app.security.models import Principal
from app.security.service import create_session


def _user() -> int:
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(
            users.insert().values(
                email=f"auth-{s}@e.com", normalized_email=f"auth-{s}@e.com",
                display_name=f"Auth {s}", auth_subject=f"auth-{s}", status="active",
            ).returning(users.c.id)
        ).scalar_one()


# --- identity model ----------------------------------------------------------

def test_identity_from_principal_excludes_capabilities():
    principal = Principal(7, "u@e.com", "User Seven", frozenset({"some.capability"}))
    identity = AuthenticatedIdentity.from_principal(principal)
    assert identity.user_id == 7
    assert identity.email == "u@e.com"
    assert identity.display_name == "User Seven"
    assert identity.provider == "session"
    # Authentication carries no authorization data.
    assert not hasattr(identity, "capabilities")
    assert identity.subject_ref == "user:7"


def test_identity_dict_roundtrip():
    identity = AuthenticatedIdentity(user_id=1, email="a@e.com", display_name="A")
    assert AuthenticatedIdentity.from_dict(identity.to_dict()) == identity


# --- context -----------------------------------------------------------------

def test_context_anonymous_and_authenticated():
    anon = AuthenticationContext.anonymous(session_present=True, request_id="r1")
    assert anon.is_authenticated is False
    assert anon.to_dict()["identity"] is None

    identity = AuthenticatedIdentity(user_id=2, email="b@e.com", display_name="B")
    ctx = AuthenticationContext.for_identity(identity, request_id="r2")
    assert ctx.is_authenticated is True
    assert ctx.provider == "session"
    assert ctx.to_dict()["identity"]["user_id"] == 2


# --- session provider (reuses resolve_principal) -----------------------------

def test_session_service_resolves_real_session():
    uid = _user()
    token = create_session(uid)
    identity = resolve_identity(token)
    assert identity is not None
    assert identity.user_id == uid
    assert identity.provider == "session"
    assert SessionAuthenticationService().validate_session(token) is True


def test_invalid_token_is_anonymous():
    assert resolve_identity("not-a-real-token") is None
    ctx = authenticate_token("not-a-real-token")
    assert ctx.is_authenticated is False
    assert ctx.session_present is True
    assert authenticate_token(None).session_present is False


# --- middleware projection ---------------------------------------------------

def test_current_context_reads_request_state():
    principal = Principal(9, "p@e.com", "P Nine", frozenset())
    request = types.SimpleNamespace(
        state=types.SimpleNamespace(principal=principal, request_id="req-9"),
        session={"session_token": "tok"},
    )
    ctx = current_context(request)
    assert ctx.is_authenticated is True
    assert ctx.identity.user_id == 9
    assert ctx.request_id == "req-9"

    anon_request = types.SimpleNamespace(state=types.SimpleNamespace(request_id="req-0"), session={})
    anon_ctx = current_context(anon_request)
    assert anon_ctx.is_authenticated is False
    assert anon_ctx.session_present is False


# --- provider registry (extension point) -------------------------------------

def test_provider_registry():
    assert "session" in list_providers()
    assert get_provider("session").provider_name == "session"
    with pytest.raises(UnknownProviderError):
        get_provider("does-not-exist")

    class DummyProvider:
        provider_name = "dummy-e2-1"

        def resolve_identity(self, token):
            return AuthenticatedIdentity(user_id=1, email="d@e.com", display_name="D", provider="dummy-e2-1")

    register_provider(DummyProvider())
    assert get_provider("dummy-e2-1").provider_name == "dummy-e2-1"
    assert resolve_identity("anything", provider="dummy-e2-1").provider == "dummy-e2-1"


# --- authentication events (F1.3/F1.4) ---------------------------------------

def test_emit_authentication_event_is_reference_only():
    identity = AuthenticatedIdentity(user_id=5, email="secret@e.com", display_name="Secret Person")
    with engine.begin() as conn:
        event_id = emit_authentication_event(conn, AUTH_AUTHENTICATED, identity=identity)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(outbox_events).where(outbox_events.c.event_id == event_id)
            ).mappings().first()
        assert row["name"] == AUTH_AUTHENTICATED
        envelope = row["payload"]
        assert envelope["event_type"] == AUTH_AUTHENTICATED
        assert envelope["payload"]["user_id"] == 5
        assert envelope["subject_ref"] == "user:5"
        assert envelope["producer"] == "security.authentication"
        # No PII (email / display name) anywhere in the event.
        blob = json.dumps(envelope)
        assert "secret@e.com" not in blob and "Secret Person" not in blob
    finally:
        with engine.begin() as conn:
            conn.execute(delete(outbox_events).where(outbox_events.c.event_id == event_id))
