"""Enterprise Operational Intelligence & Explainable Recommendation layer (Phase D.46).

A governed, READ-ONLY composition over the platform's existing authoritative recommendation sources — the
deterministic advisor_intelligence Signal engine, the domain observation sets (pipeline/bizdev/firm), the
unified work queue, and the D.44 engagement summary. It produces one explainable, prioritized, deduplicated
advisor-recommendation surface WITHOUT a second recommendation/workflow/opportunity/CRM/analytics/AI engine,
without ML/predictive scoring, and without any mutation. Every recommendation is explainable (why + evidence
+ deep link into an authoritative surface); non-explainable ones are never emitted.
"""
from .service import (
                      client_recommendations,
                      explain_recommendation,
                      household_recommendations,
                      recommendation_summary,
                      workspace_recommendations,
)

__all__ = ["client_recommendations", "household_recommendations", "workspace_recommendations",
           "recommendation_summary", "explain_recommendation"]
