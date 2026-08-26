# Client Portal Compliance Gate (Phase D.43)

> **STATUS: BLOCKED — external production access is NOT authorized.**
> No accountable compliance reviewer has been recorded. Until this artifact records a named reviewer and a
> sign-off decision, `portal.production_signed_off` MUST remain OFF and the portal MUST NOT serve external
> client data in production.

This artifact is the control record that gates external production access to the Client Portal. It is
required by [`ADR-048`](adr/ADR-048-secure-client-portal.md). Local and test environments proceed behind
the disabled runtime gate; production external access is blocked by default.

## The gate
- Runtime gate: `portal.production_signed_off` (default **OFF**, evaluated via the governed Runtime Engine,
  no environment fallback — `app/portal/gate.py`). Since migration `b5d82e04c917` it is seeded as a runtime
  **feature flag** and is genuinely governed; before that it existed only as a configuration item, so
  `gate()` always returned the hard-coded default and the sign-off decision could not take effect.
  `portal.mfa_required` was corrected the same way. It remains **OFF**, and this artifact remains BLOCKED.
- Code gate: `production_ready()` returns true only when `portal.enabled` **AND**
  `portal.production_signed_off` are both on. All external financial/document/messaging/appointment gates
  are independently OFF by default as well.
- Effect while blocked: implementation and local/test proceed, but production never serves external client
  data because the sign-off gate is off.

## Evidence on file: controlled synthetic activation test (COMPLETED, PASS)
A controlled single-identity activation test was executed in production against SHA
`bbbd3bc5d4cea115b0b6de5baa1c01ccc6dada4d` / Alembic `b5d82e04c917` and passed every step it exercised:
synthetic local IdP verification, MFA verification, invitation acceptance, account activation,
authentication-subject linkage, session creation, resolution and revocation, runtime gate rollback, and
inactivation of the synthetic person and grant. Details and retained artifacts are recorded in
[`CLIENT_PORTAL_OPERATIONS.md`](CLIENT_PORTAL_OPERATIONS.md).

**What this evidence is:** proof that the *technical* activation path works — the gates, the governed
sign-off flag, the invitation/MFA/session lifecycle, and a clean rollback to the closed state.

**What this evidence is NOT.** Four things must stay distinct, and this test only speaks to the first two:

1. **Technical portal activation validation** — demonstrated.
2. **Synthetic local IdP validation** — demonstrated, using the deterministic offline provider.
3. **Production external identity-provider readiness** — *not* demonstrated. No real external IdP exists
   or was exercised. A passing synthetic local IdP test is not evidence about a production IdP.
4. **Regulatory/compliance authorization for real-client access** — *not* granted. That is this artifact's
   decision, and it remains BLOCKED.

### Defect found and remediated during the item-#6 design audit
Designing the production verification for pre-condition #6 uncovered a security defect in
`GET /api/v1/portal/documents/{document_id}/download`: it read the canonical staff `documents` table and
authorized on `documents.person_id` + `require_scope` alone. That table has no `client_visible` column,
so a client holding a documents grant could download any non-archived canonical document filed against
their **own** person — internal work product included — and the download was not audited. The `88305f5`
isolation fix had corrected document *listing* but not this route, and no test exercised the route.

Both download versions now delegate to the single authoritative vault policy, every resource-level denial
returns an identical generic 404, and HTTP-level regression coverage exists
(`tests/test_portal_download_route_isolation.py`, which fails against the pre-fix code).

This is **remediation plus automated proof, not the production verification** pre-condition #6 requires.
Criterion #6 therefore remains `[ ]`.

### Visibility remediation, task 1 of 2 (criterion #3)
The design audit for pre-condition #3 found that several portal read surfaces returned whole database
rows, so internal fields reached external clients. Task 1 remediated six of them: conversation listing,
thread messages, document requests, notifications, dashboard meetings, and the v1 profile endpoint (which
returned `principal.__dict__`). Each now returns an explicit fixed-key projection, covered by exact-key
tests, a hand-written projection→registry mapping, and recursive HTTP-level disclosure tests.

Two caller-level permission omissions were fixed at the same time: `client_threads` and
`client_document_requests` resolved scope without a `permission`, so a grant with `messages: False` or
`documents: False` still listed that data. Both now pass the grant permission and have negative
regression tests.

**Task 2 remains outstanding** — `client_tasks` (raw `workflow_steps` including `definition_snapshot`),
`portal_intakes` and `portal_returns` (internal identifiers and reuse of the staff return detail), plus
four further callers resolving scope without a permission. The open set is pinned in
`tests/test_portal_visibility_isolation.py::REMAINING_TASK_2_FINDINGS`.

**Criterion #3 therefore remains `[ ]`.** It cannot be considered until task 2 lands and the whole portal
read-surface inventory passes. Note also that `validate_portal()` being clean proves the registry is
self-consistent — it does **not** prove response reachability; the disclosure tests carry that claim.

