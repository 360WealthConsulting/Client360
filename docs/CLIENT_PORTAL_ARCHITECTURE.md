# Client Portal Architecture (Phase D.43)

The Client & Household Portal is a **governed external composition + delegated-action surface** over the
authoritative Client360 platform. It is NOT a second system: every read reuses an authoritative service and
every mutation delegates to the owning service. D.43 hardens and extends the portal that already existed
(`app/portal/`, `app/routes/portal.py`, 15 `portal_*` tables); it does not rebuild it. See
[`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Boundary and isolation
- **Middleware fork.** `app/security/middleware.py` routes any `/portal*` or `/api/v1/portal*` request to
  the portal branch: it resolves a `PortalPrincipal` from the portal session (never the staff principal),
  applies portal security headers + CSP, and audits external mutations. Internal staff surfaces are on a
  separate branch with capability RBAC. A portal failure never reaches internal surfaces (failure
  isolation).
- **Two principals.** `PortalPrincipal(account_id, person_id, email, display_name)` is distinct from the
  staff `Principal`. Portal sessions and staff sessions are cryptographically isolated — a token that
  resolves on one branch resolves to `None` on the other.
- **Internal admin is on the staff fork.** `/admin/client-portal/*` deliberately does NOT start with
  `/portal`, so it stays on the staff principal + capability RBAC.

## Authoritative source map (reuse, never rebuild)
| Portal concern | Authoritative owner |
| --- | --- |
| Identity / activation / sessions | `app/portal/service.py` + `PORTAL_IDENTITY_PROVIDERS` |
| Access scope | `portal_scope` over `portal_access_grants` (grant-based; never `record.read_all`) |
| Secure messaging | `app/portal/service.py` threads/messages (append-only) |
| Documents (read/download) | `document_platform` / `app/services/documents.py` |
| Document requests / uploads | `app/portal/service.py` request flow |
| Financial summary (masked read) | `accounts` records via `app/portal/financial.py` |
| Appointments (read) | scheduling → `timeline_events` (`calendar_event`) |
| Appointments (request) | delegated to secure messaging; advisor books in `scheduling.service` |
| Consent / electronic delivery | `portal_consents` via `app/portal/consent.py` |
| Feature enablement | Runtime Engine (`runtime.consumption.feature_enabled`) via `app/portal/gate.py` |
| Audit | authoritative audit ledger (references only) |
| Events | transactional outbox (D.43 adds NO new contracts) |

## D.43 additive modules
- `visibility.py` — declarative field-level visibility registry (the single source of external-exposure
  decisions). See [`CLIENT_PORTAL_VISIBILITY_REGISTRY.md`](CLIENT_PORTAL_VISIBILITY_REGISTRY.md).
- `gate.py` — runtime + production compliance gates, all default OFF.
- `consent.py` — consent / electronic-delivery ledger over `portal_consents`.
- `financial.py` — masked, gated, fail-closed financial summary.
- `identity_local.py` — deterministic local/test identity provider (production-guarded).
- `diagnostics.py` — internal-only, low-cardinality health.
- `governance.py` — read-only invariant checker.
- `appointments.py` — appointment-request delegation.
- `stats.py` — in-process low-cardinality counters.

## Invariants
Runtime Engine = sole evaluator. Runtime Policy Engine = sole decision engine. Transactional outbox = sole
event bus. Document/communication/scheduling services = sole mutation layers. The portal never exposes
internal notes, assignments, compliance reasoning, audit logs, advisor work, AI briefs, work-queue, or
hidden workflow state (enforced by the visibility registry + `governance.py`).

## References
`app/portal/*`, `app/routes/portal.py`, `app/routes/portal_admin.py`, `app/security/middleware.py`,
`docs/platform_architecture_manifest.yaml`, `tests/test_secure_client_portal.py`, ADR-048, and
`docs/adr/README.md`.
