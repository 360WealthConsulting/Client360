"""Enterprise Regulatory Examination Readiness registries (Phase D.59) — the declarative catalogs of the
regulatory readiness / evidence / examination / certification composition layer.

Six frozen, declarative catalogs; the layer owns NO persistence and defines NO second compliance platform,
examination-management system, audit platform, document repository, records-management system, regulatory
filing system, certification engine, evidence vault, supervisory approval engine, or policy-management system:

  * REGULATORY_OBLIGATION_REGISTRY — obligation domains (IA registration, Form ADV, books & records, privacy,
    cybersecurity, continuity, vendor oversight, communications supervision, advertising review, suitability,
    replacement/1035, licensing, CE, fee/billing, custody, best-interest, conflicts, complaints, retention,
    supervisory review, tax-practice, insurance-practice, employee-benefits controls). Each names its
    authoritative / evidence / review / exception / approval / filing / retention owner + accountable business
    owner + accountable compliance reviewer + capabilities + runtime gate + deep links + config status.
  * EVIDENCE_REGISTRY — evidence classes, each naming authoritative / storage / metadata / retention /
    verification owner + applicable obligation keys + freshness metadata. **References evidence only — never
    creates, copies, stores, alters, or certifies evidence.**
  * EXAMINATION_REQUEST_REGISTRY — a readiness MAP of common examination-request categories → required
    evidence classes + owners + review owner + export owner. **Never represents an active regulator request —
    no authoritative examination-case owner exists.**
  * CERTIFICATION_REGISTRY — sign-off domains, each naming scope + rule-set version + accountable reviewer
    role + named reviewer (only when authoritatively confirmed) + reviewer qualification requirement + review
    date + status + evidence owner + approval-artifact owner. **Reviewer authority is never inferred; business
    approval is never regulatory certification; the reviewer_authorities catalog is seeded empty, so every
    certification defaults to `reviewer_not_confirmed` / `blocked`.**
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * READINESS_DASHBOARDS — every readiness dashboard.

Governance verifies every key is unique, every configured entry names an authoritative owner, every evidence
entry names a storage/evidence owner, every derived value is labeled, every blocked certification states why,
reviewer authority is never inferred, business approval is not treated as certification, and that this layer
never becomes a second compliance / examination / audit / document / filing / certification platform. Where no
authoritative owner exists (regulatory filing / acknowledgements, examination-case ownership, certification
reviewers, evidence export, backup/restore evidence, and several obligation domains), the entry is declared
`not_configured` and reported honestly — never a fabricated evidence, approval, certification, filing, or
examination-readiness status. Operational readiness is never regulatory certification.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"
# certification statuses — the reviewer_authorities catalog is seeded empty, so these default to blocked.
REVIEWER_NOT_CONFIRMED = "reviewer_not_confirmed"
BLOCKED = "blocked"

_REVIEWER_EMPTY_REASON = ("no appropriately authorized compliance reviewer is recorded in the authoritative "
                          "reviewer_authorities catalog (seeded empty); reviewer authority is never inferred "
                          "and business approval is not regulatory certification")


# --- regulatory obligation registry ------------------------------------------

@dataclass(frozen=True)
class Obligation:
    key: str
    label: str
    reg_domain: str
    authoritative_owner: str
    evidence_owner: str
    review_owner: str
    exception_owner: str
    approval_owner: str
    filing_owner: str
    retention_owner: str
    business_owner: str
    compliance_reviewer: str   # accountable compliance reviewer (or reviewer_not_confirmed)
    capabilities: tuple
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _ob(key, label, domain, authoritative_owner, deep_links, *, evidence_owner=NOT_CONFIGURED,
        review_owner="compliance_intelligence", exception_owner="exception_engine",
        approval_owner=NOT_CONFIGURED, filing_owner=NOT_CONFIGURED, retention_owner=NOT_CONFIGURED,
        business_owner="business_operations", compliance_reviewer=REVIEWER_NOT_CONFIRMED,
        capabilities=("compliance.supervise",), runtime_gate="regulatory_readiness.enabled",
        config_status=CONFIGURED):
    return Obligation(key, label, domain, authoritative_owner, evidence_owner, review_owner, exception_owner,
                      approval_owner, filing_owner, retention_owner, business_owner, compliance_reviewer,
                      tuple(capabilities), runtime_gate, tuple(deep_links), config_status)


REGULATORY_OBLIGATION_REGISTRY = (
    _ob("investment_adviser_registration", "Investment-Adviser Registration", "registration", NOT_CONFIGURED,
        ("/regulatory-readiness",), config_status=NOT_CONFIGURED),
    _ob("form_adv_governance", "Form ADV Governance", "registration", NOT_CONFIGURED,
        ("/regulatory-readiness",), config_status=NOT_CONFIGURED),
    _ob("books_and_records", "Books & Records", "records", "document_intelligence",
        ("/documents", "/regulatory-readiness"), evidence_owner="document_intelligence",
        retention_owner="document_intelligence", capabilities=("documents.view",)),
    _ob("privacy_safeguarding", "Privacy & Safeguarding", "privacy", "security_operations",
        ("/security-operations",), evidence_owner="security_operations", capabilities=("security.view",)),
    _ob("cybersecurity_governance", "Cybersecurity Governance", "security", "security_operations",
        ("/security-operations",), evidence_owner="security_operations", capabilities=("security.view",)),
    _ob("business_continuity", "Business Continuity", "resilience", "business_continuity",
        ("/business-continuity",), evidence_owner="business_continuity", capabilities=("observability.view",)),
    _ob("vendor_oversight", "Vendor Oversight", "vendor", "vendor_management", ("/vendor-management",),
        evidence_owner="vendor_management", capabilities=("integration.view",)),
    _ob("communications_supervision", "Communications Supervision", "supervision", "compliance_intelligence",
        ("/supervision", "/communications"), evidence_owner="compliance_intelligence",
        approval_owner="compliance_reviews"),
    _ob("advertising_marketing_review", "Advertising & Marketing Review", "supervision", NOT_CONFIGURED,
        ("/regulatory-readiness",), config_status=NOT_CONFIGURED),
    _ob("suitability", "Suitability", "supervision", "compliance_intelligence", ("/supervision",),
        evidence_owner="compliance_intelligence", approval_owner="compliance_reviews"),
    _ob("replacement_1035_review", "Replacement & 1035 Review", "supervision", "compliance_intelligence",
        ("/supervision",), evidence_owner="compliance_intelligence", approval_owner="compliance_reviews"),
    _ob("licensing_registration", "Licensing & Registration", "licensing", "insurance_licensing",
        ("/insurance",), evidence_owner="insurance_licensing", capabilities=("integration.view",)),
    _ob("continuing_education", "Continuing Education", "licensing", "insurance_licensing", ("/insurance",),
        evidence_owner="insurance_licensing", capabilities=("integration.view",)),
    _ob("fee_billing_oversight", "Fee & Billing Oversight", "financial", "financial_operations",
        ("/financial-operations",), evidence_owner="financial_operations",
        capabilities=("analytics.executive",)),
    _ob("custody_asset_verification", "Custody & Asset Verification", "financial", NOT_CONFIGURED,
        ("/regulatory-readiness",), capabilities=("analytics.executive",), config_status=NOT_CONFIGURED),
    _ob("best_interest_obligations", "Best-Interest Obligations", "supervision", "compliance_intelligence",
        ("/supervision",), evidence_owner="compliance_intelligence", approval_owner="compliance_reviews"),
    _ob("conflicts_of_interest", "Conflicts of Interest", "supervision", NOT_CONFIGURED,
        ("/regulatory-readiness",), config_status=NOT_CONFIGURED),
    _ob("complaint_handling", "Complaint Handling", "supervision", NOT_CONFIGURED,
        ("/regulatory-readiness",), config_status=NOT_CONFIGURED),
    _ob("document_retention", "Document Retention", "records", "document_intelligence", ("/documents",),
        evidence_owner="document_intelligence", retention_owner="document_intelligence",
        capabilities=("documents.view",)),
    _ob("supervisory_review", "Supervisory Review", "supervision", "compliance_intelligence",
        ("/supervision",), evidence_owner="compliance_intelligence", approval_owner="compliance_reviews"),
    _ob("tax_practice_controls", "Tax-Practice Controls", "tax", "exception_engine",
        ("/tax", "/exceptions"), evidence_owner="exception_engine"),
    _ob("insurance_practice_controls", "Insurance-Practice Controls", "insurance", "insurance_licensing",
        ("/insurance",), evidence_owner="insurance_reporting", capabilities=("integration.view",)),
    _ob("employee_benefits_controls", "Employee-Benefits Controls", "benefits", "exception_engine",
        ("/benefits", "/exceptions"), evidence_owner="exception_engine"),
)

_OB_BY_KEY = {o.key: o for o in REGULATORY_OBLIGATION_REGISTRY}


# --- evidence registry -------------------------------------------------------

@dataclass(frozen=True)
class EvidenceClass:
    key: str
    evidence_class: str
    authoritative_owner: str
    storage_owner: str
    metadata_owner: str
    retention_owner: str
    verification_owner: str
    obligation_keys: tuple
    freshness: str             # continuous | periodic | per_event | per_commit | not_tracked
    capabilities: tuple
    runtime_gate: str
    deep_link: str
    config_status: str = CONFIGURED


def _ev(key, cls, authoritative_owner, storage_owner, obligation_keys, deep_link, *,
        metadata_owner=None, retention_owner=NOT_CONFIGURED, verification_owner=None, freshness="continuous",
        capabilities=("compliance.supervise",), runtime_gate="evidence_governance.enabled",
        config_status=CONFIGURED):
    return EvidenceClass(key, cls, authoritative_owner, storage_owner,
                         metadata_owner or authoritative_owner, retention_owner,
                         verification_owner or authoritative_owner, tuple(obligation_keys), freshness,
                         tuple(capabilities), runtime_gate, deep_link, config_status)


EVIDENCE_REGISTRY = (
    _ev("policies_procedures", "policy", "compliance_rule_catalog", "compliance_rule_catalog",
        ("supervisory_review",), "/supervision", freshness="periodic"),
    _ev("supervisory_reviews", "review", "compliance_reviews", "compliance_reviews",
        ("supervisory_review",), "/supervision"),
    _ev("compliance_approvals", "approval", "compliance_reviews", "compliance_reviews",
        ("supervisory_review",), "/supervision"),
    _ev("exception_resolutions", "exception", "exception_engine", "exception_engine",
        ("tax_practice_controls",), "/exceptions"),
    _ev("licensing_records", "licensing", "insurance_licensing", "insurance_licensing",
        ("licensing_registration",), "/insurance", capabilities=("integration.view",)),
    _ev("ce_records", "licensing", "insurance_licensing", "insurance_licensing",
        ("continuing_education",), "/insurance", capabilities=("integration.view",)),
    _ev("communications_review", "review", "compliance_intelligence", "compliance_intelligence",
        ("communications_supervision",), "/supervision"),
    _ev("document_completeness", "documentation", "document_intelligence", "document_intelligence",
        ("books_and_records",), "/documents", retention_owner="document_intelligence",
        capabilities=("documents.view",)),
    _ev("suitability_evidence", "review", "compliance_intelligence", "compliance_intelligence",
        ("suitability",), "/supervision"),
    _ev("replacement_1035_evidence", "review", "compliance_intelligence", "compliance_intelligence",
        ("replacement_1035_review",), "/supervision"),
    _ev("vendor_review", "review", "vendor_management", "vendor_management",
        ("vendor_oversight",), "/vendor-management", capabilities=("integration.view",)),
    _ev("cybersecurity_evidence", "security", "security_operations", "security_operations",
        ("cybersecurity_governance",), "/security-operations", capabilities=("security.view",)),
    _ev("access_review", "security", "security_operations", "security_operations",
        ("privacy_safeguarding",), "/security-operations", capabilities=("security.view",)),
    _ev("business_continuity_evidence", "resilience", "business_continuity", "business_continuity",
        ("business_continuity",), "/business-continuity", capabilities=("observability.view",)),
    _ev("backup_restore_evidence", "resilience", NOT_CONFIGURED, NOT_CONFIGURED,
        ("business_continuity",), "/business-continuity", capabilities=("observability.view",),
        config_status=NOT_CONFIGURED),
    _ev("financial_reconciliation", "financial", "financial_operations", "financial_operations",
        ("fee_billing_oversight",), "/financial-operations", capabilities=("analytics.executive",)),
    _ev("commission_reconciliation", "financial", "insurance_reporting", "insurance_reporting",
        ("insurance_practice_controls",), "/financial-operations", capabilities=("analytics.executive",)),
    _ev("audit_log_verification", "audit", "observability.audit", "observability.audit",
        ("books_and_records",), "/observability", capabilities=("observability.audit",)),
    _ev("data_quality_validation", "data", "data_governance", "data_governance",
        ("books_and_records",), "/data-governance", capabilities=("governance.view",)),
    _ev("architecture_governance_tests", "assurance", "continuous_integration", "continuous_integration",
        ("supervisory_review",), "/regulatory-readiness", freshness="per_commit"),
    _ev("automated_test_evidence", "assurance", "continuous_integration", "continuous_integration",
        ("supervisory_review",), "/regulatory-readiness", freshness="per_commit"),
    _ev("ci_evidence", "assurance", "continuous_integration", "continuous_integration",
        ("supervisory_review",), "/regulatory-readiness", freshness="per_commit"),
    _ev("regulatory_filing_acknowledgements", "filing", NOT_CONFIGURED, NOT_CONFIGURED,
        ("form_adv_governance",), "/regulatory-readiness", freshness="not_tracked",
        config_status=NOT_CONFIGURED),
    _ev("state_filing_acknowledgements", "filing", NOT_CONFIGURED, NOT_CONFIGURED,
        ("investment_adviser_registration",), "/regulatory-readiness", freshness="not_tracked",
        config_status=NOT_CONFIGURED),
    _ev("filing_history", "filing", NOT_CONFIGURED, NOT_CONFIGURED,
        ("form_adv_governance",), "/regulatory-readiness", freshness="not_tracked",
        config_status=NOT_CONFIGURED),
    _ev("examination_correspondence", "examination", NOT_CONFIGURED, NOT_CONFIGURED,
        ("supervisory_review",), "/regulatory-readiness", freshness="not_tracked",
        config_status=NOT_CONFIGURED),
    _ev("remediation_evidence", "remediation", "exception_engine", "exception_engine",
        ("supervisory_review",), "/exceptions"),
)

_EV_BY_KEY = {e.key: e for e in EVIDENCE_REGISTRY}


# --- examination request registry --------------------------------------------

@dataclass(frozen=True)
class ExaminationRequest:
    key: str
    category: str
    description: str
    required_evidence: tuple   # evidence-class keys
    authoritative_owners: tuple
    review_owner: str
    export_owner: str          # evidence-export owner (not_configured — no export owner exists)
    capabilities: tuple
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _req(key, category, description, required_evidence, owners, deep_links, *,
         review_owner="compliance_intelligence", export_owner=NOT_CONFIGURED,
         capabilities=("compliance.supervise",), runtime_gate="regulatory_readiness.enabled",
         config_status=CONFIGURED):
    return ExaminationRequest(key, category, description, tuple(required_evidence), tuple(owners),
                              review_owner, export_owner, tuple(capabilities), runtime_gate,
                              tuple(deep_links), config_status)


EXAMINATION_REQUEST_REGISTRY = (
    _req("organizational_records", "organizational", "Organizational and formation records",
         ("policies_procedures", "document_completeness"), ("document_intelligence",), ("/documents",)),
    _req("registrations_licenses", "registration", "Registrations and licenses",
         ("licensing_records", "state_filing_acknowledgements"), ("insurance_licensing",), ("/insurance",),
         capabilities=("integration.view",), config_status=NOT_CONFIGURED),
    _req("policies_procedures", "policies", "Written policies and procedures",
         ("policies_procedures", "supervisory_reviews"), ("compliance_rule_catalog",), ("/supervision",)),
    _req("client_agreements", "agreements", "Client advisory agreements",
         ("document_completeness",), ("document_intelligence",), ("/documents",),
         capabilities=("documents.view",)),
    _req("advisory_billing", "billing", "Advisory billing records",
         ("financial_reconciliation",), ("financial_operations",), ("/financial-operations",),
         capabilities=("analytics.executive",)),
    _req("portfolio_custody_records", "custody", "Portfolio and custody records",
         ("financial_reconciliation",), (NOT_CONFIGURED,), ("/regulatory-readiness",),
         capabilities=("analytics.executive",), config_status=NOT_CONFIGURED),
    _req("communications_advertising", "communications", "Communications and advertising",
         ("communications_review",), ("compliance_intelligence",), ("/supervision",)),
    _req("suitability_best_interest", "suitability", "Suitability and best-interest records",
         ("suitability_evidence",), ("compliance_intelligence",), ("/supervision",)),
    _req("replacement_documentation", "replacement", "Replacement and 1035 documentation",
         ("replacement_1035_evidence",), ("compliance_intelligence",), ("/supervision",)),
    _req("complaints_incidents", "complaints", "Complaints and incidents",
         ("exception_resolutions",), (NOT_CONFIGURED,), ("/regulatory-readiness",),
         config_status=NOT_CONFIGURED),
    _req("cybersecurity", "cybersecurity", "Cybersecurity program records",
         ("cybersecurity_evidence",), ("security_operations",), ("/security-operations",),
         capabilities=("security.view",)),
    _req("privacy", "privacy", "Privacy and safeguarding records",
         ("access_review",), ("security_operations",), ("/security-operations",),
         capabilities=("security.view",)),
    _req("business_continuity", "continuity", "Business-continuity records",
         ("business_continuity_evidence",), ("business_continuity",), ("/business-continuity",),
         capabilities=("observability.view",)),
    _req("vendor_oversight", "vendor", "Vendor-oversight records",
         ("vendor_review",), ("vendor_management",), ("/vendor-management",),
         capabilities=("integration.view",)),
    _req("financial_records", "financial", "Financial records",
         ("financial_reconciliation", "commission_reconciliation"), ("financial_operations",),
         ("/financial-operations",), capabilities=("analytics.executive",)),
    _req("employee_supervision", "supervision", "Employee supervision records",
         ("supervisory_reviews",), ("compliance_intelligence",), ("/supervision",)),
    _req("training_ce", "training", "Training and continuing education",
         ("ce_records",), ("insurance_licensing",), ("/insurance",), capabilities=("integration.view",)),
    _req("tax_practice_controls", "tax", "Tax-practice control records",
         ("exception_resolutions",), ("exception_engine",), ("/tax",)),
    _req("insurance_practice_controls", "insurance", "Insurance-practice control records",
         ("commission_reconciliation", "licensing_records"), ("insurance_reporting",), ("/insurance",),
         capabilities=("integration.view",)),
    _req("books_records_retention", "records", "Books-and-records retention",
         ("document_completeness", "audit_log_verification"), ("document_intelligence",), ("/documents",),
         capabilities=("documents.view",)),
    _req("audit_trails", "audit", "Audit trails",
         ("audit_log_verification",), ("observability.audit",), ("/observability",),
         capabilities=("observability.audit",)),
    _req("remediation_history", "remediation", "Remediation history",
         ("remediation_evidence",), ("exception_engine",), ("/exceptions",)),
)

_REQ_BY_KEY = {r.key: r for r in EXAMINATION_REQUEST_REGISTRY}


# --- certification & sign-off registry ---------------------------------------

@dataclass(frozen=True)
class Certification:
    key: str
    scope: str
    ruleset_version: str       # rule-set / artifact version (or "not_configured")
    accountable_reviewer_role: str
    named_reviewer: str        # only when authoritatively confirmed — else "reviewer_not_confirmed"
    reviewer_qualification: str
    review_date: str           # only when authoritatively recorded — else "not_configured"
    status: str                # reviewer_not_confirmed | blocked | not_configured (never a fabricated "approved")
    blocked_reason: str
    evidence_owner: str
    approval_artifact_owner: str
    runtime_gate: str
    capabilities: tuple
    deep_link: str
    config_status: str = CONFIGURED


def _cert(key, scope, reviewer_role, qualification, evidence_owner, approval_artifact_owner, deep_link, *,
          ruleset_version=NOT_CONFIGURED, status=REVIEWER_NOT_CONFIRMED, blocked_reason=_REVIEWER_EMPTY_REASON,
          capabilities=("compliance.supervise",), runtime_gate="certification_signoff.enabled",
          config_status=CONFIGURED):
    # named_reviewer + review_date are NEVER fabricated — the reviewer_authorities catalog is seeded empty,
    # so the reviewer stays unconfirmed and the certification stays blocked. Business approval is not
    # regulatory certification.
    return Certification(key, scope, ruleset_version, reviewer_role, REVIEWER_NOT_CONFIRMED, qualification,
                         NOT_CONFIGURED, status, blocked_reason, evidence_owner, approval_artifact_owner,
                         runtime_gate, tuple(capabilities), deep_link, config_status)


CERTIFICATION_REGISTRY = (
    _cert("compliance_rule_set_approval", "compliance rule set", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority for the rule set",
          "compliance_rule_catalog", "compliance_reviews", "/supervision"),
    _cert("suitability_rule_approval", "suitability rule set", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "compliance_rule_catalog",
          "compliance_reviews", "/supervision"),
    _cert("replacement_1035_approval", "replacement / 1035 rule set", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "compliance_rule_catalog",
          "compliance_reviews", "/supervision"),
    _cert("licensing_rule_approval", "licensing rule set", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "insurance_licensing",
          "compliance_reviews", "/insurance"),
    _cert("ce_rule_approval", "continuing-education rule set", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "insurance_licensing",
          "compliance_reviews", "/insurance"),
    _cert("supervisory_policy_approval", "supervisory policy", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "compliance_rule_catalog",
          "compliance_reviews", "/supervision"),
    _cert("cybersecurity_policy_approval", "cybersecurity policy", "authorized_security_officer",
          "authorized security officer with recorded reviewer authority", "security_operations",
          "compliance_reviews", "/security-operations", capabilities=("security.view",)),
    _cert("business_continuity_review", "business-continuity plan", "authorized_resilience_owner",
          "authorized resilience owner with recorded reviewer authority", "business_continuity",
          "compliance_reviews", "/business-continuity", capabilities=("observability.view",)),
    _cert("vendor_risk_approval", "vendor-risk program", "authorized_vendor_manager",
          "authorized vendor manager with recorded reviewer authority", "vendor_management",
          "compliance_reviews", "/vendor-management", capabilities=("integration.view",)),
    _cert("financial_control_review", "financial-control framework", "authorized_finance_owner",
          "authorized finance owner with recorded reviewer authority", "financial_operations",
          "compliance_reviews", "/financial-operations", capabilities=("analytics.executive",)),
    _cert("records_retention_approval", "records-retention policy", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "document_intelligence",
          "compliance_reviews", "/documents", capabilities=("documents.view",)),
    _cert("annual_compliance_review", "annual compliance review", "authorized_compliance_principal",
          "licensed compliance principal with recorded reviewer authority", "compliance_rule_catalog",
          "compliance_reviews", "/supervision"),
    _cert("architecture_governance_approval", "architecture governance", "authorized_platform_owner",
          "authorized platform owner with recorded reviewer authority", "continuous_integration",
          "compliance_reviews", "/regulatory-readiness"),
    _cert("release_readiness_approval", "release readiness", "authorized_platform_owner",
          "authorized platform owner with recorded reviewer authority", "continuous_integration",
          "compliance_reviews", "/regulatory-readiness"),
)

_CERT_BY_KEY = {c.key: c for c in CERTIFICATION_REGISTRY}


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
    # registry-derived (DERIVED, catalog)
    _p("regulatory_obligation_inventory", "regulatory_readiness", "regulatory_readiness.registry", "obligation",
       "count", "list", "compliance.supervise", "/regulatory-readiness",
       "The registered regulatory-obligation catalog — each naming its authoritative / evidence / review / "
       "approval / filing / retention owner + config status. Applicability asserted only where the platform "
       "establishes an owner; unknown applicability is reported honestly.", derived=True),
    _p("configured_obligation_coverage", "regulatory_readiness", "regulatory_readiness.registry", "obligation",
       "coverage", "gauge", "compliance.supervise", "/regulatory-readiness",
       "Configured vs not_configured obligation coverage — a DERIVED operational-readiness indicator, never a "
       "compliance certification.", derived=True),
    _p("unconfigured_obligation_inventory", "regulatory_readiness", "regulatory_readiness.registry",
       "obligation", "list", "list", "compliance.supervise", "/regulatory-readiness",
       "Obligations with no authoritative owner in the platform today (IA registration, Form ADV, advertising "
       "review, custody, conflicts, complaints) — reported honestly, never fabricated.", derived=True),
    _p("evidence_class_inventory", "regulatory_readiness", "regulatory_readiness.registry", "evidence",
       "count", "list", "compliance.supervise", "/regulatory-readiness",
       "The registered evidence-class catalog — each naming its authoritative / storage / verification owner "
       "+ applicable obligations + freshness. References evidence only.", derived=True),
    _p("examination_request_coverage", "regulatory_readiness", "regulatory_readiness.registry", "examination",
       "coverage", "list", "compliance.supervise", "/regulatory-readiness?dashboard=obligation_coverage",
       "Examination-request readiness map — which request categories have owned evidence vs not_configured. A "
       "readiness map only; never an active regulator request (no examination-case owner exists).", derived=True),
    _p("blocked_certifications", "regulatory_readiness", "regulatory_readiness.registry", "certification",
       "count", "list", "compliance.supervise", "/regulatory-readiness?dashboard=certification_signoff",
       "Certifications that are blocked / reviewer_not_confirmed — each stating why (the reviewer_authorities "
       "catalog is seeded empty; reviewer authority is never inferred; business approval is not regulatory "
       "certification).", derived=True),
    _p("reviewer_not_confirmed_certifications", "regulatory_readiness", "regulatory_readiness.registry",
       "certification", "count", "list", "compliance.supervise",
       "/regulatory-readiness?dashboard=certification_signoff",
       "Certifications whose accountable compliance reviewer is not authoritatively confirmed. Michael Shelton "
       "is the business owner but is not the regulatory certifier unless recorded reviewer authority confirms "
       "it.", derived=True),
    _p("approval_artifact_coverage", "regulatory_readiness", "regulatory_readiness.registry", "certification",
       "coverage", "gauge", "compliance.supervise", "/regulatory-readiness?dashboard=certification_signoff",
       "Approval-artifact coverage — which certifications reference an authoritative approval-artifact owner "
       "(compliance reviews / rule catalog). Coverage of the artifact owner, never a fabricated approval.",
       derived=True),
    _p("derived_readiness_coverage", "regulatory_readiness", "regulatory_readiness.compose", "readiness",
       "coverage", "gauge", "compliance.supervise", "/regulatory-readiness",
       "DERIVED operational-readiness coverage — configured obligations + owned evidence classes − blocked "
       "certifications − stale/unconfigured areas. Deterministic, authoritative inputs; describes OPERATIONAL "
       "READINESS, not regulatory compliance. An absent finding is never interpreted as compliance.",
       derived=True),
    # evidence availability / completeness / freshness (composed)
    _p("evidence_availability", "regulatory_readiness", "regulatory_readiness.compose", "evidence", "coverage",
       "gauge", "compliance.supervise", "/regulatory-readiness?dashboard=evidence_completeness",
       "Evidence availability across the registered classes — which classes have an available authoritative "
       "owner. Counts + coverage only, never evidence files.", derived=True),
    _p("evidence_completeness", "document_intelligence", "document_intelligence.document_summary", "evidence",
       "coverage", "gauge", "documents.view", "/documents",
       "Documentation completeness (completeness score + missing documents), from Document Intelligence. "
       "Counts + status only, never document contents."),
    _p("stale_evidence", "regulatory_readiness", "regulatory_readiness.registry", "evidence", "age_band",
       "list", "compliance.supervise", "/regulatory-readiness?dashboard=evidence_freshness",
       "Evidence classes whose freshness is periodic / not_tracked (a staleness signal) — a DERIVED age-band "
       "summary from the registry freshness metadata. Never a document payload.", derived=True),
    _p("unverifiable_evidence", "regulatory_readiness", "regulatory_readiness.registry", "evidence", "count",
       "list", "compliance.supervise", "/regulatory-readiness?dashboard=evidence_completeness",
       "Evidence classes with no verification owner / not_configured storage (unverifiable) — reported "
       "honestly. Never a fabricated verification status.", derived=True),
    _p("retention_coverage", "document_intelligence", "document_intelligence.document_summary", "evidence",
       "coverage", "card", "documents.view", "/documents",
       "Records-retention coverage (expiring documents), from Document Intelligence. Counts + status only."),
    _p("documentation_gaps", "document_intelligence", "document_intelligence.document_summary", "evidence",
       "count", "card", "documents.view", "/documents",
       "Documentation-completeness gaps, from Document Intelligence. Counts only, never contents."),
    # supervisory + findings + exceptions + remediation
    _p("supervisory_review_status", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "review", "count", "card", "compliance.supervise", "/supervision",
       "Supervisory-review status (open reviews / pending approvals / blocked), from Compliance Intelligence. "
       "No second supervisory-approval engine."),
    _p("unresolved_compliance_findings", "compliance_intelligence",
       "compliance_intelligence.supervisory_dashboard", "findings", "count", "card", "compliance.supervise",
       "/supervision",
       "Unresolved compliance findings (open reviews + exceptions), from Compliance Intelligence. An absent "
       "finding never certifies compliance."),
    _p("unresolved_exceptions", "exception_engine", "compliance_intelligence.supervisory_dashboard",
       "exceptions", "distribution", "chart", "compliance.supervise", "/exceptions",
       "Unresolved exception severity distribution, from the authoritative Exception Engine via Compliance "
       "Intelligence. No second exception system."),
    _p("remediation_evidence", "exception_engine", "compliance_intelligence.supervisory_dashboard",
       "remediation", "count", "card", "compliance.supervise", "/exceptions",
       "Remediation evidence availability (open + blocked exceptions), from the authoritative Exception "
       "Engine. The layer never resolves an exception."),
    # licensing / CE
    _p("licensing_evidence", "insurance_licensing", "insurance_licensing.list_licenses", "evidence", "count",
       "card", "integration.view", "/insurance",
       "Producer-licensing evidence (licenses by status), from the Insurance licensing owner (requires "
       "insurance.licensing.read internally; unavailable otherwise)."),
    _p("ce_evidence", "insurance_licensing", "insurance_licensing.list_ce", "evidence", "count", "card",
       "integration.view", "/insurance",
       "Continuing-education evidence (CE records by status), from the Insurance licensing owner (requires "
       "insurance.licensing.read internally; unavailable otherwise)."),
    # domain evidence (composed)
    _p("communications_review_evidence", "compliance_intelligence",
       "compliance_intelligence.supervisory_dashboard", "evidence", "count", "card", "compliance.supervise",
       "/supervision",
       "Communications-review evidence (supervisory review counts), from Compliance Intelligence."),
    _p("suitability_evidence", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "evidence", "count", "card", "compliance.supervise", "/supervision",
       "Suitability-review evidence (supervisory review counts), from Compliance Intelligence."),
    _p("replacement_1035_evidence", "compliance_intelligence",
       "compliance_intelligence.supervisory_dashboard", "evidence", "count", "card", "compliance.supervise",
       "/supervision",
       "Replacement / 1035 review evidence (supervisory review counts), from Compliance Intelligence."),
    _p("vendor_review_evidence", "vendor_management", "vendor_management.vendor_summary", "evidence", "count",
       "card", "integration.view", "/vendor-management",
       "Vendor-review evidence (governance score + dependencies), from the D.56 Vendor Management layer."),
    _p("cybersecurity_evidence", "security_operations", "security_operations.security_summary", "evidence",
       "count", "card", "security.view", "/security-operations",
       "Cybersecurity evidence (security posture summary), from Security Operations. Counts + status only."),
    _p("continuity_evidence", "business_continuity", "business_continuity.continuity_summary", "evidence",
       "count", "card", "observability.view", "/business-continuity",
       "Business-continuity evidence (resilience posture), from the D.55 Business Continuity layer."),
    _p("financial_control_evidence", "financial_operations", "financial_operations.firm_financial_summary",
       "evidence", "count", "card", "analytics.executive", "/financial-operations",
       "Financial-control evidence (reconciliation status), from the D.57 Financial Operations layer."),
    _p("commission_reconciliation_evidence", "insurance_commissions", "insurance_reporting.commission_report",
       "evidence", "count", "card", "analytics.executive", "/financial-operations",
       "Commission-reconciliation evidence (outstanding / variance), from the authoritative commission "
       "ledger. Aggregate totals only."),
    _p("audit_log_verification", "observability.audit", "observability.audit", "evidence", "status", "card",
       "observability.audit", "/observability",
       "Audit-log verification availability (the hash-chain audit log is the authoritative evidence). "
       "Availability only — never an audit payload."),
    _p("architecture_test_evidence", "continuous_integration", "regulatory_readiness.registry", "evidence",
       "status", "card", "observability.audit", "/regulatory-readiness",
       "Architecture-governance test evidence (the CI architecture-guard suite is the authoritative "
       "evidence). Availability only.", derived=True),
    _p("ci_evidence", "continuous_integration", "regulatory_readiness.registry", "evidence", "status", "card",
       "observability.audit", "/regulatory-readiness",
       "CI verification evidence (the CI pipeline is the authoritative evidence). Availability only.",
       derived=True),
    # filing / examination (not_configured — reported honestly)
    _p("federal_filing_acknowledgements", "not_configured", "regulatory_readiness.registry", "filing",
       "status", "card", "compliance.supervise", "/regulatory-readiness",
       "Federal regulatory-filing acknowledgements — NO authoritative filing owner exists in the platform "
       "today; reported not_configured, never a fabricated acknowledgement.", derived=True),
    _p("state_filing_acknowledgements", "not_configured", "regulatory_readiness.registry", "filing", "status",
       "card", "compliance.supervise", "/regulatory-readiness",
       "State regulatory-filing acknowledgements — NO authoritative filing owner exists; reported "
       "not_configured, never fabricated.", derived=True),
    _p("filing_history", "not_configured", "regulatory_readiness.registry", "filing", "status", "card",
       "compliance.supervise", "/regulatory-readiness",
       "Regulatory filing history — NO authoritative filing owner exists; reported not_configured, never "
       "fabricated.", derived=True),
    _p("examination_correspondence_availability", "not_configured", "regulatory_readiness.registry",
       "examination", "status", "card", "compliance.supervise", "/regulatory-readiness",
       "Examination-correspondence availability — NO authoritative examination-case owner exists; reported "
       "not_configured. The readiness map is never an active regulator request.", derived=True),
    _p("evidence_export_availability", "not_configured", "regulatory_readiness.registry", "evidence",
       "status", "card", "observability.audit", "/regulatory-readiness",
       "Evidence-export availability — NO authoritative evidence-export owner exists; reported not_configured. "
       "The layer never packages or submits evidence.", derived=True),
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


_RR_CAPS = ("compliance.supervise", "analytics.executive")

READINESS_DASHBOARDS = (
    _d("examination_readiness", "regulatory_readiness", "executive", "regulatory_readiness.enabled",
       ("regulatory_obligation_inventory", "derived_readiness_coverage", "examination_request_coverage"),
       _RR_CAPS, "/regulatory-readiness?dashboard=examination_readiness",
       ("compliance_intelligence", "enterprise_risk", "regulatory_readiness")),
    _d("obligation_coverage", "regulatory_readiness", "supervisory", "regulatory_readiness.enabled",
       ("configured_obligation_coverage", "unconfigured_obligation_inventory", "examination_request_coverage"),
       _RR_CAPS, "/regulatory-readiness?dashboard=obligation_coverage",
       ("regulatory_readiness",)),
    _d("evidence_completeness", "regulatory_readiness", "supervisory", "evidence_governance.enabled",
       ("evidence_availability", "evidence_completeness", "unverifiable_evidence", "documentation_gaps"),
       _RR_CAPS, "/regulatory-readiness?dashboard=evidence_completeness",
       ("document_intelligence", "regulatory_readiness")),
    _d("evidence_freshness", "regulatory_readiness", "supervisory", "evidence_governance.enabled",
       ("stale_evidence", "evidence_class_inventory", "retention_coverage"),
       _RR_CAPS, "/regulatory-readiness?dashboard=evidence_freshness",
       ("document_intelligence", "regulatory_readiness")),
    _d("supervisory_reviews", "regulatory_readiness", "supervisory", "regulatory_readiness.enabled",
       ("supervisory_review_status", "unresolved_compliance_findings", "unresolved_exceptions"),
       _RR_CAPS, "/regulatory-readiness?dashboard=supervisory_reviews",
       ("compliance_intelligence", "exception_engine")),
    _d("certification_signoff", "regulatory_readiness", "executive", "certification_signoff.enabled",
       ("blocked_certifications", "reviewer_not_confirmed_certifications", "approval_artifact_coverage"),
       _RR_CAPS, "/regulatory-readiness?dashboard=certification_signoff",
       ("compliance_rule_catalog", "compliance_reviews")),
    _d("filing_readiness", "regulatory_readiness", "executive", "filing_readiness.enabled",
       ("federal_filing_acknowledgements", "state_filing_acknowledgements", "filing_history",
        "examination_correspondence_availability"),
       _RR_CAPS, "/regulatory-readiness?dashboard=filing_readiness",
       ("regulatory_readiness",)),
    _d("remediation_evidence", "regulatory_readiness", "supervisory", "regulatory_readiness.enabled",
       ("remediation_evidence", "unresolved_exceptions", "audit_log_verification"),
       _RR_CAPS, "/regulatory-readiness?dashboard=remediation_evidence",
       ("exception_engine", "observability.audit")),
)

_DASH_BY_KEY = {d.key: d for d in READINESS_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def obligation(key) -> Obligation | None:
    return _OB_BY_KEY.get(key)


def evidence_class(key) -> EvidenceClass | None:
    return _EV_BY_KEY.get(key)


def examination_request(key) -> ExaminationRequest | None:
    return _REQ_BY_KEY.get(key)


def certification(key) -> Certification | None:
    return _CERT_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def configured_obligations() -> tuple:
    return tuple(o.key for o in REGULATORY_OBLIGATION_REGISTRY if o.config_status == CONFIGURED)


def not_configured_obligations() -> tuple:
    return tuple(o.key for o in REGULATORY_OBLIGATION_REGISTRY if o.config_status == NOT_CONFIGURED)


def blocked_certifications() -> tuple:
    return tuple(c.key for c in CERTIFICATION_REGISTRY
                 if c.status in (BLOCKED, REVIEWER_NOT_CONFIRMED))


def coverage() -> dict:
    return {
        "obligations": len(REGULATORY_OBLIGATION_REGISTRY),
        "evidence_classes": len(EVIDENCE_REGISTRY),
        "examination_requests": len(EXAMINATION_REQUEST_REGISTRY),
        "certifications": len(CERTIFICATION_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(READINESS_DASHBOARDS),
        "configured_obligations": len(configured_obligations()),
        "not_configured_obligations": len(not_configured_obligations()),
        "blocked_certifications": len(blocked_certifications()),
    }
