"""Enterprise Risk Management registries (Phase D.58) — the declarative catalogs of the risk / control /
assurance composition layer.

Five frozen, declarative catalogs; the layer owns NO persistence and defines NO second GRC platform, risk
register, compliance engine, exception system, audit platform, incident-management system, control-testing
application, policy engine, or approval engine:

  * ENTERPRISE_RISK_REGISTRY — every enterprise risk domain (regulatory, compliance, operational,
    cybersecurity, identity/access, data-governance, integration, vendor/third-party, business-continuity,
    financial-control, client-service, technology-lifecycle, model/AI, privacy, records-management). Each
    names its authoritative owner, signal owners, exception owner, incident owner, remediation owner,
    assurance owner, capability requirements, runtime gate, deep links, and configuration status.
  * CONTROL_REGISTRY — every internal-control family (access, authentication/MFA, authorization/SoD,
    validation, retention, documentation, approval, supervisory review, licensing, suitability, replacement,
    communications supervision, vendor oversight, backup/recovery, incident response, financial authorization,
    commission reconciliation, change management, runtime governance, policy enforcement). Each names its
    objective, authoritative owner, evidence owner, monitoring owner, test owner, approval owner, remediation
    owner, runtime gate, capabilities, deep links, and configuration status. **Control testing has no
    authoritative owner in the platform — every `test_owner` is `not_configured`; the layer never invents
    control effectiveness.**
  * ASSURANCE_REGISTRY — every assurance source (compliance reviews, supervisory approvals, audit-log
    verification, security monitoring, runtime governance checks, architecture guards, data-quality
    validation, workflow governance checks, vendor-risk reviews, continuity-readiness reviews, financial
    reconciliation, documentation completeness, licensing validation, automated test evidence, CI
    verification). Each names its assurance owner, evidence source, scope, frequency, reviewer role, approval
    artifact, runtime gate, capabilities, deep link, and configuration status. **This registry references
    evidence; it never creates evidence.**
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * RISK_DASHBOARDS — every risk dashboard (owner, audience, runtime gate, panel list, required capabilities,
    navigation, refresh, governing services).

Governance verifies every risk domain + control family + assurance source is registered, every configured
entry names an authoritative owner, every panel names an authoritative owner + source + deep link, every
derived value is labeled, and that this layer never becomes a second GRC / risk / compliance / exception /
incident / control-testing / policy / approval system. Where no authoritative owner exists (model/AI risk,
privacy risk, control testing, financial authorization, change management), the entry is declared
`not_configured` and reported honestly — never a fabricated risk, control, or assurance status.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"
CONFIGURED = "configured"


# --- enterprise risk registry ------------------------------------------------

@dataclass(frozen=True)
class RiskDomain:
    key: str
    label: str
    risk_category: str
    authoritative_owner: str   # the authoritative owner of the risk signal (or "not_configured")
    signal_owners: tuple       # the authoritative signal owners composed
    exception_owner: str       # the authoritative exception owner (or "not_configured")
    incident_owner: str        # the authoritative incident owner (or "not_configured")
    remediation_owner: str     # the authoritative remediation owner (or "not_configured")
    assurance_owner: str       # the authoritative assurance owner (or "not_configured")
    capabilities: tuple        # capability requirements
    runtime_gate: str
    deep_links: tuple
    config_status: str = CONFIGURED


def _risk(key, label, category, authoritative_owner, signal_owners, deep_links, *,
          exception_owner=NOT_CONFIGURED, incident_owner=NOT_CONFIGURED, remediation_owner=NOT_CONFIGURED,
          assurance_owner=NOT_CONFIGURED, capabilities=("compliance.supervise",),
          runtime_gate="enterprise_risk.enabled", config_status=CONFIGURED):
    return RiskDomain(key, label, category, authoritative_owner, tuple(signal_owners), exception_owner,
                      incident_owner, remediation_owner, assurance_owner, tuple(capabilities), runtime_gate,
                      tuple(deep_links), config_status)


ENTERPRISE_RISK_REGISTRY = (
    _risk("regulatory_risk", "Regulatory Risk", "regulatory", "compliance_intelligence",
          ("compliance_intelligence", "exception_engine"), ("/supervision", "/enterprise-risk"),
          exception_owner="exception_engine", remediation_owner="exception_work",
          assurance_owner="compliance_intelligence"),
    _risk("compliance_risk", "Compliance Risk", "compliance", "compliance_intelligence",
          ("compliance_intelligence", "exception_engine"), ("/supervision", "/enterprise-risk"),
          exception_owner="exception_engine", incident_owner="security.incidents",
          remediation_owner="exception_work", assurance_owner="compliance_intelligence"),
    _risk("operational_risk", "Operational Risk", "operational", "exception_engine",
          ("exception_engine", "practice_management", "automation_orchestration"),
          ("/enterprise-risk?dashboard=operational_risk", "/exceptions"),
          exception_owner="exception_engine", remediation_owner="exception_work",
          assurance_owner="exception_reporting"),
    _risk("cybersecurity_risk", "Cybersecurity Risk", "security", "security.incidents",
          ("security.incidents", "security_operations"), ("/security-operations", "/security/incidents"),
          exception_owner="security.incidents", incident_owner="security.incidents",
          assurance_owner="security_operations", capabilities=("security.view",)),
    _risk("identity_access_risk", "Identity & Access Risk", "security", "security_operations",
          ("security_operations", "security.incidents"), ("/security-operations", "/admin"),
          incident_owner="security.incidents", assurance_owner="security_operations",
          capabilities=("security.view",)),
    _risk("data_governance_risk", "Data-Governance Risk", "data", "data_governance",
          ("data_governance",), ("/data-governance", "/enterprise-risk"),
          assurance_owner="data_governance", capabilities=("governance.view",)),
    _risk("integration_risk", "Integration Risk", "integration", "integration_hub",
          ("integration.sync", "integration.service"), ("/integration-hub", "/integration"),
          assurance_owner="integration_hub", capabilities=("integration.view",)),
    _risk("vendor_third_party_risk", "Vendor & Third-Party Risk", "vendor", "vendor_management",
          ("vendor_management", "security.incidents"), ("/vendor-management", "/enterprise-risk"),
          incident_owner="security.incidents", assurance_owner="vendor_management",
          capabilities=("integration.view",)),
    _risk("business_continuity_risk", "Business-Continuity Risk", "resilience", "business_continuity",
          ("business_continuity",), ("/business-continuity", "/enterprise-risk"),
          assurance_owner="business_continuity", capabilities=("observability.view",)),
    _risk("financial_control_risk", "Financial-Control Risk", "financial", "financial_operations",
          ("financial_operations",), ("/financial-operations", "/enterprise-risk"),
          assurance_owner="financial_operations", capabilities=("analytics.executive",)),
    _risk("client_service_risk", "Client-Service Risk", "operational", "exception_engine",
          ("exception_engine", "practice_management"), ("/enterprise-risk?dashboard=operational_risk",),
          exception_owner="exception_engine", remediation_owner="exception_work",
          assurance_owner="exception_reporting"),
    _risk("technology_lifecycle_risk", "Technology-Lifecycle Risk", "technology", "vendor_management",
          ("vendor_management", "observability.catalog"), ("/vendor-management", "/observability"),
          assurance_owner="vendor_management", capabilities=("integration.view",)),
    _risk("model_ai_risk", "Model & AI Risk", "model", NOT_CONFIGURED, ("ai_assist",),
          ("/enterprise-risk",), capabilities=("analytics.executive",), config_status=NOT_CONFIGURED),
    _risk("privacy_risk", "Privacy Risk", "privacy", NOT_CONFIGURED,
          ("data_governance", "security_operations"), ("/enterprise-risk",),
          capabilities=("governance.view",), config_status=NOT_CONFIGURED),
    _risk("records_management_risk", "Records-Management Risk", "records", "document_intelligence",
          ("document_intelligence",), ("/documents", "/enterprise-risk"),
          assurance_owner="document_intelligence", capabilities=("documents.view",)),
)

_RISK_BY_KEY = {r.key: r for r in ENTERPRISE_RISK_REGISTRY}


# --- control registry --------------------------------------------------------

@dataclass(frozen=True)
class ControlFamily:
    key: str
    control_family: str
    control_objective: str
    authoritative_owner: str
    evidence_owner: str
    monitoring_owner: str
    test_owner: str            # control testing has no owner in the platform → always not_configured
    approval_owner: str
    remediation_owner: str
    runtime_gate: str
    capabilities: tuple
    deep_links: tuple
    config_status: str = CONFIGURED


def _control(key, family, objective, authoritative_owner, evidence_owner, monitoring_owner, deep_links, *,
             approval_owner=NOT_CONFIGURED, remediation_owner=NOT_CONFIGURED,
             capabilities=("compliance.supervise",), runtime_gate="controls_assurance.enabled",
             config_status=CONFIGURED):
    # test_owner is ALWAYS not_configured — the platform owns no control-testing / effectiveness engine.
    return ControlFamily(key, family, objective, authoritative_owner, evidence_owner, monitoring_owner,
                         NOT_CONFIGURED, approval_owner, remediation_owner, runtime_gate, tuple(capabilities),
                         tuple(deep_links), config_status)


CONTROL_REGISTRY = (
    _control("access_control", "access", "Restrict record access to authorized principals",
             "object_security", "observability.audit", "security_operations", ("/admin", "/security-operations"),
             capabilities=("security.view",)),
    _control("authentication_mfa", "authentication", "Enforce authentication and MFA",
             "security_operations", "observability.audit", "security_operations", ("/security-operations",),
             capabilities=("security.view",)),
    _control("authorization_sod", "authorization", "Enforce RBAC and segregation of duties",
             "policy", "observability.audit", "security_operations", ("/security-operations", "/admin"),
             capabilities=("security.view",)),
    _control("data_validation", "data", "Validate data quality at entry",
             "data_governance", "data_governance", "data_governance", ("/data-governance",),
             capabilities=("governance.view",)),
    _control("record_retention", "records", "Retain records per retention policy",
             "document_intelligence", "document_intelligence", "document_intelligence", ("/documents",),
             capabilities=("documents.view",)),
    _control("document_completeness", "documentation", "Ensure required documentation is complete",
             "document_intelligence", "document_intelligence", "document_intelligence", ("/documents",),
             capabilities=("documents.view",)),
    _control("workflow_approval", "workflow", "Route approvals through governed workflow",
             "automation_orchestration", "automation_orchestration", "automation_orchestration",
             ("/automation",), approval_owner="automation_orchestration", capabilities=("automation.view",)),
    _control("supervisory_review", "supervisory", "Supervisory review of client work",
             "compliance_intelligence", "compliance_intelligence", "compliance_intelligence",
             ("/supervision",), approval_owner="compliance_intelligence"),
    _control("licensing_registration", "licensing", "Maintain producer licensing and registration",
             "insurance_licensing", "insurance_licensing", "vendor_management", ("/insurance", "/vendor-management"),
             capabilities=("integration.view",)),
    _control("suitability", "suitability", "Suitability review of recommendations",
             "compliance_intelligence", "compliance_intelligence", "compliance_intelligence", ("/supervision",)),
    _control("replacement_1035", "suitability", "Replacement / 1035 exchange review",
             "compliance_intelligence", "exception_engine", "compliance_intelligence", ("/supervision",)),
    _control("communications_supervision", "supervisory", "Supervise firm communications",
             "compliance_intelligence", "compliance_intelligence", "compliance_intelligence",
             ("/supervision", "/communications")),
    _control("vendor_oversight", "vendor", "Oversee third-party vendors",
             "vendor_management", "vendor_management", "vendor_management", ("/vendor-management",),
             capabilities=("integration.view",)),
    _control("backup_recovery", "resilience", "Maintain backup and recovery readiness",
             "business_continuity", "business_continuity", "business_continuity", ("/business-continuity",),
             capabilities=("observability.view",)),
    _control("incident_response", "incident", "Respond to security incidents",
             "security.incidents", "security.incidents", "security_operations", ("/security/incidents",),
             capabilities=("security.view",)),
    _control("financial_authorization", "financial", "Authorize financial transactions",
             NOT_CONFIGURED, NOT_CONFIGURED, "financial_operations", ("/financial-operations",),
             capabilities=("analytics.executive",), config_status=NOT_CONFIGURED),
    _control("commission_reconciliation", "financial", "Reconcile insurance commissions",
             "insurance_commissions", "insurance_reporting", "financial_operations", ("/financial-operations", "/insurance"),
             capabilities=("analytics.executive",)),
    _control("change_management", "change", "Manage and review system changes",
             NOT_CONFIGURED, "observability.catalog", "observability", ("/observability",),
             capabilities=("observability.view",), config_status=NOT_CONFIGURED),
    _control("runtime_governance", "runtime", "Govern runtime feature gates",
             "runtime", "runtime", "runtime", ("/runtime", "/enterprise-risk"),
             capabilities=("observability.view",)),
    _control("policy_enforcement", "policy", "Enforce policy decisions",
             "policy", "observability.audit", "policy", ("/enterprise-risk",),
             capabilities=("observability.view",)),
)

_CONTROL_BY_KEY = {c.key: c for c in CONTROL_REGISTRY}


# --- assurance registry ------------------------------------------------------

@dataclass(frozen=True)
class AssuranceSource:
    key: str
    label: str
    assurance_owner: str
    evidence_source: str       # the authoritative evidence this references (never created here)
    scope: str                 # firm | client | household
    frequency: str             # continuous | periodic | per_event | per_commit
    reviewer_role: str
    approval_artifact: str
    runtime_gate: str
    capabilities: tuple
    deep_link: str
    config_status: str = CONFIGURED


def _assurance(key, label, assurance_owner, evidence_source, reviewer_role, approval_artifact, deep_link, *,
               scope="firm", frequency="continuous", capabilities=("observability.audit",),
               runtime_gate="controls_assurance.enabled", config_status=CONFIGURED):
    return AssuranceSource(key, label, assurance_owner, evidence_source, scope, frequency, reviewer_role,
                           approval_artifact, runtime_gate, tuple(capabilities), deep_link, config_status)


ASSURANCE_REGISTRY = (
    _assurance("compliance_reviews", "Compliance Reviews", "compliance_intelligence",
               "compliance_intelligence.supervisory_dashboard", "supervisor", "review_signoff", "/supervision",
               frequency="periodic", capabilities=("compliance.supervise",)),
    _assurance("supervisory_approvals", "Supervisory Approvals", "compliance_intelligence",
               "compliance_intelligence", "supervisor", "approval", "/supervision", frequency="per_event",
               capabilities=("compliance.supervise",)),
    _assurance("audit_log_verification", "Audit-Log Verification", "observability.audit",
               "observability.audit", "auditor", "audit_trail", "/observability"),
    _assurance("security_monitoring", "Security Monitoring", "security_operations",
               "security.incidents.metrics", "security_officer", "monitoring_report", "/security-operations",
               capabilities=("security.view",)),
    _assurance("runtime_governance_checks", "Runtime Governance Checks", "runtime",
               "runtime.consumption", "platform_owner", "gate_snapshot", "/runtime",
               capabilities=("observability.view",)),
    _assurance("architecture_guards", "Architecture Guards", "continuous_integration",
               "tests.test_platform_architecture", "platform_owner", "ci_run", "/enterprise-risk",
               frequency="per_commit"),
    _assurance("data_quality_validation", "Data-Quality Validation", "data_governance",
               "data_governance.governance_summary", "data_steward", "validation_report", "/data-governance",
               capabilities=("governance.view",)),
    _assurance("workflow_governance_checks", "Workflow Governance Checks", "automation_orchestration",
               "automation_orchestration.automation_summary", "ops_owner", "workflow_report", "/automation",
               capabilities=("automation.view",)),
    _assurance("vendor_risk_reviews", "Vendor-Risk Reviews", "vendor_management",
               "vendor_management.vendor_summary", "vendor_manager", "vendor_review", "/vendor-management",
               frequency="periodic", capabilities=("integration.view",)),
    _assurance("continuity_readiness_reviews", "Continuity-Readiness Reviews", "business_continuity",
               "business_continuity.continuity_summary", "resilience_owner", "readiness_review",
               "/business-continuity", frequency="periodic", capabilities=("observability.view",)),
    _assurance("financial_reconciliation", "Financial Reconciliation", "financial_operations",
               "insurance_reporting.commission_report", "finance_owner", "reconciliation", "/financial-operations",
               frequency="periodic", capabilities=("analytics.executive",)),
    _assurance("documentation_completeness", "Documentation Completeness", "document_intelligence",
               "document_intelligence.document_summary", "ops_owner", "doc_review", "/documents",
               capabilities=("documents.view",)),
    _assurance("licensing_validation", "Licensing Validation", "insurance_licensing",
               "insurance_licensing.list_licenses", "compliance_officer", "license_check", "/insurance",
               capabilities=("integration.view",)),
    _assurance("automated_test_evidence", "Automated Test Evidence", "continuous_integration",
               "tests", "engineer", "test_run", "/enterprise-risk", frequency="per_commit"),
    _assurance("ci_verification", "CI Verification", "continuous_integration",
               "github_actions", "engineer", "ci_run", "/enterprise-risk", frequency="per_commit"),
)

_ASSURANCE_BY_KEY = {a.key: a for a in ASSURANCE_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service (or "not_configured")
    source: str                # the authoritative read the value is composed from
    measure: str
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative risk-owner surface to drill into
    explainability: str
    derived: bool = False
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       derived=False, refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    derived, refresh, lifecycle)


PANEL_REGISTRY = (
    # risk-domain inventory + posture (derived, catalog)
    _p("risk_domain_inventory", "enterprise_risk", "enterprise_risk.registry", "risk", "count", "list",
       "compliance.supervise", "/enterprise-risk",
       "The registered enterprise risk-domain catalog — each naming its authoritative owner, signal / "
       "exception / incident / remediation / assurance owners, and configuration status. Metadata only.",
       derived=True),
    _p("enterprise_risk_posture", "enterprise_risk", "enterprise_risk.compose", "risk", "distribution",
       "gauge", "compliance.supervise", "/enterprise-risk",
       "Deterministic enterprise-risk posture — configured vs not_configured domains + open-finding / "
       "exception counts + severity distribution across the authoritative owners. A DERIVED coverage "
       "summary, never a certified composite risk score or regulatory rating.", derived=True),
    # compliance
    _p("open_compliance_findings", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard",
       "findings", "count", "card", "compliance.supervise", "/supervision",
       "Open supervisory compliance findings (reviews / exceptions), from Compliance Intelligence (requires "
       "compliance.supervise). No second compliance engine."),
    _p("compliance_exception_severity", "exception_engine", "compliance_intelligence.supervisory_dashboard",
       "exceptions", "distribution", "chart", "compliance.supervise", "/exceptions",
       "Open compliance-exception severity distribution (blocker / high / …), from the authoritative "
       "Exception Engine via Compliance Intelligence supervisory composition. No second exception system."),
    _p("unresolved_remediation_workload", "exception_engine", "compliance_intelligence.supervisory_dashboard",
       "remediation", "count", "card", "compliance.supervise", "/exceptions",
       "Unresolved remediation workload (open exceptions + blocked reviews), from the authoritative "
       "Exception Engine via Compliance Intelligence. The layer never assigns remediation."),
    _p("operational_incidents", "exception_engine", "compliance_intelligence.supervisory_dashboard",
       "incidents", "count", "card", "compliance.supervise", "/exceptions",
       "Operational incidents (blocked / pending-approval reviews), from the authoritative Exception Engine "
       "via Compliance Intelligence. The layer never acknowledges an incident."),
    # security
    _p("security_incidents", "security.incidents", "security.incidents.metrics", "incidents", "count", "card",
       "security.view", "/security/incidents",
       "Open security incidents / findings / pending exceptions, from the Security incidents domain. No "
       "second incident-management system."),
    _p("identity_access_warnings", "security_operations", "security_operations.security_summary", "risk",
       "count", "card", "security.view", "/security-operations",
       "Identity & access warnings (access-grant posture), from Security Operations. Counts + status only — "
       "no credentials or tokens."),
    # data governance
    _p("data_quality_exceptions", "data_governance", "data_governance.governance_summary", "exceptions",
       "count", "card", "governance.view", "/data-governance",
       "Data-quality validation exceptions, from Data Governance. No second data-quality engine."),
    _p("duplicate_lineage_issues", "data_governance", "data_governance.governance_summary", "risk", "count",
       "card", "governance.view", "/data-governance",
       "Duplicate-candidate / lineage issues, from Data Governance. Counts only — never a client payload."),
    # integration
    _p("integration_failures", "integration", "integration.service.overview_metrics", "incidents", "count",
       "card", "integration.view", "/integration",
       "Firm integration failures (providers / connected connectors / sync failures), from the Integration "
       "Platform. No second integration platform."),
    _p("synchronization_failures", "integration", "integration.sync.metrics", "incidents", "count", "card",
       "integration.view", "/integration",
       "Integration synchronization failures / connector errors / unresolved conflicts, from the Integration "
       "Platform sync engine."),
    # vendor / third party
    _p("vendor_risk_findings", "vendor_management", "vendor_management.vendor_summary", "risk", "count",
       "card", "integration.view", "/vendor-management",
       "Vendor / third-party risk signals (governance score + dependencies), from the D.56 Vendor "
       "Management layer. Counts + status only."),
    _p("expiring_technology_certificates", "vendor_management", "vendor_management.vendor_summary", "risk",
       "count", "card", "integration.view", "/vendor-management",
       "Expiring technology / certificates (a renewal-risk signal), from the D.56 Vendor Management layer. "
       "Status only — no key material."),
    # resilience
    _p("continuity_gaps", "business_continuity", "business_continuity.continuity_summary", "risk", "count",
       "card", "observability.view", "/business-continuity",
       "Business-continuity gaps (resilience posture), from the D.55 Business Continuity layer. Counts + "
       "status only — never an infrastructure payload."),
    _p("backup_recovery_config", "business_continuity", "business_continuity.continuity_summary", "coverage",
       "status", "card", "observability.view", "/business-continuity",
       "Backup / recovery configuration status, from the D.55 Business Continuity layer. Backup / restore / "
       "DR have no authoritative owner and are reported not_configured honestly, never fabricated."),
    # operational / workflow
    _p("workflow_escalations", "automation_orchestration", "automation_orchestration.automation_summary",
       "incidents", "count", "card", "automation.view", "/automation",
       "Workflow escalations, from the D.51 Automation Orchestration layer over the Workflow facade. No "
       "second workflow engine."),
    _p("overdue_approvals", "automation_orchestration", "automation_orchestration.automation_summary",
       "exceptions", "count", "card", "automation.view", "/automation",
       "Overdue workflow approvals, from the D.51 Automation Orchestration layer. The layer never approves. "
       "A dedicated approval owner is otherwise not_configured."),
    # documentation / licensing
    _p("documentation_gaps", "document_intelligence", "document_intelligence.document_summary", "coverage",
       "count", "card", "documents.view", "/documents",
       "Documentation completeness gaps, from the D.50 Document Intelligence layer. Counts + status only — "
       "never document contents."),
    _p("licensing_gaps", "insurance_licensing", "insurance_licensing.list_licenses", "coverage", "count",
       "card", "integration.view", "/insurance",
       "Producer-licensing gaps (expired / approaching expiry), from the Insurance licensing owner "
       "(requires insurance.licensing.read internally; unavailable otherwise)."),
    # financial control
    _p("financial_reconciliation_status", "financial_operations", "financial_operations.firm_financial_summary",
       "coverage", "status", "card", "analytics.executive", "/financial-operations",
       "Financial reconciliation status (commission collection health), from the D.57 Financial Operations "
       "layer. Aggregate status only — no accounting payload."),
    _p("commission_exceptions", "insurance_commissions", "insurance_reporting.commission_report", "exceptions",
       "count", "card", "analytics.executive", "/financial-operations",
       "Commission reconciliation exceptions (outstanding / variance), from the authoritative commission "
       "ledger. Aggregate totals only."),
    # controls + assurance (derived, catalog)
    _p("control_coverage", "enterprise_risk", "enterprise_risk.registry", "controls", "count", "list",
       "compliance.supervise", "/enterprise-risk?dashboard=controls_assurance",
       "Control-family coverage — which control families have an authoritative owner vs not_configured, and "
       "which have a control-test owner (none: control testing is not_configured platform-wide). Metadata "
       "only; never invents control effectiveness.", derived=True),
    _p("assurance_evidence_coverage", "enterprise_risk", "enterprise_risk.registry", "assurance", "count",
       "list", "compliance.supervise", "/enterprise-risk?dashboard=controls_assurance",
       "Assurance-evidence coverage — which assurance sources reference authoritative evidence vs "
       "not_configured. References evidence; never creates it.", derived=True),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # executive | supervisory | operations | security
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


_RISK_CAPS = ("compliance.supervise", "analytics.executive")

RISK_DASHBOARDS = (
    _d("enterprise_risk", "enterprise_risk", "executive", "enterprise_risk.enabled",
       ("risk_domain_inventory", "enterprise_risk_posture", "control_coverage"),
       _RISK_CAPS, "/enterprise-risk?dashboard=enterprise_risk",
       ("compliance_intelligence", "exception_engine", "enterprise_risk")),
    _d("compliance_risk", "enterprise_risk", "supervisory", "risk_dashboards.enabled",
       ("open_compliance_findings", "compliance_exception_severity", "unresolved_remediation_workload"),
       _RISK_CAPS, "/enterprise-risk?dashboard=compliance_risk",
       ("compliance_intelligence", "exception_engine")),
    _d("operational_risk", "enterprise_risk", "operations", "risk_dashboards.enabled",
       ("operational_incidents", "workflow_escalations", "unresolved_remediation_workload"),
       _RISK_CAPS, "/enterprise-risk?dashboard=operational_risk",
       ("exception_engine", "automation_orchestration")),
    _d("security_risk", "enterprise_risk", "security", "risk_dashboards.enabled",
       ("security_incidents", "identity_access_warnings", "vendor_risk_findings"),
       _RISK_CAPS, "/enterprise-risk?dashboard=security_risk",
       ("security.incidents", "security_operations", "vendor_management")),
    _d("third_party_risk", "enterprise_risk", "operations", "risk_dashboards.enabled",
       ("vendor_risk_findings", "expiring_technology_certificates", "integration_failures"),
       _RISK_CAPS, "/enterprise-risk?dashboard=third_party_risk",
       ("vendor_management", "integration")),
    _d("resilience_risk", "enterprise_risk", "operations", "risk_dashboards.enabled",
       ("continuity_gaps", "backup_recovery_config", "synchronization_failures"),
       _RISK_CAPS, "/enterprise-risk?dashboard=resilience_risk",
       ("business_continuity", "integration")),
    _d("financial_control_risk", "enterprise_risk", "executive", "risk_dashboards.enabled",
       ("financial_reconciliation_status", "commission_exceptions", "licensing_gaps"),
       _RISK_CAPS, "/enterprise-risk?dashboard=financial_control_risk",
       ("financial_operations", "insurance_commissions", "insurance_licensing")),
    _d("controls_assurance", "enterprise_risk", "executive", "controls_assurance.enabled",
       ("control_coverage", "assurance_evidence_coverage", "documentation_gaps"),
       _RISK_CAPS, "/enterprise-risk?dashboard=controls_assurance",
       ("enterprise_risk", "document_intelligence")),
)

_DASH_BY_KEY = {d.key: d for d in RISK_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def risk_domain(key) -> RiskDomain | None:
    return _RISK_BY_KEY.get(key)


def control_family(key) -> ControlFamily | None:
    return _CONTROL_BY_KEY.get(key)


def assurance_source(key) -> AssuranceSource | None:
    return _ASSURANCE_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def risk_registered(key) -> bool:
    return key in _RISK_BY_KEY


def control_registered(key) -> bool:
    return key in _CONTROL_BY_KEY


def assurance_registered(key) -> bool:
    return key in _ASSURANCE_BY_KEY


def configured_domains() -> tuple:
    return tuple(r.key for r in ENTERPRISE_RISK_REGISTRY if r.config_status == CONFIGURED)


def not_configured_domains() -> tuple:
    return tuple(r.key for r in ENTERPRISE_RISK_REGISTRY if r.config_status == NOT_CONFIGURED)


def coverage() -> dict:
    return {
        "risk_domains": len(ENTERPRISE_RISK_REGISTRY),
        "control_families": len(CONTROL_REGISTRY),
        "assurance_sources": len(ASSURANCE_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(RISK_DASHBOARDS),
        "configured_risk_domains": len(configured_domains()),
        "not_configured_risk_domains": len(not_configured_domains()),
    }
