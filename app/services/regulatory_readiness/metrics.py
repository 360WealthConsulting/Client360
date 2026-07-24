"""Enterprise Regulatory Examination Readiness analytics readers (Phase D.59).

These are LOW-CARDINALITY operational counters ABOUT the readiness layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO evidence / compliance / document / filing / risk business metrics (those stay
owned by their authoritative owners) and NO second metrics registry. Never document contents, regulator
correspondence, client narratives, tax-return data, credentials, tokens, account numbers, filing payloads, or
protected supervisory details.
"""
from __future__ import annotations

from . import registry, stats


def regulatory_readiness_metrics(principal=None) -> dict:
    s = stats.readiness_stats()
    cov = registry.coverage()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "obligations": cov["obligations"],
        "evidence_classes": cov["evidence_classes"],
        "examination_requests": cov["examination_requests"],
        "certifications": cov["certifications"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def readiness_dashboards_composed(principal) -> int:
    return int(stats.readiness_stats().get("dashboards_composed", 0))


def readiness_panels_composed(principal) -> int:
    return int(stats.readiness_stats().get("panels_composed", 0))


def readiness_panel_failures(principal) -> int:
    return int(stats.readiness_stats().get("aggregation_failures", 0))


def readiness_authorization_failures(principal) -> int:
    return int(stats.readiness_stats().get("authorization_failures", 0))
