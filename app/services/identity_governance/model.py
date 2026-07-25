"""Enterprise Identity & Access Governance read-models (Phase D.65).

Normalized, read-only projections of a composed identity / role / capability / authentication / authorization
panel and an identity dashboard. Every panel REFERENCES its authoritative owner (owning service + read + deep
link) and never authenticates a user, authorizes a request, assigns a role, grants or revokes a permission,
modifies a policy, creates an identity, creates a session, or manages a password — the value is composed on
READ by the authoritative owner (the Identity service for the user / role / capability / team directory,
Security RBAC for role & capability resolution, Security Authentication for providers, the Policy engine for
policy coverage, and Security Authorization for record-scope decisions). Every panel is explainable
(explanation + source + deep link); a non-explainable panel is never emitted. A panel the principal is not
entitled to see is emitted ``restricted`` (never its value, count, or leaking metadata); an owner-not-configured
panel is emitted ``available=False`` with ``config_status='not_configured'`` (fail closed). **No passwords,
secrets, tokens, session IDs, credentials, authentication payloads, raw identities (emails / names /
auth_subjects), privileged-role membership, or user-level permission maps are ever carried in a panel — counts,
coverage, status, ratios, and verification results only.** A derived value carries ``derived=True``, describes
GOVERNANCE READINESS / COVERAGE (never an authentication result, an authorization decision, a granted
permission, or a certified access review), and keeps unavailable / not_configured / failed areas visible — **a
capability inventory is not a grant, a role definition is not an assignment, a provider registration is not an
authentication, and coverage is not certification.**
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PanelResult:
    key: str
    title: str
    owner: str                 # authoritative owning service (or "not_configured")
    source: str                # the authoritative read the value is composed from
    measure: str               # identity | role | capability | authentication | authorization | policy | verification
    unit: str                  # count | coverage | status | distribution | ratio | verification
    viz: str                   # card | chart | gauge | list | leaderboard
    value: object              # the composed value(s) — computed by the authoritative owner
    explanation: str           # what this panel shows + where it comes from
    deep_link: str | None      # the authoritative owner surface to drill into
    restricted: bool = False   # entitlement withheld from a principal lacking the panel capability
    available: bool = True      # False when the authoritative owner is not_configured / unavailable (fail closed)
    derived: bool = False      # True when the value is a deterministic derivation (readiness, never a decision)
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
class IdentityDashboard:
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
                "capability_inventory_is_not_a_grant": True,
                "role_definition_is_not_an_assignment": True,
                "provider_registration_is_not_an_authentication": True,
                "governance_coverage_not_certification": True,
                "panel_count": len(self.panels)}
