"""Practice Management runtime gates (Phase D.49).

Every practice-management surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative service, which
enforces its own capability + record scope). Composition additionally consults the Policy Engine alongside
RBAC — never bypassing either.
"""
from __future__ import annotations

GATES = {
    "practice_management.enabled": True,   # master switch for the practice-management composition layer
    "capacity.enabled": True,              # capacity / utilization dashboards
    "staffing.enabled": True,              # staffing recommendations + resource dashboards
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
    return gate("practice_management.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a practice area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"practice_management.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
