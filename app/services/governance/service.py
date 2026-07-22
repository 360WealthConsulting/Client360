"""Data Governance service facade (Phase D.23).

Aggregates the governance submodules (catalog / quality / mdm / retention) for the overview surface
and exposes cross-cutting reads (metrics, audit history). Governance is an authoritative governance
domain: it owns findings/decisions/holds/assignments but references canonical records and never owns
them. It imports source/producer services (matching/merge/document retention) — never a composition
layer (annual_review/business_owner/reporting).
"""
from __future__ import annotations

from . import quality, retention
from .common import audit_history  # re-exported for routes


def overview_metrics(principal) -> dict:
    q = quality.metrics(principal)
    r = retention.metrics(principal)
    return {"open_findings": q["open"], "critical_findings": q["critical_open"],
            "active_legal_holds": r["active_legal_holds"],
            "pending_deletion_reviews": r["pending_deletion_reviews"],
            "open_cases": r["open_cases"]}


__all__ = ["overview_metrics", "audit_history"]
