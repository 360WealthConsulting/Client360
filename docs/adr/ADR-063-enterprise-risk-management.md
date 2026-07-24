# ADR-063 — Enterprise Risk Management, Internal Controls & Assurance Governance: A Read-Only Composition, Not a Second GRC / Risk / Incident / Control-Testing Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Enterprise Risk / Internal Controls / Assurance); Compliance /
Supervision; Security / Authorization (RBAC ownership); Reliability / Operations; Business Operations Owner
(Michael Shelton).

## Context
The mandatory D.58 audit inventoried every risk / control / exception / incident / audit / approval / assurance
owner in the platform. Findings:

* **Exceptions / findings / remediation** — `exception_engine.py` is the *single authoritative exception
  owner* (severity `blocker/high/medium/low`; status `open/acknowledged/in_progress/waiting/escalated/reopened`
  → `resolved/cancelled`), surfaced by `exception_reporting` and by **Compliance Intelligence**
  (`compliance_intelligence.supervisory_dashboard`, `compliance.supervise`).
* **Compliance reviews / approvals** — `compliance/reviews.py` (decision types `approved / …`,
  `compliance.review.read`); **Security incidents / findings** — `security/incidents.py`
  (`metrics → open_incidents/open_findings/pending_exceptions`, `security.view`) + Security Operations (D.54).
* **Data quality** — Data Governance (D.52, `governance.view`); **integration / sync** — the Integration
  Platform + Integration Hub (D.53, `integration.view`); **resilience** — Business Continuity (D.55,
  `observability.view`); **third-party** — Vendor Management (D.56, `integration.view`/`security.view`);
  **financial control** — Financial Operations (D.57, `analytics.view`/`analytics.executive`);
  **documentation** — Document Intelligence (D.50, `documents.view`); **workflow** — Automation Orchestration
  (D.51, `automation.view`); **licensing** — Insurance licensing (`insurance.licensing.read`); **audit** —
  `observability` audit log (`observability.audit`).
* **Genuinely absent (not_configured):** there is **no control-testing / control-effectiveness owner**, **no
  model/AI-risk owner**, **no privacy-risk owner**, **no financial-authorization owner**, and **no
  change-management owner**. There are **no `risk.*` / `controls.*` / `assurance.*` capabilities** — existing
  capabilities express the required access boundary.

There was **no enterprise-risk composition layer** unifying these into named, firm-wide views of risk posture,
control coverage, and assurance evidence. Building a second GRC platform, risk register, compliance engine,
exception system, audit platform, incident-management system, control-testing application, policy engine, or
approval engine would violate the "no second system" invariant and duplicate governed infrastructure.

## Decision
Phase D.58 adds a **governed, read-only enterprise-risk composition layer**
(`app/services/enterprise_risk/`) with NO new capability, NO new metric, NO persistence, and NO mutation:

1. Three declarative **registries** (`registry.py`): `ENTERPRISE_RISK_REGISTRY` (15 risk domains — regulatory,
   compliance, operational, cybersecurity, identity/access, data-governance, integration, vendor/third-party,
   business-continuity, financial-control, client-service, technology-lifecycle, model/AI, privacy,
   records-management — each naming authoritative / signal / exception / incident / remediation / assurance
   owner + capabilities + runtime gate + deep links + config status), `CONTROL_REGISTRY` (20 control families,
   each naming objective + authoritative / evidence / monitoring / **test (always not_configured)** / approval
   / remediation owner), and `ASSURANCE_REGISTRY` (15 assurance sources, each referencing evidence + scope +
   frequency + reviewer role + approval artifact), plus `PANEL_REGISTRY` (24 panels) and `RISK_DASHBOARDS`
   (8 dashboards: enterprise_risk, compliance_risk, operational_risk, security_risk, third_party_risk,
   resilience_risk, financial_control_risk, controls_assurance).
