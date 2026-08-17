"""Shared helpers for client-portal route tests.

The suite has no httpx/TestClient, so portal routes are exercised by calling the route functions
directly with a real ``PortalPrincipal`` (auth/scope is enforced inside the delegated services, which
is exactly what these tests assert) and a lightweight fake request. Seeding mirrors
tests/test_portal_vault.py: invite → accept → session → principal.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import insert, select

from app.db import engine, households, people, roles, user_roles, users
from app.portal.service import (
    accept_invitation,
    create_portal_session,
    invite_portal_account,
    resolve_portal_session,
)


def seed_staff_user(*, role_code="advisor") -> int:
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=f"staff-{suffix}@e.test", normalized_email=f"staff-{suffix}@e.test",
            display_name="Portal Staff", auth_subject=f"staff-{suffix}", status="active"
        ).returning(users.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == role_code))
        if role_id:
            c.execute(insert(user_roles).values(user_id=uid, role_id=role_id))
    return uid


def seed_portal_account(staff_user_id: int, *, permissions=None):
    """Create household + person + accepted portal account + live session.

    Returns ``(account_id, principal, person_id, household_id)``.
    """
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"HH {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {suffix}", active=True)
                        .returning(people.c.id)).scalar_one()
    account_id, invitation = invite_portal_account(
        person_id=pid, household_id=hid, email=f"c-{suffix}@e.test", display_name="Portal Client",
        access_type="self", invited_by_user_id=staff_user_id,
        permissions=permissions or {"documents": True, "messages": True})
    accept_invitation(invitation, f"subject-{suffix}", True)
    token = create_portal_session(account_id, device_fingerprint=f"dev-{uuid.uuid4()}")
    return account_id, resolve_portal_session(token), pid, hid


def fake_request(path="/portal/", method="GET", query=None, session=None, state_principal=None):
    """A minimal object with the attributes the portal routes (and templates) read off ``request``.

    ``state_principal`` populates ``request.state.principal`` so the staff base template can render.
    """
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        query_params=query or {},
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}",
                              principal=state_principal, demo_mode=False),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest", "accept": "text/html"},
        session=session if session is not None else {},
    )


def render(response) -> str:
    """Body of a TemplateResponse/HTMLResponse as text."""
    return response.body.decode("utf-8")


# Minimal but genuine leading bytes per type, so uploads pass the client-path content sniffing
# (storage.content_matches_extension). Used by the portal upload tests instead of dummy bytes.
_SAMPLE = {
    "pdf": b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nsample\n",
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
    "jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 16,
    "jpeg": b"\xff\xd8\xff\xe0" + b"\x00" * 16,
    "docx": b"PK\x03\x04" + b"\x00" * 16,
    "xlsx": b"PK\x03\x04" + b"\x00" * 16,
    "csv": b"a,b,c\n1,2,3\n",
    "txt": b"hello world\n",
}


def sample_upload(ext="pdf") -> bytes:
    """Valid minimal file content for ``ext`` (passes client-upload content validation)."""
    return _SAMPLE.get(ext.lower(), _SAMPLE["pdf"])
