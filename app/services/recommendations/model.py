"""Operational Intelligence recommendation read-model (Phase D.46).

A normalized, read-only projection of a single explainable advisor recommendation. It REFERENCES its
authoritative source (source service + deep link) and never mutates. Every recommendation carries its
explanation (why + governing rule), supporting evidence, authoritative source, a DETERMINISTIC rule-based
confidence (never probabilistic/ML), a deep link, and the recommended next action. A recommendation WITHOUT
an explanation + evidence must never be emitted (governance + the engine both enforce this).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Severity + priority vocabularies (mirror the authoritative advisor_intelligence Priority order).
PRIORITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "informational": 0}
SEVERITIES = ("critical", "high", "medium", "low", "info")

# Visibility.
INTERNAL = "internal"      # advisor/staff only (all D.46 recommendations are internal)


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str     # stable, source-qualified (e.g. "rec:review_cadence:signal:abc")
    type: str                  # registered recommendation type
    category: str              # registry category (attention/review/workload/opportunity/…)
    priority: str              # critical|high|medium|low|informational
    severity: str
    title: str
    summary: str
    explanation: str           # why this recommendation was generated
    governing_rule: str        # which rule/producer generated it
    evidence: tuple[str, ...]  # supporting evidence (references only — no client-sensitive content)
    authoritative_source: str  # the authoritative service that owns the underlying fact
    workflow_owner: str        # the authoritative service that owns resolution
    confidence: float          # deterministic rule-based (1.0 operational; source-supplied otherwise)
    generated_at: str | None   # ISO-8601 (caller/source supplied — deterministic)
    deep_link: str | None      # the authoritative surface the advisor should open
    recommended_next_action: str
    visibility: str = INTERNAL
    related_person_id: int | None = None
    related_household_id: int | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 0)

    @property
    def is_explainable(self) -> bool:
        """A recommendation is only emittable when it carries a why + at least one piece of evidence + a
        deep link into an authoritative surface."""
        return bool(self.explanation and self.evidence and self.deep_link)

    def to_dict(self) -> dict:
        return {
            "recommendation_id": self.recommendation_id, "type": self.type, "category": self.category,
            "priority": self.priority, "severity": self.severity, "title": self.title,
            "summary": self.summary, "explanation": self.explanation, "governing_rule": self.governing_rule,
            "evidence": list(self.evidence), "authoritative_source": self.authoritative_source,
            "workflow_owner": self.workflow_owner, "confidence": self.confidence,
            "generated_at": self.generated_at, "deep_link": self.deep_link,
            "recommended_next_action": self.recommended_next_action, "visibility": self.visibility,
            "related_person_id": self.related_person_id, "related_household_id": self.related_household_id,
            "metadata": self.metadata,
        }
