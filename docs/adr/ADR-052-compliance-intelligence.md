# ADR-052 — Enterprise Compliance Intelligence & Supervisory Operations: A Read-Only Supervisory Composition, Not a Second Compliance System

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Compliance / Supervision); Reliability / Operations; Security /
Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.47 audit found the platform already has every authoritative compliance/supervisory process,
each owned by its domain:

* **Compliance review ledger + approval engine** — `app/services/compliance/reviews.py` (D.7): the
  double-gated submit → assign → decide approval flow over `compliance_reviews` / `compliance_decisions`,
  with `reviewer_authorities` (D.8) as the approval authority. `OPEN_STATUSES` /`DECISION_TYPES` are the
  authoritative status vocabulary. `rule_catalog.py` (D.6) governs the rules.
* **Exception engine** — `app/services/exception_engine.py`: the single authoritative exception owner
  (domains tax/benefits/insurance; a first-class `compliance` category; SLA state; append-only event
  ledger).
* **Audit hash-chain** — `app/security/audit.py` (write) + `app/security/audit_export.py` (read, gated on
  `audit.read`): THE single, immutable, tamper-evident audit log.
* **Annual review** (D.11), **producer licensing/CE** (`insurance_licensing.py`), and per-domain approvals
  (document/tax/workflow/governance) — each authoritative in its domain.

Two facts shaped the decision: (1) there is **no supervisory-operations layer** — no unified view over these
for a supervisor; and (2) there is **no distinct supervisor capability** — the `compliance` role is the
de-facto supervisor (it holds `record.read_all` + `audit.read` + `compliance.review.*`; the `advisor` role
holds none of these). Building a second compliance rules engine, approval system, or audit log would violate
the platform's "no second system" invariant and duplicate governed, regulated infrastructure.

## Decision
Phase D.47 adds a **governed, read-only supervisory composition layer** (`app/services/compliance_intelligence/`)
that composes the authoritative compliance information into one explainable supervisory workspace, with NO
new compliance/approval/audit engine:

1. Two declarative **registries** (`registry.py`): `SUPERVISORY_REGISTRY` (12 review types — owner,
   governing workflow, policy owner, required evidence, approval authority, escalation path, retention
   class, deep link, runtime gate) and `EXCEPTION_REGISTRY` (10 exception types — owner, severity,
   lifecycle, suppression, governing policy, escalation). Types with no backing data yet (advertising,
   complaint, trade, account-opening, communication, workflow reviews) are declared so the catalog is
   complete; the engine emits items only where an authoritative source supplies them.
2. Normalized read-models (`model.py`): `SupervisoryItem` + `ComplianceException`, each explainable
   (explanation + evidence + deep link) and reference-only.
3. Read-only, fail-closed **adapters** (`adapters/`): `reviews` (over `compliance.reviews`), `exceptions`
   (over the exception engine + portfolio cadence), `licensing` (over `insurance_licensing`). None submits,
   assigns, decides, opens, resolves, or writes the audit log.
4. The **supervisory engine** (`service.py`): `supervisory_dashboard`, `client_compliance`,
   `household_compliance` (aggregated), `compliance_summary`, and the separate advisor-facing
   `advisor_compliance_tasks`. Explainability enforced; dedup; suppression; prioritization; returns `None`
   when unauthorized or out of scope.
5. **A new read-only capability `compliance.supervise`** (migration `n5s6u7p8v9w0`, sensitive; granted to
   administrator + compliance, NOT advisor) — the explicit supervisor-vs-advisor boundary.
6. **Runtime gates** (`compliance.intelligence.enabled` + supervision/workspace flags), **policy
   composition**, low-cardinality **analytics** (4 metrics), internal **diagnostics**
   (`observability.audit`), and a read-only **governance** checker.

One migration seeds only the capability (no table). Single Alembic head maintained.

## Alternatives considered
- **A second compliance rules / approval engine.** Rejected: `compliance.reviews` is the authoritative,
  double-gated, audited approval engine; D.47 composes it. Duplicating it would fork regulated infrastructure.
- **A second audit log.** Rejected: the hash-chain audit log is authoritative; the supervisory layer reads
  via `audit_export` and never writes a second log.
- **A second exception store.** Rejected: `exception_engine` is the single authoritative owner; the
  supervisory exceptions are a composed, registered view (derived from cadence reads + engine rows).
