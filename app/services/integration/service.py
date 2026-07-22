"""Enterprise Integration service facade (Phase D.24).

Aggregates the integration submodules (connectors / sync / webhooks / api / events) for the overview
surface and cross-cutting reads. Integration is an authoritative integration domain: it owns
integration metadata but references canonical records and never owns them. It imports source/producer
services and the platform outbox — never a composition layer (annual_review/business_owner/reporting).
"""
from __future__ import annotations

from . import api, sync, webhooks
from .common import audit_history  # re-exported for routes


def overview_metrics(principal) -> dict:
    s, w, a = sync.metrics(principal), webhooks.metrics(principal), api.metrics(principal)
    from sqlalchemy import func, select

    from app.db import engine, integration_connectors, integration_providers
    with engine.connect() as c:
        providers = c.scalar(select(func.count()).select_from(integration_providers)) or 0
        connected = c.scalar(select(func.count()).select_from(integration_connectors)
                             .where(integration_connectors.c.status == "connected")) or 0
    return {"providers": providers, "connected_connectors": connected,
            "sync_failures": s["sync_failures"], "connector_errors": s["connector_errors"],
            "unresolved_conflicts": s["unresolved_conflicts"],
            "webhook_failures": w["webhook_failures"], "unverified_endpoints": w["unverified_endpoints"],
            "active_api_clients": a["active_api_clients"], "api_requests": a["api_requests"]}


__all__ = ["overview_metrics", "audit_history"]
