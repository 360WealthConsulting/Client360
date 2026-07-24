# Client Portal Governance (Phase D.43)

`app/portal/governance.py` is a read-only checker that verifies the portal remains a **governed external
composition + delegated-action surface** and never becomes a second system. It returns a structured report
and never raises into normal use. See [`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Invariants enforced
1. **No second event bus / decision engine.** No portal module publishes to the outbox
   (`publish`/`publish_safe`), embeds a policy/decision engine, reads raw environment variables, or seeds a
   parallel RBAC capability. (The governance module itself is excluded from the source scan because it
   holds the detection patterns as string literals — it enforces by *checking*, not by *doing*.)
2. **No forbidden field externally visible.** Every externally-served registry field is `visible` /
   `conditional`; each declared `internal_only` / `prohibited` field (advisor notes, assignments,
   compliance reasoning, suitability findings, audit history, policy explanations, AI briefs, revenue,
   relationship graph, net worth, work queue) is present and never externally visible.
3. **Account numbers are masked.** `financial.account_number` carries the `account_last4` masking rule and
   the masking helper never emits a full number.
4. **Gates default OFF; production blocked.** Every gate defaults OFF (except `portal.mfa_required` ON),
   and with no runtime override the portal is not production-ready (the compliance sign-off gate blocks).
5. **Financial surface fails closed and never mutates portfolio.** It is gated on
   `portal.financial_summary_enabled` and contains no insert/update/delete against `accounts`.
6. **Consent writes are audited; diagnostics leaks no identifiers.** `consent.py` calls
   `write_audit_event`; `diagnostics.py` references no `account_id` / `person_id` / token / email.
7. **Local identity provider is production-guarded.** `identity_local.py` checks
   `portal.production_signed_off` before registering.

## How it runs
`validate_portal()` returns `{ok, issue_count, findings}`. It is surfaced through the internal diagnostics
(`app/portal/diagnostics.py`) on the admin surface (`observability.audit`) and asserted clean by
`tests/test_secure_client_portal.py::test_governance_clean`.

## Relationship to platform governance
The portal reuses the authoritative Runtime Engine (sole evaluator), Runtime Policy Engine (sole decision
engine), transactional outbox (sole event bus — D.43 adds NO contracts), audit ledger, and the
document/communication/scheduling services (sole mutation layers). Governance is the executable proof that
these boundaries hold.

## References
`app/portal/governance.py`, `app/portal/visibility.py`, `app/portal/gate.py`,
`tests/test_secure_client_portal.py`, ADR-048, `docs/CLIENT_PORTAL_VISIBILITY_REGISTRY.md`.
