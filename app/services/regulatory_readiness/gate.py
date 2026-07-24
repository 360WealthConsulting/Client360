"""Enterprise Regulatory Examination Readiness runtime gates (Phase D.59).

Every readiness / evidence / certification / filing surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — Compliance
Intelligence, the Exception Engine, Document Intelligence, Data Governance, Security Operations, Business
Continuity, Vendor Management, Financial Operations, the rule catalog + reviewer-authority owner, Insurance
licensing, audit logging, and the CI pipeline — which enforces its own capability + record scope AND its own
runtime gate). Composition additionally consults the Policy Engine alongside RBAC — never bypassing either.
The layer also respects the runtime gate of every composed source (each source's own summary short-circuits
when its gate is off).
"""
from __future__ import annotations

GATES = {
    "regulatory_readiness.enabled": True,   # master switch for the readiness composition layer
    "evidence_governance.enabled": True,    # evidence completeness / freshness dashboards
    "certification_signoff.enabled": True,  # certification & sign-off dashboards
    "filing_readiness.enabled": True,       # filing / acknowledgement readiness dashboards
    "readiness_ai_summary.enabled": True,   # AI summarize-only grounding
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
    return gate("regulatory_readiness.enabled")


def ai_summary_enabled() -> bool:
    return gate("readiness_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a readiness area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"regulatory_readiness.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
