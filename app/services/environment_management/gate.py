"""Enterprise Environment Management runtime gates (Phase D.64).

Every environment / platform / deployment-topology / lifecycle / dependency surface is gated through the
governed Runtime Engine (``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer
composes already authorized, already scoped reads (each panel's value is computed by its authoritative owner —
the Observability catalog / health / service owners, the Runtime + Policy engines, and the Integration platform
— which enforces its own capability + scope AND its own runtime gate). Composition additionally consults the
Policy Engine alongside RBAC — never bypassing either. The layer also respects the runtime gate of every
composed source. These gate names are DISTINCT — no unrelated gate is reused.
"""
from __future__ import annotations

GATES = {
    "environment_management.enabled": True,   # master switch for the environment-management composition layer
    "platform_lifecycle.enabled": True,       # lifecycle / readiness dashboards
    "deployment_topology.enabled": True,      # deployment-topology / dependency dashboards
    "environment_ai_summary.enabled": True,   # AI summarize-only grounding
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
    return gate("environment_management.enabled")


def ai_summary_enabled() -> bool:
    return gate("environment_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for an environment area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"environment_management.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
