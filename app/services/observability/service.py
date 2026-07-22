"""Enterprise Observability service facade (Phase D.26).

Aggregates the observability submodules (catalog / health / telemetry / alerts / incidents) for the
overview surface and cross-cutting reads. Enterprise Observability is an authoritative
platform-operations domain: it owns observability metadata but references operational records and
never owns them. It imports its own submodules and shared infrastructure — never a composition layer
(annual_review/business_owner/reporting).
"""
from __future__ import annotations

from . import alerts, catalog, health, incidents, telemetry
from .common import audit_history  # re-exported for routes


def overview_metrics(principal) -> dict:
    cat = catalog.metrics(principal)
    hlt = health.metrics(principal)
    tel = telemetry.metrics_summary(principal)
    alr = alerts.metrics(principal)
    inc = incidents.metrics(principal)
    return {"operational_services": cat["operational_services"], "total_services": cat["total_services"],
            "degraded_services": cat["degraded_services"],
            "failed_health_checks": hlt["failed_health_checks"],
            "diagnostic_failures": hlt["diagnostic_failures"],
            "telemetry_metrics": tel["telemetry_metrics"], "telemetry_sources": tel["telemetry_sources"],
            "open_alerts": alr["open_alerts"], "active_maintenance_windows": alr["active_maintenance_windows"],
            "reliability_incidents": inc["reliability_incidents"],
            "reliability_findings": inc["reliability_findings"]}


__all__ = ["overview_metrics", "audit_history"]
