"""Enterprise Data Governance Intelligence registries (Phase D.66) — the declarative catalogs of the
data-domain / lineage / stewardship / quality / retention composition layer.

Seven frozen, declarative catalogs; the layer owns NO persistence and defines NO second data catalog, metadata
repository, ETL platform, MDM platform, warehouse, governance platform, lineage engine, or quality engine:

  * DATA_DOMAIN_REGISTRY — the data-domain governance domains (data domains, data elements, data-quality rules,
    survivorship rules, MDM merge candidates; external data catalog, business glossary, data classification are
    NOT_CONFIGURED), each naming its authoritative owner, read surface, prohibited mutation surface, evidence
    source, governing capability, runtime gate, deep links, and config status.
  * DATA_LINEAGE_REGISTRY — the lineage domains (source-system provenance, entity lineage, record-scoped
    lineage; automated column-level lineage, data-sharing agreements / contracts are NOT_CONFIGURED).
  * DATA_STEWARDSHIP_REGISTRY — the stewardship domains (domain stewards, stewardship coverage, remediation
    cases; a formal stewardship-assignment workflow and data-product ownership are NOT_CONFIGURED).
  * DATA_QUALITY_REGISTRY — the quality domains (quality rules, quality findings, critical findings, quality
    coverage; data-quality scorecards / SLAs are NOT_CONFIGURED).
  * DATA_RETENTION_REGISTRY — the retention domains (retention assignments, legal holds, deletion requests,
    remediation cases; a retention-policy catalog beyond the Document Platform and DPIA are NOT_CONFIGURED).
  * PANEL_REGISTRY — every dashboard panel. * DATA_GOVERNANCE_DASHBOARDS — every data-governance dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second data catalog / metadata repository / ETL platform / MDM platform / warehouse / governance
platform / lineage engine / quality engine. Where no authoritative owner exists (external data catalog,
business glossary, data classification, automated column-level lineage, data contracts, DQ scorecards / SLAs,
golden records, DPIA, data products), the entry is declared `not_configured` and reported honestly — never
fabricated lineage, source systems, stewardship assignments, quality scores, retention policies, metadata,
catalog entries, or data owners. **A registered rule is not an executed check, a steward assignment is not a
governance guarantee, a lineage record is not a complete lineage, and coverage is not certification.**

NOTE: distinct from the D.52 Data Governance layer (`app/services/data_governance/`, `/data-governance`,
`governance.view`). Both are read-only views over the SINGLE authoritative D.23 Governance package
(`app/services/governance/`); neither owns, persists, or duplicates governance data. This D.66 layer's master
runtime gate is `data_governance_intelligence.enabled` — NOT the D.52 layer's `data_governance.enabled`.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


@dataclass(frozen=True)
class DomainEntry:
    key: str
    label: str
    owner: str                 # authoritative owner (or "not_configured")
    read_surface: str          # the authoritative read
    mutation_surface: str      # the prohibited mutation surface (never called)
    evidence_source: str       # where the evidence lives
    capabilities: tuple
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _e(key, label, owner, read_surface, mutation_surface, evidence_source, deep_links, *,
       capabilities=("governance.view",), runtime_gate="data_governance_intelligence.enabled",
       config_status=CONFIGURED):
    return DomainEntry(key, label, owner, read_surface, mutation_surface, evidence_source, tuple(capabilities),
                       runtime_gate, tuple(deep_links), config_status)


_GOV = ("/data-governance",)
_DGI = ("/data-governance-intelligence",)
_NC = NOT_CONFIGURED


# --- data domain registry ----------------------------------------------------

DATA_DOMAIN_REGISTRY = (
    _e("data_domains", "Data Domains", "governance.catalog", "governance.catalog.list_domains",
       "create_domain", "governance_data_domains", _GOV),
    _e("data_elements", "Data Elements", "governance.catalog", "governance.catalog.list_elements",
       "create_element", "governance_data_elements", _GOV),
    _e("data_quality_rules", "Data-Quality Rules", "governance.catalog", "governance.catalog.list_rules",
       "create_rule", "governance_rules", _GOV),
    _e("survivorship_rules", "Survivorship Rules", "governance.catalog",
       "governance.catalog.list_survivorship_rules", "create_survivorship_rule", "governance_survivorship_rules",
       _GOV),
    _e("mdm_merge_candidates", "MDM Merge Candidates", "governance.mdm", "governance.mdm.list_candidates",
       "record_merge_decision", "governance_match_candidates", _GOV),
    _e("external_data_catalog", "External Data Catalog", _NC, "n/a", "n/a", "n/a", _DGI, config_status=_NC),
    _e("business_glossary", "Business Glossary / Data Dictionary", _NC, "n/a", "n/a", "n/a", _DGI,
       config_status=_NC),
    _e("data_classification", "Data Classification Taxonomy", _NC, "n/a", "n/a", "n/a", _DGI,
       config_status=_NC),
)


# --- data lineage registry ---------------------------------------------------

DATA_LINEAGE_REGISTRY = (
    _e("entity_lineage", "Entity Lineage", "governance.mdm", "governance.mdm.list_lineage", "record_lineage",
       "governance_lineage", _GOV, runtime_gate="lineage_landscape.enabled"),
    _e("record_lineage_provenance", "Record Lineage / Provenance", "governance.mdm",
       "governance.mdm.person_lineage", "record_lineage", "governance_lineage", _GOV,
       runtime_gate="lineage_landscape.enabled"),
    _e("source_system_provenance", "Source-System Provenance", "governance.mdm", "governance.mdm.list_lineage",
       "record_lineage", "governance_lineage.source_system", _GOV, runtime_gate="lineage_landscape.enabled"),
    _e("automated_column_lineage", "Automated Column-Level Lineage", _NC, "n/a", "n/a", "n/a", _DGI,
       runtime_gate="lineage_landscape.enabled", config_status=_NC),
    _e("data_sharing_agreements", "Data-Sharing Agreements / Contracts", _NC, "n/a", "n/a", "n/a", _DGI,
       runtime_gate="lineage_landscape.enabled", config_status=_NC),
)


# --- data stewardship registry -----------------------------------------------

DATA_STEWARDSHIP_REGISTRY = (
    _e("domain_stewards", "Domain Stewards", "governance.catalog", "governance.catalog.list_domains",
       "create_domain", "data_domains.steward_user_id", _GOV),
    _e("stewardship_coverage", "Stewardship Coverage", "governance.catalog", "governance.catalog.list_domains",
       "create_domain", "data_domains.steward_user_id", _GOV),
    _e("remediation_cases", "Remediation Cases", "governance.retention", "governance.retention.list_cases",
       "create_case", "governance_cases", _GOV),
    _e("stewardship_assignment_workflow", "Stewardship Assignment Workflow", _NC, "n/a", "n/a", "n/a", _DGI,
       config_status=_NC),
    _e("data_product_ownership", "Data-Product Ownership", _NC, "n/a", "n/a", "n/a", _DGI, config_status=_NC),
)


# --- data quality registry ---------------------------------------------------

DATA_QUALITY_REGISTRY = (
    _e("quality_rules", "Data-Quality Rules", "governance.catalog", "governance.catalog.list_rules",
       "create_rule", "governance_rules", _GOV, runtime_gate="data_quality_landscape.enabled"),
    _e("quality_findings", "Data-Quality Findings", "governance.quality", "governance.quality.metrics",
       "create_finding", "governance_findings", _GOV, runtime_gate="data_quality_landscape.enabled"),
    _e("critical_findings", "Critical Data-Quality Findings", "governance.quality",
       "governance.quality.metrics", "create_finding", "governance_findings.critical", _GOV,
       runtime_gate="data_quality_landscape.enabled"),
    _e("quality_coverage", "Data-Quality Coverage", "governance.catalog", "governance.catalog.list_rules",
       "run_check", "governance_rules + findings", _GOV, runtime_gate="data_quality_landscape.enabled"),
    _e("quality_scorecards", "Data-Quality Scorecards / SLAs", _NC, "n/a", "n/a", "n/a", _DGI,
       runtime_gate="data_quality_landscape.enabled", config_status=_NC),
)


# --- data retention registry -------------------------------------------------

DATA_RETENTION_REGISTRY = (
    _e("retention_assignments", "Retention Assignments", "governance.retention",
       "governance.retention.list_retention_assignments", "create_retention_assignment",
       "governance_retention_assignments", _GOV),
    _e("legal_holds", "Legal Holds", "governance.retention", "governance.retention.list_legal_holds",
       "place_legal_hold", "governance_legal_holds", _GOV),
    _e("deletion_requests", "Deletion Requests", "governance.retention",
       "governance.retention.list_deletion_requests", "execute_deletion", "governance_deletion_requests", _GOV),
    _e("retention_review_status", "Retention Review Status", "governance.retention",
       "governance.retention.metrics", "review_due_retention", "governance_retention", _GOV),
    _e("retention_policy_catalog", "Retention Policy Catalog", _NC, "n/a", "n/a", "n/a", _DGI,
       config_status=_NC),
    _e("data_privacy_impact_assessments", "Data Privacy Impact Assessments (DPIA)", _NC, "n/a", "n/a", "n/a",
       _DGI, config_status=_NC),
)

_CD_BY_KEY = {}
for _reg in (DATA_DOMAIN_REGISTRY, DATA_LINEAGE_REGISTRY, DATA_STEWARDSHIP_REGISTRY, DATA_QUALITY_REGISTRY,
             DATA_RETENTION_REGISTRY):
    for _entry in _reg:
        _CD_BY_KEY[_entry.key] = _entry


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str
    source: str
    measure: str
    unit: str
    viz: str
    permission: str
    deep_link: str
    explainability: str
    derived: bool = False
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       derived=False, refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    derived, refresh, lifecycle)


_NC_NOTE = "NO authoritative owner exists in the platform today; reported not_configured, never fabricated."

PANEL_REGISTRY = (
    # data domain / inventory
    _p("data_inventory", "data_governance_intelligence", "data_governance_intelligence.registry", "domain",
       "count", "list", "governance.view", "/data-governance-intelligence",
       "The registered data-governance domains — each naming its authoritative owner + read + prohibited "
       "mutation surface + evidence + config status. Metadata only. A registered rule is not an executed "
       "check.", derived=True),
    _p("data_domain_coverage", "governance.catalog", "governance.catalog.list_domains", "domain", "count",
       "card", "governance.view", "/data-governance",
       "Registered data domains, from the Governance catalog. Counts only — never confidential metadata."),
    _p("data_element_coverage", "governance.catalog", "governance.catalog.list_elements", "domain", "count",
       "card", "governance.view", "/data-governance",
       "Registered data elements (fields catalogued across data domains), from the Governance catalog. Counts "
       "only."),
    _p("source_of_truth_coverage", "governance.catalog", "data_governance_intelligence.compose", "domain",
       "coverage", "gauge", "governance.view", "/data-governance-intelligence",
       "DERIVED source-of-truth coverage — data domains carrying a catalogued element + a rule. A coverage "
       "summary, never a certified source of truth.", derived=True),
    _p("governance_gaps", "data_governance_intelligence", "data_governance_intelligence.registry", "domain",
       "list", "list", "governance.view", "/data-governance-intelligence",
       "Data-governance areas with no authoritative owner (external catalog, business glossary, classification, "
       "column lineage, contracts, scorecards, DPIA) — reported honestly.", derived=True),
    # lineage
    _p("lineage_coverage", "governance.mdm", "data_governance_intelligence.compose", "lineage", "coverage",
       "gauge", "governance.view", "/data-governance-intelligence",
       "DERIVED lineage-domain coverage — configured lineage domains (entity lineage + record provenance + "
       "source-system provenance, via the Governance MDM owner) vs not_configured. A lineage record is not a "
       "complete lineage; automated column-level lineage has no owner.", derived=True),
    _p("record_lineage_availability", "governance.mdm", "governance.mdm.person_lineage", "lineage", "status",
       "card", "governance.view", "/data-governance",
       "Record-scoped lineage / source-system provenance is available via the Governance MDM owner "
       "(`person_lineage`); firm-wide provenance is composed at record scope (Client 360 / Household 360), "
       "never aggregated or inferred here. Status only."),
    _p("mdm_candidate_coverage", "governance.mdm", "governance.mdm.list_candidates", "lineage", "count", "card",
       "governance.view", "/data-governance",
       "MDM merge candidates (duplicate-resolution backlog), from the Governance MDM owner. Counts only."),
    _p("automated_lineage_availability", "not_configured", "data_governance_intelligence.registry", "lineage",
       "status", "card", "governance.view", "/data-governance-intelligence",
       "Automated column-level lineage / data-sharing contracts. " + _NC_NOTE + " Lineage is never inferred.",
       derived=True),
    # stewardship
    _p("stewardship_coverage", "governance.catalog", "data_governance_intelligence.compose", "stewardship",
       "coverage", "gauge", "governance.view", "/data-governance-intelligence",
       "DERIVED stewardship coverage — data domains with an assigned steward vs unstewarded. A coverage ratio; "
       "a steward assignment is not a governance guarantee.", derived=True),
    _p("stewarded_domains", "governance.catalog", "governance.catalog.list_domains", "stewardship", "count",
       "card", "governance.view", "/data-governance",
       "Data domains with an assigned steward (the `steward_user_id` presence), from the Governance catalog. "
       "Counts only — never a steward identity."),
    _p("remediation_case_coverage", "governance.retention", "governance.retention.list_cases", "stewardship",
       "count", "card", "governance.view", "/data-governance",
       "Open remediation cases (stewardship follow-up), from the Governance retention / case owner. Counts "
       "only."),
    _p("stewardship_workflow_availability", "not_configured", "data_governance_intelligence.registry",
       "stewardship", "status", "card", "governance.view", "/data-governance-intelligence",
       "A formal stewardship-assignment workflow / data-product ownership. " + _NC_NOTE, derived=True),
    # quality
    _p("quality_rule_coverage", "governance.catalog", "governance.catalog.list_rules", "quality", "count",
       "card", "governance.view", "/data-governance",
       "Registered data-quality rules, from the Governance catalog. A registered rule is not an executed "
       "check."),
    _p("quality_finding_summary", "governance.quality", "governance.quality.metrics", "quality", "status",
       "card", "governance.view", "/data-governance",
       "Open data-quality findings (open + critical), from the Governance Quality owner. Counts only — never a "
       "finding detail / data value / PII."),
    _p("critical_finding_summary", "governance.quality", "governance.quality.metrics", "quality", "count",
       "card", "governance.view", "/data-governance",
       "Open critical data-quality findings, from the Governance Quality owner. Counts only."),
    _p("quality_coverage", "governance.catalog", "data_governance_intelligence.compose", "quality", "coverage",
       "gauge", "governance.view", "/data-governance-intelligence",
       "DERIVED data-quality coverage — active quality rules vs data domains, with the open-finding load. A "
       "coverage indicator, never a certified quality score.", derived=True),
    _p("quality_scorecard_availability", "not_configured", "data_governance_intelligence.registry", "quality",
       "status", "card", "governance.view", "/data-governance-intelligence",
       "Data-quality scorecards / SLAs. " + _NC_NOTE + " A quality score is never fabricated.", derived=True),
    # retention
    _p("retention_assignment_coverage", "governance.retention",
       "governance.retention.list_retention_assignments", "retention", "count", "card", "governance.view",
       "/data-governance",
       "Retention assignments, from the Governance Retention owner. Counts only."),
    _p("legal_hold_summary", "governance.retention", "governance.retention.metrics", "retention", "count",
       "card", "governance.view", "/data-governance",
       "Active legal holds, from the Governance Retention owner. Counts only — never a hold reason / detail."),
    _p("deletion_request_summary", "governance.retention", "governance.retention.metrics", "retention",
       "count", "card", "governance.view", "/data-governance",
       "Pending deletion reviews, from the Governance Retention owner. Counts only. The layer never executes a "
       "deletion."),
    _p("retention_coverage", "governance.retention", "data_governance_intelligence.compose", "retention",
       "coverage", "gauge", "governance.view", "/data-governance-intelligence",
       "DERIVED retention coverage — retention assignments + active holds + pending reviews. A coverage "
       "indicator, never an enforced retention decision.", derived=True),
    _p("retention_policy_catalog_availability", "not_configured", "data_governance_intelligence.registry",
       "retention", "status", "card", "governance.view", "/data-governance-intelligence",
       "A retention-policy catalog beyond the Document Platform / DPIA. " + _NC_NOTE, derived=True),
    # governance readiness + risk + executive
    _p("configured_data_domains", "data_governance_intelligence", "data_governance_intelligence.registry",
       "domain", "coverage", "gauge", "governance.view", "/data-governance-intelligence",
       "Configured vs not_configured data-domain / lineage / stewardship / quality / retention coverage — a "
       "DERIVED coverage summary.", derived=True),
    _p("unconfigured_data_domains", "data_governance_intelligence", "data_governance_intelligence.registry",
       "domain", "list", "list", "governance.view", "/data-governance-intelligence",
       "The data-governance areas with no authoritative owner — reported honestly, never fabricated.",
       derived=True),
    _p("governance_readiness", "data_governance_intelligence", "data_governance_intelligence.compose",
       "verification", "coverage", "gauge", "governance.view", "/data-governance-intelligence",
       "DERIVED governance readiness — data-domain + lineage + stewardship + quality + retention coverage − "
       "not_configured areas. GOVERNANCE READINESS ONLY, never a repaired dataset, created lineage, assigned "
       "steward, executed rule, or enforced retention.", derived=True),
    _p("data_risk_indicators", "data_governance_intelligence", "data_governance_intelligence.compose",
       "verification", "status", "card", "governance.view", "/data-governance-intelligence",
       "DERIVED data-risk indicators — open critical findings + active legal holds + pending deletion reviews + "
       "unstewarded domains. A risk-visibility summary, never a remediation or an enforcement action.",
       derived=True),
    _p("governance_health_status", "data_governance_intelligence", "data_governance_intelligence.compose",
       "verification", "count", "card", "governance.view", "/data-governance-intelligence",
       "Composed governance status across the read-only layers — a DERIVED count of clean vs failing "
       "governance checkers. Governance coverage, never certification.", derived=True),
    _p("executive_data_governance_posture", "data_governance_intelligence",
       "data_governance_intelligence.compose", "verification", "distribution", "gauge", "analytics.executive",
       "/data-governance-intelligence",
       "DERIVED executive data-governance posture — domains + lineage + stewardship + quality + retention "
       "coverage + configured vs not_configured. Governance coverage only, never a certified data-governance "
       "outcome.", derived=True),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str
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


_DG_CAPS = ("governance.view", "analytics.executive")

DATA_GOVERNANCE_DASHBOARDS = (
    _d("enterprise_data_inventory", "data_governance_intelligence", "governance",
       "data_governance_intelligence.enabled",
       ("data_domain_coverage", "data_element_coverage", "source_of_truth_coverage", "data_inventory",
        "configured_data_domains"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=enterprise_data_inventory",
       ("governance.catalog",)),
    _d("lineage_landscape", "data_governance_intelligence", "governance", "lineage_landscape.enabled",
       ("lineage_coverage", "record_lineage_availability", "mdm_candidate_coverage",
        "automated_lineage_availability"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=lineage_landscape",
       ("governance.mdm",)),
    _d("stewardship_coverage", "data_governance_intelligence", "governance",
       "data_governance_intelligence.enabled",
       ("stewardship_coverage", "stewarded_domains", "remediation_case_coverage",
        "stewardship_workflow_availability"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=stewardship_coverage",
       ("governance.catalog", "governance.retention")),
    _d("data_quality_coverage", "data_governance_intelligence", "governance",
       "data_quality_landscape.enabled",
       ("quality_rule_coverage", "quality_finding_summary", "critical_finding_summary", "quality_coverage",
        "quality_scorecard_availability"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=data_quality_coverage",
       ("governance.catalog", "governance.quality")),
    _d("retention_coverage", "data_governance_intelligence", "governance",
       "data_governance_intelligence.enabled",
       ("retention_assignment_coverage", "legal_hold_summary", "deletion_request_summary",
        "retention_coverage", "retention_policy_catalog_availability"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=retention_coverage",
       ("governance.retention",)),
    _d("executive_data_governance", "data_governance_intelligence", "executive",
       "data_governance_intelligence.enabled",
       ("executive_data_governance_posture", "governance_readiness", "data_domain_coverage",
        "quality_finding_summary"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=executive_data_governance",
       ("data_governance_intelligence", "governance.catalog")),
    _d("governance_readiness", "data_governance_intelligence", "governance",
       "data_governance_intelligence.enabled",
       ("governance_readiness", "governance_health_status", "unconfigured_data_domains",
        "source_of_truth_coverage"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=governance_readiness",
       ("data_governance_intelligence", "governance.catalog")),
    _d("data_risk_overview", "data_governance_intelligence", "governance",
       "data_governance_intelligence.enabled",
       ("data_risk_indicators", "critical_finding_summary", "legal_hold_summary", "stewardship_coverage"),
       _DG_CAPS, "/data-governance-intelligence?dashboard=data_risk_overview",
       ("governance.quality", "governance.retention", "governance.catalog")),
)

_DASH_BY_KEY = {d.key: d for d in DATA_GOVERNANCE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def domain(key) -> DomainEntry | None:
    return _CD_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*DATA_DOMAIN_REGISTRY, *DATA_LINEAGE_REGISTRY, *DATA_STEWARDSHIP_REGISTRY, *DATA_QUALITY_REGISTRY,
            *DATA_RETENTION_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "data_domain_entries": len(DATA_DOMAIN_REGISTRY),
        "lineage_entries": len(DATA_LINEAGE_REGISTRY),
        "stewardship_entries": len(DATA_STEWARDSHIP_REGISTRY),
        "quality_entries": len(DATA_QUALITY_REGISTRY),
        "retention_entries": len(DATA_RETENTION_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(DATA_GOVERNANCE_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
