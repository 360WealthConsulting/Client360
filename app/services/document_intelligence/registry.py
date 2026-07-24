"""Document Intelligence registries (Phase D.50) — the declarative catalogs of the document-intelligence
layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new metrics, storage, OCR,
or index:

  * DOCUMENT_REGISTRY — every document class (tax returns, financial plans, IPS, investment/insurance/estate/
    trust/compliance documents, correspondence, signed agreements). Each names its OWNER (the authoritative
    Document Platform), storage source, metadata source, retention policy, lifecycle owner, runtime gate,
    refresh policy, and deep links. The layer stores NOTHING — it composes these owners.
  * RETENTION_REGISTRY — every retention policy (IRS, SEC, FINRA, state insurance, internal operations,
    client communications). Each names its owner, retention period, archive owner, disposition policy,
    governing regulation, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * INTELLIGENCE_DASHBOARDS — every document dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every document class + retention policy is registered, every panel names an
authoritative owner + source + deep link, and that this layer never becomes a second DMS / OCR / index /
archive / metadata / records store.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- document registry -------------------------------------------------------

@dataclass(frozen=True)
class DocumentClass:
    key: str
    label: str
    owner: str                 # authoritative service that owns storage + metadata (the Document Platform)
    storage_source: str        # the authoritative read documents are composed from
    metadata_source: str       # the authoritative metadata owner (never duplicated)
    classification: str        # the Document Platform classification this class maps to
    retention_policy: str      # key into RETENTION_REGISTRY
    lifecycle: str             # the authoritative lifecycle owner (never a second state machine)
    runtime_gate: str
    refresh_policy: str
    deep_links: tuple          # authoritative document surfaces to drill into


def _doc(key, label, storage_source, classification, retention_policy, deep_links, *,
         owner="document_platform", metadata_source="document_platform",
         lifecycle="document_platform._TRANSITIONS", runtime_gate="lifecycle.enabled",
         refresh_policy="on_view"):
    return DocumentClass(key, label, owner, storage_source, metadata_source, classification,
                         retention_policy, lifecycle, runtime_gate, refresh_policy, tuple(deep_links))


DOCUMENT_REGISTRY = (
    _doc("tax_returns", "Tax Returns", "document_platform.list_documents", "tax", "irs",
         ("/document-library?classification=tax", "/tax")),
    _doc("financial_plans", "Financial Plans", "document_platform.list_documents", "client", "sec",
         ("/document-library?classification=client",)),
    _doc("ips", "Investment Policy Statements", "document_platform.list_documents", "investment", "sec",
         ("/document-library?classification=investment",)),
    _doc("investment_documents", "Investment Documents", "document_platform.list_documents", "investment",
         "finra", ("/document-library?classification=investment", "/portfolio")),
    _doc("insurance_documents", "Insurance Documents", "document_platform.list_documents", "insurance",
         "state_insurance", ("/document-library?classification=insurance", "/insurance")),
    _doc("estate_documents", "Estate Documents", "document_platform.list_documents", "estate", "internal_operations",
         ("/document-library?classification=estate",)),
    _doc("trust_documents", "Trust Documents", "document_platform.list_documents", "legal", "internal_operations",
         ("/document-library?classification=legal",)),
    _doc("compliance_documents", "Compliance Documents", "document_platform.list_documents", "compliance",
         "finra", ("/document-library?classification=compliance", "/compliance/reviews")),
    _doc("correspondence", "Correspondence", "document_platform.list_documents", "client", "client_communications",
         ("/document-library?classification=client", "/communications"),
         metadata_source="communications"),
    _doc("signed_agreements", "Signed Agreements", "document_platform.list_documents", "legal", "finra",
         ("/document-library?classification=legal",),
         lifecycle="portal.signatures + document_platform._TRANSITIONS"),
)

_DOC_BY_KEY = {d.key: d for d in DOCUMENT_REGISTRY}


# --- retention registry ------------------------------------------------------

@dataclass(frozen=True)
class RetentionPolicy:
    key: str
    label: str
    owner: str                 # authoritative retention-policy owner (Document Platform)
    retention_period: str      # human-readable period (the authoritative rule)
    archive_owner: str         # authoritative archive/disposition owner (Governance retention)
    disposition_policy: str    # review | archive | delete (deterministic action on expiry)
    governing_regulation: str  # the regulation/authority the period derives from
    runtime_gate: str = "retention.enabled"


def _ret(key, label, retention_period, disposition_policy, governing_regulation, *,
         owner="document_platform", archive_owner="governance.retention"):
    return RetentionPolicy(key, label, owner, retention_period, archive_owner, disposition_policy,
                           governing_regulation)


RETENTION_REGISTRY = (
    _ret("irs", "IRS Recordkeeping", "7 years after filing", "review",
         "IRS §6501 / federal tax recordkeeping"),
    _ret("sec", "SEC Books & Records", "6 years (2 readily accessible)", "archive",
         "SEC Rule 17a-4 / Advisers Act 204-2"),
    _ret("finra", "FINRA Books & Records", "6 years", "archive", "FINRA Rule 4511 / SEC 17a-4"),
    _ret("state_insurance", "State Insurance Records", "5 years (varies by state)", "review",
         "State insurance record-retention statutes"),
    _ret("internal_operations", "Internal Operations", "3 years", "review",
         "Internal operations records policy"),
    _ret("client_communications", "Client Communications", "3–6 years", "archive",
         "SEC 17a-4 / FINRA 4511 (communications)"),
)

_RET_BY_KEY = {r.key: r for r in RETENTION_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str               # inventory | retention | archive | lifecycle | completeness | gaps
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative document surface to drill into
    explainability: str        # what the panel shows + where it comes from
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # inventory
    _p("inventory_by_classification", "document_platform", "document_platform.list_documents", "inventory",
       "count", "chart", "documents.view", "/document-library",
       "Document inventory by classification, from the Document Platform (the authoritative repository). "
       "Counts only — no document content. No second document store."),
    _p("inventory_by_status", "document_platform", "document_platform.list_documents", "inventory", "count",
       "chart", "documents.view", "/document-library",
       "Document inventory by lifecycle status, from the Document Platform."),
    _p("folder_inventory", "document_platform", "document_platform.list_folders", "inventory", "count",
       "card", "documents.view", "/document-library/folders",
       "Registered document folders, from the Document Platform."),
    # retention
    _p("retention_policies", "document_platform", "document_platform.list_retention_policies", "retention",
       "count", "list", "documents.view", "/document-library",
       "Registered retention policies, from the Document Platform (the authoritative policy store)."),
    _p("retention_assignments", "governance.retention", "governance.retention.list_retention_assignments",
       "retention", "count", "chart", "documents.view", "/governance",
       "Retention assignments by status, from Governance retention (the records retention owner)."),
    _p("retention_metrics", "governance.retention", "governance.retention.metrics", "retention", "count",
       "card", "documents.view", "/governance",
       "Active legal holds + pending disposition reviews, from Governance retention."),
    # archive
    _p("archived_documents", "document_platform", "document_platform.list_documents", "archive", "count",
       "card", "documents.view", "/document-library?status=archived",
       "Documents in the archived lifecycle state, from the Document Platform. Archive is a lifecycle "
       "state, not a second archive system."),
    _p("archive_readiness", "governance.retention", "governance.retention.list_retention_assignments",
       "archive", "count", "list", "documents.view", "/governance",
       "Retention assignments eligible for archival/disposition, from Governance retention (deterministic)."),
    _p("disposition_requests", "governance.retention", "governance.retention.list_deletion_requests",
       "archive", "count", "chart", "documents.view", "/governance",
       "Open archival/deletion disposition requests, from Governance retention."),
    # lifecycle
    _p("lifecycle_status", "document_platform", "document_platform.list_documents", "lifecycle", "count",
       "chart", "documents.view", "/document-library",
       "Documents by lifecycle state (draft/active/review/approved/superseded/archived), from the "
       "Document Platform state machine. No second lifecycle engine."),
    _p("pending_review", "document_platform", "document_platform.list_documents", "lifecycle", "count",
       "card", "documents.view", "/document-library?status=review",
       "Documents pending review, from the Document Platform lifecycle."),
    _p("superseded_documents", "document_platform", "document_platform.list_documents", "lifecycle", "count",
       "card", "documents.view", "/document-library?status=superseded",
       "Superseded documents, from the Document Platform lifecycle."),
    # missing documentation
    _p("missing_documents", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "gaps", "count", "list", "documents.view", "/supervision",
       "Missing-document gaps, composed from Compliance Intelligence (which normalizes the authoritative "
       "exception engine). No document content."),
    _p("unsigned_agreements", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "gaps", "count", "card", "documents.view", "/supervision",
       "Unsigned-disclosure / signature gaps, composed from Compliance Intelligence."),
    _p("documentation_gaps", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "gaps", "count", "chart", "documents.view", "/supervision",
       "Documentation gaps by type (missing document / unsigned / missing beneficiary), from Compliance "
       "Intelligence."),
    # completeness
    _p("completeness_score", "document_intelligence", "document_intelligence.compose", "completeness",
       "percent", "gauge", "documents.view", "/document-library",
       "Deterministic document-completeness indicator (inventory present vs open documentation gaps) — "
       "advisory only; never alters metadata or documents."),
    _p("ocr_status", "document_platform", "document_platform.documents:ocr_status", "completeness", "count",
       "chart", "documents.view", "/document-library",
       "OCR/preview processing status reported from the Document Platform metadata (the platform's own "
       "ocr_status). This layer NEVER runs OCR — it reports the owner's status."),
    _p("expiring_documents", "document_platform", "document_platform.list_documents", "completeness",
       "count", "card", "documents.view", "/document-library",
       "Documents past or approaching their retention expiration, from the Document Platform metadata."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # records | compliance | operations | executive | advisor
    runtime_gate: str
    panels: tuple              # tuple of panel keys
    required_capabilities: tuple
    navigation: str
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, panels, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(panels), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


INTELLIGENCE_DASHBOARDS = (
    _d("document_inventory", "document_intelligence", "records", "document_intelligence.enabled",
       ("inventory_by_classification", "inventory_by_status", "folder_inventory"),
       ("documents.view",), "/document-intelligence?dashboard=document_inventory", ("document_platform",)),
    _d("retention", "document_intelligence", "records", "retention.enabled",
       ("retention_policies", "retention_assignments", "retention_metrics"),
       ("documents.view",), "/document-intelligence?dashboard=retention",
       ("document_platform", "governance.retention")),
    _d("archive", "document_intelligence", "records", "retention.enabled",
       ("archived_documents", "archive_readiness", "disposition_requests"),
       ("documents.view",), "/document-intelligence?dashboard=archive",
       ("document_platform", "governance.retention")),
    _d("lifecycle", "document_intelligence", "records", "lifecycle.enabled",
       ("lifecycle_status", "pending_review", "superseded_documents"),
       ("documents.view",), "/document-intelligence?dashboard=lifecycle", ("document_platform",)),
    _d("missing_documentation", "document_intelligence", "compliance", "document_intelligence.enabled",
       ("missing_documents", "unsigned_agreements", "documentation_gaps"),
       ("documents.view",), "/document-intelligence?dashboard=missing_documentation",
       ("compliance_intelligence",)),
    _d("document_completeness", "document_intelligence", "operations", "document_intelligence.enabled",
       ("completeness_score", "ocr_status", "expiring_documents"),
       ("documents.view",), "/document-intelligence?dashboard=document_completeness",
       ("document_platform", "compliance_intelligence")),
)

_DASH_BY_KEY = {d.key: d for d in INTELLIGENCE_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def document_class(key) -> DocumentClass | None:
    return _DOC_BY_KEY.get(key)


def retention_policy(key) -> RetentionPolicy | None:
    return _RET_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def document_class_registered(key) -> bool:
    return key in _DOC_BY_KEY


def retention_policy_registered(key) -> bool:
    return key in _RET_BY_KEY


def coverage() -> dict:
    return {
        "document_classes": len(DOCUMENT_REGISTRY),
        "retention_policies": len(RETENTION_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(INTELLIGENCE_DASHBOARDS),
    }
