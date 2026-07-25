# Enterprise Reporting & Executive Intelligence (Phase D.48)

The Executive Reporting layer provides firm-wide operational visibility by composing the platform's
authoritative operational services and the **single Analytics Registry** into named executive dashboards. It
is a governed, **read-only composition** — **not** another analytics engine, data warehouse, BI platform,
reporting database, ETL layer, or metrics system — and it never mutates. See
[`ADR-053`](adr/ADR-053-executive-reporting.md).

## Where it lives
`app/services/executive_intelligence/` — `registry.py`, `model.py`, `service.py`, `widgets.py`, `gate.py`,
`stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`. Routes: `app/routes/executive_intelligence.py`.

## Composition, not duplication
| What | Authoritative owner | How the layer uses it |
| --- | --- | --- |
| Every KPI value | **Analytics Registry** (`analytics.metrics`, the single metrics registry) | `compute_metric(principal, key)` — inherits record scope + the `analytics.executive` gate |
| AUM trend | Analytics trends | `metric_trend` |
| Firm intelligence | Analytics firm intelligence | `firm_intelligence(principal)` |
| Advisor workload | Unified Work Queue | `work_queue_summary` |
| Workflow status/aging | Workflow automation | `workflow_metrics` |
| Review cadence | Portfolio | `accounts_due_for_review` |
| Opportunity pipeline | Opportunity | `pipeline_report` |
| Communication activity | Communications | `metrics` |
| Operational health | Operational Intelligence (D.46) | `workspace_recommendations` |
| Runtime health | Runtime + Observability | `adoption_stats` + health metrics |

The layer defines **no new metrics** — it registers only four low-cardinality operational counters (about
itself) into the ONE Analytics Registry. Every business KPI comes from `compute_metric`.

## Executive gating (inherited, no bypass)
`compute_metric` enforces `analytics.executive` server-side: an executive (firm revenue/AUM) metric returns
`restricted` (value withheld) for a non-executive. The dashboard registry additionally declares each
dashboard's `required_capabilities` — `analytics.executive` for the executive/revenue dashboards,
`analytics.view` for the operational ones. A principal lacking a dashboard's capability gets `None` (404);
executive widgets on a mixed dashboard self-restrict. Values are never leaked.

## Explainability
Every widget carries its explanation (what it shows + where it comes from), authoritative owner, source, and
a deep link to drill into the authoritative surface. Every dashboard carries a generated timestamp, source
inventory, governing services, explainable widgets, and deep links. A non-explainable widget is never
emitted.

## Runtime & policy governance
Gated through the Runtime Engine (`reporting.enabled` + `executive_dashboard.enabled` +
`executive_widgets.enabled`; no env fallback) AND the Policy Engine, alongside the RBAC capability checks.

## Rationale: no second BI platform
A second BI platform / warehouse would need ETL, copied operational data, a parallel metrics catalog, and its
own access model — duplicating governed, gated infrastructure with reconciliation + drift risk and no
benefit the composition doesn't provide. Composing over the single Analytics Registry keeps one source of
truth for every KPI and zero copied data. See [`REPORTING_GOVERNANCE.md`](REPORTING_GOVERNANCE.md).

## Integration
Advisor Workspace gains an **Executive Insights** panel; Client 360 + Household 360 gain an executive-only
**Executive** section; AI Assist **summarizes** executive KPI values (executive-only, never invents a
metric). The client portal is excluded (no executive dashboards, no operational metrics). See
[`EXECUTIVE_DASHBOARDS.md`](EXECUTIVE_DASHBOARDS.md), [`DASHBOARD_REGISTRY.md`](DASHBOARD_REGISTRY.md).

**Practice Management (D.49):** the registry gains a `practice_management` executive dashboard composed from
**existing** widgets (`advisor_workload`, `workflow_status`, `workflow_aging`, `operational_health`,
`tax_workload`) — no new widget — whose navigation deep-links to the full practice-management surface at
`/practice`. The dedicated Practice Management layer (firm-wide capacity/utilization/staffing/backlog) lives
in `app/services/practice_management/`; see [`PRACTICE_MANAGEMENT.md`](PRACTICE_MANAGEMENT.md) and ADR-054.

