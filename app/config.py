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
  PUBLIC_BASE_URL         Canonical external origin of this deployment, e.g.
                          "https://app.example.com" — scheme + host (+ port)
                          only, no path/query/fragment/credentials. Used to build
                          OAuth redirect URIs instead of the inbound Host header,
                          which nothing validates. REQUIRED in production for the
                          client-portal sign-in flow, which fails closed without
                          it. See app/security/origin.py.

Client-portal sign-in. Clients do NOT authenticate through an external identity provider: they prove
possession of the mailbox the firm invited, by emailed one-time code (`app/portal/email_auth.py`).
The keys below are how that code is delivered, and are therefore REQUIRED for client sign-in.
Staff/admin Microsoft sign-in is a different surface entirely and is unaffected.
NON-SECRET unless marked SECRET:

  PORTAL_EMAIL_ENABLED    "true" turns on outbound portal invitation email. Off by
                          default; with it off an invitation is still created and the
                          activation link is handed to staff once, but nothing is sent.
  PORTAL_EMAIL_SENDER     The firm mailbox invitations are sent FROM, e.g.
                          "clientservices@example.com". Selects the mailbox; it is NOT
                          a security boundary — only Exchange can enforce which mailbox
                          the app may send as. Never a staff member's own mailbox;
                          delegated send-as-self is a separate feature.
  PORTAL_EMAIL_TENANT_ID  SECRET-adjacent. Tenant of the DEDICATED outbound-mail app
  PORTAL_EMAIL_CLIENT_ID  registration. Application Mail.Send sends with no signed-in
  PORTAL_EMAIL_CLIENT_SECRET  user, so it is issued to its own app registration and its
                          own credential namespace — never MICROSOFT_CLIENT_ID/SECRET,
                          which must not gain that privilege. PORTAL_EMAIL_CLIENT_SECRET
                          is a SECRET. The dedicated app needs the Graph APPLICATION
                          permission Mail.Send ONLY, with admin consent, restricted to
                          PORTAL_EMAIL_SENDER through Exchange Online Application RBAC.

OpenAI provider (Phase 1). One centralized server-side provider,
:mod:`app.services.openai_provider`, is the only thing that may talk to OpenAI. It is OFF by
default and nothing in the platform calls it yet.

  OPENAI_ENABLED          "true" turns the provider on. Default false. With it off the provider
                          answers "disabled" and makes NO network call, so a deployment that has
                          not opted in behaves exactly as it did before.
  OPENAI_MODEL            The model id every request uses, e.g. "gpt-5.6". No default and NO
                          fallback: if it is unset the provider is "not_configured" and refuses
                          rather than quietly substituting some other model.
  OPENAI_API_KEY          SECRET. Read at call time by the provider and by nothing else — never
                          by this module, never returned to a browser, never logged. Only its
                          PRESENCE is observable here (:func:`openai_api_key_configured`).
  OPENAI_TIMEOUT_SECONDS  Per-request timeout, clamped to 1-300. Default 30.
  OPENAI_MAX_RETRIES      Bounded SDK retries for transient failures, clamped to 0-5. Default 2.

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


