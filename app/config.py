"""Application configuration and startup validation.

Required / recommended environment variables (see
docs/RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md for the authoritative list):

  CLIENT360_ENVIRONMENT   "production" enables strict mode (secret required,
                          HTTPS-only cookies). Default "development".
  DATABASE_URL            PostgreSQL connection string.
  SESSION_SECRET          Signing key for session cookies. REQUIRED in
                          production (startup fails without it); a marked
                          insecure fallback is used only in development.
  MICROSOFT_TOKEN_KEY     Fernet key encrypting Microsoft OAuth token caches.
                          REQUIRED for Microsoft 365 sync; must be backed up
                          separately from the database.
"""
import logging
import os

logger = logging.getLogger("client360.config")

ENVIRONMENT = os.getenv("CLIENT360_ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

_DEV_SESSION_SECRET = "development-only-change-me"
SESSION_SECRET = os.getenv("SESSION_SECRET")
if IS_PRODUCTION and not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is required in production")
USING_DEV_SESSION_SECRET = not SESSION_SECRET
SESSION_SECRET = SESSION_SECRET or _DEV_SESSION_SECRET
SESSION_HTTPS_ONLY = IS_PRODUCTION


# --- Benefits (Release 0.9.11) -----------------------------------------------
# Detector thresholds and the scheduled-scan cadence are read at call time (env,
# with safe defaults) so operations can tune them without a settings UI or a code
# change, and tests can override them. Defaults preserve the Phase-3 detector
# semantics exactly (windows unchanged; grace periods default to 0 = no change).

def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def benefits_new_hire_window_days() -> int:
    return _int_env("BENEFITS_NEW_HIRE_WINDOW_DAYS", 30)


def benefits_renewal_warning_days() -> int:
    return _int_env("BENEFITS_RENEWAL_WARNING_DAYS", 60)


def benefits_open_enrollment_warning_days() -> int:
    return _int_env("BENEFITS_OE_WARNING_DAYS", 7)


def benefits_census_grace_days() -> int:
    return _int_env("BENEFITS_CENSUS_GRACE_DAYS", 0)


def benefits_document_grace_days() -> int:
    return _int_env("BENEFITS_DOCUMENT_GRACE_DAYS", 0)


def benefits_scan_interval_minutes() -> int:
    # Conservative default (30 min), consistent with the Microsoft document sync;
    # heavier than the 5-min SLA sweep because a detector scan reads the whole book.
    return max(1, _int_env("BENEFITS_SCAN_INTERVAL_MINUTES", 30))


def configuration_warnings() -> list[str]:
    """Return operational configuration warnings (empty when fully configured).

    Read at startup so misconfiguration is loud in the logs without failing a
    development boot. Production-fatal problems (missing SESSION_SECRET) are
    raised at import time above, not returned here.
    """
    warnings: list[str] = []
    if USING_DEV_SESSION_SECRET:
        warnings.append(
            "SESSION_SECRET is not set; using an INSECURE development fallback. "
            "Set SESSION_SECRET before deploying outside development."
        )
    if not os.getenv("MICROSOFT_TOKEN_KEY"):
        warnings.append(
            "MICROSOFT_TOKEN_KEY is not set; Microsoft 365 sync and token "
            "decryption are disabled. Set it and back it up separately from the DB."
        )
    if not os.getenv("DATABASE_URL"):
        warnings.append("DATABASE_URL is not set; using the built-in default connection.")
    return warnings


def validate_startup_configuration() -> None:
    """Emit configuration warnings at startup (called from the app lifespan)."""
    for message in configuration_warnings():
        logger.warning("configuration: %s", message)
