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

## References
`app/services/executive_intelligence/*`, `app/routes/executive_intelligence.py`,
`docs/platform_architecture_manifest.yaml`, `tests/test_executive_reporting.py`, ADR-053.
