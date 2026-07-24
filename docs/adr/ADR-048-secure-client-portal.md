# ADR-048 — Secure Client & Household Portal: Governed External Composition with Delegated Actions

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Client Experience); Reliability / Operations; Security /
Authorization (RBAC + external identity ownership); Compliance; Business Operations Owner
(Michael Shelton). Authorized compliance reviewer: **Not yet designated** — production external access is
BLOCKED until recorded (see `docs/CLIENT_PORTAL_COMPLIANCE_GATE.md`).

## Context
The mandatory D.43 repository audit found a **substantial Client Portal already exists**: the
`app/portal/` package (`service.py`, `providers.py`, `signatures.py`), `app/routes/portal.py`, a distinct
`PortalPrincipal`, a middleware fork isolating `/portal*` and `/api/v1/portal*` from the staff principal,
15 `portal_*` tables (accounts, grant-based access, invitations with hashed tokens + expiry, auth tokens,
devices, sessions, append-only threads/messages/receipts/attachments, document requests, document
versions, notifications, signature requests), a grant-based scope resolver (`portal_scope`, never
`record.read_all`), and a pluggable external identity provider ABC (`PORTAL_IDENTITY_PROVIDERS`).

D.43 must therefore **harden and extend** that portal — not build a second one. Rebuilding any of CRM,
identity, documents, messaging, scheduling, tasks, workflow, policy, or the event bus would violate the
"no second system" invariant. The gaps the audit identified were: no declarative field-level visibility
control (external-visibility decisions risked scattering into templates), no consent / electronic-delivery
record, no masked financial summary, no offline identity provider for local/test, no internal-only portal
diagnostics, no portal governance checker, and no production compliance gate.

## Decision
Phase D.43 adds an **additive** hardening layer over the existing portal:

1. A single new persistent structure, `portal_consents` (migration `m4p5o6r7t8c9`) — a versioned consent /
   electronic-delivery ledger. No other tables; the portal otherwise extends in code.
2. A declarative **visibility registry** (`app/portal/visibility.py`) — the sole source of external-field
   decisions. Every externally-served field is registered (source, required grant permission, scope,
   masking, freshness, mutation owner, deep-link, compliance owner); internal-only / prohibited fields
   (advisor notes, assignments, compliance reasoning, AI briefs, work queue, audit, revenue, net worth)
   are declared explicitly so governance can assert they are never externally visible.
3. **Runtime + production gates** (`app/portal/gate.py`) — every external capability is gated through the
   governed Runtime Engine with a production-safe default of OFF and no environment fallback; external
   production access AND-gates on a compliance sign-off gate (`portal.production_signed_off`), blocked by
   default.
4. A masked, gated, fail-closed **financial summary** (`app/portal/financial.py`) that reads the
   authoritative `accounts` records, masks account numbers to last-4, and marks freshness.
5. A **consent** service (`app/portal/consent.py`) over `portal_consents`, delegating its trail to the
   authoritative audit ledger (references only).
6. A deterministic **local identity provider** (`app/portal/identity_local.py`) registered ONLY when not
   production-signed-off, so activation works offline in local/test without weakening production and never
   auto-links by email.
7. Internal-only **diagnostics** (`app/portal/diagnostics.py`) and a **governance** checker
   (`app/portal/governance.py`) enforcing the invariants read-only.
8. An **appointment-request** delegation (`app/portal/appointments.py`): the client requests via a governed
   secure-message thread; the advisor books the real meeting in the authoritative scheduling service.
9. New external surfaces (financial, preferences/consents, security center, scoped document download,
   appointment request) under `/portal/*`, and an **internal admin** surface under
   `/admin/client-portal/*` (staff fork, capability-guarded, record-scoped, no impersonation, activation
   token never returned).

The Runtime Engine remains the sole evaluator, the Runtime Policy Engine the sole decision engine, the
transactional outbox the sole event bus (D.43 adds NO outbox contracts), and the existing document /
communication / scheduling services the sole mutation layers. No new RBAC capability is seeded — the portal
is grant-based.