### Visibility remediation, task 2A (criterion #3)
Task 2A closed every task and tax finding: `client_tasks` no longer returns raw `workflow_steps`
(including `definition_snapshot`, the internal workflow definition), and `portal_intakes` /
`portal_returns` now use portal-only projections instead of the staff intake/return detail structures.
The staff services are unchanged — a regression test asserts staff `return_detail` still carries the
internal reviewer note and id that the portal projection omits. Five callers (`client_tasks`,
`client_action_needed`, `client_action_detail`, `portal_intakes`, `portal_returns`) now resolve scope
with `permission="tasks"`, the permission the tax mutation paths already required.

**Still open (task 2B), and the reason criterion #3 cannot close:** billing and engagement have no
independent per-client feature decision at the service boundary. Billing is gated only by middleware —
bypassable by calling `client_invoices` directly — and additionally returns raw `invoices` rows and
reuses the staff invoice detail. Engagement has only a firm-wide runtime flag, no `client_can` at all.
Resolving these needs an authorization-model decision (no `billing`, `payroll` or `timeline` grant
permission exists), not a projection change. Payroll, by contrast, already qualifies as base
relationship scope **plus** an explicit entity-scoped `client_can(FEATURE_KEY, organization_id=...)` on
its only portal read path.

The open set is pinned in `tests/test_portal_visibility_isolation.py::REMAINING_VISIBILITY_FINDINGS`.

**Criterion #3 therefore remains `[ ]`.**

### Visibility remediation, task 2B1 — billing (criterion #3)
Billing was authorized only by the middleware rules in `portal_gate._RULES`; calling `client_invoices`
or `client_invoice_detail` directly bypassed the per-client feature decision entirely. It also served raw
rows — the whole `invoices` row from the list, and the staff `invoice_detail` structure (raw line items
and raw `payments` rows carrying the processor reference, metadata and the recording staff id) from the
detail.

Each client billing read now enforces its Core feature at the service boundary — `billing` for the
invoice list, agreements and payment history; `invoice_view` for invoice detail, matching the more
specific route rule — and every response is an explicit fixed-key projection. Middleware remains
defence-in-depth. The staff `invoice_detail` is unchanged, proved by a test asserting it still carries
the internal note, bill-to identifiers and processor reference the portal must never show.

**Engagement is now the sole remaining criterion-#3 finding:** `portal_engagement` has no per-client
`client_can` decision, only the firm-wide `portal.timeline.enabled` runtime flag, and no Core feature in
the catalog semantically represents client timeline/engagement. Adding one is a catalog decision that has
not been taken, so the surface is deliberately left unremediated rather than mapped onto an unrelated
feature.

**Criterion #3 therefore remains `[ ]`.**

### Visibility remediation, final task — engagement + scope architecture (criterion #3)
`portal_engagement` had no per-client authorization at all: only the firm-wide
`portal.timeline.enabled` runtime flag, and no `portal_gate` rule mapped its paths, so not even
middleware `client_can` covered it. Its rows were `Interaction.to_dict()`, the internal model
serialization shared with staff surfaces.

A new Core feature `client_timeline` (firm-**enabled**, so existing portal clients keep the surface
exactly as today) supplies the missing per-client decision. It is enforced inside `portal_engagement`
before anything is queried, mapped in `portal_gate._RULES` for `/portal/engagement` and
`/api/{v1/}portal/engagement`, and the rows are now an explicit projection. The runtime flag remains a
separate firm-wide kill switch. `Interaction.to_dict()` is unchanged for its staff callers.

The scope contract was then split. `portal_scope(account_id, *, permission)` now **requires** a
permission — no default, no `None` — and `portal_base_scope(account_id)` names identity-only resolution
explicitly. Exactly six callers use the base helper, each because an independent authorization layer
decides access afterwards: the dashboard identity bootstrap, the staff entitlement preview, `client_can`'s
own relationship resolution (where requiring a permission would be circular), and the payroll, billing and
engagement surfaces that each follow it with an explicit `client_can`. An AST-based guard
(`tests/test_portal_scope_architecture.py`) classifies every call site exactly once and fails on an
unclassified caller, a missing permission, or a caller in both classes.

`REMAINING_VISIBILITY_FINDINGS` is now **empty**, and the assertion that it stays empty is retained so a
future finding cannot vanish silently.

**No pre-condition below is closed by this test.** Each one is either about the production IdP, a human
or legal review, or a surface that stayed gated OFF for the duration of the test. The acceptance criteria
are recorded verbatim and are not reinterpreted to fit the evidence obtained.

## Pre-conditions for sign-off (to be verified by the reviewer)
- [ ] A real external identity provider is integrated and registered (the deterministic local provider is
      non-production only).
      *Remains:* integrate and register a real external IdP. Only the deterministic local provider exists
      (`app/portal/identity_local.py` is the sole `PORTAL_IDENTITY_PROVIDERS.register` call site). The
      controlled test used exactly that provider, which the criterion excludes by its own wording.
      `portal.local_identity_provider_enabled` is back OFF and must stay OFF before real-client onboarding.
