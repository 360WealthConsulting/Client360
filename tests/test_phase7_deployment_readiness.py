"""Release 0.9.9 Phase 7 — deployment-readiness tests.

Covers the readiness/liveness endpoints, the CSRF Origin/Referer defense, and the
startup configuration validation. No business behavior is exercised.
"""
import json

import pytest


# --- WP7.1 readiness / liveness ----------------------------------------------

def test_readiness_reports_db_migrations_scheduler_and_sync():
    from app.routes.ops import readiness
    response = readiness()
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["status"] == "ready"
    checks = body["checks"]
    assert checks["database"] == "ok"
    assert checks["migrations"]["in_sync"] is True
    assert checks["migrations"]["current_head"] == checks["migrations"]["expected_head"]
    assert "scheduler" in checks and "microsoft_sync" in checks


def test_readiness_returns_503_when_database_unavailable(monkeypatch):
    import app.routes.ops as ops

    def _boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(ops.engine, "connect", _boom)
    response = ops.readiness()
    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "error"


def test_health_is_database_independent():
    from app.routes.dashboard import health
    # Liveness probe must not touch the database.
    assert health()["status"] == "ok"


def test_readiness_is_a_public_path():
    from app.security.middleware import PUBLIC_EXACT
    assert "/readiness" in PUBLIC_EXACT
    assert "/health" in PUBLIC_EXACT


# --- WP7.3 CSRF Origin/Referer defense ---------------------------------------

BASE = "http://client360.example/"


def test_matching_origin_passes():
    from app.security.middleware import _is_cross_site
    assert _is_cross_site("http://client360.example", None, BASE) is False


def test_mismatched_origin_rejected():
    from app.security.middleware import _is_cross_site
    assert _is_cross_site("http://evil.example", None, BASE) is True


def test_referer_fallback_when_origin_absent():
    from app.security.middleware import _is_cross_site
    # cross-site Referer is rejected...
    assert _is_cross_site(None, "http://evil.example/attack", BASE) is True
    # ...same-site Referer passes...
    assert _is_cross_site(None, "http://client360.example/portal", BASE) is False
    # ...and no Origin and no Referer still passes (behaviour unchanged).
    assert _is_cross_site(None, None, BASE) is False


def test_origin_takes_precedence_over_referer():
    from app.security.middleware import _is_cross_site
    # A matching Origin passes even with a cross-site Referer present.
    assert _is_cross_site("http://client360.example", "http://evil.example/x", BASE) is False


# --- WP7.2 configuration validation ------------------------------------------

def test_configuration_warnings_flag_dev_session_secret(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)
    import importlib
    import app.config as config
    importlib.reload(config)
    try:
        warnings = config.configuration_warnings()
        assert any("SESSION_SECRET" in w for w in warnings)
        assert any("MICROSOFT_TOKEN_KEY" in w for w in warnings)
    finally:
        importlib.reload(config)


def test_configuration_clean_when_secrets_present(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "a-real-secret")
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "a-real-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    import importlib
    import app.config as config
    importlib.reload(config)
    try:
        assert config.configuration_warnings() == []
    finally:
        importlib.reload(config)
