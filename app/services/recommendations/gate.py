"""Operational Intelligence runtime gates (Phase D.46).

Every recommendation surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped, deterministic reads, so the gates default ON (feature-flaggable off). Traversal
of client/household recommendations additionally composes the Policy Engine at the call site
(``policy.evaluate("recommendations.*")``) alongside RBAC — never bypassing either.
"""
from __future__ import annotations

GATES = {
    "recommendations.enabled": True,            # master switch for the operational-intelligence layer
    "recommendations.workspace.enabled": True,  # advisor-workspace operational-intelligence panel
    "recommendations.household.enabled": True,  # household aggregation
    "recommendations.ai.enabled": True,         # AI Assist may summarize recommendations
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
    return gate("recommendations.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a recommendation area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny decision is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"recommendations.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
