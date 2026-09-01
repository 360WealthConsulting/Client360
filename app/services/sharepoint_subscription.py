from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
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
    return datetime.now(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _headers() -> dict[str, str]:
    from app.services.microsoft_identity import connected_accounts, get_microsoft_access_token

    accounts = connected_accounts()
    if len(accounts) != 1:
        raise RuntimeError(
            "SharePoint subscription management requires exactly one connected "
            f"Microsoft 365 account; found {len(accounts)}."
        )

    token = get_microsoft_access_token(accounts[0])
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
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# Microsoft Graph ECHOES clientState back in the subscription object on create, GET and PATCH. It is a
# SHARED SECRET: the webhook handler compares it to prove a notification really came from Graph, so
# anything that leaks it lets a caller forge notifications. Management output is written to Task
# Scheduler logs, terminals and CI transcripts, so the raw Graph payload must never be returned or
# printed verbatim.
#
# This redacts at the SERVICE boundary rather than in the CLI, so every caller is safe by default and a
# future caller cannot reintroduce the leak by forgetting to sanitise. Redaction is by exact field name
# plus a substring sweep, so a field Graph adds later (an encryption key, a token) is caught rather than
# published. Everything operationally useful — id, resource, notificationUrl, expirationDateTime,
# changeType, applicationId — is preserved untouched.
REDACTED = "***REDACTED***"

_SECRET_FIELDS = frozenset({
    "clientstate",                  # the webhook shared secret
    "encryptioncertificate",        # rich-notification payload key material
    "encryptioncertificateid",
})

_SECRET_SUBSTRINGS = ("secret", "token", "password", "credential", "privatekey", "clientstate")


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return lowered in _SECRET_FIELDS or any(part in lowered for part in _SECRET_SUBSTRINGS)


def redact_secrets(value: Any) -> Any:
    """Recursively replace secret-bearing values with ``REDACTED``.

    Structure-preserving: keys, ordering, and every non-secret value survive unchanged, so the result
    is still a faithful description of the subscription — just not a usable one for forging
    notifications. Only the VALUE is replaced; the key stays visible so an operator can see that a
    secret field exists without learning its contents.
    """
    if isinstance(value, dict):
        return {k: (REDACTED if _is_secret_key(k) else redact_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def ensure_subscription() -> dict[str, Any]:
    existing = find_matching_subscription()

    if existing is None:
        created = create_subscription()
        return {
            "action": "created",
            "subscription": redact_secrets(created),
        }

    sub_id = str(existing.get("id") or "")
    expires_raw = str(existing.get("expirationDateTime") or "")

    if not sub_id:
        raise RuntimeError("Existing subscription has no id")

    if not expires_raw:
        renewed = renew_subscription(sub_id)
        return {
            "action": "renewed",
            "subscription": redact_secrets(renewed),
        }

    expires = _parse_graph_time(expires_raw)
    remaining = expires - _utcnow()

    if remaining <= timedelta(minutes=RENEW_BEFORE_MINUTES):
        renewed = renew_subscription(sub_id)
        return {
            "action": "renewed",
            "subscription": redact_secrets(renewed),
        }

    return {
        "action": "unchanged",
        "subscription": redact_secrets(existing),
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
