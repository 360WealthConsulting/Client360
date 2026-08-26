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
