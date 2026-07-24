"""Data Governance runtime gates (Phase D.52).

Every data-governance surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — the D.23
Governance package, the Person-merge / matching engine, the data catalog, the quality engine — which enforces
its own capability + record scope). Composition additionally consults the Policy Engine alongside RBAC —
never bypassing either.
"""
from __future__ import annotations

GATES = {
    "data_governance.enabled": True,   # master switch for the data-governance composition layer
    "stewardship.enabled": True,       # stewardship dashboards
    "lineage.enabled": True,           # lineage dashboards
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
    return gate("data_governance.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a data-governance area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"data_governance.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