- [ ] MFA enforcement confirmed (`portal.mfa_required` ON) end-to-end with the production IdP.
      *Remains:* the end-to-end confirmation **with the production IdP**. MFA was verified end to end
      against the synthetic local provider (invitation acceptance rejects unverified MFA), and
      `portal.mfa_required` is ON — but the criterion names the production IdP, which does not exist yet.
      Note also the recorded follow-up in `CLIENT_PORTAL_OPERATIONS.md`: the `portal.mfa_required` gate
      does not currently drive the unconditional MFA checks.
- [ ] Visibility registry reviewed; no `internal_only` / `prohibited` field is externally reachable;
      governance report clean.
      *Remains:* the reviewer's registry walkthrough, and confirmation that no `internal_only` /
      `prohibited` field is externally reachable. The governance reports are clean
      (`validate_portal()` and runtime `validate()` both report zero findings), but the controlled test
      ended at session revocation and exercised no document or field-level read, so reachability was not
      demonstrated in production.
- [ ] Account-number masking and financial-summary minimization confirmed.
      *Remains:* everything. `portal.financial_summary_enabled` was OFF for the whole test and no
      financial surface was exercised.
- [ ] Consent / electronic-delivery records reviewed and legally sufficient.
      *Remains:* everything. This is a legal review; the controlled test produced no consent records.
- [ ] Scope resolver verified default-deny; household access does not grant every member; out-of-scope
      returns 404 without disclosure.
      *Note (added after the production verification):* the production run of this criterion remains
      valid — the scope **resolver** was verified default-deny and behaved correctly. A separate,
      caller-level omission was discovered afterwards during the criterion-#3 audit: some callers invoked
      `portal_scope` without a `permission`, so the resolver was never asked to enforce the grant.
      `client_threads` and `client_document_requests` were fixed in task 1; `client_tasks`,
      `client_action_needed`, `client_action_detail`, `portal_intakes` and `portal_returns` in task 2A.
      All are regression-covered. The billing and engagement surfaces remain a separate
      authorization-model question, not evidence that the resolver itself failed. The original
      production test did not exercise these later-discovered caller omissions, and the recorded
      resolver evidence stands. The base-scope/feature-scope split now makes a silent permission
      omission impossible: `portal_scope` requires an explicit permission and identity-only resolution
      must name itself, with an AST guard over every call site.
      *Remains:* production verification with more than one identity. The test used a single self grant
      with `portal.household_enabled` OFF, so no cross-client or household-expansion case was exercised in
      production. Automated coverage exists (cross-household isolation, forged ids, per-permission
      default-deny) but the reviewer verification is not satisfied by a one-identity test.
- [ ] Audit coverage of external mutations confirmed; no tokens/PII in logs or diagnostics.
      *Remains:* the reviewer's confirmation. The test did generate external mutations (acceptance,
      session create/revoke) and the raw invitation token was shown once on the console only — never
      stored, logged or committed, with only its SHA-256 hash in the database — but no audit-coverage
      review of those mutations was recorded as evidence.
- [ ] Failure isolation confirmed (portal failure never affects internal surfaces).
      *Remains:* everything. No portal failure was induced during the test, so isolation was not observed.
- [ ] Data-retention, incident-response, and client-notification procedures approved.
      *Remains:* everything. Organizational approval; not addressed by a technical test.

## Sign-off record (to be completed)
| Field | Value |
| --- | --- |
| Accountable compliance reviewer | **NOT YET DESIGNATED** |
| Decision | **BLOCKED** |
| Date | — |
| Runtime change authorized | **No** — `portal.production_signed_off` remains OFF |
| Notes | Controlled synthetic activation test PASSED (see above and `CLIENT_PORTAL_OPERATIONS.md`). It validates the technical activation path only and closes **none** of the pre-conditions above. Production portal is closed: `production_ready()` False, `portal.production_signed_off` False, `portal.local_identity_provider_enabled` False, all client feature gates False except `portal.mfa_required` True, zero usable invitations, zero unrevoked sessions. |

## Enabling procedure (only after sign-off is recorded here)
1. Record the named reviewer, decision, and date above.
2. Enable the required runtime gates in the governed Runtime snapshot (`portal.enabled`, then the specific
   surface gates), and finally `portal.production_signed_off`.
3. Confirm `production_ready()` is true and re-run the portal governance report.

## References
`app/portal/gate.py` (`production_ready`, `portal.production_signed_off`),
`app/portal/governance.py`, `app/portal/identity_local.py`, `docs/CLIENT_PORTAL_SECURITY.md`,
`docs/CLIENT_PORTAL_OPERATIONS.md`, ADR-048.
