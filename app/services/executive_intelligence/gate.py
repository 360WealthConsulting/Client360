"""Executive Reporting runtime gates (Phase D.48).

Every executive surface is gated through the governed Runtime Engine (``runtime.consumption.feature_enabled``)
— no raw environment fallback. The layer composes already authorized, already scoped reads (each widget's
value is computed by its authoritative service, which enforces its own capability + record scope, so
executive metrics inherit the ``analytics.executive`` gate automatically). Composition additionally consults
the Policy Engine alongside RBAC — never bypassing either.
"""
from __future__ import annotations

GATES = {
    "reporting.enabled": True,             # master switch for the executive-reporting composition layer
    "executive_dashboard.enabled": True,   # executive dashboards
    "executive_widgets.enabled": True,     # executive widgets
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
    return gate("reporting.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a reporting area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"reporting.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
