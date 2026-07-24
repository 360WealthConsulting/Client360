"""Integration Hub read-models (Phase D.53).

Normalized, read-only projections of a composed integration panel and an integration dashboard. Every panel
REFERENCES its authoritative owner (owning service + read + deep link) and never mutates an external system,
triggers synchronization, invokes an API, refreshes a token, or changes an integration setting — the value
is composed on READ by the authoritative owner (the D.24 Integration Platform, the Event registry, the M365
connector). Every panel is explainable (explanation + source + deep link); a non-explainable panel is never
emitted. A panel the principal is not entitled to see is emitted ``restricted`` (never its value).
**No secrets, tokens, credentials, or client payloads are ever carried in a panel — counts and status only.**
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PanelResult:
    key: str
    title: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str               # integrations | sync | auth | webhooks | connectors | api | events
    unit: str                  # count | percent | ratio | status | mixed
    viz: str                   # card | chart | gauge | list | leaderboard
    value: object              # the composed value(s) — computed by the authoritative owner (counts/status)
    explanation: str           # what this panel shows + where it comes from
    deep_link: str | None      # the authoritative connector-owner surface to drill into
    restricted: bool = False   # entitlement withheld from a principal lacking the panel capability
    available: bool = True

    @property
    def is_explainable(self) -> bool:
        return bool(self.explanation and self.source and self.deep_link)

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "owner": self.owner, "source": self.source,
                "measure": self.measure, "unit": self.unit, "viz": self.viz, "value": self.value,
                "explanation": self.explanation, "deep_link": self.deep_link,
                "restricted": self.restricted, "available": self.available}


@dataclass(frozen=True)
class IntegrationDashboard:
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

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "audience": self.audience,
                "generated_at": self.generated_at, "panels": [p.to_dict() for p in self.panels],
                "governing_services": list(self.governing_services),
                "source_inventory": list(self.source_inventory), "deep_links": self.deep_links,
                "navigation": self.navigation, "refresh_policy": self.refresh_policy,
                "panel_count": len(self.panels)}
