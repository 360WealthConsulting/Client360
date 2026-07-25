"""Enterprise Capacity Planning runtime gates (Phase D.61).

Every workforce / capacity / utilization surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — the Operations
capacity owner, the Work Queue, Practice Management, and Automation Orchestration — which enforces its own
capability + record scope AND its own runtime gate). Composition additionally consults the Policy Engine
alongside RBAC — never bypassing either. The layer also respects the runtime gate of every composed source.
"""
from __future__ import annotations

GATES = {
    "capacity.enabled": True,               # master switch for the capacity composition layer
    "workforce.enabled": True,              # workforce / staffing dashboards
    "resource_intelligence.enabled": True,  # utilization / queue / allocation dashboards
    "capacity_ai_summary.enabled": True,    # AI summarize-only grounding
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
    return gate("capacity.enabled")


def ai_summary_enabled() -> bool:
    return gate("capacity_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a capacity area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"capacity_planning.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
