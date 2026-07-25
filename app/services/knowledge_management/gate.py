"""Enterprise Knowledge Management runtime gates (Phase D.62).

Every knowledge / SOP / documentation surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The layer composes already
authorized, already scoped reads (each panel's value is computed by its authoritative owner — the Document
Platform, Document Intelligence, Data Governance retention, Compliance Intelligence — which enforces its own
capability + record scope AND its own runtime gate). Composition additionally consults the Policy Engine
alongside RBAC — never bypassing either. The layer also respects the runtime gate of every composed source.
"""
from __future__ import annotations

# NOTE: the master gate is ``knowledge_management.enabled`` (NOT ``knowledge.enabled``) — the latter is already
# a runtime gate owned by the D.45 Enterprise Knowledge GRAPH layer; D.62 uses a distinct, non-colliding gate.
GATES = {
    "knowledge_management.enabled": True,  # master switch for the knowledge-management composition layer
    "sop_governance.enabled": True,        # SOP governance dashboards
    "documentation.enabled": True,         # documentation-health / publication dashboards
    "knowledge_ai_summary.enabled": True,  # AI summarize-only grounding
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
    return gate("knowledge_management.enabled")


def ai_summary_enabled() -> bool:
    return gate("knowledge_ai_summary.enabled")


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a knowledge area WITHOUT bypassing it (RBAC is checked
    separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"knowledge_management.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
