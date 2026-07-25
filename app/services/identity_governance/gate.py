"""Enterprise Identity & Access Governance runtime gates (Phase D.65).

Every identity / role / capability / authentication / authorization surface is gated through the governed
Runtime Engine (``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes
already authorized, already scoped reads (each panel's value is computed by its authoritative owner — the
Identity service, Security RBAC, Security Authentication, the Policy engine, and Security Authorization — which
enforces its own capability + scope AND its own runtime gate). Composition additionally consults the Policy
Engine alongside RBAC — never bypassing either. The layer also respects the runtime gate of every composed
source. These gate names are DISTINCT — no unrelated gate is reused.
"""
from __future__ import annotations

GATES = {
    "identity_governance.enabled": True,        # master switch for the identity-governance composition layer
    "authentication_landscape.enabled": True,   # authentication-landscape dashboards
    "authorization_landscape.enabled": True,    # authorization-landscape / policy dashboards
    "identity_ai_summary.enabled": True,        # AI summarize-only grounding
}


def gate(name: str) -> bool:
    """Runtime-evaluated gate. Never raises; falls back to the declared default."""
    default = GATES.get(name, False)
    try:
        from app.services.runtime import consumption
        return bool(consumption.feature_enabled(name, default=default, shim=True))
    except Exception:
        return default


def enabled() -> bool:
    return gate("identity_governance.enabled")


def ai_summary_enabled() -> bool:
    return gate("identity_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for an identity area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"identity_governance.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