- **Reuse `compliance.review.read` as the supervisor boundary (no migration).** Considered, but the spec's
  strong supervisor-vs-advisor separation mandate is better served by an explicit, testable capability;
  seeding one read-only capability is a minimal, well-established migration.

## Reasons for the decision
Supervisors need one operational view; the authoritative systems provide the facts. A read-only composition
gives that view with full explainability (governing policy + authoritative owner + evidence + deep link) and
a hard supervisor-vs-advisor boundary, while every approval, decision, and audit entry stays with its
authoritative owner. The explicit `compliance.supervise` capability makes "supervisory information never
leaks to an advisor" a testable invariant.

## Consequences

### Positive consequences
- One explainable supervisory workspace with no second compliance/approval/audit engine and no mutation.
- Supervisor-vs-advisor separation is explicit and enforced: supervisory findings require
  `compliance.supervise`; advisors get only the narrow governed compliance TASKS.
- A complete declarative catalog of review + exception types (populated where data exists).

### Negative consequences and tradeoffs
- One migration (a single read-only capability, no table) — the only schema touch; single head maintained.
- Several review types are declared-but-unpopulated (advertising, complaint, trade, …) until their
  authoritative source exists — the catalog is ahead of the data by design.

## Separation between supervision and execution
The layer strictly separates **supervision** (this read-only composition — it observes, explains, and
prioritizes) from **execution** (the authoritative approval / exception / audit / workflow engines that own
every mutation). The supervisory layer never approves, waives, resolves, or writes the audit log; it only
deep-links a supervisor to the authoritative surface where the action is performed under that surface's own
gates.

## Enforcement
`tests/test_compliance_intelligence.py` (registries; supervisor-vs-advisor authorization — advisor → None
everywhere, no supervisory facts; out-of-scope → None; explainable exception generation; every review
registered + deep-linked; advisor tasks governed-only; runtime + policy gates; Client 360 / Household 360
supervisor-only sections; Advisor Workspace advisor tasks; AI summarize-only; analytics; diagnostics;
governance; architecture invariants — no Table / no audit write / no submit/assign/decide/resolve / no
mutation). `app/services/compliance_intelligence/governance.py` enforces the invariants at runtime. Route
count, section registry, migration head, and the seeded capability are guarded by
`tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` + the manifest.

## Exceptions
The `licensing` adapter reads producer licensing/CE through `insurance_licensing` (which enforces
`insurance.licensing.read`); it fails closed to empty when the supervisor lacks that capability, leaving the
type registered-but-unpopulated.

## Revisit conditions
Revisit when an advertising / complaint / trade / account-opening review model is added (populate the
declared types), when a supervisor tier distinct from `compliance` is required, or if a supervisory
acknowledgement trail is genuinely justified (it would be owned by the audit log, never a second store).

## References
- `app/services/compliance_intelligence/*` (`registry.py`, `model.py`, `service.py`, `gate.py`, `stats.py`,
  `metrics.py`, `diagnostics.py`, `governance.py`, `adapters/reviews.py`, `adapters/exceptions.py`,
  `adapters/licensing.py`)
- `app/routes/compliance_intelligence.py`; C360 section in `app/services/client360/{registry,sections}.py`;
  HH360 section in `app/services/client360/household.py`; advisor tasks panel in
  `app/services/workspace/service.py`; AI grounding in `app/services/ai_assist/context.py`; analytics in
  `app/services/analytics/{sources,metrics}.py`
- `migrations/versions/n5s6u7p8v9w0_compliance_supervise_capability.py`
- Reuses `app/services/compliance/reviews.py`, `app/services/exception_engine.py`,
  `app/security/audit_export.py`, `app/services/annual_review.py`, `app/services/insurance_licensing.py`,
  `app/services/portfolio.py`, the D.46 recommendations layer
- `docs/COMPLIANCE_INTELLIGENCE.md`, `docs/SUPERVISORY_WORKSPACE.md`, `docs/SUPERVISORY_REGISTRY.md`,
  `docs/COMPLIANCE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_compliance_intelligence.py`; relates to ADR-004, ADR-006, ADR-007, ADR-008, ADR-017, ADR-028,
  ADR-030, ADR-046 through ADR-051
