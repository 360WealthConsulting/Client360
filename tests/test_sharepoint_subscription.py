from datetime import UTC, datetime, timedelta

import pytest

import app.services.sharepoint_subscription as sub


def test_subscription_resource(monkeypatch):
    monkeypatch.setenv("MICROSOFT_SHAREPOINT_DRIVE_ID", "drive-123")
    assert sub.subscription_resource() == "drives/drive-123/root"


def test_client_state_rejects_too_long(monkeypatch):
    monkeypatch.setenv("MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE", "x" * 129)
    with pytest.raises(RuntimeError):
        sub.client_state()


def test_ensure_creates_when_missing(monkeypatch):
    monkeypatch.setattr(sub, "find_matching_subscription", lambda: None)
    monkeypatch.setattr(sub, "create_subscription", lambda: {"id": "new"})
    result = sub.ensure_subscription()
    assert result["action"] == "created"
    assert result["subscription"]["id"] == "new"


def test_ensure_renews_near_expiry(monkeypatch):
    expires = (datetime.now(UTC) + timedelta(minutes=60)).isoformat()
    monkeypatch.setattr(
        sub,
        "find_matching_subscription",
        lambda: {"id": "abc", "expirationDateTime": expires},
    )
    monkeypatch.setattr(sub, "renew_subscription", lambda value: {"id": value})
    result = sub.ensure_subscription()
    assert result["action"] == "renewed"
    assert result["subscription"]["id"] == "abc"


def test_ensure_leaves_healthy_subscription(monkeypatch):
    expires = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        sub,
        "find_matching_subscription",
        lambda: {"id": "abc", "expirationDateTime": expires},
    )
    result = sub.ensure_subscription()
    assert result["action"] == "unchanged"
