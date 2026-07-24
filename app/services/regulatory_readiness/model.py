"""Enterprise Regulatory Examination Readiness read-models (Phase D.59).

Normalized, read-only projections of a composed readiness/evidence/certification panel and a readiness
dashboard. Every panel REFERENCES its authoritative owner (owning service + read + deep link) and never
creates an examination, opens a regulatory case, uploads or modifies evidence, approves a rule set, certifies
compliance, signs an attestation, files a form, submits evidence, closes a finding, resolves an exception,
changes retention, fabricates a filing acknowledgement, or infers regulatory acceptance — the value is
composed on READ by the authoritative owner (Compliance Intelligence, the Exception Engine, Document
Intelligence, Data Governance, Security Operations, Business Continuity, Vendor Management, Financial
Operations, Automation Orchestration, the Integration Platform, Insurance licensing, the rule catalog +
reviewer-authority owner, audit logging, and the CI pipeline). Every panel is explainable (explanation +
source + deep link); a non-explainable panel is never emitted. A panel the principal is not entitled to see is
emitted ``restricted`` (never its value, count, freshness, or leaking metadata); an owner-not-configured panel
is emitted ``available=False`` with ``config_status='not_configured'`` (fail closed). **No document contents,
tax-return contents, client narratives, regulator-correspondence contents, audit payloads, credentials,
tokens, account numbers, license keys, PII, private incident narratives, or evidence files are ever carried in
a panel — counts, status, coverage, freshness, and age bands only.** A derived value carries ``derived=True``,
describes operational readiness (never regulatory certification), and never interprets an absence of findings
as compliance.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PanelResult:
    key: str
    title: str
    owner: str                 # authoritative owning service (or "not_configured")
    source: str                # the authoritative read the value is composed from
    measure: str               # obligation | evidence | certification | review | filing | examination | coverage | readiness
    unit: str                  # count | percent | ratio | status | freshness | age_band | coverage | distribution
    viz: str                   # card | chart | gauge | list | leaderboard
    value: object              # the composed value(s) — computed by the authoritative owner
    explanation: str           # what this panel shows + where it comes from
    deep_link: str | None      # the authoritative owner surface to drill into
    restricted: bool = False   # entitlement withheld from a principal lacking the panel capability
    available: bool = True      # False when the authoritative owner is not_configured / unavailable (fail closed)
    derived: bool = False      # True when the value is a deterministic derivation (operational readiness, never certification)
    config_status: str = "configured"   # configured | not_configured
    blocked: bool = False      # True for a blocked / reviewer_not_confirmed certification panel
    blocked_reason: str | None = None

    @property
    def is_explainable(self) -> bool:
        return bool(self.explanation and self.source and self.deep_link)

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "owner": self.owner, "source": self.source,
                "measure": self.measure, "unit": self.unit, "viz": self.viz, "value": self.value,
                "explanation": self.explanation, "deep_link": self.deep_link,
                "restricted": self.restricted, "available": self.available, "derived": self.derived,
                "config_status": self.config_status, "blocked": self.blocked,
                "blocked_reason": self.blocked_reason}


@dataclass(frozen=True)
class ReadinessDashboard:
    key: str
    name: str
    audience: str
    generated_at: str | None
    panels: tuple = ()                # tuple[PanelResult]
    governing_services: tuple = ()    # the authoritative services composed
    source_inventory: tuple = ()      # the panel sources (for explainability + governance)
    deep_links: dict = field(default_factory=dict)
    navigation: str | None = None
    refresh_policy: str = "on_view"
    configured_domains: tuple = ()
    not_configured_domains: tuple = ()
    blocked_domains: tuple = ()

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "audience": self.audience,
                "generated_at": self.generated_at, "panels": [p.to_dict() for p in self.panels],
                "governing_services": list(self.governing_services),
                "source_inventory": list(self.source_inventory), "deep_links": self.deep_links,
                "navigation": self.navigation, "refresh_policy": self.refresh_policy,
                "configured_domains": list(self.configured_domains),
                "not_configured_domains": list(self.not_configured_domains),
                "blocked_domains": list(self.blocked_domains),
                "operational_readiness_not_certification": True,
                "panel_count": len(self.panels)}
