"""Enterprise Data Governance, Lineage & Information Stewardship Intelligence layer (Phase D.66).

A governed, READ-ONLY composition that provides a unified, governed view of the firm's data-governance posture
— enterprise data inventory, source-of-truth coverage, lineage coverage, stewardship coverage, quality-rule
coverage, retention coverage, governance readiness, data-risk indicators, and governance gaps — WITHOUT
introducing a second data catalog, metadata repository, ETL platform, MDM platform, warehouse, governance
platform, lineage engine, or quality engine. It composes named data-governance dashboards from declarative
data-domain + lineage + stewardship + quality + retention registries over the platform's AUTHORITATIVE owners:
the Governance catalog (data domains, elements, quality rules, survivorship rules, stewardship), Governance MDM
(lineage & provenance, merge candidates), Governance Quality (findings), and Governance Retention (assignments,
legal holds, deletion requests, cases). External data catalog, business glossary, data classification,
automated column-level lineage, data contracts, DQ scorecards / SLAs, retention-policy catalog, and DPIA have no
authoritative owner in the platform today — declared registry entries with a `not_configured` status, never
fabricated lineage, source systems, stewardship assignments, quality scores, retention policies, metadata,
catalog entries, or data owners. It defines no new metrics, owns no persistence, and NEVER transforms data,
synchronizes systems, mutates metadata, repairs data, creates lineage, assigns a steward, executes a quality
rule, or enforces retention; every panel is explainable, deep-links to its authoritative owner, and carries
counts / coverage / status / ratios only — never a sensitive data value, client PII, credential, secret, token,
confidential metadata, internal governance note, or quality-rule internal. The derived posture is a
GOVERNANCE-READINESS summary, never a repaired dataset, a created lineage edge, an assigned steward, an executed
quality rule, or an enforced retention decision: **a registered rule is not an executed check, a steward
assignment is not a governance guarantee, a lineage record is not a complete lineage, and coverage is not
certification.** (Distinct from the D.52 Data Governance layer; both are read-only views over the single
authoritative D.23 Governance package — this layer's master gate is `data_governance_intelligence.enabled`.)
"""
from .service import (
    client_data_governance,
    compose_dashboard,
    data_governance_summary,
    get_panel,
    household_data_governance,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "data_governance_summary",
    "client_data_governance",
    "household_data_governance",
]
