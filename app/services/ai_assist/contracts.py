"""Structured output + grounding contracts for Advisor AI Assist (Phase D.42).

Every AI response is a structured envelope grounded in platform facts. Required safety/provenance fields
(``citations``, ``limitations``, ``human_review``) can never be omitted — ``validate_output`` rejects an
envelope that lacks them. A GroundedFact distinguishes a confirmed platform fact from derived arithmetic,
a model-generated summary, and missing/untracked information — an unsupported inference is never
presented as fact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from .common import HUMAN_REVIEW_LABEL

# Fact classes — how a value is known.
CONFIRMED = "confirmed_platform_fact"
DERIVED = "derived_arithmetic"
MODEL = "model_generated_summary"
MISSING = "missing_or_untracked"
PROHIBITED = "recommendation_prohibited_by_scope"

# The safety/provenance fields an output envelope must always carry.
REQUIRED_OUTPUT_FIELDS = ("kind", "human_review", "citations", "limitations", "generated_at")


@dataclass
class GroundedFact:
    source_type: str          # e.g. "client360", "work_queue", "daily_brief"
    source_label: str         # human label, e.g. "Client 360"
    fact_key: str
    fact_value: object
    fact_class: str = CONFIRMED
    source_id: str | None = None       # a reference, never a raw sensitive id in user text
    freshness: str | None = None
    deep_link: str | None = None
    security_context: str | None = None
    available: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Citation:
    source: str               # e.g. "Client 360"
    fact_keys: tuple = ()
    deep_link: str | None = None

    def to_dict(self) -> dict:
        return {"source": self.source, "fact_keys": list(self.fact_keys), "deep_link": self.deep_link}


def unavailable(source_type, source_label, fact_key, *, reason="Not tracked", deep_link=None) -> GroundedFact:
    """Mark a fact the platform does not track / cannot supply — never fabricate a value."""
    return GroundedFact(source_type=source_type, source_label=source_label, fact_key=fact_key,
                        fact_value=reason, fact_class=MISSING, available=False, deep_link=deep_link)


def envelope(kind, *, provider_status, sections, facts, citations, limitations, navigation=None,
             unavailable_facts=None, now=None) -> dict:
    """Assemble a validated output envelope. Required safety/provenance fields are always present."""
    now = now or datetime.now(UTC)
    out = {
        "kind": kind,
        "human_review": HUMAN_REVIEW_LABEL,
        "generated_at": now.isoformat(),
        "provider": provider_status,
        "sections": sections,
        "facts": [f.to_dict() for f in facts],
        "citations": [c.to_dict() for c in citations],
        "limitations": list(limitations or []),
        "navigation": list(navigation or []),
        "unavailable": [u.fact_key for u in (unavailable_facts or [])],
    }
    return out


def validate_output(out: dict) -> list[str]:
    """Return a list of contract violations (empty = valid). Enforces required safety/provenance."""
    problems = []
    for f in REQUIRED_OUTPUT_FIELDS:
        if f not in out or out[f] in (None, ""):
            problems.append(f"missing required field: {f}")
    if out.get("human_review") != HUMAN_REVIEW_LABEL:
        problems.append("human_review label missing or altered")
    if not isinstance(out.get("citations"), list):
        problems.append("citations must be a list")
    if not isinstance(out.get("limitations"), list):
        problems.append("limitations must be a list")
    return problems


def refusal_output(category, message, *, suggested_link=None, now=None) -> dict:
    """A constrained refusal for a regulated/unsupported request — still grounded + human-review labelled."""
    now = now or datetime.now(UTC)
    return {
        "kind": "refusal",
        "human_review": HUMAN_REVIEW_LABEL,
        "generated_at": now.isoformat(),
        "refused": True,
        "refusal_category": category,
        "message": message,
        "citations": [],
        "limitations": ["Advisor AI Assist is read-only and cannot make this determination.",
                        "Use the authoritative workflow for any regulated decision or action."],
        "navigation": ([{"label": "Open authoritative workflow", "href": suggested_link}]
                       if suggested_link else []),
        "unsupported": True,
    }


_DEFAULT_LIMITATIONS = [
    "Read-only summary — the assistant cannot create, update, approve, assign, file, submit, send, or "
    "complete any record.",
    "Not investment, tax, legal, insurance, or suitability advice; no compliance approval.",
    "Grounded only in the platform data listed in citations; unlisted facts are Not tracked or "
    "Unavailable.",
]


def default_limitations(extra=None) -> list:
    return [*_DEFAULT_LIMITATIONS, *(extra or [])]


@dataclass
class ContextBundle:
    """The assembled, authorized, minimized context handed to the provider."""
    capability: str
    facts: list = field(default_factory=list)
    sources_used: list = field(default_factory=list)
    suppressed_sources: list = field(default_factory=list)
    unavailable: list = field(default_factory=list)
    navigation: list = field(default_factory=list)
    context_size: int = 0
