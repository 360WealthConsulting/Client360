"""Merge-forward coverage — server Drake integration + release UI, on release/0.13.0.

Verifies the artifacts brought forward from the Windows-server backup plus the release changes they were
merged with:
  * Entra/Azure OIDC email fallback (preferred_username / upn) in oidc.exchange_code
  * the drake01 migration created the four production drake_* tables
  * the Drake identity-review route renders (template + tables + wiring) and its reject/defer actions work
  * the Drake tax-workspace read surfaces Drake returns (and is fail-soft when the table is absent)
  * the Vault upload JS + template hooks are present
  * release back-navigation + human audit labels are still present in workspace.html (both sides merged)
  * the advisor capability boundary is unchanged (record.read_all yes; write_all / admin no)

Temp/test rows only; Drake seed rows are cleaned up.
"""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import engine, people
from app.security.models import Principal

REPO = Path(__file__).resolve().parents[1]


# --- 1. Entra / Azure OIDC email fallback ------------------------------------

def _provider(monkeypatch):
    from app.integrations.identity import oidc
    p = oidc.OidcIdentityProvider("https://identity.example", "client", "secret")
    monkeypatch.setattr(p, "_discovery",
                        lambda: {"token_endpoint": "https://identity.example/token",
                                 "jwks_uri": "https://identity.example/jwks"})

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"id_token": "tok"}

    class _Key:
        key = "k"

    class _JWKClient:
        def __init__(self, *a, **k): pass
        def get_signing_key_from_jwt(self, _t): return _Key()

    monkeypatch.setattr(oidc.requests, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(oidc.jwt, "PyJWKClient", _JWKClient)
    return oidc, p


def test_entra_email_falls_back_to_preferred_username(monkeypatch):
    oidc, p = _provider(monkeypatch)
    monkeypatch.setattr(oidc.jwt, "decode",
                        lambda *a, **k: {"sub": "s1", "preferred_username": "lauren@360wc.example",
                                         "name": "Lauren A", "amr": ["mfa"]})
    claims = p.exchange_code(code="c", redirect_uri="https://client360.example/auth/callback")
    assert claims.email == "lauren@360wc.example"      # no `email` claim → preferred_username used
    assert claims.mfa_authenticated is True


def test_entra_email_falls_back_to_upn(monkeypatch):
    oidc, p = _provider(monkeypatch)
    monkeypatch.setattr(oidc.jwt, "decode",
                        lambda *a, **k: {"sub": "s2", "upn": "sarah@360wc.example", "amr": []})
    claims = p.exchange_code(code="c", redirect_uri="https://client360.example/auth/callback")
    assert claims.email == "sarah@360wc.example"       # neither email nor preferred_username → upn
    assert claims.display_name == "sarah@360wc.example"


def test_email_claim_still_wins_when_present(monkeypatch):
    oidc, p = _provider(monkeypatch)
    monkeypatch.setattr(oidc.jwt, "decode",
                        lambda *a, **k: {"sub": "s3", "email": "primary@360wc.example",
                                         "preferred_username": "other@360wc.example"})
    claims = p.exchange_code(code="c", redirect_uri="https://client360.example/auth/callback")
    assert claims.email == "primary@360wc.example"


# --- 2. drake01 migration created the production tables -----------------------

def test_drake_tables_exist():
    with engine.connect() as c:
        present = set(c.scalars(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'drake_%'")))
    assert {"drake_client_returns", "drake_efile_records", "drake_identity",
            "drake_identity_match_candidates"} <= present


# --- 3. Drake identity-review route + actions ---------------------------------

@pytest.fixture
def drake_candidate():
    tag = uuid.uuid4().hex[:8]
    h = f"h{tag}"
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Casey", last_name=f"Drake{tag}", full_name=f"Casey Drake{tag}",
            active=True).returning(people.c.id)).scalar_one()
        c.execute(text("""INSERT INTO drake_identity
            (identifier_hash, primary_person_id, first_year, last_year, return_count, taxpayer_name)
            VALUES (:h, NULL, 2021, 2024, 4, :name)"""), {"h": h, "name": f"Casey Drake{tag}"})
        c.execute(text("""INSERT INTO drake_identity_match_candidates
            (identifier_hash, person_id, score, reasons, rank, status)
            VALUES (:h, :pid, 88, '[]'::jsonb, 1, 'pending')"""), {"h": h, "pid": pid})
    yield {"hash": h, "person_id": pid, "name": f"Casey Drake{tag}"}
    with engine.begin() as c:
        c.execute(text("DELETE FROM drake_identity_match_candidates WHERE identifier_hash=:h"), {"h": h})
        c.execute(text("DELETE FROM drake_identity WHERE identifier_hash=:h"), {"h": h})
        c.execute(people.delete().where(people.c.id == pid))


def _req():
    from starlette.requests import Request
    r = Request({"type": "http", "method": "GET", "path": "/matches/drake", "headers": [],
                 "query_string": b"", "state": {}})
    r.state.principal = Principal(1, "admin@e.test", "Admin",
                                  frozenset({"client.read", "record.read_all"}))
    r.state.request_id = "t"
    return r


def test_drake_identity_review_renders_empty_state():
    from app.routes.matches import drake_identity_review_page
    resp = drake_identity_review_page(_req(), status="pending", saved=None)
    assert resp.status_code == 200
    assert b"Drake" in resp.body            # the template rendered (proves template + tables + wiring)


def test_drake_identity_review_shows_seeded_candidate(drake_candidate):
    from app.routes.matches import drake_identity_review_page
    body = drake_identity_review_page(_req(), status="pending", saved=None).body.decode()
    assert drake_candidate["name"] in body


def test_drake_reject_and_defer_change_status(drake_candidate):
    from app.routes.matches import defer_drake_identity, reject_drake_identity
    h = drake_candidate["hash"]

    def _status():
        with engine.connect() as c:
            return c.scalar(text("SELECT status FROM drake_identity_match_candidates "
                                 "WHERE identifier_hash=:h"), {"h": h})

    assert defer_drake_identity(h).status_code == 303
    assert _status() == "deferred"
    assert reject_drake_identity(h).status_code == 303      # reject accepts pending OR deferred
    assert _status() == "rejected"


# --- 4. Drake tax-workspace read (surfaces returns; fail-soft) ---------------

def test_drake_tax_workspace_surfaces_returns():
    from app.services.client360.tax_workspace import _drake_returns
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Robin", last_name=f"Filer{tag}", full_name=f"Robin Filer{tag}",
            active=True).returning(people.c.id)).scalar_one()
        c.execute(text("""INSERT INTO drake_client_returns
            (tax_year, source_row_number, taxpayer_first_name, taxpayer_last_name,
             return_type, source_updated_at, raw_data)
            VALUES (2024, :rn, 'Robin', :ln, '1040', now(), '{}'::jsonb)"""),
            {"rn": int(tag[:6], 16), "ln": f"Filer{tag}"})
    try:
        with engine.connect() as conn:
            rows = _drake_returns(conn, [pid])
        assert any(r["year"] == 2024 for r in rows)      # _drake_returns maps tax_year -> "year"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM drake_client_returns WHERE taxpayer_last_name=:ln"),
                      {"ln": f"Filer{tag}"})
            c.execute(people.delete().where(people.c.id == pid))


