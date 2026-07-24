"""Compliance Intelligence runtime gates + supervisor authorization (Phase D.47).

Every supervisory surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback — AND authorized by the
``compliance.supervise`` capability (the supervisor-vs-advisor boundary; the ``advisor`` role does not hold
it). Composition additionally consults the Policy Engine at the call site alongside RBAC — never bypassing
either.
"""
from __future__ import annotations

SUPERVISE_CAP = "compliance.supervise"

GATES = {
    "compliance.intelligence.enabled": True,   # master switch for the supervisory composition layer
    "supervision.enabled": True,               # supervisory reviews + exceptions
    "supervisor.workspace.enabled": True,      # the enterprise supervisory dashboard
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
    return gate("compliance.intelligence.enabled")


def supervisor_authorized(principal) -> bool:
    """The supervisor-vs-advisor boundary: only a principal holding ``compliance.supervise`` may see any
    supervisory item/exception. Never raises."""
    try:
        return bool(principal.can(SUPERVISE_CAP))
    except Exception:
        return False


def policy_ok(area: str) -> bool:
    """Compose the Runtime Policy Engine for a supervisory area WITHOUT bypassing it (RBAC + the supervise
    capability are checked separately). Never raises; an explicit deny is honored."""
    try:
        from app.services.policy import evaluate
        from app.services.runtime import consumption
        return bool(evaluate(f"supervision.{area}", context=consumption.runtime_context(),
                             default=True).decision)
    except Exception:
        return True


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
