# Enterprise Risk Management, Internal Controls & Assurance Governance (Phase D.58)

`app/services/enterprise_risk/` is a governed, **read-only composition** that provides a unified, governed view
of enterprise risk posture — risk domains, control coverage, open findings, exceptions, incidents, remediation
workload, and assurance coverage — over the platform's **authoritative** risk / control / assurance owners. It
is **not** a second GRC platform, risk register, compliance engine, exception system, audit platform,
incident-management system, control-testing application, policy engine, or approval engine: **no new
capability, no new metric, no persistence, no mutation, no duplicated risk data, no migration** (single Alembic
head `n5s6u7p8v9w0`).

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Findings / exceptions / remediation | Exception Engine via Compliance Intelligence | `compliance_intelligence.supervisory_dashboard` | `compliance.supervise` |
| Security incidents / findings | Security incidents (D.25) | `security.incidents.metrics` | `security.view` |
| Identity & access | Security Operations (D.54) | `security_operations.security_summary` | `security.view` |
| Data quality / lineage | Data Governance (D.52) | `data_governance.governance_summary` | `governance.view` |
| Integration / sync failures | Integration Platform (D.53) | `integration.service.overview_metrics` / `integration.sync.metrics` | `integration.view` |
| Vendor / third-party risk | Vendor Management (D.56) | `vendor_management.vendor_summary` | `integration.view` |
| Continuity / backup gaps | Business Continuity (D.55) | `business_continuity.continuity_summary` | `observability.view` |
| Workflow escalations / approvals | Automation Orchestration (D.51) | `automation_orchestration.automation_summary` | `automation.view` |
| Documentation gaps | Document Intelligence (D.50) | `document_intelligence.document_summary` | `documents.view` |
| Licensing gaps | Insurance licensing | `insurance_licensing.list_licenses` | `integration.view` |
| Financial control / commissions | Financial Operations (D.57) + commission ledger | `financial_operations.firm_financial_summary` / `insurance_reporting.commission_report` | `analytics.executive` |
| Audit / assurance evidence | Runtime + Policy + audit log + CI | registries reference evidence | `observability.audit` |

## The not_configured domains (reported honestly)

The D.58 audit confirmed several domains have **no authoritative owner** in the platform today. Rather than
fabricate status, they are declared `not_configured` and reported honestly (the D.55 / D.56 / D.57 precedent):

- **Control testing / effectiveness** — no control-testing platform exists; every control family's
  `test_owner` is `not_configured`. The layer never invents control effectiveness.
- **Model & AI risk** and **Privacy risk** — no authoritative owner; declared `not_configured` risk domains.
- **Financial authorization** and **Change management** — no authoritative owner; declared `not_configured`
  control families.

## Registries, panels, dashboards

Three declarative registries — `ENTERPRISE_RISK_REGISTRY` (15 risk domains) + `CONTROL_REGISTRY` (20 control
families) + `ASSURANCE_REGISTRY` (15 assurance sources) — plus 24 panels and 8 dashboards (enterprise_risk,
compliance_risk, operational_risk, security_risk, third_party_risk, resilience_risk, financial_control_risk,
controls_assurance). See [ENTERPRISE_RISK_REGISTRY.md](ENTERPRISE_RISK_REGISTRY.md),
[CONTROL_REGISTRY.md](CONTROL_REGISTRY.md), and [ASSURANCE_REGISTRY.md](ASSURANCE_REGISTRY.md). Every dashboard
carries a generated timestamp, governing services, source inventory, explainable panels, deep links, and its
configured / not_configured domain lists.

## Risk scoring — no fabricated composite score

The layer does **not** implement an independent risk-scoring engine. Any displayed severity / status comes from
an authoritative source (exception severity `blocker/high/medium/low`, incident status, compliance
classification, security classification, operational-health calculation). The `enterprise_risk_posture` panel
is a **DERIVED** coverage summary — deterministic, authoritative inputs (configured vs not_configured domains +
authoritative open-signal counts), documented, and labeled `derived`. It **never** claims regulatory
certification and is never a fabricated composite risk score.