def test_drake_read_fail_soft_when_table_absent():
    # to_regclass returning None (table not provisioned) must degrade to [] rather than raise.
    from app.services.client360 import tax_workspace

    class _Conn:
        def execute(self, *a, **k):
            class _R:
                def scalar(self_inner): return None
            return _R()
    assert tax_workspace._drake_returns(_Conn(), [1]) == []


# --- 5. Vault upload JS + template hooks present -----------------------------

def test_vault_upload_hooks_present():
    ws = (REPO / "app/templates/client360/workspace.html").read_text()
    js = (REPO / "app/static/js/app.js").read_text()
    assert "vault-upload-toggle" in ws and 'id="vault-upload"' in ws
    assert "vault-upload-toggle" in js and "/api/vault/documents" in js


# --- 6. release UI (backnav + audit labels) AND server Drake both in workspace.html ---

def test_workspace_html_contains_both_release_and_server_changes():
    ws = (REPO / "app/templates/client360/workspace.html").read_text()
    # release (#184 backnav, #189 human audit labels)
    assert "c360-backnav" in ws
    assert "a.entity_label" in ws and "a.actor_name" in ws
    # server (Drake tax section + vault upload)
    assert "Drake Tax" in ws and "vault-upload-toggle" in ws


# --- 7. advisor capability boundary unchanged --------------------------------

def test_advisor_boundary_read_all_not_write_all_not_admin():
    from sqlalchemy import select

    from app.db import capabilities, role_capabilities, roles
    with engine.connect() as c:
        rid = c.scalar(select(roles.c.id).where(roles.c.code == "advisor"))
        caps = set(c.scalars(select(capabilities.c.code).select_from(
            role_capabilities.join(capabilities, capabilities.c.id == role_capabilities.c.capability_id))
            .where(role_capabilities.c.role_id == rid)))
    assert "record.read_all" in caps
    assert "record.write_all" not in caps
    assert "identity.manage" not in caps and "role.manage" not in caps
