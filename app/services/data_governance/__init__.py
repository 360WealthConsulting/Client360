"""Enterprise Data Governance, Master Data & Platform Stewardship layer (Phase D.52).

A governed, READ-ONLY composition that provides enterprise-wide visibility into data quality, lineage,
stewardship, and ownership — WITHOUT introducing a second master-data platform, identity system,
synchronization engine, entity-resolution engine, metadata repository, or merge engine. It composes named
governance dashboards from declarative master-data + stewardship + panel registries over the platform's
AUTHORITATIVE data owners: the D.23 Governance package (`governance.catalog` metadata, `governance.quality`
validation, `governance.mdm` duplicate/lineage, `governance.retention` cases, `governance.service`
overview), the Person-merge / entity-resolution engine, the Event registry (event lineage), and the domain
entity owners. It defines no new metrics, owns no persistence, and never merges an entity, alters an
identity, modifies metadata, approves stewardship, or changes ownership; every panel is explainable,
deep-links to its authoritative entity-owner surface, and carries counts + status only — never a
client-sensitive payload.
"""
from .service import (
    client_governance,
    compose_dashboard,
    get_panel,
    governance_summary,
    household_governance,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "governance_summary",
    "client_governance",
    "household_governance",
]
