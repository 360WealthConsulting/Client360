"""End-to-end (real browser) test harness.

These tests boot the actual ASGI application under uvicorn and drive it with a
real Chromium browser via Playwright — the layer the unit suite (which calls
handlers directly) cannot cover: HTML rendering, static assets, redirects, and
client-side behaviour.

This directory is deliberately OUTSIDE ``tests/`` (pytest ``testpaths=["tests"]``)
so the browser suite never runs as part of the unit run. Invoke it explicitly:

    python -m pytest e2e/ -q

It requires Playwright + browsers (``requirements-e2e.txt`` + ``playwright
install``) and a migrated disposable test database (same ``DATABASE_URL`` guard
as the unit suite). If Playwright is not installed the whole directory is skipped
rather than failing, so ``python -m pytest`` in a browser-less environment is a
no-op here.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

# Skip the entire browser suite cleanly when the toolchain is absent.
pytest.importorskip(
    "playwright",
    reason="Playwright not installed; `pip install -r requirements-e2e.txt && playwright install`.",
)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="session")
def live_server():
    """Boot the real app under uvicorn against the disposable test DB; yield its base URL."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_URL": os.environ.get("DATABASE_URL", "postgresql://localhost/client360_test"),
        "SESSION_SECRET": os.environ.get("SESSION_SECRET", "test-session-secret-not-for-production"),
        "CLIENT360_ENVIRONMENT": "development",
        "SESSION_HTTPS_ONLY": "false",
        # Enable the development-only sign-in provider so the browser can authenticate
        # without an external IdP. dev_auth_enabled() still refuses under production.
        "CLIENT360_DEV_AUTH": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(60):
            if proc.poll() is not None:
                output = proc.stdout.read().decode() if proc.stdout else ""
                raise RuntimeError(f"uvicorn exited early (code {proc.returncode}):\n{output}")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("uvicorn did not become ready within the timeout")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


@pytest.fixture(scope="session")
def seeded_client(live_server):
    """Create one canonical person (+ household + linked source contact) for the
    authenticated browser flows. Written to the same database the live server reads."""
    import json
    import uuid

    from sqlalchemy import insert

    from app.db import engine, households, people, person_source_links, source_contacts

    tag = "E2E" + uuid.uuid4().hex[:6]
    name = f"E2e Client {tag}"
    email = f"{tag.lower()}@example.com"
    with engine.begin() as connection:
        household_id = connection.execute(
            households.insert().values(name=f"E2E Household {tag}").returning(households.c.id)
        ).scalar_one()
        person_id = connection.execute(
            people.insert().values(
                household_id=household_id, full_name=name, active=True, primary_email=email
            ).returning(people.c.id)
        ).scalar_one()
        source_id = connection.execute(
            insert(source_contacts).values(
                source_system="wealthbox", source_file="e2e.csv", source_hash=uuid.uuid4().hex,
                raw_data=json.dumps({"name": name}), full_name=name, last_name=tag, email=email,
            ).returning(source_contacts.c.id)
        ).scalar_one()
        connection.execute(insert(person_source_links).values(
            person_id=person_id, source_contact_id=source_id,
            match_method="e2e", match_score=100, confirmed=True))
    return {"person_id": person_id, "name": name, "tag": tag}


@pytest.fixture
def app_page(page, live_server):
    """A browser page signed in as the deterministic Administrator persona via the
    development-only sign-in provider (real session; full RBAC)."""
    page.goto(f"{live_server}/dev-auth/login")
    page.click('button[data-persona="admin"]')
    page.wait_for_load_state("networkidle")
    assert "/auth/login" not in page.url  # signed in, not bounced to the IdP login
    return page
