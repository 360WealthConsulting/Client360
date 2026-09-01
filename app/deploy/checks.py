"""Shared deployment checks — reused by the config-check CLI AND the /readiness route.

Every function returns presence/booleans only — NEVER a secret value — so the same results can be
surfaced in the public readiness probe without leaking credentials.
"""
from __future__ import annotations

import os
import uuid

# Required in production (fail-fast): the app cannot serve authenticated staff without these.
PRODUCTION_REQUIRED = ("DATABASE_URL", "SESSION_SECRET", "OIDC_ISSUER", "OIDC_CLIENT_ID",
                       "OIDC_CLIENT_SECRET")
# Recommended (warn, not fatal): degraded features or hardening if absent.
RECOMMENDED = ("MICROSOFT_TOKEN_KEY", "MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID",
               "MICROSOFT_CLIENT_SECRET", "MICROSOFT_REDIRECT_URI", "ALLOWED_HOSTS",
               "TRUSTED_PROXY", "PUBLIC_STAFF_URL", "PUBLIC_PORTAL_URL", "LOG_DIR",
               "VAULT_STORAGE_ROOT", "IMAGE_DERIVATIVE_ROOT")

_DEV_SESSION_SECRET = "development-only-change-me"


def is_production() -> bool:
    return os.getenv("CLIENT360_ENVIRONMENT", "development").strip().lower() == "production"


def _present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def using_dev_session_secret() -> bool:
    value = (os.getenv("SESSION_SECRET") or "").strip()
    return value == "" or value == _DEV_SESSION_SECRET


def dev_auth_enabled() -> bool:
    return (os.getenv("CLIENT360_DEV_AUTH") or "").strip().lower() in {"1", "true", "yes", "on"}


def vault_storage_writable() -> tuple[bool, str | None]:
    """True if the Vault storage root exists and is writable (probe write + delete a temp marker)."""
    try:
        from app.services.vault.storage import storage_root
        root = storage_root()                       # creates the dir if missing
        marker = root / f".readiness-{uuid.uuid4().hex}"
        marker.write_bytes(b"ok")
        marker.unlink()
        return True, str(root)
    except Exception as exc:  # noqa: BLE001 — report, never raise into a probe
        return False, str(exc)


def image_derivative_storage_writable() -> tuple[bool, str | None]:
    """True if the normalized-image derivative root resolves and is writable (probe write + delete).

    Also enforces the production posture: ``derivative_root()`` refuses an unset or relative
    ``IMAGE_DERIVATIVE_ROOT`` in production, because the development default is relative to the
    service working directory and could place generated derivatives inside a deployed source tree.
    Derivatives are generated files only — no original document is ever stored here."""
    try:
        from app.services.image_normalization import derivative_root
        root = derivative_root()                    # creates the dir if missing; raises in prod if unset
        marker = root / f".readiness-{uuid.uuid4().hex}"
        marker.write_bytes(b"ok")
        marker.unlink()
        return True, str(root)
    except Exception as exc:  # noqa: BLE001 — report, never raise into a probe
        return False, str(exc)


def config_presence() -> dict:
    """Presence-only map for required + recommended config (no values). For readiness + CLI."""
    prod = is_production()
    present = {name: _present(name) for name in (*PRODUCTION_REQUIRED, *RECOMMENDED)}
    return {
        "environment": os.getenv("CLIENT360_ENVIRONMENT", "development"),
        "production": prod,
        "present": present,
        "session_secret_ok": not (prod and using_dev_session_secret()),
        "dev_auth_disabled_in_prod": not (prod and dev_auth_enabled()),
        "secure_cookies": prod,                     # SESSION_HTTPS_ONLY is derived from production
    }


def config_ready() -> bool:
    """True when the required production config is present + safe (the readiness config gate)."""
    p = config_presence()
    if not p["production"]:
        return True                                 # dev/staging never gates on production secrets
    if not (p["session_secret_ok"] and p["dev_auth_disabled_in_prod"]):
        return False
    return all(p["present"][name] for name in PRODUCTION_REQUIRED)