**Document Intelligence (D.50):** the registry gains a `document_intelligence` executive dashboard composed
from **existing** widgets (`compliance_workload`, `operational_health`, `tax_workload`) — no new widget —
whose navigation deep-links to the full document-intelligence surface at `/document-intelligence`. The
dedicated Document Intelligence layer (firm-wide document inventory/retention/archive/lifecycle/completeness)
lives in `app/services/document_intelligence/`; see [`DOCUMENT_INTELLIGENCE.md`](DOCUMENT_INTELLIGENCE.md) and
ADR-055.

**Automation Orchestration (D.51):** the registry gains an `automation` executive dashboard composed from
**existing** widgets (`workflow_status`, `workflow_aging`, `operational_health`) — no new widget — whose
navigation deep-links to the full automation surface at `/automation-orchestration`. The dedicated Automation
Orchestration layer (firm-wide automation inventory/workflow/trigger/execution/pending/failed) lives in
`app/services/automation_orchestration/`; see [`AUTOMATION_ORCHESTRATION.md`](AUTOMATION_ORCHESTRATION.md) and
ADR-056.

**Data Governance (D.52):** the registry gains a `data_governance` executive dashboard composed from
**existing** widgets (`compliance_workload`, `operational_health`, `runtime_health`) — no new widget — whose
navigation deep-links to the full data-governance surface at `/data-governance`. The dedicated Data Governance
layer (firm-wide master data / stewardship / lineage / ownership / duplicate / validation / data quality)
lives in `app/services/data_governance/`; see [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md) and ADR-057.

**Integration Health (D.53):** the registry gains an `integration_health` executive dashboard composed from
**existing** widgets (`runtime_health`, `operational_health`) — no new widget — whose navigation deep-links to
the full integration surface at `/integration-hub`. The dedicated Integration Hub layer (firm-wide
integrations / synchronization / authentication / webhooks / connectors / API health / event routing) lives in
`app/services/integration_hub/`; see [`INTEGRATION_HUB.md`](INTEGRATION_HUB.md) and ADR-058.

**Security Operations (D.54):** the registry gains a `security_operations` executive dashboard composed from
**existing** widgets (`compliance_workload`, `runtime_health`, `operational_health`) — no new widget — whose
navigation deep-links to the full security surface at `/security-operations`. The dedicated Security
Operations layer (firm-wide authentication / authorization / identity governance / MFA / sessions / audit /
security posture) lives in `app/services/security_operations/`; see
[`SECURITY_OPERATIONS.md`](SECURITY_OPERATIONS.md) and ADR-059.

**Operational Resilience (D.55):** the registry gains an `operational_resilience` executive dashboard composed
from **existing** widgets (`runtime_health`, `operational_health`) — no new widget — whose navigation
deep-links to the full continuity surface at `/business-continuity`. The dedicated Business Continuity layer
(firm-wide backup status / recovery readiness / restore validation / infrastructure health / runtime
resilience / maintenance / notifications / operational readiness) lives in
`app/services/business_continuity/`; see [`BUSINESS_CONTINUITY.md`](BUSINESS_CONTINUITY.md) and ADR-060.

**Technology Governance (D.56):** the registry gains a `technology_governance` executive dashboard composed
from **existing** widgets (`runtime_health`, `operational_health`) — no new widget — whose navigation
deep-links to the full vendor surface at `/vendor-management`. The dedicated Vendor Management layer (a single
governed read-only view of vendors / software / platforms / licensing / lifecycle / third-party risk, composed
over the authoritative Integration + Security + Observability + Insurance-licensing + Compliance owners) lives
in `app/services/vendor_management/`; counts + status only, never a contract/credential/key/secret/payload;
AI summarizes but never approves purchases / renews contracts / terminates vendors / alters licensing. See
[`VENDOR_MANAGEMENT.md`](VENDOR_MANAGEMENT.md) and ADR-061.

