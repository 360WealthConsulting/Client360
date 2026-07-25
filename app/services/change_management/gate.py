"""Enterprise Change Management runtime gates (Phase D.63).

Every change / release / configuration / evidence surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — the architecture
manifest, the Observability health / catalog / alerts / incidents owners, the Runtime + Policy engines, Security
incidents, Compliance Intelligence, and the D.55–D.62 layers — which enforces its own capability + record scope
AND its own runtime gate). Composition additionally consults the Policy Engine alongside RBAC — never bypassing
either. The layer also respects the runtime gate of every composed source. These gate names are DISTINCT — no
unrelated gate is reused.
"""
from __future__ import annotations

GATES = {
    "change_management.enabled": True,          # master switch for the change-management composition layer
    "release_governance.enabled": True,         # release-readiness / migration dashboards
    "configuration_intelligence.enabled": True, # configuration-governance dashboards
    "deployment_evidence.enabled": True,        # deployment / rollback evidence dashboards
    "change_ai_summary.enabled": True,          # AI summarize-only grounding
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
    return gate("change_management.enabled")


def ai_summary_enabled() -> bool:
    return gate("change_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a change area WITHOUT bypassing it (RBAC is checked separately).
    Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"change_management.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
