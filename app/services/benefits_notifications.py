"""Internal benefits notifications (Release 0.9.11, Phase 5 — ADR-18).

Reuses the existing notification provider + honest-outcome architecture. **Internal staff
notifications only — never the employer portal** (Phase 7 owns employer-facing delivery).
SLA at-risk / breach / escalation notifications are dispatched by the shared exception SLA
sweep (``exception_sla``); this module adds the "scheduled scan needs attention" signal.

Every dispatch records an **honest** outcome (delivered / disabled / skipped) and an audit
event, and carries **no** sensitive employee/health/compensation/EIN/deferral data — only
counts.
"""
import uuid

from app.portal.providers import NOTIFICATION_PROVIDERS
from app.security.audit import write_audit_event

OPS_RECIPIENT = "benefits-operations"


def record_scan_health(result, *, actor_user_id=None):
    """Notify internal staff when a scheduled benefits scan reports failures. Deduped by only
    firing on failures (the scan cadence is the cooldown); honest provider outcome recorded."""
    failures = int(result.get("failures", 0) or 0)
    if failures <= 0:
        return {"notified": False, "outcome": "skipped"}
    provider = NOTIFICATION_PROVIDERS.get("in_app")
    if provider is None:
        outcome = "unavailable"
        delivered = False
    else:
        delivered = provider.deliver(
            recipient=OPS_RECIPIENT, title="Benefits scan attention required",
            body=f"The scheduled benefits scan reported {failures} failure(s).",
            metadata={"scanned_organizations": result.get("scanned_organizations")})["delivered"]
        outcome = "delivered" if delivered else "disabled"
    write_audit_event(action="benefits.scan.health", entity_type="benefits_scan", entity_id="scheduled",
                      actor_user_id=actor_user_id, request_id=f"benefits-scan-{uuid.uuid4()}",
                      metadata={"failures": failures, "outcome": outcome,
                                "scanned_organizations": result.get("scanned_organizations")})
    return {"notified": True, "outcome": outcome}
