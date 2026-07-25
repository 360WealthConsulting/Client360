"""Enterprise Data Governance Intelligence read-models (Phase D.66).

Normalized, read-only projections of a composed data-domain / lineage / stewardship / quality / retention panel
and a data-governance dashboard. Every panel REFERENCES its authoritative owner (owning service + read + deep
link) and never transforms data, synchronizes systems, mutates metadata, repairs data, creates lineage, assigns
a steward, executes a quality rule, or enforces retention — the value is composed on READ by the authoritative
owner (the Governance catalog for data domains / elements / rules / stewardship, Governance MDM for lineage &
provenance, Governance Quality for findings, and Governance Retention for assignments / legal holds / deletion
requests / cases). Every panel is explainable (explanation + source + deep link); a non-explainable panel is
never emitted. A panel the principal is not entitled to see is emitted ``restricted`` (never its value, count,
or leaking metadata); an owner-not-configured panel is emitted ``available=False`` with
``config_status='not_configured'`` (fail closed). **No sensitive data values, client PII, credentials, secrets,
tokens, confidential metadata, internal governance notes, or quality-rule internals are ever carried in a panel
— counts, coverage, status, ratios, and verification results only.** A derived value carries ``derived=True``,
describes GOVERNANCE READINESS / COVERAGE (never a repaired dataset, a created lineage edge, an assigned
steward, an executed quality rule, or an enforced retention decision), and keeps unavailable / not_configured /
failed areas visible — **a registered rule is not an executed check, a steward assignment is not a governance
guarantee, a lineage record is not a complete lineage, and coverage is not certification.**
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PanelResult:
    key: str
    title: str
    owner: str                 # authoritative owning service (or "not_configured")
    source: str                # the authoritative read the value is composed from
    measure: str               # domain | lineage | stewardship | quality | retention | verification
    unit: str                  # count | coverage | status | distribution | ratio | verification
    viz: str                   # card | chart | gauge | list | leaderboard
    value: object              # the composed value(s) — computed by the authoritative owner
    explanation: str           # what this panel shows + where it comes from
    deep_link: str | None      # the authoritative owner surface to drill into
    restricted: bool = False   # entitlement withheld from a principal lacking the panel capability
    available: bool = True      # False when the authoritative owner is not_configured / unavailable (fail closed)
    derived: bool = False      # True when the value is a deterministic derivation (readiness, never a mutation)
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
class DataGovernanceDashboard:
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
                "registered_rule_is_not_an_executed_check": True,
                "steward_assignment_is_not_a_governance_guarantee": True,
                "lineage_record_is_not_complete_lineage": True,
                "governance_coverage_not_certification": True,
                "panel_count": len(self.panels)}