2. Normalized read-models (`model.py`): `PanelResult` + `RiskDashboard`, each explainable (explanation +
   source + deep link, a hard emit gate), carrying `derived` + `config_status` flags; **counts, status,
   severity distributions, and coverage summaries only, never sensitive evidence** (client narratives, audit
   payloads, security details, credentials, tokens, bank info, tax-return contents, document contents, private
   incident narratives).
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner.
   Fail-closed; every panel self-restricts to its source capability (compliance panels `compliance.supervise`,
   security panels `security.view`, data `governance.view`, integration `integration.view`, resilience
   `observability.view`, financial `analytics.executive`, documentation `documents.view`, workflow
   `automation.view`). The `enterprise_risk_posture` panel is a **DERIVED coverage summary** (configured vs
   not_configured domains + authoritative open-signal counts) — labeled `derived`, never a certified composite
   risk score or regulatory rating.
4. The **risk-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `risk_summary`, plus `client_risk_controls` / `household_risk_controls` (client-relevant, record-scoped
   signals from ONLY the owners that support per-entity scope — never firm-wide findings). Every dashboard
   carries generated timestamp, governing services, source inventory, explainable panels, deep links, and its
   configured / not_configured domain lists. Dashboard-level authorization admits a **supervisor OR an
   executive** (`compliance.supervise` / `analytics.executive`).
5. **Runtime gates** (`enterprise_risk.enabled`, `controls_assurance.enabled`, `risk_dashboards.enabled`,
   `risk_ai_summary.enabled`) + the runtime gate of every composed source, **policy composition**, **analytics
   reuse** (four operational counters into the ONE Analytics Registry — no second registry), internal
   **diagnostics** (`observability.audit`), and a read-only **governance** checker that forbids mutation,
   persistence, any risk / finding / exception / incident / review / approval / policy mutation
   (`raise_exception`, `resolve`, `record_decision`, `create_incident`, `write_audit`, …), a fabricated
   composite risk score, and any fabricated control-test owner. AI Assist may summarize risk counts but never
   assigns risk, changes severity, accepts risk, closes findings, certifies controls, approves exceptions,
   acknowledges incidents, assigns remediation, certifies compliance, invents evidence, or infers regulatory
   approval.

