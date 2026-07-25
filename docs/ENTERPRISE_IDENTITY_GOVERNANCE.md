# Enterprise Identity, Access Governance & Authorization Intelligence (Phase D.65)

`app/services/identity_governance/` is a governed, **read-only composition** that provides a unified, governed
view of the firm's identity and access posture — identity inventory, role coverage, capability coverage,
permission mappings, authentication coverage, authorization coverage, policy coverage, least-privilege
indicators, access-governance readiness, and identity gaps. It is **not** a second identity provider,
authentication service, authorization engine, RBAC system, directory, SSO platform, policy engine, or
user-management platform: **no new capability, no new metric, no persistence, no mutation, no duplicated
identity / authorization data, no migration** (single Alembic head `n5s6u7p8v9w0`).

> These are **governance-readiness summaries**, never an authentication result, an authorization decision, a
> granted permission, or a certified access review. **A capability inventory is not a grant, a role definition
> is not an assignment, a provider registration is not an authentication, and coverage is not certification.**

> **No secrets, ever.** No password, secret, token, session ID, credential, authentication payload, raw
> identity (email / name / auth_subject), privileged-role membership, or user-level permission map is ever
> carried in a panel — counts, coverage, status, and ratios only.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| User directory (status, MFA) | Identity service | `list_identity_data` (users) | `identity.manage` |
| Roles (active, system) | Identity service / Security RBAC | `list_identity_data` (roles), `resolve_roles` | `identity.manage` |
| Capabilities (sensitive) | Identity service / Security RBAC | `list_identity_data` (capabilities), `resolve_capabilities` | `identity.manage` |
| Teams | Identity service | `list_identity_data` (teams) | `identity.manage` |
| Authentication providers | Security Authentication | `list_providers` | `identity.manage` |
| Authorization policies | Security RBAC | `list_policies` | `identity.manage` |
| Policy-engine coverage | Policy engine (D.31) | `registry.coverage` | `identity.manage` |
| Record-scope authorization | Security Authorization | `record_in_scope` | `identity.manage` |

## The not_configured domains (reported honestly)

The D.65 audit confirmed several domains have **no authoritative owner** and are declared `not_configured`,
never fabricated: **SSO / external identity providers** (only the `session` provider exists), **MFA
enforcement** (the `mfa_enabled` flag is enrollment, not enforcement), **service accounts**, **API-key / token
identities**, **access reviews / certification**, **privileged access management (PAM)**, **segregation of
duties / toxic combinations**, **identity lifecycle / JML provisioning**, and **password management**
(authentication is external — claims / auth_subject; no password store is owned by the platform).

## Registries, panels, dashboards

Five declarative registries — Identity (8) + Role (7) + Capability (6) + Authentication (7) + Authorization (7)
= 35 domain entries (22 configured, 13 not_configured) — plus 33 panels and 8 dashboards (identity_overview,
authentication_landscape, authorization_landscape, role_governance, capability_coverage, policy_coverage,
executive_identity_governance, identity_readiness). See `IDENTITY_REGISTRY.md`, `ROLE_REGISTRY.md`, and
`AUTHORIZATION_REGISTRY.md`.

Each panel is **explainable** (explanation + source + deep link — a hard emit gate) and self-restricts to its
authoritative-source capability. A principal lacking the panel capability sees `restricted` (never the value or
count). A panel whose owner is `not_configured` is emitted `available=False` with
`config_status='not_configured'` — fail closed. **Derived** panels (executive_identity_posture,
access_governance_readiness, least_privilege_indicators, the coverage / verification panels) carry
`derived=True` and describe governance readiness only.

## Engine + surfaces

`service.py` exposes `compose_dashboard`, `list_dashboards`, `get_panel`, `identity_summary`, and the
record-scoped `client_authorization_context` / `household_authorization_context`. Dashboard-level authorization
admits **an identity administrator OR an executive** (`identity.manage` / `analytics.executive`, via
`require_any_capability`).

- **Advisor Workspace** — an Identity & Access Status panel (`identity_summary` in `workspace/service.py`),
  self-gated to `identity.manage`.
- **Client 360 / Household 360** — an Authorization Context section that composes ONLY the current principal's
  OWN record-scope authorization decision (`record_in_scope`, the platform's actual already-made decision).
  **No internal identities, privileged roles, permission maps, authentication metadata, or security
  configuration are ever exposed, and authorization is never inferred.**
- **Executive** — an Enterprise Identity & Access Governance dashboard reusing existing widgets
  (compliance_workload + operational_health; **no new widget**).
- **AI Assist** — summarize-only grounding: AI may summarize identity coverage / role governance / capability
  coverage / authentication readiness / authorization readiness and state the current principal's own in-scope
  decision, but never authenticates, authorizes, assigns a role, recommends privilege escalation, fabricates a
  permission, invents an identity, or bypasses policy.

## Runtime gates, policy, analytics, diagnostics

- **Runtime gates** (`gate.py`): `identity_governance.enabled`, `authentication_landscape.enabled`,
  `authorization_landscape.enabled`, `identity_ai_summary.enabled` — all distinct (no reused/unrelated gate),
  evaluated through `runtime.consumption.feature_enabled` with **no runtime-variable bypass**. The layer also
  respects the runtime gate of every composed source.
- **Policy** composition alongside RBAC (`policy_ok(area)`), never bypassing either.
- **Analytics** (`metrics.py` → `analytics/{sources,metrics}.py`): four low-cardinality operational counters
  (identity_dashboards_composed, identity_panels_composed, identity_panel_failures,
  identity_authorization_failures) registered into the ONE Analytics Registry — no second metrics store.
- **Diagnostics** (`diagnostics.py`): an `observability.audit`-only report (gate snapshot, registry coverage,
  panel availability, governance findings).
- **Governance** (`governance.py`): `validate_identity_governance()` returns `{ok, issue_count, findings}` and
  never raises — see `IDENTITY_GOVERNANCE.md`.

## What it never does

No authentication, no authorization decision, no policy mutation, no role assignment, no user creation, no
password management, no session creation, no identity lifecycle mutation, no persistence, no second metrics
registry, no fabricated user / identity / role / permission / provider / session / capability / policy
assignment / access review, and no exposure of any password, secret, token, session ID, credential, raw
identity, privileged-role membership, or user-level permission map.

## References
- Code: `app/services/identity_governance/*`, `app/routes/identity_governance.py`,
  `app/templates/identity_governance/home.html`
- Surfaces: `app/services/workspace/service.py`, `app/services/client360/{registry,sections}.py`,
  `app/services/client360/household.py`, `app/services/executive_intelligence/registry.py`,
  `app/services/ai_assist/context.py`, `app/services/analytics/{sources,metrics}.py`
- Tests: `tests/test_identity_governance.py`; ADR-070; `docs/IDENTITY_REGISTRY.md`, `docs/ROLE_REGISTRY.md`,
  `docs/AUTHORIZATION_REGISTRY.md`, `docs/IDENTITY_GOVERNANCE.md`
