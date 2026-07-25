# ADR-070 — Enterprise Identity, Access Governance & Authorization Intelligence: A Read-Only Composition, Not a Second Identity Provider / RBAC System / Authorization Engine

## Status
Accepted

## Date
2026-07-25

## Decision owners
Platform Architecture; Domain Owner (Identity / Access Governance / Authorization Intelligence); Security;
Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.65 audit inventoried every identity / authentication / authorization / role / capability /
permission owner the platform actually has, and the ones it does **not**:

* **Identity service (`app/services/identity.py`)** — the authoritative user / role / capability / team
  directory. `list_identity_data()` returns users (id, email, display_name, auth_subject, status [invited /
  active / disabled], mfa_enabled, last_login_at), teams, roles (code, name, system_role, active), and
  capabilities (code, description, sensitive). Mutations (`invite_user`, `set_user_status`, `assign_role`,
  `compose_role`, `add_team_membership`, `assign_record`) are the prohibited surface.
* **Security RBAC (`app/security/rbac.py`)** — role & capability resolution (`resolve_roles`,
  `resolve_capabilities`), authorization policies (`list_policies` → the `capability` policy), and the
  authorization event ledger (`emit_authorization_event`). Mutation: `register_policy`.
* **Security Authentication (`app/security/authentication.py`)** — provider registry (`list_providers` →
  `session` today; SSO / MFA are declared future Protocols). Mutation: `register_provider`. **Security service**
  — session management (`create_session`, `revoke_session`). **Policy engine (D.31)** — `registry.coverage`
  (decision-area coverage). **Security Authorization (`app/security/authorization.py`)** — record-scope
  decisions (`record_in_scope` over `record_assignments`). Mutation: `assign_record`.
* **Genuinely absent (not_configured):** there is **no SSO / external identity provider (only the session
  provider), no MFA-enforcement policy owner (the `mfa_enabled` flag is enrollment, not enforcement), no
  service-account owner, no platform API-key / token identity owner, no access-review / certification owner, no
  privileged access management (PAM), no segregation-of-duties / toxic-combination owner, no identity-lifecycle
  / JML provisioning workflow, and no password store** (authentication is external — claims / auth_subject).

No `identity_governance.enabled` / `authentication_landscape.enabled` / `authorization_landscape.enabled` /
`identity_ai_summary.enabled` gate existed. There was **no identity-governance composition layer** unifying
these into named, firm-wide views of identity inventory, role / capability coverage, authentication /
authorization landscape, policy coverage, and least-privilege indicators. Building a second identity provider,
authentication service, authorization engine, RBAC system, directory, SSO platform, policy engine, or
user-management platform would violate the "no second system" invariant and duplicate governed, security-critical
infrastructure — and would invite fabricated users, permissions, or access reviews the platform cannot
truthfully assert.

## Decision
Phase D.65 adds a **governed, read-only identity-governance composition layer**
(`app/services/identity_governance/`) with NO new capability, NO new metric, NO persistence, and NO mutation:

1. Five declarative **registries** (`registry.py`): `IDENTITY_REGISTRY` (8 — identity lifecycle / service
   accounts / provisioning not_configured), `ROLE_REGISTRY` (7 — birthright roles / role certification
   not_configured), `CAPABILITY_REGISTRY` (6 — segregation of duties / entitlement review not_configured),
   `AUTHENTICATION_REGISTRY` (7 — SSO / MFA enforcement / API-key auth / password management not_configured),
   and `AUTHORIZATION_REGISTRY` (7 — PAM / access-review certification not_configured), each naming
   authoritative owner + read surface + **prohibited mutation surface** + evidence source + capabilities +
   runtime gate + identity scope + deep links + config status. Plus `PANEL_REGISTRY` (33) and
   `IDENTITY_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `IdentityDashboard`, each explainable (a hard emit
   gate), carrying `derived` / `config_status`; **counts, coverage, status, and ratios only — never a password,
   secret, token, session ID, credential, authentication payload, raw identity (email / name / auth_subject),
   privileged-role membership, or user-level permission map.**
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Identity service, Security RBAC / Authentication / Authorization, the Policy engine); fail-closed; every
   panel self-restricts. SSO / MFA enforcement / API-key auth / password management / PAM / access-review
   panels are emitted `available=False` with `config_status='not_configured'` — honest, never fabricated.
   `executive_identity_posture`, `access_governance_readiness`, `least_privilege_indicators`, and the coverage /
   verification panels are **DERIVED** governance-readiness summaries (labeled `derived`).
4. The **identity-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `identity_summary`, plus `client_authorization_context` / `household_authorization_context` — which compose
   ONLY the current principal's OWN record-scope authorization decision (`record_in_scope`, the platform's
   actual already-made decision), **never another user's identity, a privileged role, a permission map,
   authentication metadata, or security configuration, and never an INFERRED authorization.** Dashboard-level
   authorization admits **an identity administrator OR an executive** (`identity.manage` / `analytics.executive`,
   via `require_any_capability`).
5. **Runtime gates** (`identity_governance.enabled`, `authentication_landscape.enabled`,
   `authorization_landscape.enabled`, `identity_ai_summary.enabled` — all distinct, no reused/unrelated gate, no
   runtime-variable fallback) + the runtime gate of every composed source, **policy composition**, **analytics
   reuse** (four operational counters into the ONE Analytics Registry — no second registry), internal
   **diagnostics** (`observability.audit`), and a read-only **governance** checker that forbids mutation,
   persistence, any identity / role / capability / policy / session mutation (`invite_user`, `set_user_status`,
   `assign_role`, `compose_role`, `register_policy`, `register_provider`, `create_session`, `assign_record`,
   `authenticate`, `authorize`, `grant`, `revoke`, …), a second metrics registry, and a fabricated identity /
   role / permission. AI Assist may summarize identity coverage / role governance / capability coverage /
   authentication readiness / authorization readiness but never authenticates, authorizes, assigns a role,
   recommends privilege escalation, fabricates a permission, invents an identity, or bypasses policy.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`. **A capability inventory is not a grant, a role definition is not an assignment, a provider
registration is not an authentication, and coverage is not certification** — every derived summary states this
explicitly.

## Alternatives considered
- **A second identity provider / authentication service / authorization engine / RBAC system / directory / SSO
  platform / policy engine / user-management platform.** Rejected: the Identity service, Security RBAC /
  Authentication / Authorization, and the Policy engine are the authoritative owners; D.65 composes them.
  Governance forbids a second store and any identity / role / capability / policy / session mutation. Where no
  owner exists (SSO, MFA enforcement, service accounts, API-key identities, access reviews, PAM, segregation of
  duties, identity lifecycle, password management), the entry declares `not_configured`.
- **An access-scoring engine that fabricates access reviews or permission grants.** Rejected: any figure comes
  from an authoritative source; the derived summaries are deterministic, labeled `derived`, keep configured /
  not_configured visible, and are governance-readiness summaries — never an authentication result, authorization
  decision, granted permission, or certified access review.
- **Exposing per-user permission maps / privileged-role membership at Client 360 / Household 360.** Rejected:
  that would leak internal identities and privileged roles; the record-scoped section composes ONLY the current
  principal's OWN in-scope decision (the platform's actual decision), never another user's identity or a
  permission map, and never infers authorization.
- **Reusing an existing gate (e.g. `security.enabled`).** Rejected: identity governance is a distinct concern;
  D.65 uses its own distinct master gates so the layer can be governed and disabled independently.

## Reasons for the decision
Identity / access governance needs one operational view; the Identity service, Security RBAC / Authentication /
Authorization, and the Policy engine already own every signal with the correct scoping and sensitivity controls.
A read-only composition gives that view with full explainability (source + deep link) while every user, role,
capability, policy, provider, session, and authorization decision stays owned by its security-critical owner.
Emitting counts / coverage / ratios only — and reporting the genuinely absent owners as `not_configured` — keeps
passwords, tokens, session IDs, raw identities, and fabricated access state out of the layer entirely.