## Panels — counts, status, distributions, coverage only

Panels carry counts, status, severity distributions, or coverage summaries only. They **never** return
client-sensitive evidence, audit payloads, security details, credentials, tokens, bank information, tax-return
contents, document contents, or private incident narratives.

## Authorization

- Routes + dashboards admit a **supervisor OR an executive** (`compliance.supervise` / `analytics.executive`,
  via `require_any_capability`); diagnostics by `observability.audit`.
- Each **panel self-restricts** to its authoritative-source capability. A principal lacking the panel
  capability receives a `restricted` panel with `value = None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY owners that support per-entity record scope — firm-wide findings are
  never exposed to a client-scoped view.

## Runtime, governance, analytics, observability

Every surface is gated through the Runtime Engine (`enterprise_risk.enabled`, `controls_assurance.enabled`,
`risk_dashboards.enabled`, `risk_ai_summary.enabled`) **and** the runtime gate of every composed source, plus
the Policy Engine — **no environment bypass**. Governance (`validate_enterprise_risk()`) returns
`{ok, issue_count, findings}` and forbids persistence, mutation, any risk/finding/exception/incident/review/
approval/policy mutation, a second metrics registry, a fabricated composite risk score, and any fabricated
control-test owner — see [RISK_GOVERNANCE.md](RISK_GOVERNANCE.md). Four low-cardinality operational counters
(`risk_dashboards_composed`, `risk_panels_composed`, `risk_panel_failures`, `risk_authorization_failures`)
register into the **single** Analytics Registry. Internal diagnostics (`/enterprise-risk/diagnostics`,
`observability.audit`) report registry coverage, configured vs not_configured counts, panel availability, and
the governance summary.

## Surfaces

- **Advisor Workspace** — an **Enterprise Risk & Controls** panel (`ws["enterprise_risk"]`).
- **Client 360 / Household 360** — a **Risk & Controls** section (`compliance.supervise`): client-relevant,
  record-scoped signals (compliance exceptions, documentation gaps, data-quality issues, integration
  dependencies) from the per-entity owners.
- **Executive Dashboard** — an **Enterprise Risk & Assurance** dashboard reusing existing widgets
  (`compliance_workload`, `operational_health`, `runtime_health` — no new widget).
- **AI Assist** — summarizes risk-domain counts / severity distributions / control coverage / open findings /
  overdue reviews / assurance gaps / remediation workload / source-provided ratings, distinguishing confirmed
  facts, authoritative-source ratings, derived summaries, unavailable information, and not_configured domains.
  It **never** assigns risk, changes severity, accepts risk, closes findings, certifies controls, approves
  exceptions, acknowledges incidents, assigns remediation, certifies compliance, invents evidence, or infers
  regulatory approval.

**An absent finding never certifies compliance** — risk visibility reflects only what the authorized composed
owners currently report.

## Routes

`/enterprise-risk` (HTML) + `/api/v1/enterprise-risk/{dashboards, dashboard/{key}, summary, registry,
panel/{key}, metrics}` + `/enterprise-risk/diagnostics`.

See [ENTERPRISE_RISK_REGISTRY.md](ENTERPRISE_RISK_REGISTRY.md), [CONTROL_REGISTRY.md](CONTROL_REGISTRY.md),
[ASSURANCE_REGISTRY.md](ASSURANCE_REGISTRY.md), [RISK_GOVERNANCE.md](RISK_GOVERNANCE.md), and
[ADR-063](adr/ADR-063-enterprise-risk-management.md).

**Related (D.59):** the **Regulatory Readiness** layer (`/regulatory-readiness`) is the examination-readiness /
evidence-governance sibling of this layer — it composes the same authoritative owners (Compliance, Exception
Engine, Document Intelligence, Security, Vendor, Continuity, Financial) but frames them as *evidence for
regulatory obligations + certification sign-off*, and adds honest `not_configured` filing / examination areas
and blocked certifications. Two read-only composition views, neither a second store; operational readiness is
not regulatory certification. See [REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md) and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).