No migration, no new table, no new capability (reuses `compliance.supervise` + `analytics.executive` +
`security.view` + `governance.view` + `integration.view` + `observability.view` + `automation.view` +
`documents.view` + `observability.audit`), no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`.

## Alternatives considered
- **A second GRC platform / risk register / compliance engine / exception system / audit platform /
  incident-management system / control-testing application / policy engine / approval engine.** Rejected: the
  Exception Engine, Compliance Intelligence, Security incidents, Data Governance, the Integration Platform,
  Business Continuity, Vendor Management, Financial Operations, and audit logging are the authoritative owners;
  D.58 composes them. Governance forbids a second store and any risk/finding/incident mutation. Where no owner
  exists (control testing, model/AI risk, privacy risk, financial authorization, change management), the entry
  declares `not_configured` rather than inventing status.
- **An independent risk-scoring engine.** Rejected: any displayed severity / status / score comes from an
  authoritative source (exception severity, incident status, compliance classification, security
  classification, operational-health calculation). The layer reports domain counts + status/severity
  distributions + coverage indicators, and one DERIVED, deterministic, documented, labeled coverage summary
  that never claims regulatory certification. It never fabricates a composite risk score.
- **A new `risk.*` capability.** Rejected: the audit proved existing supervisory/executive capabilities
  express the required access boundary; a new capability is created only when the audit proves existing ones
  cannot.

## Reasons for the decision
Enterprise risk posture needs one operational view; the Exception Engine, Compliance Intelligence, Security,
and the D.52–D.57 layers already own every signal with the correct scoping. A read-only composition gives that
view with full explainability (source + deep link) while every finding stays owned by the Exception Engine,
every incident by Security, every review/approval by Compliance, and every evidence artifact by its owner. Deep
links (never inline mutation) route the operator to the authoritative surface to act. Emitting counts / status
/ distributions / coverage only keeps sensitive evidence out of the layer entirely.

## Rationale for avoiding a second GRC, risk, incident, exception, or control-testing platform
A second GRC / risk / incident / exception / control-testing platform would require duplicated risks, findings,
exceptions, incidents, controls, approvals, and remediation state, plus its own scoring + workflow model —
duplicating governed infrastructure and creating reconciliation + drift + shadow-register risk, with no benefit
the composition does not already provide. Composing over the single Exception Engine + the authoritative domain
owners keeps one source of truth for every risk signal and zero duplicated risk data.

## Consequences

### Positive consequences
- One firm-wide risk / controls / assurance surface with no second GRC platform, risk register, compliance
  engine, exception system, audit platform, incident manager, control-testing application, policy engine, or
  approval engine.
- Record scope + capability are inherited from the composed owner reads; a principal lacking a panel capability
  sees a restricted panel that leaks no value or metadata.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Enterprise Risk & Controls panel + Client 360 / Household 360 Risk & Controls sections + an
  Executive Enterprise Risk & Assurance dashboard (reusing existing widgets) + AI summarize-only, all from one
  layer.
- Honest governance: control testing, model/AI risk, privacy risk, financial authorization, and change
  management are reported `not_configured` — never fabricated; an absent finding never certifies compliance.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Coverage is bounded by the owners' read surface; a genuinely new risk signal is added to the owning domain
  first, then surfaces here.
- Control effectiveness is not asserted until an authoritative control-testing owner exists — reported
  `not_configured`, honest rather than green-washed.

## Enforcement
`tests/test_enterprise_risk.py` (three registries + completeness + duplicate-key prevention + configured-owner
validation + honest not_configured + control-testing not_configured; explainable dashboard composition;
authorization — unauthorized → None, unentitled panel restricted with no leaking metadata; runtime + policy
gates; the firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry;
diagnostics; routes registered + capability-gated (supervisor OR executive); AI summarize-only; and the
architecture invariants — no second GRC/risk/incident/exception/control-testing engine, no mutation, no
fabricated composite risk score, no sensitive evidence, every configured panel references an authoritative
owner, every dashboard deep-links, every derived summary labeled).
`app/services/enterprise_risk/governance.py` enforces the invariants at runtime. Route count, section
registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global signal reads that do not self-gate (security incident metrics, integration metrics) are exposed
only within dashboards whose required capability (`compliance.supervise` / `analytics.executive`) the principal
holds; each panel additionally self-restricts to its source capability. Client-scoped sections compose ONLY
owners that support per-entity record scope (Compliance Intelligence, Document Intelligence, Data Governance,
the Integration Hub) — firm-wide findings are never exposed to a client-scoped view.

## Revisit conditions
Revisit when an authoritative control-testing / control-effectiveness owner (or a model/AI-risk, privacy-risk,
financial-authorization, or change-management owner) is added to the platform (compose it here, replacing the
`not_configured` entries — never a second GRC platform), or if a materialized risk read-model is ever justified
(it would be a governed projection, never a second risk register).

## References
- `app/services/enterprise_risk/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/enterprise_risk.py`; `app/security/dependencies.py` (`require_any_capability`); Client 360
  section in `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Enterprise Risk & Controls panel in
  `app/services/workspace/service.py`; Executive Enterprise Risk & Assurance dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/compliance_intelligence/*`, `app/services/exception_engine.py`,
  `app/services/exception_reporting.py`, `app/services/security/incidents.py`,
  `app/services/security_operations/*`, `app/services/data_governance/*`, `app/services/integration/*`,
  `app/services/integration_hub/*`, `app/services/business_continuity/*`, `app/services/vendor_management/*`,
  `app/services/financial_operations/*`, `app/services/document_intelligence/*`,
  `app/services/automation_orchestration/*`, `app/services/insurance_licensing.py`,
  `app/services/insurance_reporting.py`, `app/services/observability/*`, the Runtime + Policy engines
- `docs/ENTERPRISE_RISK_MANAGEMENT.md`, `docs/ENTERPRISE_RISK_REGISTRY.md`, `docs/CONTROL_REGISTRY.md`,
  `docs/ASSURANCE_REGISTRY.md`, `docs/RISK_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_enterprise_risk.py`; relates to ADR-047, ADR-052, ADR-054 through ADR-062
