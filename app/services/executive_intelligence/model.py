"""Executive Reporting read-models (Phase D.48).

Normalized, read-only projections of a composed widget result and an executive dashboard. Every widget
REFERENCES its authoritative source (source service + deep link) and never mutates or copies operational
data — values are computed on read by the authoritative service (the Analytics Registry's ``compute_metric``
and the domain firm reads). Every widget is explainable (explanation + source + deep link); a non-explainable
widget is never emitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WidgetResult:
    key: str
    title: str
    owner: str                 # authoritative owning service
    source: str                # the read the value is composed from
    aggregation: str           # count | sum | rollup | trend | health | distribution
    unit: str                  # currency | percent | count | number | status | mixed
    viz: str                   # card | trendline | gauge | leaderboard | chart | list
    value: object              # the composed value(s) — computed by the authoritative service
    explanation: str           # why/what this widget shows and where it comes from
    deep_link: str | None      # the authoritative surface to drill into
    restricted: bool = False   # executive metric withheld from a non-executive (inherited gating)
    available: bool = True

    @property
    def is_explainable(self) -> bool:
        return bool(self.explanation and self.source and self.deep_link)

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "owner": self.owner, "source": self.source,
                "aggregation": self.aggregation, "unit": self.unit, "viz": self.viz, "value": self.value,
                "explanation": self.explanation, "deep_link": self.deep_link, "restricted": self.restricted,
                "available": self.available}


@dataclass(frozen=True)
class Dashboard:
    key: str
    name: str
    audience: str
    generated_at: str | None
    widgets: tuple = ()               # tuple[WidgetResult]
    governing_services: tuple = ()    # the authoritative services composed
    source_inventory: tuple = ()      # the widget sources (for explainability + governance)
    deep_links: dict = field(default_factory=dict)
    navigation: str | None = None
    refresh_policy: str = "on_view"

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "audience": self.audience,
                "generated_at": self.generated_at, "widgets": [w.to_dict() for w in self.widgets],
                "governing_services": list(self.governing_services),
                "source_inventory": list(self.source_inventory), "deep_links": self.deep_links,
                "navigation": self.navigation, "refresh_policy": self.refresh_policy,
                "widget_count": len(self.widgets)}