def orchestration_enabled() -> bool:
    """Whether the Workflow Orchestration housekeeping tick runs as a scheduler job (Phase D.33).

    Default OFF: the orchestration engine ships (definitions/instances are coordinated synchronously
    through the engine, and the diagnostics/replay/simulation surfaces are always available), but the
    background housekeeping tick is not registered unless explicitly enabled — keeping runtime behavior
    unchanged by default (same posture as the automation tick). The scheduler infrastructure is
    unchanged; this only launches orchestration.
    """
    return os.getenv("ORCHESTRATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def orchestration_tick_interval_seconds() -> int:
    # Poll cadence for the orchestration housekeeping tick; minimum 5s to avoid a hot loop.
    return max(5, _int_env("ORCHESTRATION_TICK_INTERVAL_SECONDS", 60))


def projections_enabled() -> bool:
    """Whether the projection incremental tick runs as a scheduler job (Phase D.36).

    Default OFF: the read-model projection engine ships (projections can be rebuilt/replayed on demand
    via the API, and read models are always rebuildable from events), but the background tick that
    incrementally applies new outbox events is not registered unless explicitly enabled — keeping
    runtime behavior unchanged by default (same posture as the outbox dispatcher). Read models are
    disposable; nothing depends on them until a read surface adopts one.
    """
    return os.getenv("PROJECTIONS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def projections_tick_interval_seconds() -> int:
    # Poll cadence for the projection incremental tick; minimum 5s to avoid a hot loop.
    return max(5, _int_env("PROJECTIONS_TICK_INTERVAL_SECONDS", 60))


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


# --- OpenAI provider (Phase 1) -----------------------------------------------
# Read at call time (env, safe defaults), like every other toggle above, so operations can change
# the posture without a code change and tests can exercise both. The provider is OFF by default:
# the platform must behave identically until someone deliberately opts in.

def openai_enabled() -> bool:
    """Whether the centralized OpenAI provider may make network calls at all.

    Default OFF (same posture as the outbox dispatcher / automation tick). With it off the
    provider short-circuits to a "disabled" result before any client is built, so an absent or
    half-finished configuration can never produce a surprise API call."""
    return os.getenv("OPENAI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def openai_model() -> str:
    """The configured model id, or "" when unset.

    There is deliberately NO default. A wrong-but-plausible fallback model would spend money and
    return output nobody asked for; an unset model makes the provider refuse instead."""
    return os.getenv("OPENAI_MODEL", "").strip()


def openai_api_key_configured() -> bool:
    """Whether OPENAI_API_KEY is present. PRESENCE ONLY — the value is never read here.

    The key's value is read in exactly one place, app.services.openai_provider._api_key(), so the
    credential has a single, auditable point of entry. This function exists so startup warnings and
    diagnostics can say "configured / not configured" without touching the secret."""
    return bool((os.getenv("OPENAI_API_KEY", "") or "").strip())


def openai_timeout_seconds() -> float:
    """Per-request wall-clock budget. Clamped to 1-300s so a typo cannot mean "wait forever"."""
    try:
        value = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        value = 30.0
    return min(300.0, max(1.0, value))


def openai_max_retries() -> int:
    """Bounded retries for transient failures. Clamped to 0-5; retries are never unlimited."""
    return min(5, max(0, _int_env("OPENAI_MAX_RETRIES", 2)))


def _openai_warnings() -> list[str]:
    """Warn when the provider is switched on but cannot actually call anything. Names only.

    Silence when it is off: disabled is the normal, expected state in every environment that has
    not adopted OpenAI, so only a HALF-configured provider is worth saying out loud."""
    if not openai_enabled():
        return []
    missing = []
    if not openai_api_key_configured():
        missing.append("OPENAI_API_KEY")
    if not openai_model():
        missing.append("OPENAI_MODEL")
    if not missing:
        return []
    return ["OPENAI_ENABLED is on but the OpenAI provider cannot make requests; missing "
            f"{', '.join(missing)}. Every request will be refused as not_configured until it is set."]


def is_production_now() -> bool:
    """Environment read at call time (not import), so tests exercise both postures."""
    return os.getenv("CLIENT360_ENVIRONMENT", "development").strip().lower() == "production"


def canonical_origin_status() -> tuple[bool, str | None]:
    """``(configured_and_valid, error)`` for PUBLIC_BASE_URL. Never raises."""
    from app.security.origin import canonical_origin_status as _status
    return _status()


def _portal_email_warnings() -> list[str]:
    """Warn when invitation email is switched on but cannot actually send. Names only, no values.

    Silence when it is off: no outbound email is a normal state — the one-time staff handoff still
    delivers the activation link — so only a HALF-configured sender is worth saying out loud."""
    if os.getenv("PORTAL_EMAIL_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return []
    # The DEDICATED mail credentials — MICROSOFT_* deliberately absent. PUBLIC_BASE_URL too:
    # without it no activation URL can be built, so there would be nothing to send.
    missing = [name for name in ("PORTAL_EMAIL_SENDER", "PORTAL_EMAIL_TENANT_ID",
                                 "PORTAL_EMAIL_CLIENT_ID", "PORTAL_EMAIL_CLIENT_SECRET",
                                 "PUBLIC_BASE_URL")
               if not os.getenv(name, "").strip()]
    if not missing:
        return []
    return ["PORTAL_EMAIL_ENABLED is on but portal invitation email cannot be sent; missing "
            f"{', '.join(missing)}. Invitations will still be created and the activation link "
            "handed to staff, but no email will go out."]


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
    ok, origin_error = canonical_origin_status()
    if origin_error:
        warnings.append(f"PUBLIC_BASE_URL is invalid and will be refused: {origin_error}")
    elif not ok and is_production_now():
        warnings.append(
            "PUBLIC_BASE_URL is not set. Client-portal sign-in fails closed in production rather "
            "than build an OAuth redirect URI from the unvalidated Host header. Set it to the "
            "canonical external origin before enabling the portal.")
    warnings.extend(_portal_email_warnings())
    warnings.extend(_openai_warnings())
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
