"""Automation Orchestration analytics readers (Phase D.51).

These are LOW-CARDINALITY operational counters ABOUT the automation-orchestration layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO business metrics and NO second metrics registry; every automation count comes
from the authoritative Workflow / Automation / Event owners. Never workflow payloads or client-sensitive data.
"""
from __future__ import annotations

from . import registry, stats


def automation_orchestration_metrics(principal=None) -> dict:
    s = stats.automation_stats()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "automations": registry.coverage()["automations"],
        "triggers": registry.coverage()["triggers"],
        "actions": registry.coverage()["actions"],
        "dashboards": registry.coverage()["dashboards"],
        "panels": registry.coverage()["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def automation_dashboards_composed(principal) -> int:
    return int(stats.automation_stats().get("dashboards_composed", 0))


def automation_panels_composed(principal) -> int:
    return int(stats.automation_stats().get("panels_composed", 0))


def automation_panel_failures(principal) -> int:
    return int(stats.automation_stats().get("aggregation_failures", 0))


def automation_authorization_failures(principal) -> int:
    return int(stats.automation_stats().get("authorization_failures", 0))
