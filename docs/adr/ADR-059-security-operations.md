# ADR-059 — Enterprise Security Operations & Identity Governance: A Read-Only Composition, Not a Second IAM/Security Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Security / Identity & Access Management); Reliability / Operations;
Security / Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.54 audit found the platform already owns every authoritative security owner —
authentication, authorization, RBAC, MFA, sessions, audit logging, and identity management:

* **Security metadata domain** (`app/services/security/`) — `service.overview_metrics(principal)` +
  per-module `metrics(principal)` (policies, providers, secrets, incidents) + list reads. Owns security
  metadata (policies / providers / secrets / incidents), reuses `app.security.service` / `authorization` /
  `audit`.
* **Identity** (`app/services/identity.py`) — `list_identity_data()` (users / teams / roles / capabilities,
  firm-wide). **RBAC foundation** (`app/security/rbac.py`) — `resolve_capabilities`, `resolve_roles`.
  Authorization (`app/security/authorization.py`) — `record_in_scope`, `accessible_person_ids`, and
  `object_security.resolve_assignments` (record-level access grants).
* **Authentication** — OIDC login (`app/routes/auth.py`), `security.service.create_session` /
  `resolve_principal`, `AuthenticationMiddleware`. **MFA** — the single `users.mfa_enabled` flag, surfaced by
  the analytics counter `security_mfa_enabled_user_count`. **Sessions** — `user_sessions` (no list read;
  session activity surfaces through the audit log).
* **Audit** — the authoritative hash-chain audit log `app/security/audit_export.py`
  (`read_audit_events`, `verify_integrity`, `build_export`; gated by `audit.read`).

There was **no security-operations composition layer** unifying these into named, firm-wide views of
authentication, authorization, identity governance, MFA, sessions, audit, and security posture. Building a
second IAM platform, identity provider, RBAC engine, authentication system, authorization engine, MFA
provider, audit-logging platform, or SIEM would violate the "no second system" invariant and duplicate
governed, gated, security-critical infrastructure.

## Decision
Phase D.54 adds a **governed, read-only security-operations composition layer**
(`app/services/security_operations/`) with NO new metrics, NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `IDENTITY_REGISTRY` (6 user classes — advisor, employee,
   service-account, system, external, client identities — each naming authoritative / authentication /
   authorization owner + runtime gate + deep links) and `SECURITY_REGISTRY` (6 security domains —
   authentication, MFA, sessions, audit, policies, monitoring — each naming authoritative / provider /
   monitoring owner + category + runtime gate), plus `PANEL_REGISTRY` (21 panels) and `SECURITY_DASHBOARDS`
   (7 dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `SecurityDashboard`, each explainable (explanation +
   source + deep link, a hard emit gate) and reference-only; **counts + status only, never a password /
   secret / token / session ID / authentication payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Security metadata domain, the Identity owner, the RBAC foundation, the hash-chain audit log, the MFA
   flag via Analytics). Fail-closed (a missing `audit.read` yields an unavailable audit panel, never an
   exception); every panel self-restricts to `security.view`.
4. The **security-operations engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `security_summary`, plus `client_security` / `household_security` (record-level access grants via
   `object_security.resolve_assignments`). Every dashboard carries generated timestamp, governing services,
   source inventory, explainable panels, and deep links. Dashboard-level authorization (`security.view`).
5. **Runtime gates** (`security.enabled` + `identity.enabled` + `audit.enabled`), **policy composition**,
   **analytics reuse** (four operational counters registered into the ONE Analytics Registry — no second
   registry), internal **diagnostics** (`observability.audit`), and a read-only **governance** checker that
   forbids mutation, persistence, and any authentication / identity / session / RBAC / audit mutation call
   (`authenticate_claims`, `create_session`, `revoke_session`, `invite_user`, `assign_role`,
   `write_audit_event`, `resolve_principal`, …). AI Assist may summarize security counts but never
   authenticates, authorizes, elevates permissions, issues tokens, resets passwords, disables MFA, or
   bypasses security.

No migration, no new table, no new capability (reuses `security.view` + `audit.read` + `observability.audit`),
no new metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second IAM platform / identity provider / RBAC engine / authentication system / MFA provider /
  audit-logging platform / SIEM.** Rejected: the Security metadata domain, Identity, the RBAC foundation, and
  the hash-chain audit log are the authoritative owners; D.54 composes them. Governance forbids a second
  store, identity system, and any auth/audit mutation call.
