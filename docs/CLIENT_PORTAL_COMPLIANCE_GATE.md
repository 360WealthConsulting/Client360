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
  no environment fallback — `app/portal/gate.py`).
- Code gate: `production_ready()` returns true only when `portal.enabled` **AND**
  `portal.production_signed_off` are both on. All external financial/document/messaging/appointment gates
  are independently OFF by default as well.
- Effect while blocked: implementation and local/test proceed, but production never serves external client
  data because the sign-off gate is off.

## Pre-conditions for sign-off (to be verified by the reviewer)
- [ ] A real external identity provider is integrated and registered (the deterministic local provider is
      non-production only).
- [ ] MFA enforcement confirmed (`portal.mfa_required` ON) end-to-end with the production IdP.
- [ ] Visibility registry reviewed; no `internal_only` / `prohibited` field is externally reachable;
      governance report clean.
- [ ] Account-number masking and financial-summary minimization confirmed.
- [ ] Consent / electronic-delivery records reviewed and legally sufficient.
- [ ] Scope resolver verified default-deny; household access does not grant every member; out-of-scope
      returns 404 without disclosure.
- [ ] Audit coverage of external mutations confirmed; no tokens/PII in logs or diagnostics.
- [ ] Failure isolation confirmed (portal failure never affects internal surfaces).
- [ ] Data-retention, incident-response, and client-notification procedures approved.

## Sign-off record (to be completed)
| Field | Value |
| --- | --- |
| Accountable compliance reviewer | **NOT YET DESIGNATED** |
| Decision | **BLOCKED** |
| Date | — |
| Runtime change authorized | **No** — `portal.production_signed_off` remains OFF |
| Notes | — |

## Enabling procedure (only after sign-off is recorded here)
1. Record the named reviewer, decision, and date above.
2. Enable the required runtime gates in the governed Runtime snapshot (`portal.enabled`, then the specific
   surface gates), and finally `portal.production_signed_off`.
3. Confirm `production_ready()` is true and re-run the portal governance report.

## References
`app/portal/gate.py` (`production_ready`, `portal.production_signed_off`),
`app/portal/governance.py`, `app/portal/identity_local.py`, `docs/CLIENT_PORTAL_SECURITY.md`,
`docs/CLIENT_PORTAL_OPERATIONS.md`, ADR-048.
