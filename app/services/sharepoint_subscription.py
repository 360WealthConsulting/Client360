from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_LIFETIME_MINUTES = 4200
RENEW_BEFORE_MINUTES = 720


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _headers() -> dict[str, str]:
    from app.services.microsoft_identity import get_microsoft_access_token
    token = get_microsoft_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def subscription_resource() -> str:
    drive_id = _required("MICROSOFT_SHAREPOINT_DRIVE_ID")
    return f"drives/{drive_id}/root"


def notification_url() -> str:
    return _required("MICROSOFT_SHAREPOINT_WEBHOOK_URL")


def client_state() -> str:
    value = _required("MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE")
    if len(value) > 128:
        raise RuntimeError("MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE exceeds 128 characters")
    return value


def desired_expiration() -> str:
    return _iso_z(_utcnow() + timedelta(minutes=DEFAULT_LIFETIME_MINUTES))


def list_subscriptions() -> list[dict[str, Any]]:
    response = requests.get(
        f"{GRAPH_BASE}/subscriptions",
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    value = payload.get("value", [])
    return value if isinstance(value, list) else []


def find_matching_subscription() -> dict[str, Any] | None:
    wanted_resource = subscription_resource().lstrip("/")
    wanted_url = notification_url()

    for sub in list_subscriptions():
        resource = str(sub.get("resource") or "").lstrip("/")
        url = str(sub.get("notificationUrl") or "")
        if resource == wanted_resource and url == wanted_url:
            return sub

    return None


def create_subscription() -> dict[str, Any]:
    body = {
        "changeType": "updated",
        "notificationUrl": notification_url(),
        "resource": subscription_resource(),
        "expirationDateTime": desired_expiration(),
        "clientState": client_state(),
        "latestSupportedTlsVersion": "v1_2",
    }

    response = requests.post(
        f"{GRAPH_BASE}/subscriptions",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def renew_subscription(subscription_id: str) -> dict[str, Any]:
    body = {
        "expirationDateTime": desired_expiration(),
    }

    response = requests.patch(
        f"{GRAPH_BASE}/subscriptions/{subscription_id}",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _parse_graph_time(raw: str) -> datetime:
    value = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_subscription() -> dict[str, Any]:
    existing = find_matching_subscription()

    if existing is None:
        created = create_subscription()
        return {
            "action": "created",
            "subscription": created,
        }

    sub_id = str(existing.get("id") or "")
    expires_raw = str(existing.get("expirationDateTime") or "")

    if not sub_id:
        raise RuntimeError("Existing subscription has no id")

    if not expires_raw:
        renewed = renew_subscription(sub_id)
        return {
            "action": "renewed",
            "subscription": renewed,
        }

    expires = _parse_graph_time(expires_raw)
    remaining = expires - _utcnow()

    if remaining <= timedelta(minutes=RENEW_BEFORE_MINUTES):
        renewed = renew_subscription(sub_id)
        return {
            "action": "renewed",
            "subscription": renewed,
        }

    return {
        "action": "unchanged",
        "subscription": existing,
    }


def subscription_status() -> dict[str, Any]:
    existing = find_matching_subscription()

    if existing is None:
        return {
            "configured": True,
            "exists": False,
            "resource": subscription_resource(),
            "notification_url": notification_url(),
        }

    return {
        "configured": True,
        "exists": True,
        "id": existing.get("id"),
        "resource": existing.get("resource"),
        "notification_url": existing.get("notificationUrl"),
        "expiration": existing.get("expirationDateTime"),
        "change_type": existing.get("changeType"),
    }