**Financial Operations (D.57):** the registry gains a `financial_operations` executive dashboard composed from
**existing** widgets (`revenue_kpi`, `firm_aum`, `operational_health`) — no new widget — whose navigation
deep-links to the full financial surface at `/financial-operations`. The dedicated Financial Operations layer
(a single read-only view of firm financial performance — revenue / profitability / expenses / payroll /
commissions / firm KPIs, composed over the authoritative insurance commission ledger + portfolio AUM owner +
the single Analytics Registry + Executive Reporting + Practice Management) lives in
`app/services/financial_operations/`; firm-level aggregate totals + status only, never a payroll detail / tax
return / bank account number / payment credential / accounting payload; billing / payroll / GL / profitability
have no authoritative owner and are reported `not_configured`; AI summarizes firm KPIs but never issues
invoices / processes payroll / modifies accounting records / changes commissions / alters billing. See
[`FINANCIAL_OPERATIONS.md`](FINANCIAL_OPERATIONS.md) and ADR-062.

**Enterprise Risk & Assurance (D.58):** the registry gains an `enterprise_risk_assurance` executive dashboard
composed from **existing** widgets (`compliance_workload`, `operational_health`, `runtime_health`) — no new
widget — whose navigation deep-links to the full risk surface at `/enterprise-risk`. The dedicated Enterprise
Risk layer (a unified read-only view of enterprise risk posture — risk domains / control coverage / findings /
exceptions / incidents / remediation / assurance, composed over Compliance Intelligence + the Exception Engine,
Security, Data Governance, the Integration Platform, Business Continuity, Vendor Management, Financial
Operations, Document Intelligence, Automation Orchestration, Insurance licensing, and the Runtime + Policy
engines + audit logging) lives in `app/services/enterprise_risk/`; counts / status / severity distributions /
coverage only, never sensitive evidence; every severity comes from an authoritative source and the posture
panel is a DERIVED coverage summary, never a fabricated composite risk score; control testing / model-AI /
privacy / financial-authorization / change-management are reported `not_configured`; AI summarizes but never
assigns risk / certifies controls / approves exceptions / acknowledges incidents / certifies compliance. See
[`ENTERPRISE_RISK_MANAGEMENT.md`](ENTERPRISE_RISK_MANAGEMENT.md) and ADR-063.

**Regulatory Readiness & Evidence (D.59):** the registry gains a `regulatory_readiness_evidence` executive
dashboard composed from **existing** widgets (`compliance_workload`, `operational_health`, `runtime_health`) —
no new widget — whose navigation deep-links to the full readiness surface at `/regulatory-readiness`. The
dedicated Regulatory Readiness layer (a unified read-only view of the firm's operational readiness to respond to
regulatory examinations — obligation coverage / evidence availability / completeness / freshness / supervisory
reviews / certification status / filing readiness / remediation evidence, composed over Compliance Intelligence
+ compliance reviews + the rule catalog + the reviewer-authority owner, the Exception Engine, Document
Intelligence, Insurance licensing, and the D.52–D.58 layers) lives in `app/services/regulatory_readiness/`;
counts / status / coverage / freshness only, never sensitive evidence; every certification is blocked /
reviewer_not_confirmed (reviewer authority never inferred; business approval is not certification); filing /
examination / evidence-export are reported not_configured; **operational readiness is not regulatory
certification** and an absent finding is never compliance; AI summarizes but never certifies compliance /
approves a rule set / signs an attestation / files a form. See
[`REGULATORY_EXAMINATION_READINESS.md`](REGULATORY_EXAMINATION_READINESS.md) and ADR-064.

**Enterprise Operational Resilience (D.60):** the registry gains an `enterprise_operational_resilience`
executive dashboard composed from **existing** widgets (`operational_health`, `runtime_health`) — no new
widget — whose navigation deep-links to the full resilience surface at `/operational-resilience`. The dedicated
Operational Resilience layer (a unified read-only view of firm operational resilience — service health /
incident inventory / alerts / maintenance windows / continuity coverage / recovery readiness / dependency
health / vendor operational status, composed over the Observability service catalog / health / incidents /
alerts owners, Security incidents, the Integration Platform, Vendor Management, Automation Orchestration, and
Business Continuity) lives in `app/services/operational_resilience/`; counts / status / coverage only, never a
sensitive operational payload; backup / restore / DR / recovery-testing / failover / outage-history / vendor
incidents are reported not_configured (maintenance windows + alerting ARE owned by Observability); the
executive posture is DERIVED and labeled — **operational posture, never a certification that production is
healthy or continuity assured**; AI summarizes but never declares production healthy / certifies continuity /
generates alerts. See [`ENTERPRISE_OPERATIONAL_RESILIENCE.md`](ENTERPRISE_OPERATIONAL_RESILIENCE.md) and
ADR-065.

