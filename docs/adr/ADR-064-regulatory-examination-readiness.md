# ADR-064 — Enterprise Regulatory Examination Readiness, Evidence Governance & Supervisory Certification: A Read-Only Composition, Not a Second Compliance / Examination / Evidence / Filing / Certification Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Regulatory Readiness / Evidence Governance / Supervisory Certification);
Compliance / Supervision; Security / Authorization (RBAC ownership); Business Operations Owner
(Michael Shelton, business owner — **not** the regulatory certifier unless recorded reviewer authority
confirms it).

## Context
The mandatory D.59 audit inventoried every regulatory / evidence / certification / reviewer-authority owner:

* **Reviewer authority (the load-bearing gate)** — `compliance/reviewer_authority.py`
  `reviewer_authority(principal_id, *, rule_id, policy_gate, today=)` returns an active `reviewer_authorities`
  record or `None`. The catalog is **seeded EMPTY**, so it returns `None` for everyone → final approval stays
  blocked. "No reviewer is ever fabricated or inferred from a job-title string." `compliance/reviews.py`
  `record_decision` double-gates approval on `pending_review` + `validate_against_catalog` + a non-None
  `reviewer_authority(...)`; failure → `blocked_pending_authorized_reviewer`.
* **Rule-set catalog** — `compliance/rule_catalog.py` `RuleCatalog.from_registry()` (approval_status:
  draft/pending_assignment/pending_review/approved/…; `owner_name`/dates always `None`).
* **Evidence-owning reads** — Compliance Intelligence (`supervisory_dashboard`), the Exception Engine,
  Document Intelligence (`document_summary`, completeness/retention), Data Governance, Security Operations,
  Business Continuity, Vendor Management, Financial Operations, Insurance licensing (`list_licenses`,
  `list_ce`, `insurance.licensing.read`), records retention (`governance/retention.py`), audit logging, and
  the CI pipeline.
* **Genuinely absent (not_configured):** there is **no Form ADV / regulatory-filing owner, no filing
  acknowledgement store, no examination-case owner, no certification / attestation store, no policy-
  acknowledgement owner, and no evidence-export / examination-bundle owner.** Several obligation domains (IA
  registration, Form ADV, advertising review, custody, conflicts, complaints) have no authoritative owner.
  There are **no `regulatory.*` / `readiness.*` / `certification.*` / `evidence.*` / `examination.*`
  capabilities** — existing supervisory/executive/domain capabilities express the boundary.

There was **no regulatory-readiness composition layer** unifying these into named, firm-wide views of
obligation coverage, evidence availability/completeness/freshness, supervisory reviews, certification status,
filing readiness, and remediation evidence. Building a second compliance / examination / audit / document /
records / filing / certification / evidence-vault / approval / policy platform would violate the "no second
system" invariant and duplicate governed infrastructure.

## Decision
Phase D.59 adds a **governed, read-only regulatory-readiness composition layer**
(`app/services/regulatory_readiness/`) with NO new capability, NO new metric, NO persistence, and NO mutation:

1. Four declarative **registries** (`registry.py`): `REGULATORY_OBLIGATION_REGISTRY` (23 obligation domains —
   17 configured, 6 not_configured — each naming authoritative / evidence / review / exception / approval /
   filing / retention owner + accountable business owner + accountable compliance reviewer),
   `EVIDENCE_REGISTRY` (27 evidence classes — references evidence only), `EXAMINATION_REQUEST_REGISTRY` (22
   request categories — a readiness map only, never an active regulator request), and `CERTIFICATION_REGISTRY`
   (14 sign-off domains). Plus `PANEL_REGISTRY` (37 panels) and `READINESS_DASHBOARDS` (8 dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `ReadinessDashboard`, each explainable (a hard emit
   gate), carrying `derived` / `config_status` / `blocked` / `blocked_reason`; **counts, status, coverage,
   freshness, and age bands only, never sensitive evidence** (document contents, tax-return contents, client
   narratives, regulator correspondence, audit payloads, credentials, tokens, account numbers, license keys,
   PII, private incident narratives, evidence files).
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner;
   fail-closed; every panel self-restricts to its source capability. Filing / examination-correspondence /
   evidence-export panels are emitted `available=False` with `config_status='not_configured'` — honest, never
   a fabricated acknowledgement. The `derived_readiness_coverage` panel is a **DERIVED** operational-readiness
   summary (labeled `derived`) that **describes operational readiness, never regulatory certification, and
   never interprets an absence of findings as compliance** — there is no single "compliant" result.
4. **Certifications are blocked / `reviewer_not_confirmed` by construction.** Because `reviewer_authorities`
   is seeded empty, every certification carries `status = reviewer_not_confirmed`, `named_reviewer =
   reviewer_not_confirmed`, `review_date = not_configured`, and a `blocked_reason` — reviewer authority is
   never inferred, and business approval is never regulatory certification. Michael Shelton is the business
   owner but is not the regulatory certifier unless a recorded `reviewer_authorities` record confirms it.
5. The **examination-readiness engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `readiness_summary`, plus `client_evidence_readiness` / `household_evidence_readiness` (client-relevant,
   record-scoped evidence signals from ONLY the owners that support per-entity scope — never firm-wide
   examination posture). Dashboard-level authorization admits a **supervisor OR an executive**
   (`compliance.supervise` / `analytics.executive`, via `require_any_capability`).
6. **Runtime gates** (`regulatory_readiness.enabled`, `evidence_governance.enabled`,
   `certification_signoff.enabled`, `filing_readiness.enabled`, `readiness_ai_summary.enabled`) + the runtime
   gate of every composed source, **policy composition**, **analytics reuse** (four operational counters into
   the ONE Analytics Registry — no second registry), internal **diagnostics** (`observability.audit`), and a
   read-only **governance** checker. AI Assist may summarize readiness counts but never certifies compliance,
   claims regulator acceptance, approves a rule set, signs an attestation, infers reviewer authority, invents
   evidence, fabricates a filing acknowledgement, files a form, closes a finding, resolves an exception, or
   treats business approval as regulatory certification.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`.

## Alternatives considered
- **A second compliance / examination / audit / document / records / filing / certification / evidence-vault /
  approval / policy platform.** Rejected: the compliance/review/rule-catalog/reviewer-authority owners, the
  Exception Engine, Document Intelligence, and the D.52–D.58 layers are the authoritative owners; D.59 composes
  them. Governance forbids a second store and any evidence/review/approval/filing/retention mutation. Where no
  owner exists (filing, examination case, certification store, evidence export, several obligations), the entry
  declares `not_configured`.
- **A regulatory-readiness scoring engine that implies legal compliance.** Rejected: any status comes from an
  authoritative source; the one derived summary is deterministic, documented, labeled `derived`, describes
  operational readiness (not certification), keeps configured/not_configured/blocked visible, and never
  interprets an absent finding as compliance. There is no single "compliant/noncompliant" result.
- **Treating business approval as regulatory certification.** Rejected: certification requires a recorded
  `reviewer_authorities` record; until then the certification is blocked. Reviewer authority is never inferred
  from a role string, and the business owner is never the regulatory certifier by default.

## Reasons for the decision
Examination readiness needs one operational view; the compliance/review/reviewer-authority owners and the
D.50–D.58 layers already own every signal with the correct scoping. A read-only composition gives that view
with full explainability (source + deep link) while every review/approval stays owned by
`compliance/reviews.py`, every evidence artifact by its owner, and every certification stays blocked until an
authoritative reviewer is recorded. Emitting counts / status / coverage / freshness only keeps sensitive
evidence out of the layer entirely.

## Rationale for avoiding a second compliance, examination, evidence, filing, or certification platform
A second platform would require duplicated evidence, documents, records, findings, approvals, certifications,
filing acknowledgements, and retention state, plus its own reviewer-authority and readiness-scoring model —
duplicating governed infrastructure and creating reconciliation + drift + shadow-evidence risk, and (worst of
all) risking a fabricated certification or filing acknowledgement. Composing over the single compliance +
reviewer-authority owners keeps one source of truth and zero fabricated regulatory status.

## Consequences

### Positive consequences
- One firm-wide readiness / evidence / certification surface with no second platform.
- Certifications are honestly blocked / `reviewer_not_confirmed` until an authoritative reviewer is recorded;
  reviewer authority is never inferred; business approval is never regulatory certification.
- Filing, examination-case, evidence-export, and several obligations are reported `not_configured` — honest,
  never fabricated.
- Record scope + capability inherited from composed owners; a restricted panel leaks no value/count/freshness.
- Zero schema change; Advisor Workspace Regulatory Readiness panel + Client 360 / Household 360 Evidence &
  Supervisory Readiness sections + an Executive Regulatory Readiness & Evidence dashboard + AI summarize-only.
- Operational readiness is explicitly NOT regulatory certification; an absent finding is never compliance.

### Negative consequences and tradeoffs
- Every certification is blocked today (empty reviewer-authority catalog) — correct and honest, not a defect.
- Dashboards recompute per request (no persistence).
- Filing / examination / export coverage stays `not_configured` until an authoritative owner exists.

## Enforcement
`tests/test_regulatory_readiness.py` (four registries + completeness + duplicate-key prevention +
configured-owner validation + honest not_configured; all-certifications-blocked + reviewer-never-inferred +
business-approval-not-certification + blocked-states-why; explainable composition; authorization — unauthorized
→ None, unentitled panel restricted with no leaking metadata; runtime + policy gates; the firm summary +
record-scoped client/household rollups; analytics reuse; diagnostics; routes registered + capability-gated
supervisor-OR-executive; AI summarize-only; the no-fabricated-readiness-score + no-compliance-from-missing-
findings invariants; and the architecture invariants — no second platform, no evidence persistence/mutation, no
filing submission, no certification creation, no sensitive evidence).
`app/services/regulatory_readiness/governance.py` enforces the invariants at runtime. Route count, section
registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`compliance.supervise` / `analytics.executive`) the principal holds; each panel additionally self-restricts.
Client-scoped sections compose ONLY owners that support per-entity record scope (Document Intelligence,
Compliance Intelligence) — firm-wide examination posture is never exposed to a client-scoped view.

## Revisit conditions
Revisit when an authoritative regulatory-filing owner, examination-case owner, certification/attestation store,
evidence-export owner, or a `reviewer_authorities` record is added (compose it here, replacing the
`not_configured` / blocked entries — never a second platform).

## References
- `app/services/regulatory_readiness/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/regulatory_readiness.py`; `app/security/dependencies.py` (`require_any_capability`); Client 360
  section in `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Regulatory Readiness panel in `app/services/workspace/service.py`;
  Executive dashboard in `app/services/executive_intelligence/registry.py`; AI grounding in
  `app/services/ai_assist/context.py`; analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/compliance_intelligence/*`, `app/services/compliance/{reviews,reviewer_authority,rule_catalog}.py`,
  `app/services/exception_engine.py`, `app/services/document_intelligence/*`, `app/services/data_governance/*`,
  `app/services/security_operations/*`, `app/services/business_continuity/*`, `app/services/vendor_management/*`,
  `app/services/financial_operations/*`, `app/services/insurance_licensing.py`, `app/services/insurance_reporting.py`,
  `app/services/governance/retention.py`, `app/services/observability/*`, the Runtime + Policy engines
- `docs/REGULATORY_EXAMINATION_READINESS.md`, `docs/REGULATORY_OBLIGATION_REGISTRY.md`, `docs/EVIDENCE_REGISTRY.md`,
  `docs/EXAMINATION_REQUEST_REGISTRY.md`, `docs/CERTIFICATION_SIGNOFF_REGISTRY.md`,
  `docs/REGULATORY_READINESS_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_regulatory_readiness.py`; relates to ADR-007, ADR-008, ADR-047, ADR-052, ADR-055 through ADR-063
