"""Enterprise Security service facade (Phase D.25).

Aggregates the security submodules (policies / providers / secrets / incidents) for the overview
surface and cross-cutting reads. Enterprise Security is an authoritative security-metadata domain: it
owns security metadata but references canonical records/users and never owns them. It imports the
shared security infrastructure and its own submodules — never a composition layer
(annual_review/business_owner/reporting).
"""
from __future__ import annotations

from . import incidents, policies, providers, secrets
from .common import audit_history  # re-exported for routes


def overview_metrics(principal) -> dict:
    p, pr, se, inc = (policies.metrics(principal), providers.metrics(principal),
                      secrets.metrics(principal), incidents.metrics(principal))
    return {"active_policies": p["active_policies"],
            "unapplied_configurations": p["unapplied_configurations"],
            "enabled_providers": pr["enabled_providers"], "total_providers": pr["total_providers"],
            "overdue_secret_rotations": se["overdue_secret_rotations"],
            "expired_certificates": se["expired_certificates"],
            "open_incidents": inc["open_incidents"], "open_findings": inc["open_findings"],
            "pending_exceptions": inc["pending_exceptions"]}


__all__ = ["overview_metrics", "audit_history"]
