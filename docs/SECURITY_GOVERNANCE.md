# Security Operations Governance (Phase D.54)

`app/services/security_operations/governance.py` is a read-only checker that verifies the Security Operations
layer stays a **composition** over the authoritative security owners and never becomes a second IAM platform,
identity provider, RBAC engine, authentication system, authorization engine, MFA provider, audit-logging
platform, or SIEM. It returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_security_operations()` is surfaced through the internal diagnostics endpoint
(`/security-operations/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second IAM / no auth action.** No module calls an authentication / identity / session / RBAC / audit
   **mutation** — `authenticate_claims(`, `create_session(`, `revoke_session(`, `invite_user(`,
   `set_user_status(`, `assign_role(`, `compose_role(`, `add_team_membership(`, `assign_record(`,
   `write_audit_event(`, `audit_denied(`, `bootstrap_administrator(`, `resolve_principal(`, `reset_password(`,
   `rotate_secret(`, `register_policy(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every identity class declares authoritative + authentication
   + authorization owner + runtime gate + deep links; every security domain declares category + authoritative
   + provider + monitoring owner + runtime gate + deep links; every dashboard declares owner + audience +
   runtime gate + navigation + panels + required capabilities + governing services, and references only
   registered panels; every panel declares owner + source + deep link + explainability + permission; all
   registry keys are unique.
5. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
6. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## No passwords, secrets, tokens, session IDs, or authentication payloads, ever

Panels and summaries carry **counts + status only** — never passwords, secrets, tokens, session IDs, or
authentication payloads. The audit reads deliberately exclude `ip_address` / `user_agent` (the audit owner
strips them); credential/secret references are composed as counts only. Diagnostics and analytics counters
are low-cardinality aggregates about the layer itself.

## Authorization & least privilege

- Security routes are gated by `security.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`security.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to `security.view`: a principal lacking it receives a `restricted` panel with
  `value = None` — never leaked. **Audit panels additionally require `audit.read`** (enforced inside
  `audit_export`); a `security.view` holder without `audit.read` sees them unavailable (fail-closed).
- All composed reads inherit the record scope + capability checks of their authoritative owner (the security
  service's scope, the audit log's `audit.read` gate).

## AI Assist boundary

AI Assist may **summarize** security counts (authentication health, authorization status, MFA coverage, audit
summaries, security posture) — fact class `DERIVED`, counts only, deep links only. It **never** authenticates,
authorizes, elevates permissions, issues tokens, resets passwords, disables MFA, or bypasses security — every
fact comes from a composed section/summary.

## Enforcement

`tests/test_security_operations.py` exercises the registries, explainable composition, authorization (`None`
+ restricted), gate/policy behavior, the analytics-counter reuse, diagnostics, the routes (registered +
capability-gated), AI summarize-only, and the architecture invariants (no second IAM / RBAC / MFA / audit
platform, no duplicated identities, no mutation, security reads composed from the security owners, every
dashboard deep-links, every summary names an authoritative owner). Route count, section registries, ADR
count, and the single migration head are guarded by `tests/test_platform_architecture.py`,
`tests/test_client360_workspace.py`, `tests/test_household360_workspace.py`,
`tests/test_architecture_decision_records.py`, and the manifest.

See [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md), [IDENTITY_REGISTRY.md](IDENTITY_REGISTRY.md),
[SECURITY_REGISTRY.md](SECURITY_REGISTRY.md), and [ADR-059](adr/ADR-059-security-operations.md).
