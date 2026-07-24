"""Knowledge graph runtime gates (Phase D.45).

Every knowledge surface is gated through the governed Runtime Engine (``runtime.consumption.feature_enabled``)
— no raw environment fallback. The layer composes already-authorized, already-scoped reads, so the gates
default ON (feature-flaggable off). Traversal/search additionally compose the Policy Engine at the call site
(``policy.evaluate("knowledge.*")``) alongside RBAC — never bypassing either.
"""
from __future__ import annotations

GATES = {
    "knowledge.enabled": True,           # master switch for the knowledge composition layer
    "knowledge.search.enabled": True,    # semantic entity search
    "explain.enabled": True,             # relationship explanations
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
    return gate("knowledge.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a knowledge area WITHOUT bypassing it (RBAC is checked
    separately by the route/service). Fails open to True only when policy is unavailable, matching the
    platform's never-raise policy contract; an explicit deny decision is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"knowledge.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
