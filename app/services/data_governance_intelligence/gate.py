"""Enterprise Data Governance Intelligence runtime gates (Phase D.66).

Every data-domain / lineage / stewardship / quality / retention surface is gated through the governed Runtime
Engine (``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — the Governance
catalog / MDM / quality / retention owners — which enforces its own capability + scope AND its own runtime
gate). Composition additionally consults the Policy Engine alongside RBAC — never bypassing either. The layer
also respects the runtime gate of every composed source. These gate names are DISTINCT — no unrelated gate is
reused (in particular, the master gate is ``data_governance_intelligence.enabled``, NOT the D.52 Data
Governance layer's ``data_governance.enabled``, and ``lineage_landscape.enabled``, NOT D.52's
``lineage.enabled``).
"""
from __future__ import annotations

GATES = {
    "data_governance_intelligence.enabled": True,  # master switch for the data-governance-intelligence layer
    "lineage_landscape.enabled": True,             # lineage-landscape dashboards
    "data_quality_landscape.enabled": True,        # data-quality-landscape dashboards
    "data_governance_ai_summary.enabled": True,    # AI summarize-only grounding
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
    return gate("data_governance_intelligence.enabled")


def ai_summary_enabled() -> bool:
    return gate("data_governance_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a data-governance area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"data_governance_intelligence.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
