"""E1.2 — Development environment & build system acceptance tests.

Verifies the reproducible-dev-environment artifacts exist and are coherent, and
that the application's public health endpoint responds. These tests do not start
background services (no lifespan), so they have no side effects.
"""
from __future__ import annotations

import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_health_endpoint_registered_and_ok():
    """The public liveness endpoint exists and returns an ok payload.

    Called directly (no httpx/TestClient dependency): /health is a plain,
    DB-independent handler used by the Docker healthcheck and dev smoke checks.
    """
    from app.main import app
    from app.routes.dashboard import health

    paths = {getattr(r, "path", "") for r in app.router.routes}
    assert "/health" in paths, "/health route must be registered"

    payload = health()
    assert payload.get("status") == "ok"


def test_infrastructure_files_exist():
    infra = REPO_ROOT / "infrastructure"
    assert (infra / "docker-compose.yml").is_file()
    assert (infra / "Dockerfile").is_file()
    assert (REPO_ROOT / ".dockerignore").is_file()


def test_dockerfile_pins_python_312():
    dockerfile = (REPO_ROOT / "infrastructure" / "Dockerfile").read_text()
    assert "python:3.12" in dockerfile, "Dockerfile must pin the Python 3.12 base image"


def test_compose_defines_postgres_and_optional_app():
    compose = (REPO_ROOT / "infrastructure" / "docker-compose.yml").read_text()
    assert "postgres:16" in compose, "compose must pin postgres:16 (matches CI)"
    assert "profiles:" in compose and "app" in compose, "app service must be optional (profile)"
    assert "pg_isready" in compose, "db service must define a healthcheck"


def test_dev_script_exists_and_is_executable():
    dev = REPO_ROOT / "scripts" / "dev.sh"
    assert dev.is_file()
    mode = dev.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/dev.sh must be executable"
    body = dev.read_text()
    # The one documented workflow exposes these commands.
    for command in ("setup", "doctor", "migrate", "run", "db-up", "db-down"):
        assert command in body, f"dev.sh missing command: {command}"
    # Must never reset/drop the development database (safety).
    assert "dropdb" not in body, "dev.sh must not drop the development database"


def test_dev_script_refuses_production():
    body = (REPO_ROOT / "scripts" / "dev.sh").read_text()
    assert "REFUSED" in body and "production" in body


def test_local_development_guide_present():
    assert (REPO_ROOT / "docs" / "LOCAL_DEVELOPMENT.md").is_file()


def test_python_version_pinned_to_312():
    assert (REPO_ROOT / ".python-version").read_text().strip().startswith("3.12")


def test_env_example_has_no_committed_secrets():
    example = (REPO_ROOT / "config" / ".env.example").read_text()
    for line in example.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in {"SESSION_SECRET", "MICROSOFT_TOKEN_KEY"}:
            assert value.strip() == "", f"{key} must be empty in the committed example"
    # Sanity: the real secret file is not present in the build context root as tracked.
    assert "app/.env" in (REPO_ROOT / ".dockerignore").read_text()
