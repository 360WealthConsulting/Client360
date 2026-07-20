"""Sprint 2 (Release Readiness) — production startup safety guards."""
from __future__ import annotations

import pytest

from app.config import validate_startup_configuration


def test_production_refuses_dev_auth(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.setenv("CLIENT360_DEV_AUTH", "1")
    monkeypatch.setenv("SESSION_SECRET", "a-real-production-secret")
    with pytest.raises(RuntimeError, match="CLIENT360_DEV_AUTH"):
        validate_startup_configuration()


def test_production_boots_without_dev_auth(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.delenv("CLIENT360_DEV_AUTH", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "a-real-production-secret")
    validate_startup_configuration()   # no raise


def test_development_allows_dev_auth(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    monkeypatch.setenv("CLIENT360_DEV_AUTH", "1")
    validate_startup_configuration()   # dev auth is fine in development
