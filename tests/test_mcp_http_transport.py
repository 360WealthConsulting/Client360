"""HTTP transport and audit trail for the MCP interface.

Covers the boundary the network actually sees: that ``/mcp`` is unreachable when the feature is off,
refuses every request without a valid bearer token, does not accept a staff browser session as a
substitute, and records each call on the tamper-evident audit chain with the actor, the tool, the
target and the outcome — and without the caller's content.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, insert, select
from starlette.testclient import TestClient

from app.db import (
    audit_events,
    capabilities,
    documents,
    engine,
    households,
    mcp_access_tokens,
    people,
    record_assignments,
    role_capabilities,
    roles,
    user_roles,
    users,
)
from app.main import app
from app.mcp import audit as mcp_audit
from app.mcp import tokens as mcp_tokens
from app.mcp.scopes import CLIENT_READ, DOCUMENT_READ

MCP_CAPABILITIES = ("mcp.access", "mcp.client.read", "mcp.document.read",
                    "client.read", "documents.view")


@pytest.fixture(autouse=True)
def _mcp_enabled(monkeypatch):
    monkeypatch.setenv("CLIENT360_MCP_ENABLED", "true")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def actor():
    """A staff user who really holds the MCP capabilities, with a real issued token."""
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"mcp-http-{tag}@e.test", normalized_email=f"mcp-http-{tag}@e.test",
            display_name=f"HTTP {tag}", auth_subject=f"mcp-http-{tag}",
            status="active").returning(users.c.id)).scalar_one()
        role_id = c.execute(roles.insert().values(
            code=f"mcp-http-role-{tag}", name=f"MCP HTTP {tag}", active=True).returning(
            roles.c.id)).scalar_one()
        for code in MCP_CAPABILITIES:
            cap_id = c.scalar(select(capabilities.c.id).where(capabilities.c.code == code))
            assert cap_id is not None, (
                f"capability {code!r} is missing — migration mcp01 seeds the mcp.* ones")
            c.execute(role_capabilities.insert().values(role_id=role_id, capability_id=cap_id))
        c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))

        hh_id = c.execute(households.insert().values(
            name=f"HTTP Household {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Harriet Http {tag}", first_name="Harriet", last_name=f"Http{tag}",
            primary_email=f"harriet-{tag}@e.test", normalized_email=f"harriet-{tag}@e.test",
            household_id=hh_id, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=date.today()))
        doc_id = c.execute(documents.insert().values(
            person_id=pid, original_name="return.pdf", stored_name=f"{uuid.uuid4().hex}.pdf",
            storage_path=f"/never/read/{uuid.uuid4().hex}.pdf", size_bytes=1,
            sha256=uuid.uuid4().hex, status="active", classification="tax",
            display_name=f"Harriet 1040 {tag}", tags={"tax_year": "2024"}).returning(
            documents.c.id)).scalar_one()

    token = mcp_tokens.issue_token(user_id=uid, scopes=[CLIENT_READ, DOCUMENT_READ],
                                   label="http test")
    ids = {"tag": tag, "uid": uid, "pid": pid, "hh_id": hh_id, "doc_id": doc_id, "token": token}
    yield ids

    with engine.begin() as c:
        c.execute(delete(documents).where(documents.c.id == doc_id))
        c.execute(delete(mcp_access_tokens).where(mcp_access_tokens.c.user_id == uid))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == uid))
        c.execute(delete(people).where(people.c.id == pid))
        c.execute(delete(households).where(households.c.id == hh_id))
        c.execute(delete(user_roles).where(user_roles.c.user_id == uid))
        c.execute(delete(role_capabilities).where(role_capabilities.c.role_id == role_id))
        c.execute(delete(roles).where(roles.c.id == role_id))
        # The user stays: audit_events is append-only, so deleting the actor is refused.


def _rpc(method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


# --- the endpoint is closed by default --------------------------------------

def test_endpoint_is_absent_when_the_feature_is_disabled(client, monkeypatch):
    monkeypatch.setenv("CLIENT360_MCP_ENABLED", "false")
    response = client.post("/mcp", json=_rpc("tools/list"),
                           headers={"Authorization": "Bearer anything"})
    assert response.status_code == 404, "a disabled MCP surface must not announce itself"


def test_request_without_a_token_is_rejected(client):
    response = client.post("/mcp", json=_rpc("tools/list"))
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", [
    "Bearer wrong-token", "Basic dXNlcjpwYXNz", "wrong-token", "Bearer ",
])
def test_bad_credentials_are_rejected(client, header):
    response = client.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": header})
    assert response.status_code == 401


def test_a_staff_session_cookie_is_not_a_valid_mcp_credential(client, actor):
    """MCP tokens and browser sessions are disjoint credential classes, by design."""
    from app.security.service import create_session
    session_token = create_session(actor["uid"])
    response = client.post("/mcp", json=_rpc("tools/list"),
                           headers={"Authorization": f"Bearer {session_token}"})
    assert response.status_code == 401


def test_an_mcp_token_cannot_open_a_staff_page(client, actor):
    """...and the reverse: an MCP token is worthless against the web UI."""
    response = client.get("/documents", headers={"Authorization": f"Bearer {actor['token']}"},
                          follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403, 404)
    assert response.status_code != 200


def test_get_does_not_offer_a_stream(client, actor):
    response = client.get("/mcp", headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"


def test_oversized_body_is_refused(client, actor):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "search_clients", "arguments": {"query": "x" * 400_000}}}
    response = client.post("/mcp", json=payload,
                           headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 413


def test_malformed_json_is_a_parse_error(client, actor):
    response = client.post("/mcp", content=b"{not json",
                           headers={"Authorization": f"Bearer {actor['token']}",
                                    "Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


# --- an authenticated caller works end to end -------------------------------

def test_authenticated_caller_lists_tools(client, actor):
    response = client.post("/mcp", json=_rpc("tools/list"),
                           headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 200
    names = [t["name"] for t in response.json()["result"]["tools"]]
    assert "search_clients" in names and len(names) == 6


def test_authenticated_caller_reaches_a_tool(client, actor):
    response = client.post("/mcp", json=_rpc("tools/call", {
        "name": "search_clients", "arguments": {"query": f"Http{actor['tag']}"}}),
        headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 200
    payload = json.loads(response.json()["result"]["content"][0]["text"])
    assert any(r["id"] == actor["pid"] for r in payload["results"])


def test_a_scope_the_token_lacks_is_refused_over_http(client, actor):
    """The token carries client:read and document:read, but not document:content:read."""
    response = client.post("/mcp", json=_rpc("tools/call", {
        "name": "get_document_text", "arguments": {"document_id": actor["doc_id"]}}),
        headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "document:content:read" in result["content"][0]["text"]


def test_notification_only_body_gets_no_content(client, actor):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                           headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 202
    assert response.content == b""


# --- audit trail -------------------------------------------------------------

def _mcp_events(actor_user_id):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(audit_events).where(audit_events.c.actor_user_id == actor_user_id,
                                       audit_events.c.action == mcp_audit.ACTION)
            .order_by(audit_events.c.id)).mappings()]


def test_every_call_is_audited_with_actor_tool_target_and_outcome(client, actor):
    client.post("/mcp", json=_rpc("tools/call", {
        "name": "get_document", "arguments": {"document_id": actor["doc_id"]}}),
        headers={"Authorization": f"Bearer {actor['token']}"})

    events = _mcp_events(actor["uid"])
    assert events, "an MCP call must appear on the audit chain"
    event = events[-1]
    assert event["actor_user_id"] == actor["uid"]
    assert event["entity_id"] == "get_document"
    assert event["outcome"] == "success"
    assert event["occurred_at"] is not None
    metadata = event["metadata"] or {}
    assert metadata["tool"] == "get_document"
    assert metadata["target_type"] == "document"
    assert metadata["target_id"] == str(actor["doc_id"])
    # The chain's integrity fields are populated like any other entry.
    assert event["entry_hash"] and event["prev_hash"]


def test_denials_are_audited_too(client, actor):
    before = len(_mcp_events(actor["uid"]))
    client.post("/mcp", json=_rpc("tools/call", {
        "name": "get_document_text", "arguments": {"document_id": actor["doc_id"]}}),
        headers={"Authorization": f"Bearer {actor['token']}"})
    events = _mcp_events(actor["uid"])
    assert len(events) == before + 1
    assert events[-1]["outcome"] == "denied"
    assert "document:content:read" in (events[-1]["metadata"] or {}).get("detail", "")


def test_the_audit_trail_never_records_caller_content_or_document_text(client, actor):
    """A query string can contain anything the user pasted; it must not land in the ledger."""
    secret = f"SSN-{uuid.uuid4().hex}"
    client.post("/mcp", json=_rpc("tools/call", {
        "name": "search_clients", "arguments": {"query": secret}}),
        headers={"Authorization": f"Bearer {actor['token']}"})
    blob = json.dumps(_mcp_events(actor["uid"]), default=str)
    assert secret not in blob


def test_auditing_failure_never_breaks_a_call(client, actor, monkeypatch):
    def _explode(**kwargs):
        raise RuntimeError("audit chain unavailable")

    monkeypatch.setattr("app.mcp.audit.write_audit_event", _explode)
    response = client.post("/mcp", json=_rpc("tools/call", {
        "name": "search_clients", "arguments": {"query": f"Http{actor['tag']}"}}),
        headers={"Authorization": f"Bearer {actor['token']}"})
    assert response.status_code == 200
    assert "isError" not in response.json()["result"]


def test_last_used_at_is_stamped_on_the_token(client, actor):
    client.post("/mcp", json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {actor['token']}"})
    with engine.connect() as c:
        last_used = c.scalar(select(mcp_access_tokens.c.last_used_at).where(
            mcp_access_tokens.c.user_id == actor["uid"]))
    assert last_used is not None
    assert (datetime.now(UTC) - last_used).total_seconds() < 120
