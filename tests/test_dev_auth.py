"""Development-only auth provider — gating and provisioning.

The provider must be impossible to enable in production, off by default, and it must
provision a deterministic persona's active user + role through the real session path.
The end-to-end browser sign-in is covered by the Playwright suite (e2e/).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import engine, roles, user_roles, users
from app.demo.credentials import DEMO_STAFF
from app.routes.dev_auth import _ensure_dev_user, _guard, dev_auth_enabled


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CLIENT360_DEV_AUTH", raising=False)
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    assert dev_auth_enabled() is False


def test_impossible_to_enable_in_production(monkeypatch):
    monkeypatch.setenv("CLIENT360_DEV_AUTH", "1")
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    assert dev_auth_enabled() is False           # production wins over the toggle


def test_enabled_only_in_non_production_with_toggle(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    for value, expected in [("1", True), ("true", True), ("on", True), ("0", False), ("", False)]:
        monkeypatch.setenv("CLIENT360_DEV_AUTH", value)
        assert dev_auth_enabled() is expected


def test_guard_refuses_when_disabled(monkeypatch):
    monkeypatch.delenv("CLIENT360_DEV_AUTH", raising=False)
    with pytest.raises(Exception) as exc:
        _guard()
    assert getattr(exc.value, "status_code", None) == 404


def test_ensure_dev_user_provisions_active_user_and_role_idempotently():
    admin = next(u for u in DEMO_STAFF if u.role_code == "administrator")
    uid1 = _ensure_dev_user(admin)
    uid2 = _ensure_dev_user(admin)            # idempotent
    assert uid1 == uid2

    with engine.connect() as c:
        row = c.execute(select(users.c.status, users.c.auth_subject).where(users.c.id == uid1)).one()
        assert row.status == "active" and row.auth_subject == admin.auth_subject
        role_id = c.execute(select(roles.c.id).where(roles.c.code == "administrator")).scalar_one()
        assignments = c.execute(select(user_roles.c.id).where(
            user_roles.c.user_id == uid1, user_roles.c.role_id == role_id,
            user_roles.c.inactive_date.is_(None))).all()
    assert len(assignments) == 1              # exactly one active role assignment, not duplicated