**Enterprise Workforce & Capacity (D.61):** the registry gains an `enterprise_workforce_capacity` executive
dashboard composed from **existing** widgets (`advisor_workload`, `operational_health`) — no new widget — whose
navigation deep-links to the full capacity surface at `/capacity-planning`. The dedicated Capacity Planning
layer (a unified read-only view of firm workforce operations / capacity / utilization — staffing / workload /
queue health / utilization / capacity forecasts / assignment distribution / operational / advisor / automation
workload, composed over the Operations capacity owner, the Work Queue, Practice Management, and Automation
Orchestration) lives in `app/services/capacity_planning/`; counts / status / coverage only, never an employee
detail / payroll / HR record / calendar content / time entry; HR directory / contractors / PTO / availability /
time-tracking / payroll / meeting-onboarding-planning capacity are reported not_configured; the executive
posture is DERIVED and labeled — **an operational summary, never a certified staffing / utilization figure and
never an HR record**; AI summarizes but never assigns work / approves staffing / schedules employees /
fabricates utilization. See [`ENTERPRISE_CAPACITY_PLANNING.md`](ENTERPRISE_CAPACITY_PLANNING.md) and ADR-066.

**Enterprise Knowledge & Documentation (D.62):** the registry gains an `enterprise_knowledge_documentation`
executive dashboard composed from **existing** widgets (`compliance_workload`, `operational_health`) — no new
widget — whose navigation deep-links to the full knowledge surface at `/knowledge-management`. The dedicated
Knowledge Management layer (a unified read-only view of firm knowledge / SOPs / documentation — SOP coverage /
documentation completeness / freshness / ownership / version awareness / publication readiness / knowledge
health, composed over the Document Platform, Document Intelligence, and Data Governance retention) lives in
`app/services/knowledge_management/`; counts / status / coverage only, never document contents / confidential
procedures; SOP governance / runbooks / wiki / Confluence / search index are reported not_configured; the
executive posture is DERIVED and labeled — **a documentation-coverage summary, never a certified SOP / approval
/ institutional-knowledge figure**; AI summarizes but never invents documentation / fabricates SOPs / implies
approvals. See [`ENTERPRISE_KNOWLEDGE_MANAGEMENT.md`](ENTERPRISE_KNOWLEDGE_MANAGEMENT.md) and ADR-067.

**Enterprise Change & Release Governance (D.63):** the registry gains an `enterprise_change_release_governance`
executive dashboard composed from **existing** widgets (`compliance_workload`, `operational_health`,
`runtime_health`) — no new widget — whose navigation deep-links to the full change surface at
`/change-management`. The dedicated Change Management layer (a unified read-only view of the firm's change
posture — change-domain inventory / release readiness / CI-evidence verification / configuration governance /
migration readiness / deployment evidence / rollback readiness / executive change posture, composed over the
architecture manifest, Observability health / catalog / alerts / incidents, the Runtime + Policy engines,
Security incidents, Compliance Intelligence, and the CI pipeline evidence — with **live self-verification** of
declared-vs-live route / migration / ADR / section / dashboard drift) lives in
`app/services/change_management/`; counts / status / verification only, never a credential / token / deployment
payload / sensitive configuration value; live git / PR / CI status, deployment execution, rollback, production
verification, and post-change review are reported not_configured; the executive posture is DERIVED and labeled
— **an operational-readiness summary, never approval / certification / deployment success: a green build is not
production, a merged pull request is not deployment, an absent incident is not change success**; AI summarizes
but never creates a branch / merges / deploys / runs a migration / changes a flag / approves / rolls back /
certifies production. See [`ENTERPRISE_CHANGE_MANAGEMENT.md`](ENTERPRISE_CHANGE_MANAGEMENT.md) and ADR-068.

