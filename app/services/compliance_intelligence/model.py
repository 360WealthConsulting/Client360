"""Compliance Intelligence read-models (Phase D.47).

Normalized, read-only projections of a supervisory review item and a compliance exception. Each REFERENCES
its authoritative record (owner + deep link) and never copies mutable state or mutates. Every item is
explainable (explanation + evidence + deep link into an authoritative workflow) — a non-explainable item is
never emitted. Supervisory items are supervisor-visibility only; the advisor-visible compliance TASKS are a
separate, narrower projection (see ``service.advisor_compliance_tasks``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

PRIORITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "informational": 0}
SEVERITY_RANK = {"blocker": 4, "critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# Visibility.
SUPERVISOR = "supervisor"      # supervisor-only — never surfaced to advisors or clients
ADVISOR = "advisor"            # advisor-visible compliance task


@dataclass(frozen=True)
class SupervisoryItem:
    item_id: str               # stable, source-qualified (e.g. "sup:annual_review_oversight:review:12")
    review_type: str           # registered supervisory review type
    status: str
    priority: str
    title: str
    summary: str
    explanation: str           # why this item is on the supervisor's desk
    governing_policy: str      # the policy/rule that governs the review
    evidence: tuple[str, ...]  # references only — no client-sensitive content
    authoritative_owner: str   # the authoritative service that owns the review
    required_reviewer: str     # the approval authority / reviewer role
    due_date: str | None
    deep_link: str | None      # the authoritative workflow to open
    recommended_action: str
    visibility: str = SUPERVISOR
    related_person_id: int | None = None
    related_household_id: int | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 0)

    @property
    def is_explainable(self) -> bool:
        return bool(self.explanation and self.evidence and self.deep_link)

    def to_dict(self) -> dict:
        return {"item_id": self.item_id, "review_type": self.review_type, "status": self.status,
                "priority": self.priority, "title": self.title, "summary": self.summary,
                "explanation": self.explanation, "governing_policy": self.governing_policy,
                "evidence": list(self.evidence), "authoritative_owner": self.authoritative_owner,
                "required_reviewer": self.required_reviewer, "due_date": self.due_date,
                "deep_link": self.deep_link, "recommended_action": self.recommended_action,
                "visibility": self.visibility, "related_person_id": self.related_person_id,
                "related_household_id": self.related_household_id, "metadata": self.metadata}


@dataclass(frozen=True)
class ComplianceException:
    exception_id: str          # stable, source-qualified
    exception_type: str        # registered compliance exception type
    severity: str
    status: str
    title: str
    summary: str
    explanation: str
    governing_policy: str
    evidence: tuple[str, ...]
    owner: str                 # authoritative owner
    escalation: str            # escalation path
    deep_link: str | None
    visibility: str = SUPERVISOR
    related_person_id: int | None = None
    related_household_id: int | None = None

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    @property
    def is_explainable(self) -> bool:
        return bool(self.explanation and self.evidence and self.deep_link)

    def to_dict(self) -> dict:
        return {"exception_id": self.exception_id, "exception_type": self.exception_type,
                "severity": self.severity, "status": self.status, "title": self.title,
                "summary": self.summary, "explanation": self.explanation,
                "governing_policy": self.governing_policy, "evidence": list(self.evidence),
                "owner": self.owner, "escalation": self.escalation, "deep_link": self.deep_link,
                "visibility": self.visibility, "related_person_id": self.related_person_id,
                "related_household_id": self.related_household_id}
