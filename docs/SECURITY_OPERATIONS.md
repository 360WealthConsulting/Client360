# Security Operations (Phase D.54)

The **Security Operations** layer (`app/services/security_operations/`) is a governed, **read-only
composition** that gives security leadership a single governed operational view of platform security posture
— authentication, authorization, identity governance, MFA, sessions, audit, and security posture — **without**
building a second IAM platform, identity provider, RBAC engine, authentication system, authorization engine,
MFA provider, audit-logging platform, or SIEM. Every number is composed on read from an **authoritative
owner**; the layer owns no persistence and never authenticates a user, creates a user, revokes a session,
issues a token, resets a password, or alters a permission. **Panels carry counts + status only — never a
password, secret, token, session ID, or authentication payload.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Security posture / policies / providers / incidents | `app/services/security/` — `service.overview_metrics`, `providers`, `policies`, `incidents` |
| Identity inventory (users / teams / roles / capabilities) | `app/services/identity.py` — `list_identity_data` |
| RBAC (roles / capabilities) | `app/security/rbac.py` + the Identity catalog |
| MFA coverage | `users.mfa_enabled` via `analytics.sources.security_mfa_enabled_user_count` |
| Authentication / authorization / session events | `app/security/audit_export.py` — `read_audit_events` (hash-chain audit log) |
| Audit integrity | `app/security/audit_export.py` — `verify_integrity` |
| Record-level access (per client) | `app/security/object_security.py` — `resolve_assignments` |

See [IDENTITY_REGISTRY.md](IDENTITY_REGISTRY.md) for the identity classes,
[SECURITY_REGISTRY.md](SECURITY_REGISTRY.md) for the security domains, and
[SECURITY_GOVERNANCE.md](SECURITY_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `IDENTITY_REGISTRY` (6 identity classes), `SECURITY_REGISTRY` (6
  security domains), `PANEL_REGISTRY` (21 panels), `SECURITY_DASHBOARDS` (7 dashboards).
- `model.py` — `PanelResult` + `SecurityDashboard`. A panel is emitted only if `is_explainable` (explanation
  + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking `security.view` gets a `restricted` panel, never its value; audit panels additionally require
  `audit.read`). Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `security_summary`,
  `client_security`, `household_security`.
- `gate.py` — runtime gates (`security.enabled`, `identity.enabled`, `audit.enabled`) + policy composition.
  No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never secrets/tokens/payloads.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including auth/audit mutation-call tells.

## Dashboards

`authentication`, `authorization`, `identity_governance`, `mfa`, `sessions`, `audit`, `security_posture`.
Each carries a generated timestamp, governing services, source inventory, explainable panels, and deep links
to the authoritative security-owner surface. Dashboards are gated by `security.view`; each panel additionally
self-restricts to `security.view` (audit panels compose the audit log, which enforces `audit.read`).

## Surfaces

- **HTTP** (`app/routes/security_operations.py`, gated by `security.view`; diagnostics by
  `observability.audit`): `/security-operations` (HTML), `/api/v1/security-operations/dashboards`,
  `/dashboard/{key}`, `/summary`, `/registry`, `/panel/{key}`, `/metrics`, `/security-operations/diagnostics`.
- **Advisor Workspace** — the Security Operations panel (`security_summary`).
- **Client 360 / Household 360** — the `security_access` section (`client_security` / `household_security`,
  record-level access grants; counts only).
- **Executive Dashboard** — a `security_operations` dashboard (composed from existing D.48 widgets; no new
  widget), navigation deep-linking to `/security-operations`.
- **AI Assist** — summarizes security counts only; it never authenticates, authorizes, elevates permissions,
  issues tokens, resets passwords, disables MFA, or bypasses security.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no auth action, no outbox publication, no audit write, no second store. Every security count comes
from an authoritative security owner; every dashboard panel is explainable and deep-links to its
authoritative surface. Enforced by `app/services/security_operations/governance.py` and
`tests/test_security_operations.py`. See [ADR-059](adr/ADR-059-security-operations.md).
