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


def insurance_scan_interval_minutes() -> int:
    # Same conservative default (30 min) as the benefits detector scan; the insurance scan
    # likewise reads the whole book, so it runs less often than the 5-min SLA sweep.
    return max(1, _int_env("INSURANCE_SCAN_INTERVAL_MINUTES", 30))


def outbox_dispatcher_enabled() -> bool:
    """Whether the transactional-outbox dispatcher runs as a scheduler job.

    Default OFF (E1.6 / F1.3): the outbox mechanism ships, but nothing publishes
    events yet, so the dispatcher is not scheduled unless explicitly enabled —
    keeping runtime behavior unchanged by default.
    """
    return os.getenv("OUTBOX_DISPATCHER_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def outbox_dispatch_interval_seconds() -> int:
    # Poll cadence for the outbox dispatcher; minimum 5s to avoid a hot loop.
    return max(5, _int_env("OUTBOX_DISPATCH_INTERVAL_SECONDS", 30))


def automation_enabled() -> bool:
    """Whether the Automation runner tick runs as a scheduler job (Phase D.22).

    Default OFF: the Automation platform ships (jobs/schedules/runs are managed and can be run
    on demand via the API), but the background tick that sweeps due schedules is not registered
    unless explicitly enabled — keeping runtime behavior unchanged by default (same posture as the
    outbox dispatcher).
    """
    return os.getenv("AUTOMATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def automation_tick_interval_seconds() -> int:
    # Poll cadence for the Automation runner tick; minimum 5s to avoid a hot loop.
    return max(5, _int_env("AUTOMATION_TICK_INTERVAL_SECONDS", 60))


def runtime_refresh_enabled() -> bool:
    """Whether the Runtime Configuration Engine's periodic safe-refresh runs as a scheduler job
    (Phase D.28).

    Default OFF: the runtime engine hydrates once at startup and serves the cached effective
    configuration; the background refresh that rebuilds the snapshot on a cadence is not registered
    unless explicitly enabled (same posture as the automation tick / outbox dispatcher). A manual
    refresh is always available via the /runtime API.
    """
    return os.getenv("RUNTIME_REFRESH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def runtime_refresh_interval_seconds() -> int:
    # Poll cadence for the Runtime Configuration Engine refresh; minimum 15s to avoid churn.
    return max(15, _int_env("RUNTIME_REFRESH_INTERVAL_SECONDS", 300))


def runtime_worker_id() -> str:
    """A stable per-process worker id for distributed runtime coordination (Phase D.29).

    Prefers an explicit ``RUNTIME_WORKER_ID`` (e.g. a pod name); otherwise derives a stable id from
    the hostname + pid so each worker process is uniquely and reproducibly identifiable within a
    single boot. Cluster coordination keys worker rows on this id.
    """
    explicit = os.getenv("RUNTIME_WORKER_ID", "").strip()
    if explicit:
        return explicit
    import os as _os
    import socket
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return f"{host}:{_os.getpid()}"


def runtime_coordination_enabled() -> bool:
    """Whether the distributed runtime coordination scheduler jobs (worker heartbeat, stale-worker
    cleanup) run (Phase D.29).

    Default OFF: the coordination metadata (workers/generations/events) is always maintained on
    demand and every worker converges via the persisted generation on refresh; the periodic
    heartbeat/cleanup jobs register only when explicitly enabled (same posture as the outbox
    dispatcher / runtime refresh). Cross-process invalidation still flows through the transactional
    outbox when the outbox dispatcher is enabled.
    """
    return os.getenv("RUNTIME_COORDINATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def runtime_heartbeat_interval_seconds() -> int:
    # Cadence for a worker's coordination heartbeat + converge-if-behind check; minimum 10s.
    return max(10, _int_env("RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 30))


def runtime_worker_ttl_seconds() -> int:
    # A worker is considered stale after this many seconds without a heartbeat; minimum 30s.
    return max(30, _int_env("RUNTIME_WORKER_TTL_SECONDS", 120))


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
    # No DATABASE_URL warning here: there is no built-in default. app/db.py and
    # app/database/schema.py both raise at import if it is unset, so the process
    # cannot reach startup validation without one. The warning this replaces
    # claimed a fallback that has never existed.
    return warnings


def validate_startup_configuration() -> None:
    """Fail fast on production-fatal misconfiguration, then emit warnings (called from the app
    lifespan). Read env at call time so the checks are exercised by tests."""
    is_production = os.getenv("CLIENT360_ENVIRONMENT", "development").strip().lower() == "production"
    dev_auth_on = os.getenv("CLIENT360_DEV_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
    if is_production and dev_auth_on:
        # The dev-only sign-in provider is already refused in production (dev_auth_enabled()
        # returns False there), but a set toggle signals a serious deployment mistake — refuse
        # to boot rather than silently ignore it.
        raise RuntimeError(
            "CLIENT360_DEV_AUTH must not be enabled in production. The development-only sign-in "
            "provider is refused in production; unset CLIENT360_DEV_AUTH before deploying."
        )
    for message in configuration_warnings():
        logger.warning("configuration: %s", message)