- **A second metrics registry.** Rejected: security counts come from the security owners' `metrics()` reads
  and the audit log; the layer registers only operational counters (about itself) into the single Analytics
  Registry — the house style.
- **Persisting composed security state / a SIEM.** Rejected: dashboards are a deterministic function of the
  authoritative data at read time; a store would be a security warehouse to reconcile, and the layer must
  never hold identities, sessions, or audit events (those are the owners' job).

## Reasons for the decision
Security leadership needs one operational security-posture view; the security owners already own every number
with the correct scoping and the correct sensitivity gate. A read-only composition gives that view with full
explainability (source + deep link) while every identity stays owned by Identity, every role/capability by
the RBAC foundation, every session/authentication by the security service, and every audit event by the
hash-chain log. Deep links (never inline auth action) route the operator to the authoritative surface to act.
Emitting counts + status only keeps passwords, secrets, tokens, session IDs, and authentication payloads out
of the layer entirely.

## Rationale for avoiding a second IAM/security platform
A second IAM / SIEM would require duplicated identities + sessions + audit events, a parallel RBAC + MFA
model, its own authentication + token issuance, and its own access model — duplicating governed, gated,
security-critical infrastructure and creating credential-sprawl, split-identity, and audit-integrity risk,
with no benefit the composition does not already provide. Composing over the single security owners keeps one
source of truth for every identity and role, one authentication system, one hash-chain audit log, and zero
copied secrets.

## Consequences

### Positive consequences
- One firm-wide security-operations surface with no second IAM, RBAC, MFA, audit platform, or SIEM.
- Record scope + capability + audit sensitivity are inherited from the composed security reads; a
  non-`security.view` principal sees restricted panels, never values, and never a secret/token/payload; audit
  panels additionally require `audit.read`.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Security Operations panel + Client 360 / Household 360 Security & Access sections + an
  Executive Security Operations dashboard (reusing existing widgets) + AI summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Session activity and authentication/authorization events are surfaced through the audit log (no dedicated
  session-list read exists) — a bounded recent window, flagged as such.
- Per-client / per-household security rollups compose record-assignment access grants — a count rollup, not a
  full access-control-list surface.
- The layer's coverage is bounded by the security owners' read surface; a genuinely new security signal is
  added to the owning security service first, then surfaces here.

## Enforcement
`tests/test_security_operations.py` (two registries + single ownership; explainable dashboard composition;
authorization — unauthorized → None, unentitled panel restricted never valued; runtime + policy gates; the
firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry; diagnostics;
routes registered + capability-gated; AI summarize-only; and the architecture invariants — no second IAM /
RBAC / MFA / audit platform, no duplicated identities, no mutation, security reads composed from the security
owners, every dashboard deep-links, every summary names an authoritative owner).
`app/services/security_operations/governance.py` enforces the invariants at runtime (including auth/audit
mutation-call tells). Route count, section registries, and migration head are guarded by
`tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + the manifest.

## Exceptions
Audit reads require `audit.read` (enforced inside `audit_export`), so a `security.view` holder without
`audit.read` sees audit-derived panels as unavailable rather than restricted — fail-closed. Firm-global reads
that do not self-gate (Identity `list_identity_data`, security overview) are exposed only within dashboards
whose required capability (`security.view`) the principal holds; each panel additionally self-restricts to
`security.view`.

## Revisit conditions
Revisit when a dedicated session-list / login-history read is added (compose it, never a second session
store), when a service-account/system-identity concept is introduced (add it to Identity, then surface here),
or if a materialized security read-model is ever justified (it would be a governed projection, never a second
IAM).

## References
- `app/services/security_operations/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/security_operations.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Security Operations panel in
  `app/services/workspace/service.py`; Executive Security Operations dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/security/*` (`service.py`, `providers.py`, `policies.py`, `incidents.py`),
  `app/services/identity.py`, `app/security/{rbac,authorization,audit_export,object_security}.py`
- `docs/SECURITY_OPERATIONS.md`, `docs/IDENTITY_REGISTRY.md`, `docs/SECURITY_REGISTRY.md`,
  `docs/SECURITY_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_security_operations.py`; relates to ADR-025, ADR-046 through ADR-058
