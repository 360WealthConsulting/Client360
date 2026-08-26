"""Deployment Readiness MVP — config preflight, migrate, readiness, service, smoke, admin grant.

All reuse the existing engine (config, Alembic, /readiness, identity/roles, uvicorn). No schema.
Config/service tests are pure (env monkeypatch / command construction); DB-touching tests use the
disposable test DB. Secrets are asserted never to be printed.
"""
import os
import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import engine, roles, user_roles, users
from app.deploy import admin, checks, config_check, migrate, service, smoke


@pytest.fixture(autouse=True)
def _restore_environ():
    """Snapshot/restore os.environ around every test. The deploy CLI calls load_dotenv(app/.env),
    which writes directly to os.environ (bypassing monkeypatch's undo) — without this, config vars
    set by one test would leak into later tests (and into other test files)."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# --- configuration -----------------------------------------------------------

def _prod_env(monkeypatch, **overrides):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/client360_test")
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)


def test_missing_production_secret_is_fatal(monkeypatch):
    _prod_env(monkeypatch)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    result = config_check.validate_config()
    assert result["ok"] is False
    assert any("SESSION_SECRET" in f for f in result["fatal"])
    assert any("OIDC_ISSUER" in f for f in result["fatal"])


def test_development_fallback_rejected_in_production(monkeypatch):
    _prod_env(monkeypatch, OIDC_ISSUER="x", OIDC_CLIENT_ID="x", OIDC_CLIENT_SECRET="x")
    monkeypatch.setenv("SESSION_SECRET", "development-only-change-me")
    result = config_check.validate_config()
    assert result["ok"] is False
    assert any("insecure development fallback" in f for f in result["fatal"])


def test_dev_auth_in_production_is_fatal(monkeypatch):
    _prod_env(monkeypatch, SESSION_SECRET="strong", OIDC_ISSUER="x", OIDC_CLIENT_ID="x",
              OIDC_CLIENT_SECRET="x", CLIENT360_DEV_AUTH="1")
    result = config_check.validate_config()
    assert result["ok"] is False and any("CLIENT360_DEV_AUTH" in f for f in result["fatal"])


def test_secret_values_are_not_present_in_output(monkeypatch, capsys):
    _prod_env(monkeypatch, SESSION_SECRET="SUPER-SECRET-XYZ", OIDC_ISSUER="i",
              OIDC_CLIENT_ID="c", OIDC_CLIENT_SECRET="OIDC-SECRET-XYZ")
    config_check.main([])
    out = capsys.readouterr().out
    assert "SUPER-SECRET-XYZ" not in out and "OIDC-SECRET-XYZ" not in out


def test_valid_production_configuration_passes(monkeypatch):
    _prod_env(monkeypatch, SESSION_SECRET="a-strong-session-secret", OIDC_ISSUER="https://issuer",
              OIDC_CLIENT_ID="cid", OIDC_CLIENT_SECRET="csecret")
    result = config_check.validate_config()
    assert result["ok"] is True and result["fatal"] == []


def test_dev_environment_does_not_require_production_secrets(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    assert config_check.validate_config()["ok"] is True   # warnings only in dev


# --- database / migrate ------------------------------------------------------

def test_migrate_plan_reports_current_and_target():
    p = migrate.plan()
    assert p["connectable"] is True
    assert p["current"] and p["target"]
    assert p["up_to_date"] is (p["current"] == p["target"])


def test_current_and_target_head_resolve():
    assert migrate.current_revision() is not None
    assert migrate.target_head() == "9483fa25e622"   # portal runtime gates


def test_migrate_is_upgrade_only_no_destructive_calls():
    import pathlib
    src = pathlib.Path("app/deploy/migrate.py").read_text()
    # No destructive CALLS (the docstring may say the word "downgrade" while explaining it won't).
    for banned in ("command.downgrade", ".downgrade(", "drop_all(", "DROP DATABASE", "DROP SCHEMA",
                   "metadata.create_all"):
        assert banned not in src, f"migrate.py must be non-destructive (found {banned})"
    assert "command.upgrade" in src   # it does upgrade to head


# --- readiness additions -----------------------------------------------------

def test_vault_storage_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_STORAGE_ROOT", str(tmp_path / "vault"))
    ok, detail = checks.vault_storage_writable()
    assert ok is True


def test_config_ready_true_in_dev_and_gated_in_prod(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    assert checks.config_ready() is True
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert checks.config_ready() is False


def test_readiness_route_reports_config_and_vault_without_secrets():
    from app.routes.ops import readiness
    body = readiness().body.decode()
    assert '"configuration"' in body and '"vault_storage"' in body
    # public probe must not carry secret values
    assert "SESSION_SECRET" not in body and "CLIENT_SECRET" not in body


# --- service command construction --------------------------------------------

def test_service_uses_app_main_not_demo():
    cmds = service.nssm_commands("install")
    flat = " ".join(" ".join(c) for c in cmds)
    assert "app.main:app" in flat and "demo_app" not in flat and "app.demo" not in flat


def test_service_loads_env_file_so_production_config_is_available():
    # Without --env-file the service starts uvicorn with no production config and app.config raises
    # (SESSION_SECRET required in production) → the service would crash-loop.
    for builder in (service.nssm_commands, service.sc_commands):
        flat = " ".join(" ".join(c) for c in builder("install"))
        assert "--env-file" in flat and "app\\.env" in flat


def test_config_check_cli_loads_env_file(tmp_path, monkeypatch):
    # check-config must reflect the SAME config the app boots with (loaded from app/.env), not just
    # the ambient process env. Simulate a production app/.env and confirm the CLI reads it.
    import os

    from app.deploy import config_check
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / ".env").write_text(
        "CLIENT360_ENVIRONMENT=production\nDATABASE_URL=postgresql://h/db\n"
        "SESSION_SECRET=a-strong-secret\nOIDC_ISSUER=https://i\nOIDC_CLIENT_ID=c\n"
        f"OIDC_CLIENT_SECRET=s\nVAULT_STORAGE_ROOT={tmp_path / 'vault'}\n")
    for var in ("CLIENT360_ENVIRONMENT", "DATABASE_URL", "SESSION_SECRET", "OIDC_ISSUER",
                "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "VAULT_STORAGE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    assert config_check.main([]) == 0            # loads app/.env → valid production config → exit 0
    assert os.getenv("SESSION_SECRET") == "a-strong-secret"


def test_service_actions_construct_for_nssm_and_sc():
    for action in service.ACTIONS:
        assert service.nssm_commands(action)          # each action yields at least one command
        assert service.sc_commands(action)
    # install configures auto-start + auto-restart
    install = " ".join(" ".join(c) for c in service.nssm_commands("install"))
    assert "SERVICE_AUTO_START" in install and "Restart" in install


def test_service_host_and_port_are_configurable():
    cmds = service.nssm_commands("install", host="0.0.0.0", port=9001)
    flat = " ".join(" ".join(c) for c in cmds)
    assert "0.0.0.0" in flat and "9001" in flat


# --- smoke -------------------------------------------------------------------

def test_smoke_route_registration_covers_operational_surface():
    result = smoke.route_registration()
    assert result["ok"] is True, result["missing"]


def test_smoke_auth_gating_public_and_gated():
    result = smoke.auth_gating()
    assert result["ok"] is True
    assert result["public_missing"] == [] and result["gated_leaking"] == []


def test_portal_login_and_health_remain_public():
    from app.security.middleware import PUBLIC_EXACT
    assert "/portal/login" in PUBLIC_EXACT and "/health" in PUBLIC_EXACT
    assert "/readiness" in PUBLIC_EXACT and "/api/portal/login" in PUBLIC_EXACT
    assert "/home" not in PUBLIC_EXACT and "/work" not in PUBLIC_EXACT


# --- admin grant (existing user) ---------------------------------------------

@pytest.fixture
def existing_user():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=f"admin{tag}@e.test", normalized_email=f"admin{tag}@e.test",
            display_name="Admin Candidate", auth_subject=f"subj-{tag}", status="active"
        ).returning(users.c.id)).scalar_one()
    yield {"id": uid, "email": f"admin{tag}@e.test", "subject": f"subj-{tag}"}
    # Leave the user row: grant_administrator writes an immutable audit event referencing it, and the
    # append-only trigger blocks the FK SET NULL a user delete would trigger. Harmless in the test DB.
    with engine.begin() as c:
        c.execute(delete(user_roles).where(user_roles.c.user_id == uid))


def test_grant_admin_is_idempotent_and_no_duplicate(existing_user):
    first = admin.grant_administrator(email=existing_user["email"])
    assert first["granted"] is True and first["already"] is False
    second = admin.grant_administrator(subject=existing_user["subject"])
    assert second["granted"] is False and second["already"] is True
    with engine.connect() as c:
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        n = c.scalar(select(user_roles.c.id).where(
            user_roles.c.user_id == existing_user["id"], user_roles.c.role_id == role_id))
    assert n is not None   # exactly one active grant (unique constraint prevents dups)


def test_grant_admin_requires_existing_user():
    with pytest.raises(RuntimeError):
        admin.grant_administrator(email="nobody-here@example.invalid")
