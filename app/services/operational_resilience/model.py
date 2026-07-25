"""Enterprise Operational Resilience read-models (Phase D.60).

Normalized, read-only projections of a composed operational-resilience panel and a resilience dashboard. Every
panel REFERENCES its authoritative owner (owning service + read + deep link) and never creates an incident,
acknowledges an alert, executes recovery, modifies monitoring, schedules maintenance, or closes an incident —
the value is composed on READ by the authoritative owner (the Observability service catalog / health /
incidents / alerts, Security incidents, the Integration Platform, Vendor Management, Automation Orchestration,
and Business Continuity). Every panel is explainable (explanation + source + deep link); a non-explainable
panel is never emitted. A panel the principal is not entitled to see is emitted ``restricted`` (never its
value, count, or leaking metadata); an owner-not-configured panel is emitted ``available=False`` with
``config_status='not_configured'`` (fail closed). **No sensitive operational payloads are ever carried in a
panel — counts, status, and coverage summaries only.** A derived value carries ``derived=True``, describes
operational posture (never a certification that production is healthy or continuity assured), and never infers
recovery success.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PanelResult:
    key: str
    title: str
    owner: str                 # authoritative owning service (or "not_configured")
    source: str                # the authoritative read the value is composed from
    measure: str               # service | incident | alert | continuity | recovery | dependency | maintenance | resilience
    unit: str                  # count | percent | ratio | status | coverage | distribution
    viz: str                   # card | chart | gauge | list | leaderboard
    value: object              # the composed value(s) — computed by the authoritative owner
    explanation: str           # what this panel shows + where it comes from
    deep_link: str | None      # the authoritative owner surface to drill into
    restricted: bool = False   # entitlement withheld from a principal lacking the panel capability
    available: bool = True      # False when the authoritative owner is not_configured / unavailable (fail closed)
    derived: bool = False      # True when the value is a deterministic derivation (posture, never certification)
    config_status: str = "configured"   # configured | not_configured

    @property
    def is_explainable(self) -> bool:
        return bool(self.explanation and self.source and self.deep_link)

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "owner": self.owner, "source": self.source,
                "measure": self.measure, "unit": self.unit, "viz": self.viz, "value": self.value,
                "explanation": self.explanation, "deep_link": self.deep_link,
                "restricted": self.restricted, "available": self.available, "derived": self.derived,
                "config_status": self.config_status}


@dataclass(frozen=True)
class ResilienceDashboard:
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

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "audience": self.audience,
                "generated_at": self.generated_at, "panels": [p.to_dict() for p in self.panels],
                "governing_services": list(self.governing_services),
                "source_inventory": list(self.source_inventory), "deep_links": self.deep_links,
                "navigation": self.navigation, "refresh_policy": self.refresh_policy,
                "configured_domains": list(self.configured_domains),
                "not_configured_domains": list(self.not_configured_domains),
                "operational_posture_not_certification": True,
                "panel_count": len(self.panels)}