## Rationale for avoiding a second identity provider, RBAC system, or authorization engine
A second identity provider / RBAC / authorization engine would require duplicated users, identities, roles,
capabilities, permissions, policy assignments, sessions, and authentication state, plus its own authentication
+ authorization decision path — duplicating governed, security-critical infrastructure and creating
reconciliation + drift + shadow-identity risk (a second source of authorization truth is a security
vulnerability), and tempting the system to assert access state it cannot truthfully know. Composing over the
single Identity + RBAC + Policy owners keeps one source of truth and zero fabricated identities.

## Consequences

### Positive consequences
- One firm-wide identity / access-governance surface with no second identity provider / authentication service /
  authorization engine / RBAC / directory / SSO / policy / user-management platform.
- Sensitivity + capability inherited from composed owners; a restricted panel leaks no value or count;
  Client 360 / Household 360 sections expose only the current principal's OWN authorization decision, never
  another identity, a privileged role, or a permission map.
- Zero schema change; Advisor Workspace Identity & Access Status panel + Client 360 / Household 360
  Authorization Context sections + an Executive Enterprise Identity & Access Governance dashboard (reusing
  existing widgets) + AI summarize-only.
- SSO / MFA enforcement / service accounts / API-key auth / access reviews / PAM / segregation of duties /
  identity lifecycle / password management reported `not_configured` — honest; posture is a governance-readiness
  summary, never a certified access review or an authorization decision.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence); firm-level identity reads load the directory each compose
  (acceptable for a governance surface, not a hot path).
- Coverage is bounded by the owners' read surface; a genuinely new identity signal (e.g. a real SSO owner) is
  added to the owning domain first, then surfaces here, replacing a `not_configured` entry.
- SSO, MFA enforcement, service accounts, API-key auth, access reviews, PAM, segregation of duties, identity
  lifecycle, and password management stay `not_configured` until an authoritative owner exists — deliberately,
  to avoid fabricated access state.

## Enforcement
`tests/test_identity_governance.py` (five registries + integrity + duplicate-key prevention [incl.
cross-registry] + configured-owner validation + honest not_configured + distinct non-colliding master gates;
explainable composition; authorization — unauthorized → None, unentitled panel restricted; runtime + policy
gates; the firm summary + record-scoped client / household authorization-context sections that expose only the
principal's own decision and never infer authorization or leak identities; analytics reuse; diagnostics; routes
registered + capability-gated identity-admin OR executive; AI summarize-only; the capability-inventory-is-not-a-
grant / role-definition-is-not-an-assignment / coverage-is-not-certification invariants; and the architecture
invariants — no second identity provider / RBAC / authorization engine, no persistence, no mutation, no
fabricated / unauthorized identity exposure). `app/services/identity_governance/governance.py` enforces the
invariants at runtime. Route count, section registries, ADR count, and migration head are guarded by
`tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + `tests/test_executive_reporting.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`identity.manage` / `analytics.executive`) the principal holds; each panel additionally self-restricts to its
authoritative-source capability. Client-scoped sections compose ONLY the current principal's OWN record-scope
authorization decision — no other identity, privileged role, or permission map is exposed at record scope, and
authorization is never inferred.

## Revisit conditions
Revisit when an authoritative SSO / external-IdP, MFA-enforcement, service-account, API-key-identity,
access-review / certification, PAM, segregation-of-duties, identity-lifecycle, or password-management owner is
added (compose it here, replacing the `not_configured` entries — never a second identity provider / RBAC /
authorization engine, never a fabricated access state).

## References
- `app/services/identity_governance/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/identity_governance.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Identity & Access Status panel in
  `app/services/workspace/service.py`; Executive dashboard in `app/services/executive_intelligence/registry.py`;
  AI grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/identity.py`, `app/security/{rbac,authentication,authorization,service}.py`,
  `app/services/policy/*`, the Runtime + Policy engines
- `docs/ENTERPRISE_IDENTITY_GOVERNANCE.md`, `docs/IDENTITY_REGISTRY.md`, `docs/ROLE_REGISTRY.md`,
  `docs/AUTHORIZATION_REGISTRY.md`, `docs/IDENTITY_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_identity_governance.py`; relates to ADR-002 (authorization), ADR-031 (runtime/policy), ADR-068,
  ADR-069