## Alternatives considered
- **Build a new portal / identity / messaging stack.** Rejected: directly violates "no second system" and
  discards a working, audited portal.
- **Add outbox contracts for portal lifecycle events.** Rejected: the portal already uses the append-only
  audit ledger; new contracts add projection/migration risk for no consumer.
- **Seed a `portal.*` RBAC capability set.** Rejected: the portal authorizes via `portal_access_grants`
  (grant-based); adding RBAC caps would create a parallel authorization model.
- **Serve financial data live from the portfolio domain.** Rejected: puts an external principal in the
  portfolio read path; instead a minimized, masked, freshness-marked read is served.
- **Scatter external-visibility decisions across templates.** Rejected: not testable; replaced by the
  declarative registry.

## Reasons for the decision
Extending the existing portal preserves every platform invariant while closing the real gaps
(visibility governance, consent, masking, offline identity, diagnostics, production gating). A single
declarative registry makes external exposure auditable; gates default-OFF with a compliance AND-gate make
accidental external exposure impossible without an explicit, recorded decision; delegation keeps all
mutation with the authoritative owners.

## Consequences

### Positive consequences
- External-visibility is declarative, testable, and centrally governed; forbidden fields cannot leak.
- Production external access is impossible until a compliance reviewer is recorded (fail-safe default).
- One new table, one migration (single head), no new capability, no new outbox contract — minimal surface.
- Portal failure is isolated behind the middleware fork and never breaks internal staff surfaces.

### Negative consequences and tradeoffs
- Appointment requests are delivered as secure messages rather than a bespoke scheduling-request store,
  which couples the request to the `messages` grant permission (documented in the registry).
- The masked financial summary is a minimized read, not a full portfolio view; richer external financials
  would need a further governed decision.

## Enforcement
`tests/test_secure_client_portal.py` (gates OFF by default + production blocked; registry has no forbidden
field externally visible; account masking; governance clean; consent ledger; financial gating/scope/mask;
local identity provider + production guard; diagnostics internal-only; appointment delegation; external
route auth fork; internal admin capability + record scope; token never returned). `app/portal/governance.py`
enforces the invariants at runtime. Route count, migration head, and schema registration are guarded by
`tests/test_platform_architecture.py` + `docs/platform_architecture_manifest.yaml`.

## Exceptions
The deterministic local identity provider is registered in non-production only, purely to make offline
activation testable; it can never verify a real external activation in production.

## Revisit conditions
Revisit when a real external identity provider is integrated, when richer external financial data is
requested, when a dedicated scheduling-request store is warranted, or if any portal lifecycle event gains
an internal consumer that would justify an outbox contract.

## References
- `app/portal/{visibility,gate,consent,financial,identity_local,diagnostics,governance,appointments,stats}.py`,
  `app/portal/service.py`, `app/routes/portal.py`, `app/routes/portal_admin.py`
- `app/database/portal_consent_tables.py`, `migrations/versions/m4p5o6r7t8c9_portal_consent_records.py`
- `docs/CLIENT_PORTAL_ARCHITECTURE.md`, `docs/CLIENT_PORTAL_SECURITY.md`,
  `docs/CLIENT_PORTAL_IDENTITY_AND_SCOPE.md`, `docs/CLIENT_PORTAL_VISIBILITY_REGISTRY.md`,
  `docs/CLIENT_PORTAL_DOCUMENTS.md`, `docs/CLIENT_PORTAL_REQUESTS.md`, `docs/CLIENT_PORTAL_MESSAGING.md`,
  `docs/CLIENT_PORTAL_OPERATIONS.md`, `docs/CLIENT_PORTAL_GOVERNANCE.md`,
  `docs/CLIENT_PORTAL_COMPLIANCE_GATE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_secure_client_portal.py`; relates to ADR-004, ADR-013, ADR-028, ADR-030, ADR-038 through
  ADR-047
