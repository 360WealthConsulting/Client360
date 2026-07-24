"""Recommendation registry (Phase D.46) — the declarative catalog of every advisor recommendation type the
Operational Intelligence layer can surface.

Each type declares its owner service, source services, default severity, category, lifecycle, prerequisites,
visibility, explanation template, supporting-evidence kind, deep-link target, workflow owner, and suppression
rules. This is the AUTHORITATIVE catalog for advisor recommendations. The engine normalizes the existing
authoritative signals/observations (advisor_intelligence Signals + the domain observation sets + the work
queue + the D.44 engagement summary) onto these types — it never invents a second recommendation engine.
Governance verifies completeness against this registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import INTERNAL

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


@dataclass(frozen=True)
class RecommendationType:
    key: str
    owner_service: str                 # the authoritative owner of the underlying fact
    source_services: tuple[str, ...]   # the composed source(s) the engine reads
    default_severity: str
    category: str                      # attention | review | workload | opportunity | governed | firm | pipeline | bizdev | communication
    lifecycle: str
    visibility: str
    explanation_template: str          # the "why" shown when the source does not supply its own
    evidence_kind: str                 # what evidence supports this type
    deep_link_target: str              # the authoritative surface to open
    workflow_owner: str                # the authoritative service that owns resolution
    prerequisites: tuple[str, ...] = ()
    suppression: tuple[str, ...] = field(default_factory=tuple)


def _t(key, owner, sources, severity, category, explanation, evidence, deep_link, workflow_owner,
       *, prerequisites=(), suppression=(), visibility=INTERNAL, lifecycle="active"):
    return RecommendationType(key, owner, tuple(sources), severity, category, lifecycle, visibility,
                              explanation, evidence, deep_link, workflow_owner, tuple(prerequisites),
                              tuple(suppression))


REGISTRY = (
    _t("client_attention", "exception_engine", ("advisor_intelligence",), "high", "attention",
       "This client has open exceptions requiring attention.", "open exception records",
       "/client/{id}", "exception_engine",
       suppression=("no_open_exceptions",)),
    _t("review_cadence", "annual_review", ("advisor_intelligence", "portfolio"), "medium", "review",
       "A review is overdue or approaching for this client.", "account review dates / review sessions",
       "/annual-review", "annual_review",
       suppression=("review_current",)),
    _t("task_workload", "work_queue", ("work_queue",), "high", "workload",
       "Overdue or SLA-breaching work items need action.", "unified work-queue items",
       "/work", "work_queue",
       suppression=("no_overdue_work",)),
    _t("meeting_prep", "scheduling", ("advisor_intelligence",), "low", "review",
       "An upcoming meeting warrants preparation.", "scheduled calendar events",
       "/scheduling", "scheduling"),
    _t("service_opportunity", "opportunity", ("advisor_intelligence",), "medium", "opportunity",
       "A service opportunity is available for this client.", "portfolio/insurance review cadence",
       "/opportunities", "opportunity"),
    _t("governed_recommendation", "compliance", ("advisor_intelligence",), "medium", "governed",
       "A governed advisor recommendation applies (compliance-owned).",
       "a registered deterministic recommendation rule", "/compliance/reviews", "compliance",
       prerequisites=("compliance_review",)),
    _t("pipeline_health", "opportunity", ("opportunity.intelligence",), "medium", "pipeline",
       "A pipeline health observation applies (aging/stalled/missing next action).",
       "opportunity pipeline observations", "/opportunities", "opportunity"),
    _t("bizdev_health", "business_development", ("bizdev.intelligence",), "low", "bizdev",
       "A business-development observation applies.", "campaign/referral observations",
       "/business-development", "business_development"),
    _t("firm_health", "operations", ("analytics.intelligence",), "medium", "firm",
       "A firm-level operational observation applies (backlog/overload).",
       "firm-level operational observations", "/analytics", "operations"),
    _t("communication_followup", "communications.engagement", ("communications.engagement",), "medium",
       "communication",
       "A client communication needs follow-up (unresolved action or a stale last interaction).",
       "unified engagement summary", "/engagement", "communications.engagement",
       suppression=("no_action_required",)),
)

_BY_KEY = {t.key: t for t in REGISTRY}
CATEGORIES = tuple(dict.fromkeys(t.category for t in REGISTRY))


def recommendation_type(key) -> RecommendationType | None:
    return _BY_KEY.get(key)


def registered(key) -> bool:
    return key in _BY_KEY


def deep_link_for(key, entity_id=None) -> str:
    t = _BY_KEY.get(key)
    if t is None:
        return "/workspace"
    return t.deep_link_target.replace("{id}", str(entity_id)) if entity_id is not None \
        else t.deep_link_target


def classify_signal(signal_dict) -> str:
    """Deterministically map an advisor_intelligence Signal (dict) onto a registered recommendation type by
    its category + source-service/route keywords. Unknown → 'client_attention' (the operational default)."""
    category = signal_dict.get("category")
    src = (signal_dict.get("source_service") or "").lower()
    text = f"{signal_dict.get('id', '')} {signal_dict.get('title', '')} {signal_dict.get('route', '')}".lower()
    if category == "recommendation":
        return "governed_recommendation"
    if category == "opportunity":
        return "service_opportunity"
    # operational family — pick the finer type by source/keyword.
    if "task" in src or "task" in text or "work" in src:
        return "task_workload"
    if "meeting" in text or "schedul" in src or "timeline" in src or "calendar" in text:
        return "meeting_prep"
    if "review" in text or "portfolio" in src or "review" in src:
        return "review_cadence"
    return "client_attention"


def coverage() -> dict:
    return {
        "total_types": len(REGISTRY),
        "categories": len(CATEGORIES),
        "with_suppression": sum(1 for t in REGISTRY if t.suppression),
        "with_prerequisites": sum(1 for t in REGISTRY if t.prerequisites),
    }
