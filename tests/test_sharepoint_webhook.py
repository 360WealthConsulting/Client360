from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.sharepoint_webhook as webhook
from app.security.middleware import PUBLIC_EXACT


def _client():
    app = FastAPI()
    app.include_router(webhook.router)
    return TestClient(app)


def test_sharepoint_webhook_is_public():
    assert "/api/microsoft/sharepoint/webhook" in PUBLIC_EXACT


def test_sharepoint_webhook_validation_token(monkeypatch):
    monkeypatch.setenv(
        "MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE",
        "test-state",
    )
    response = _client().post(
        "/api/microsoft/sharepoint/webhook?validationToken=abc123"
    )
    assert response.status_code == 200
    assert response.text == "abc123"
    assert response.headers["content-type"].startswith("text/plain")


def test_sharepoint_webhook_rejects_wrong_client_state(monkeypatch):
    monkeypatch.setenv(
        "MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE",
        "correct-state",
    )
    response = _client().post(
        "/api/microsoft/sharepoint/webhook",
        json={"value": [{"clientState": "wrong-state"}]},
     )
    assert response.status_code == 403


def test_sharepoint_webhook_accepts_valid_notification(monkeypatch):
    monkeypatch.setenv(
        "MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE",
        "correct-state",
     )
    called = []
    monkeypatch.setattr(
        webhook,
        "trigger_delta_background",
        lambda: called.append(True),
     )
    response = _client().post(
        "/api/microsoft/sharepoint/webhook",
        json={"value": [{"clientState": "correct-state"}]},
    )
    assert response.status_code == 202
    assert called == [True]


def test_sharepoint_webhook_validation_requires_configured_client_state(monkeypatch):
    monkeypatch.delenv(
        "MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE",
        raising=False,
    )
    response = _client().post(
        "/api/microsoft/sharepoint/webhook",
        json={"value": [{"clientState": "anything"}]},
    )
    assert response.status_code == 503


def test_trigger_coalesces_while_running(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            started.append(
                {
                    "target": target,
                    "name": name,
                    "daemon": daemon,
                }
            )

        def start(self):
            pass

    monkeypatch.setattr(webhook.threading, "Thread", FakeThread)
    monkeypatch.setattr(webhook, "_RUNNING", True)
    monkeypatch.setattr(webhook, "_PENDING", False)

    webhook.trigger_delta_background()

    assert webhook._PENDING is True
    assert started == []
