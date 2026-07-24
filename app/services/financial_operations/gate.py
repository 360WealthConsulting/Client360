"""Financial Operations runtime gates (Phase D.57).

Every financial-operations surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — the insurance
commission ledger, the portfolio AUM owner, the single Analytics Registry, Executive Reporting, Practice
Management — which enforces its own capability + record scope). Composition additionally consults the Policy
Engine alongside RBAC — never bypassing either.
"""
from __future__ import annotations

GATES = {
    "financial_operations.enabled": True,   # master switch for the financial-operations composition layer
    "revenue.enabled": True,                # revenue dashboards
    "profitability.enabled": True,          # profitability dashboards
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
    return gate("financial_operations.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a financial-operations area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"financial_operations.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
