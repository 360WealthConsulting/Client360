"""Data Governance registries (Phase D.52) — the declarative catalogs of the data-governance layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new metrics, master data
store, identity system, metadata repository, or merge engine:

  * MASTER_DATA_REGISTRY — every governed entity (Person, Household, Organization, Advisor, Client, Prospect,
    Trust, Estate, Account, Policy, Plan, Tax Return, Engagement, Opportunity, Document). Each names its
    authoritative owner, identity owner, metadata owner, stewardship owner, lineage owner, runtime gate, and
    deep links. The layer stores NOTHING — it references these owners.
  * STEWARDSHIP_REGISTRY — every stewardship responsibility (client data, household data, tax/investment/
    insurance/benefits data, document metadata, compliance records). Each names its business owner, technical
    owner, validation owner, approval owner, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * GOVERNANCE_DASHBOARDS — every governance dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every governed entity + stewardship role is registered, every panel names an
authoritative owner + source + deep link, and that this layer never becomes a second master-data platform,
identity system, metadata repository, or merge engine.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- master data registry ----------------------------------------------------

@dataclass(frozen=True)
class GovernedEntity:
    key: str
    label: str
    authoritative_owner: str   # the authoritative service that owns the entity of record
    identity_owner: str        # the authoritative identity/merge owner (never re-implemented)
    metadata_owner: str        # the authoritative metadata owner (never duplicated)
    stewardship_owner: str     # a key into STEWARDSHIP_REGISTRY (or governance.catalog steward)
    lineage_owner: str         # the authoritative lineage/provenance owner
    runtime_gate: str
    deep_links: tuple


def _ent(key, label, authoritative_owner, deep_links, *, identity_owner="person_merge",
         metadata_owner="governance.catalog", stewardship_owner="client_data",
         lineage_owner="governance.mdm", runtime_gate="data_governance.enabled"):
    return GovernedEntity(key, label, authoritative_owner, identity_owner, metadata_owner, stewardship_owner,
                          lineage_owner, runtime_gate, tuple(deep_links))


MASTER_DATA_REGISTRY = (
    _ent("person", "Person", "people", ("/client/{id}", "/governance"),
         identity_owner="person_merge", lineage_owner="governance.mdm.person_lineage",
         stewardship_owner="client_data"),
    _ent("household", "Household", "household_derivation", ("/client/household/{id}",),
         identity_owner="household_derivation", stewardship_owner="household_data"),
    _ent("organization", "Organization", "organization_service", ("/organizations",),
         identity_owner="organization_service", stewardship_owner="client_data"),
    _ent("advisor", "Advisor", "identity", ("/identity",),
         identity_owner="identity", stewardship_owner="client_data"),
    _ent("client", "Client", "client360", ("/client/{id}",),
         identity_owner="person_merge", stewardship_owner="client_data"),
    _ent("prospect", "Prospect", "client360", ("/client/{id}",),
         identity_owner="person_merge", stewardship_owner="client_data"),
    _ent("trust", "Trust", "relationships", ("/relationships",),
         identity_owner="relationships", stewardship_owner="client_data",
         lineage_owner="governance.mdm.list_lineage"),
    _ent("estate", "Estate", "relationships", ("/relationships",),
         identity_owner="relationships", stewardship_owner="client_data",
         lineage_owner="governance.mdm.list_lineage"),
    _ent("account", "Account", "portfolio", ("/portfolio",),
         identity_owner="portfolio", stewardship_owner="investment_data"),
    _ent("policy", "Policy", "insurance", ("/insurance",),
         identity_owner="insurance", stewardship_owner="insurance_data"),
    _ent("plan", "Plan", "benefits_domain", ("/benefits",),
         identity_owner="benefits_domain", stewardship_owner="benefits_data"),
    _ent("tax_return", "Tax Return", "tax_domain", ("/tax",),
         identity_owner="tax_domain", stewardship_owner="tax_data"),
    _ent("engagement", "Engagement", "tax_domain", ("/tax",),
         identity_owner="tax_domain", stewardship_owner="tax_data"),
    _ent("opportunity", "Opportunity", "opportunity", ("/opportunities",),
         identity_owner="opportunity", stewardship_owner="client_data"),
    _ent("document", "Document", "document_platform", ("/document-library", "/document-intelligence"),
         identity_owner="document_platform", metadata_owner="document_platform",
         stewardship_owner="document_metadata", lineage_owner="governance.mdm.list_lineage"),
)

_ENT_BY_KEY = {e.key: e for e in MASTER_DATA_REGISTRY}


# --- stewardship registry ----------------------------------------------------

@dataclass(frozen=True)
class StewardshipRole:
    key: str
    label: str
    business_owner: str        # the business steward (accountable)
    technical_owner: str       # the authoritative technical owner service
    validation_owner: str      # the authoritative validation/quality owner
    approval_owner: str        # the authoritative approval owner
    runtime_gate: str = "stewardship.enabled"


def _stew(key, label, business_owner, technical_owner, *, validation_owner="governance.quality",
          approval_owner="governance.review"):
    return StewardshipRole(key, label, business_owner, technical_owner, validation_owner, approval_owner)


STEWARDSHIP_REGISTRY = (
    _stew("client_data", "Client Data", "advisor", "people"),
    _stew("household_data", "Household Data", "advisor", "household_derivation"),
    _stew("tax_data", "Tax Data", "tax_preparer", "tax_domain"),
    _stew("investment_data", "Investment Data", "advisor", "portfolio"),
    _stew("insurance_data", "Insurance Data", "advisor", "insurance"),
    _stew("benefits_data", "Benefits Data", "advisor", "benefits_domain"),
    _stew("document_metadata", "Document Metadata", "operations", "document_platform"),
    _stew("compliance_records", "Compliance Records", "compliance", "compliance",
          validation_owner="governance.quality", approval_owner="governance.review"),
)

_STEW_BY_KEY = {s.key: s for s in STEWARDSHIP_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative entity-owner surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # master data
    _p("registered_entities", "data_governance", "data_governance.registry", "master_data", "count", "list",
       "governance.view", "/data-governance",
       "The registered governed-entity catalog — each naming its authoritative / identity / metadata / "
       "stewardship / lineage owner. No second master-data store."),
    _p("data_domains", "governance.catalog", "governance.catalog.list_domains", "master_data", "count", "card",
       "governance.view", "/governance",
       "Registered data domains, from the Governance data catalog (the authoritative metadata owner)."),
    _p("data_elements", "governance.catalog", "governance.catalog.list_elements", "master_data", "count",
       "card", "governance.view", "/governance",
       "Registered data elements, from the Governance data catalog. No second metadata repository."),
    # stewardship
    _p("registered_stewardship", "data_governance", "data_governance.registry", "stewardship", "count", "list",
       "governance.view", "/data-governance",
       "The registered stewardship-role catalog — each naming its business / technical / validation / "
       "approval owner."),
    _p("domain_stewards", "governance.catalog", "governance.catalog.list_domains", "stewardship", "count",
       "card", "governance.view", "/governance",
       "Data domains with an assigned steward, from the Governance data catalog."),
    _p("remediation_cases", "governance.retention", "governance.retention.list_cases", "stewardship", "count",
       "card", "governance.view", "/governance",
       "Open governance remediation cases, from the Governance package."),
    # lineage
    _p("lineage_rules", "governance.catalog", "governance.catalog.list_survivorship_rules", "lineage", "count",
       "card", "governance.view", "/governance",
       "Registered survivorship/lineage rules, from the Governance data catalog."),
    _p("event_lineage", "events", "events.registry.dependency_graph", "lineage", "count", "chart",
       "governance.view", "/events",
       "Event-dependency lineage (causation graph), from the Event registry."),
    _p("lineage_coverage", "data_governance", "data_governance.registry", "lineage", "count", "chart",
       "governance.view", "/data-governance",
       "Governed entities with a declared lineage owner, from the master-data registry (provenance is owned "
       "by governance.mdm / person_source_links — never duplicated)."),
    # ownership
    _p("entity_ownership", "data_governance", "data_governance.registry", "ownership", "count", "chart",
       "governance.view", "/data-governance",
       "Governed entities by authoritative owner, from the master-data registry. No second identity system."),
    _p("stewardship_coverage", "data_governance", "data_governance.registry", "ownership", "count", "chart",
       "governance.view", "/data-governance",
       "Stewardship roles by business owner, from the stewardship registry."),
    # duplicate detection
    _p("duplicate_candidates", "governance.mdm", "governance.mdm.list_candidates", "duplicate", "count", "card",
       "governance.view", "/governance",
       "Open duplicate candidates, from the Governance MDM engine (composes the authoritative person-merge "
       "candidates — no second merge engine)."),
    _p("ambiguous_unlinked", "matching", "matching.promote.list_ambiguous_unlinked", "duplicate", "count",
       "card", "governance.view", "/matches/unresolved",
       "Ambiguous unlinked source contacts awaiting resolution, from the entity-resolution engine."),
    _p("merge_summary", "governance.mdm", "governance.mdm.list_candidates", "duplicate", "count", "chart",
       "governance.view", "/governance",
       "Duplicate/merge candidate summary by status (a read-only merge-history rollup). Never merges."),
    # validation
    _p("validation_findings", "governance.quality", "governance.quality.list_findings", "validation", "count",
       "chart", "governance.view", "/governance",
       "Open data-quality findings by severity, from the Governance quality engine."),
    _p("validation_metrics", "governance.quality", "governance.quality.metrics", "validation", "count", "card",
       "governance.view", "/governance",
       "Data-quality validation metrics (open / critical / total), from the Governance quality engine."),
    _p("quality_rules", "governance.catalog", "governance.catalog.list_rules", "validation", "count", "card",
       "governance.view", "/governance",
       "Registered data-quality rules, from the Governance data catalog."),
    # data quality
    _p("governance_overview", "governance", "governance.service.overview_metrics", "quality", "count", "card",
       "governance.view", "/governance",
       "Firm governance overview (findings / holds / deletion reviews / cases), from the Governance package."),
    _p("data_quality_score", "data_governance", "data_governance.compose", "quality", "percent", "gauge",
       "governance.view", "/governance",
       "Deterministic data-quality indicator (registered entities vs open validation findings) — advisory "
       "only; never alters data, merges, or approves."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # governance | operations | executive | compliance
    runtime_gate: str
    panels: tuple
    required_capabilities: tuple
    navigation: str
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, panels, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(panels), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


GOVERNANCE_DASHBOARDS = (
    _d("master_data", "data_governance", "governance", "data_governance.enabled",
       ("registered_entities", "data_domains", "data_elements"),
       ("governance.view",), "/data-governance?dashboard=master_data",
       ("governance.catalog",)),
    _d("stewardship", "data_governance", "governance", "stewardship.enabled",
       ("registered_stewardship", "domain_stewards", "remediation_cases"),
       ("governance.view",), "/data-governance?dashboard=stewardship",
       ("governance.catalog", "governance.retention")),
    _d("lineage", "data_governance", "governance", "lineage.enabled",
       ("lineage_rules", "event_lineage", "lineage_coverage"),
       ("governance.view",), "/data-governance?dashboard=lineage",
       ("governance.catalog", "events")),
    _d("ownership", "data_governance", "governance", "data_governance.enabled",
       ("entity_ownership", "domain_stewards", "stewardship_coverage"),
       ("governance.view",), "/data-governance?dashboard=ownership",
       ("governance.catalog",)),
    _d("duplicate_detection", "data_governance", "governance", "data_governance.enabled",
       ("duplicate_candidates", "ambiguous_unlinked", "merge_summary"),
       ("governance.view",), "/data-governance?dashboard=duplicate_detection",
       ("governance.mdm", "matching")),
    _d("validation", "data_governance", "governance", "data_governance.enabled",
       ("validation_findings", "validation_metrics", "quality_rules"),
       ("governance.view",), "/data-governance?dashboard=validation",
       ("governance.quality", "governance.catalog")),
    _d("data_quality", "data_governance", "operations", "data_governance.enabled",
       ("governance_overview", "validation_metrics", "data_quality_score"),
       ("governance.view",), "/data-governance?dashboard=data_quality",
       ("governance", "governance.quality")),
)

_DASH_BY_KEY = {d.key: d for d in GOVERNANCE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def governed_entity(key) -> GovernedEntity | None:
    return _ENT_BY_KEY.get(key)


def stewardship_role(key) -> StewardshipRole | None:
    return _STEW_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def entity_registered(key) -> bool:
    return key in _ENT_BY_KEY


def stewardship_registered(key) -> bool:
    return key in _STEW_BY_KEY


def coverage() -> dict:
    return {
        "governed_entities": len(MASTER_DATA_REGISTRY),
        "stewardship_roles": len(STEWARDSHIP_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(GOVERNANCE_DASHBOARDS),
    }
