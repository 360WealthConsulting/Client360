"""Enterprise Knowledge Management, SOP Governance & Institutional Intelligence layer (Phase D.62).

A governed, READ-ONLY composition that provides a unified, governed view of firm knowledge, SOPs, and
documentation — SOP coverage, documentation completeness, document freshness, ownership coverage, version
awareness, publication readiness, documentation gaps, orphaned documentation, runbook coverage, and knowledge
health — WITHOUT introducing a second wiki, document-management platform, Confluence replacement, SharePoint,
records-management platform, search engine, AI knowledge store, or document repository. It composes named
knowledge dashboards from declarative knowledge-domain + SOP-category + documentation-owner + knowledge-source
+ publication-status registries over the platform's AUTHORITATIVE owners: the Document Platform (documents,
classification, deterministic lifecycle, immutable versions, ownership), Document Intelligence (documentation
completeness / gaps / freshness), and Data Governance retention. SOP governance, runbooks, playbooks,
onboarding SOPs, a knowledge base, institutional memory, a wiki, Confluence, and a dedicated full-text / vector
search index have no authoritative owner in the platform today — declared registry entries with a
`not_configured` status, never a fabricated document, SOP approval, version history, or institutional
knowledge. (NOTE: this D.62 layer is distinct from the D.45 Enterprise Knowledge GRAPH — its master runtime
gate is `knowledge_management.enabled`, not the graph's `knowledge.enabled`.) It defines no new metrics, owns
no persistence, and never creates / edits / approves / publishes a document, changes a version, or alters
metadata; every panel is explainable, deep-links to its authoritative owner, and carries counts / status /
coverage only — never document contents, confidential procedures, credentials, tokens, or client-sensitive
documentation. The derived executive posture is a documentation-coverage summary, never fabricated documentation
or institutional knowledge.
"""
from .service import (
    client_documentation,
    compose_dashboard,
    get_panel,
    household_documentation,
    knowledge_summary,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "knowledge_summary",
    "client_documentation",
    "household_documentation",
]
