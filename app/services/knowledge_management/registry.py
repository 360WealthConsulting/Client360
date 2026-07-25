"""Enterprise Knowledge Management registries (Phase D.62) — the declarative catalogs of the knowledge / SOP /
documentation composition layer.

Seven frozen, declarative catalogs; the layer owns NO persistence and defines NO second wiki,
document-management platform, Confluence replacement, SharePoint, records-management platform, search engine,
AI knowledge store, or document repository:

  * KNOWLEDGE_DOMAIN_REGISTRY — knowledge domains (client / compliance / tax / operational / legal / internal
    documentation, knowledge base, institutional memory). Knowledge base + institutional memory have no
    authoritative owner → declared not_configured.
  * SOP_CATEGORY_REGISTRY — SOP categories (operational / compliance procedures, runbooks, playbooks,
    onboarding). Runbooks / playbooks / onboarding SOPs have no authoritative owner → not_configured (the
    Document Platform has `operations` / `internal` / `compliance` classified documents but no SOP-governance
    engine).
  * DOCUMENTATION_OWNER_REGISTRY — documentation owners (document authors, retention owners, classification /
    lifecycle owners, unassigned documentation).
  * KNOWLEDGE_SOURCE_REGISTRY — knowledge sources (the Document Platform, Document Intelligence, Data
    Governance retention, the rule catalog, wiki, Confluence, search index). Wiki / Confluence / a dedicated
    full-text-or-vector search index have no authoritative owner → not_configured.
  * PUBLICATION_STATUS_REGISTRY — document publication / lifecycle statuses (draft, review, approved,
    superseded, archived), each owned by the Document Platform lifecycle.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * KNOWLEDGE_DASHBOARDS — every knowledge dashboard.

Governance verifies every registry key is unique, every configured entry names an authoritative owner, every
panel names an authoritative owner + source + deep link, every derived value is labeled, and that this layer
never becomes a second wiki / document repository / search platform. Where no authoritative owner exists
(SOP governance, runbooks, playbooks, onboarding SOPs, knowledge base, institutional memory, wiki, Confluence,
full-text search), the entry is declared `not_configured` and reported honestly — never a fabricated document,
SOP approval, version history, or institutional knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


# --- knowledge domain registry -----------------------------------------------

@dataclass(frozen=True)
class KnowledgeDomain:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _kd(key, label, owner, deep_links, *, capabilities=("documents.view",),
        runtime_gate="knowledge_management.enabled", config_status=CONFIGURED):
    return KnowledgeDomain(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                           config_status)


KNOWLEDGE_DOMAIN_REGISTRY = (
    _kd("client_documentation", "Client Documentation", "document_platform", ("/documents",)),
    _kd("compliance_documentation", "Compliance Documentation", "document_platform",
        ("/documents", "/supervision")),
    _kd("tax_documentation", "Tax Documentation", "document_platform", ("/documents", "/tax")),
    _kd("operational_documentation", "Operational Documentation", "document_platform", ("/documents",)),
    _kd("legal_documentation", "Legal Documentation", "document_platform", ("/documents",)),
    _kd("internal_documentation", "Internal Documentation", "document_platform", ("/documents",)),
    _kd("knowledge_base", "Knowledge Base", NOT_CONFIGURED, ("/knowledge-management",),
        config_status=NOT_CONFIGURED),
    _kd("institutional_memory", "Institutional Memory", NOT_CONFIGURED, ("/knowledge-management",),
        config_status=NOT_CONFIGURED),
)

_KD_BY_KEY = {k.key: k for k in KNOWLEDGE_DOMAIN_REGISTRY}


# --- SOP category registry ---------------------------------------------------

@dataclass(frozen=True)
class SOPCategory:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _sop(key, label, owner, deep_links, *, capabilities=("documents.view",),
         runtime_gate="sop_governance.enabled", config_status=CONFIGURED):
    return SOPCategory(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links), config_status)


SOP_CATEGORY_REGISTRY = (
    _sop("operational_sops", "Operational SOPs", "document_platform", ("/documents",)),
    _sop("compliance_sops", "Compliance SOPs", "document_platform", ("/documents", "/supervision")),
    _sop("procedures", "Procedures", "document_platform", ("/documents",)),
    _sop("runbooks", "Runbooks", NOT_CONFIGURED, ("/knowledge-management",), config_status=NOT_CONFIGURED),
    _sop("playbooks", "Playbooks", NOT_CONFIGURED, ("/knowledge-management",), config_status=NOT_CONFIGURED),
    _sop("onboarding_sops", "Onboarding SOPs", NOT_CONFIGURED, ("/knowledge-management",),
         config_status=NOT_CONFIGURED),
)

_SOP_BY_KEY = {s.key: s for s in SOP_CATEGORY_REGISTRY}


# --- documentation owner registry --------------------------------------------

@dataclass(frozen=True)
class DocumentationOwner:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _do(key, label, owner, deep_links, *, capabilities=("documents.view",),
        runtime_gate="knowledge_management.enabled", config_status=CONFIGURED):
    return DocumentationOwner(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                              config_status)


DOCUMENTATION_OWNER_REGISTRY = (
    _do("document_authors", "Document Authors", "document_platform", ("/documents",)),
    _do("retention_owners", "Retention Owners", "governance.retention", ("/documents",),
        capabilities=("governance.view",)),
    _do("classification_owners", "Classification Owners", "document_platform", ("/documents",)),
    _do("lifecycle_owners", "Lifecycle Owners", "document_platform", ("/documents",)),
    _do("unassigned_documentation", "Unassigned Documentation", "document_platform", ("/documents",)),
)

_DO_BY_KEY = {d.key: d for d in DOCUMENTATION_OWNER_REGISTRY}


# --- knowledge source registry -----------------------------------------------

@dataclass(frozen=True)
class KnowledgeSource:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _ks(key, label, owner, deep_links, *, capabilities=("documents.view",),
        runtime_gate="knowledge_management.enabled", config_status=CONFIGURED):
    return KnowledgeSource(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                           config_status)


KNOWLEDGE_SOURCE_REGISTRY = (
    _ks("document_platform", "Document Platform", "document_platform", ("/documents",)),
    _ks("document_intelligence", "Document Intelligence", "document_intelligence", ("/documents",)),
    _ks("governance_retention", "Governance Retention", "governance.retention", ("/documents",),
        capabilities=("governance.view",)),
    _ks("rule_catalog", "Rule Catalog", "compliance_rule_catalog", ("/supervision",),
        capabilities=("compliance.supervise",)),
    _ks("wiki", "Wiki", NOT_CONFIGURED, ("/knowledge-management",), config_status=NOT_CONFIGURED),
    _ks("confluence", "Confluence", NOT_CONFIGURED, ("/knowledge-management",), config_status=NOT_CONFIGURED),
    _ks("search_index", "Search Index", NOT_CONFIGURED, ("/knowledge-management",),
        config_status=NOT_CONFIGURED),
)

_KS_BY_KEY = {k.key: k for k in KNOWLEDGE_SOURCE_REGISTRY}


# --- publication status registry ---------------------------------------------

@dataclass(frozen=True)
class PublicationStatus:
    key: str
    label: str
    owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _ps(key, label, owner, deep_links, *, capabilities=("documents.view",),
        runtime_gate="knowledge_management.enabled", config_status=CONFIGURED):
    return PublicationStatus(key, label, owner, runtime_gate, tuple(capabilities), tuple(deep_links),
                             config_status)


PUBLICATION_STATUS_REGISTRY = (
    _ps("draft", "Draft", "document_platform", ("/documents?status=draft",)),
    _ps("review", "In Review", "document_platform", ("/documents?status=review",)),
    _ps("approved", "Approved / Published", "document_platform", ("/documents?status=approved",)),
    _ps("superseded", "Superseded", "document_platform", ("/documents?status=superseded",)),
    _ps("archived", "Archived", "document_platform", ("/documents?status=archived",)),
)

_PS_BY_KEY = {p.key: p for p in PUBLICATION_STATUS_REGISTRY}


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


PANEL_REGISTRY = (
    # documentation health (Document Intelligence)
    _p("documentation_completeness", "document_intelligence", "document_intelligence.document_summary",
       "documentation", "percent", "gauge", "documents.view", "/documents",
       "Documentation completeness score, from the D.50 Document Intelligence layer over the Document "
       "Platform. Counts + status only, never document contents."),
    _p("documentation_gaps", "document_intelligence", "document_intelligence.document_summary",
       "documentation", "count", "card", "documents.view", "/documents",
       "Documentation gaps (missing documents), from Document Intelligence. Counts only."),
    _p("expiring_documents", "document_intelligence", "document_intelligence.document_summary", "freshness",
       "count", "card", "documents.view", "/documents",
       "Expiring documents (a freshness signal), from Document Intelligence."),
    _p("document_freshness", "document_platform", "document_platform.list_documents", "freshness", "count",
       "card", "documents.view", "/documents",
       "Document freshness (active vs stale by lifecycle status), from the Document Platform. No second DMS."),
    # inventory (Document Platform)
    _p("document_inventory_by_status", "document_platform", "document_platform.list_documents",
       "documentation", "count", "chart", "documents.view", "/documents",
       "Document inventory by lifecycle status (draft / review / approved / superseded / archived), from the "
       "Document Platform (the DMS of record). No second document repository."),
    _p("document_inventory_by_classification", "document_platform", "document_platform.list_documents",
       "documentation", "count", "chart", "documents.view", "/documents",
       "Document inventory by classification, from the Document Platform."),
    # publication (Document Platform lifecycle)
    _p("publication_readiness", "document_platform", "document_platform.list_documents", "publication",
       "coverage", "gauge", "documents.view", "/documents",
       "Publication readiness — approved vs draft/review documents, from the Document Platform deterministic "
       "lifecycle. The layer never publishes or approves a document."),
    _p("draft_documents", "document_platform", "document_platform.list_documents", "publication", "count",
       "card", "documents.view", "/documents?status=draft",
       "Draft documents, from the Document Platform lifecycle."),
    _p("approved_documents", "document_platform", "document_platform.list_documents", "publication", "count",
       "card", "documents.view", "/documents?status=approved",
       "Approved / published documents, from the Document Platform lifecycle. Status only — never an approval "
       "the layer records."),
    _p("pending_review_documents", "document_platform", "document_platform.list_documents", "publication",
       "count", "card", "documents.view", "/documents?status=review",
       "Documents in review, from the Document Platform lifecycle."),
    _p("superseded_documents", "document_platform", "document_platform.list_documents", "version", "count",
       "card", "documents.view", "/documents?status=superseded",
       "Superseded documents (version awareness), from the Document Platform lifecycle."),
    # ownership (Document Platform created_by)
    _p("ownership_coverage", "document_platform", "document_platform.list_documents", "ownership", "coverage",
       "gauge", "documents.view", "/documents",
       "Documentation ownership coverage — documents with a recorded author (`created_by`) vs unassigned, from "
       "the Document Platform. Counts only — never an author identity."),
    _p("orphaned_documentation", "document_platform", "document_platform.list_documents", "ownership", "count",
       "card", "documents.view", "/documents",
       "Orphaned documentation — documents with no recorded author, from the Document Platform."),
    _p("version_awareness", "document_platform", "document_platform.list_documents", "version", "count",
       "card", "documents.view", "/documents",
       "Version awareness — superseded (versioned) documents, from the Document Platform immutable-version "
       "store. The layer never changes a version."),
    # retention (Governance retention)
    _p("retention_coverage", "governance.retention", "governance.retention.metrics", "documentation", "count",
       "card", "governance.view", "/documents",
       "Records-retention coverage (legal holds + pending disposition reviews), from Data Governance "
       "retention. Counts only."),
    # SOP + knowledge (registry-derived / composed)
    _p("sop_coverage", "knowledge_management", "knowledge_management.registry", "sop", "coverage", "gauge",
       "documents.view", "/knowledge-management?dashboard=sop_governance",
       "SOP coverage — SOP categories with an authoritative owner (operational / compliance procedures, from "
       "the Document Platform) vs not_configured (runbooks / playbooks / onboarding). A DERIVED coverage "
       "summary, never a fabricated SOP or SOP approval.", derived=True),
    _p("runbook_coverage", "not_configured", "knowledge_management.registry", "sop", "status", "card",
       "documents.view", "/knowledge-management?dashboard=sop_governance",
       "Runbook coverage — NO authoritative runbook / playbook owner exists in the platform today; reported "
       "not_configured, never a fabricated runbook.", derived=True),
    _p("knowledge_domain_inventory", "knowledge_management", "knowledge_management.registry", "knowledge",
       "count", "list", "documents.view", "/knowledge-management",
       "The registered knowledge-domain catalog — each naming its authoritative owner + config status. "
       "Metadata only — never document contents.", derived=True),
    _p("knowledge_source_inventory", "knowledge_management", "knowledge_management.registry", "knowledge",
       "count", "list", "documents.view", "/knowledge-management",
       "The registered knowledge-source catalog — each naming its authoritative owner. Wiki / Confluence / a "
       "dedicated search index have no authoritative owner (not_configured).", derived=True),
    _p("knowledge_gaps", "knowledge_management", "knowledge_management.compose", "knowledge", "list", "list",
       "documents.view", "/knowledge-management?dashboard=knowledge_gaps",
       "Knowledge gaps — not_configured knowledge / SOP / source domains (knowledge base, institutional "
       "memory, runbooks, playbooks, onboarding SOPs, wiki, Confluence, search index). A DERIVED honesty "
       "summary, never fabricated.", derived=True),
    _p("knowledge_health", "knowledge_management", "knowledge_management.compose", "knowledge", "percent",
       "gauge", "documents.view", "/knowledge-management",
       "Knowledge health — a DERIVED coverage indicator (documentation completeness + configured vs "
       "not_configured domains). A documentation-coverage summary, never fabricated institutional knowledge.",
       derived=True),
    _p("executive_knowledge_status", "knowledge_management", "knowledge_management.compose", "knowledge",
       "distribution", "gauge", "analytics.executive", "/knowledge-management",
       "DERIVED executive knowledge posture — configured vs not_configured domains + documentation "
       "completeness + publication readiness across the authoritative owners. A documentation-coverage summary "
       "only, never fabricated documentation, SOP approval, version history, or institutional knowledge.",
       derived=True),
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


_KM_CAPS = ("documents.view", "analytics.executive")

KNOWLEDGE_DASHBOARDS = (
    _d("knowledge_overview", "knowledge_management", "operations", "knowledge_management.enabled",
       ("knowledge_domain_inventory", "knowledge_health", "knowledge_source_inventory"),
       _KM_CAPS, "/knowledge-management?dashboard=knowledge_overview",
       ("document_platform", "knowledge_management")),
    _d("sop_governance", "knowledge_management", "operations", "sop_governance.enabled",
       ("sop_coverage", "runbook_coverage", "documentation_gaps"),
       _KM_CAPS, "/knowledge-management?dashboard=sop_governance",
       ("document_platform", "knowledge_management")),
    _d("documentation_health", "knowledge_management", "operations", "documentation.enabled",
       ("documentation_completeness", "document_freshness", "expiring_documents"),
       _KM_CAPS, "/knowledge-management?dashboard=documentation_health",
       ("document_intelligence", "document_platform")),
    _d("ownership_coverage", "knowledge_management", "operations", "knowledge_management.enabled",
       ("ownership_coverage", "orphaned_documentation", "document_inventory_by_classification"),
       _KM_CAPS, "/knowledge-management?dashboard=ownership_coverage",
       ("document_platform",)),
    _d("publication_readiness", "knowledge_management", "operations", "documentation.enabled",
       ("publication_readiness", "draft_documents", "approved_documents", "pending_review_documents"),
       _KM_CAPS, "/knowledge-management?dashboard=publication_readiness",
       ("document_platform",)),
    _d("knowledge_gaps", "knowledge_management", "operations", "knowledge_management.enabled",
       ("knowledge_gaps", "documentation_gaps", "sop_coverage"),
       _KM_CAPS, "/knowledge-management?dashboard=knowledge_gaps",
       ("knowledge_management", "document_intelligence")),
    _d("executive_knowledge_status", "knowledge_management", "executive", "knowledge_management.enabled",
       ("executive_knowledge_status", "documentation_completeness", "knowledge_health"),
       _KM_CAPS, "/knowledge-management?dashboard=executive_knowledge_status",
       ("document_intelligence", "document_platform")),
    _d("documentation_quality", "knowledge_management", "operations", "documentation.enabled",
       ("documentation_completeness", "version_awareness", "superseded_documents"),
       _KM_CAPS, "/knowledge-management?dashboard=documentation_quality",
       ("document_intelligence", "document_platform")),
)

_DASH_BY_KEY = {d.key: d for d in KNOWLEDGE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def knowledge_domain(key) -> KnowledgeDomain | None:
    return _KD_BY_KEY.get(key)


def sop_category(key) -> SOPCategory | None:
    return _SOP_BY_KEY.get(key)


def documentation_owner(key) -> DocumentationOwner | None:
    return _DO_BY_KEY.get(key)


def knowledge_source(key) -> KnowledgeSource | None:
    return _KS_BY_KEY.get(key)


def publication_status(key) -> PublicationStatus | None:
    return _PS_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def _all_entries():
    return (*KNOWLEDGE_DOMAIN_REGISTRY, *SOP_CATEGORY_REGISTRY, *DOCUMENTATION_OWNER_REGISTRY,
            *KNOWLEDGE_SOURCE_REGISTRY, *PUBLICATION_STATUS_REGISTRY)


def not_configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == NOT_CONFIGURED)


def configured_domains() -> tuple:
    return tuple(e.key for e in _all_entries() if e.config_status == CONFIGURED)


def coverage() -> dict:
    return {
        "knowledge_domains": len(KNOWLEDGE_DOMAIN_REGISTRY),
        "sop_categories": len(SOP_CATEGORY_REGISTRY),
        "documentation_owners": len(DOCUMENTATION_OWNER_REGISTRY),
        "knowledge_sources": len(KNOWLEDGE_SOURCE_REGISTRY),
        "publication_statuses": len(PUBLICATION_STATUS_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(KNOWLEDGE_DASHBOARDS),
        "configured_domains": len(configured_domains()),
        "not_configured_domains": len(not_configured_domains()),
    }
