# ADR-004 — Server-side authorization and record scope

## Status
Accepted

## Date
2026-07-21

## Decision owners
Security / Authorization; Platform Architecture; Domain Owner (each domain).

## Context
Client360 handles regulated, client-confidential data across many domains and surfaces. Any
reliance on client-side hiding, or on composition layers "trusting" that a caller is allowed to
see a datum, would create authorization bypasses — especially for routes that sit outside the
shared scope middleware.

## Decision
Authorization **must** be enforced **server-side** on every request, combining a **capability**
check with **record scope**:
- Routes **enforce** a required capability via `require_capability(code)` dependencies; shared
  middleware additionally maps route families to a capability (GET→`.read`, mutation→`.write`).
- Record scope is enforced by services **scope-first**: `record_in_scope(principal, entity_type,
  id, *, write)` for `person`/`household`/`organization`; `organization_in_scope` and
  `accessible_person_ids` are team/advisor-book aware.
- Middleware `RECORD_PATH` covers `^/(people|households)/(\d+)`; families like `/organizations`,
  `/benefits`, `/insurance`, `/tax`, `/documents`, `/work` map to a capability via the RULES table.
- Route families **outside** shared scope middleware — `/advisor-work`, `/annual-review`,
  `/business-owner`, `/compliance` — **must** enforce scope **inside their services**.
- Composition workspaces **must** independently preserve each consumed domain's authorization
  (never a bypass), and **may** grant business visibility only through a **validated ownership
  relationship** or `organization_in_scope` — never a name match (URL-enumeration protection).

Template hiding **is not** security.

## Alternatives considered
1. **Middleware-only scope for all routes.** Rejected: novel prefixes (`/advisor-work`,
   `/annual-review`, `/business-owner`) match no rule; relying on middleware alone would leave
   them ungated.
2. **Composition layer trusts the person-level gate and skips per-domain checks.** Rejected: that
   is exactly the bypass this ADR forbids; each domain's capability + scope must still hold.

## Reasons for the decision
Defense in depth: capability + record scope, enforced in services, is robust to new route
prefixes and to composition. It keeps authorization coherent regardless of which surface reads a
datum.

## Consequences
### Positive consequences
- No client-side or template-only enforcement; new prefixes are safe by construction.
- Business visibility cannot be enumerated by guessing ids.

### Negative consequences and tradeoffs
- Services must remember to call scope helpers (scope-first) — enforced by tests and review.
- A sensitive section may be *restricted* for an otherwise-authorized person (ADR-005), which is
  intentional.

## Enforcement
- `app/security/dependencies.py` (`require_capability`), `app/security/authorization.py`
  (`record_in_scope`, `organization_in_scope`, `accessible_person_ids`),
  `app/security/middleware.py` (RECORD_PATH + RULES).
- In-service scope: `annual_review.compose_workspace`, `business_owner.compose_person_workspace`
  / `business_in_scope`. Enumeration test: `tests/test_business_owner.py`
  (`test_business_scope_blocks_enumeration`).

## Exceptions
`administrator`/`record.read_all` holders bypass record scope by design (documented in the role
model). No other exception is approved.

## Revisit conditions
If a new route family is added, this ADR requires it to either match a middleware rule or enforce
scope in-service; revisit only to add new scope entity types.

## References
- `app/security/{dependencies,authorization,middleware}.py`
- `app/services/{annual_review,business_owner}.py`
- `docs/PLATFORM_ARCHITECTURE.md` §9 (Authorization), §10 (Record-scope model)
- `docs/AUTHORIZATION.md`, `docs/OBJECT_SECURITY.md`, `tests/test_business_owner.py`