**Enterprise Platform & Environment Landscape (D.64):** the registry gains an
`enterprise_platform_environment_landscape` executive dashboard composed from **existing** widgets
(`operational_health`, `runtime_health`) — no new widget — whose navigation deep-links to the full environment
surface at `/environment-management`. The dedicated Environment Management layer (a unified read-only view of
the firm's environment & platform landscape — environment inventory / deployment topology / runtime topology /
platform ownership / lifecycle state / infrastructure dependencies, composed over the Observability catalog /
health / service owners, the Runtime + Policy engines, and the Integration platform) lives in
`app/services/environment_management/`; counts / status / coverage only, never a credential / token / deployment
payload / private topology / sensitive configuration value; cloud resources, servers, containers, VMs, formal
lifecycle state, retirement records, decommission schedule, host / network topology, and live deployment
execution are reported not_configured; the executive posture is DERIVED and labeled — **an operational-visibility
summary, never a certified environment health, deployment status, provisioning outcome, or retirement decision:
environment metadata is not live infrastructure, a deployment reference is not a deployment**; AI summarizes but
never invents environments / fabricates infrastructure / infers deployments / certifies platform health /
provisions resources. See [`ENTERPRISE_ENVIRONMENT_MANAGEMENT.md`](ENTERPRISE_ENVIRONMENT_MANAGEMENT.md) and
ADR-069.

**Enterprise Identity & Access Governance (D.65):** the registry gains an `enterprise_identity_access_governance`
executive dashboard composed from **existing** widgets (`compliance_workload`, `operational_health`) — no new
widget — whose navigation deep-links to the full identity surface at `/identity-governance`. The dedicated
Identity Governance layer (a unified read-only view of the firm's identity & access posture — identity inventory
/ role coverage / capability coverage / authentication coverage / authorization coverage / policy coverage /
least-privilege indicators, composed over the Identity service, Security RBAC / Authentication / Authorization
owners and the Policy engine) lives in `app/services/identity_governance/`; counts / coverage / status / ratios
only, never a password / token / session ID / credential / raw identity / privileged-role membership /
user-level permission map; SSO, MFA enforcement, service accounts, API-key auth, access reviews, PAM,
segregation of duties, identity lifecycle, and password management are reported not_configured; the executive
posture is DERIVED and labeled — **a governance-readiness summary, never an authentication result, an
authorization decision, a granted permission, or a certified access review: a capability inventory is not a
grant, a role definition is not an assignment, and coverage is not certification**; AI summarizes but never
authenticates / authorizes / assigns roles / recommends privilege escalation / fabricates permissions / invents
identities / bypasses policy. See [`ENTERPRISE_IDENTITY_GOVERNANCE.md`](ENTERPRISE_IDENTITY_GOVERNANCE.md) and
ADR-070.

**Enterprise Data Governance (D.66):** the registry gains an `enterprise_data_governance` executive dashboard
composed from **existing** widgets (`compliance_workload`, `operational_health`) — no new widget — whose
navigation deep-links to the full data-governance surface at `/data-governance-intelligence`. The dedicated
Data Governance Intelligence layer (a unified read-only view of the firm's data-governance posture — enterprise
data inventory / source-of-truth coverage / lineage coverage / stewardship coverage / quality-rule coverage /
retention coverage / governance readiness / data-risk indicators, composed over the Governance catalog / MDM /
quality / retention owners) lives in `app/services/data_governance_intelligence/`; counts / coverage / status /
ratios only, never a sensitive data value / client PII / confidential metadata / quality-rule internal; external
catalog, business glossary, classification, automated column lineage, contracts, DQ scorecards, retention-policy
catalog, and DPIA are reported not_configured; the executive posture is DERIVED and labeled — **a
governance-readiness summary, never a repaired dataset, a created lineage edge, an assigned steward, an executed
quality rule, or an enforced retention decision: a registered rule is not an executed check, and coverage is not
certification**; AI summarizes but never invents lineage / fabricates metadata / assigns stewardship / modifies
governance / repairs data / infers missing ownership. Distinct from (not a duplicate of) the D.52 Data
Governance executive dashboard; both are read-only views over the single authoritative Governance package. See
[`ENTERPRISE_DATA_GOVERNANCE.md`](ENTERPRISE_DATA_GOVERNANCE.md) and ADR-071.

## References
`app/services/executive_intelligence/*`, `app/routes/executive_intelligence.py`,
`docs/platform_architecture_manifest.yaml`, `tests/test_executive_reporting.py`, ADR-053.
