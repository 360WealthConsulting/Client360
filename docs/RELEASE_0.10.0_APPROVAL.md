# Release 0.10.0 — Release Approval

**Release 0.10.0 (non-regulated implementation) has successfully passed RC validation and is
recommended for release. AD-5-regulated functionality remains intentionally excluded pending
compliance review.**

| Field | Value |
|---|---|
| **Release** | 0.10.0 — Insurance Operations |
| **Scope** | **Non-regulated Insurance Operations (Phases 0–9)** |
| **Branch** | `feature/insurance-operations` |
| **Draft PR** | [#27](https://github.com/360WealthConsulting/Client360/pull/27) (draft — not merged) |
| **Alembic head** | `d0l1n2o3i4k5` (single head; dev + test at head) |
| **Commit used for release (RC-validated)** | `0728aa6` (`0728aa6cee89c2739e0c472f74381c760aca1386`) — the release tag will be cut from the branch tip after merge to `main` |
| **RC validation report** | [`docs/RC_0.10.0_VALIDATION.md`](RC_0.10.0_VALIDATION.md) |
| **Date** | 2026-07-16 |

## 1. Included functionality (Phases 0–9, non-regulated)

- **Phase 0** — Schema foundation: product catalog, `insurance_case`, policy/party/producer;
  `insurance.*` capabilities/roles; exception-engine + work-management registration.
- **Phase 1** — Policies core + coverages/riders/parties/values; product-version evolution;
  CRUD API + book/detail UI; lifecycle statuses + shared Timeline/Audit.
- **Phase 2** — New-business pipeline *(non-regulated skeleton)*: case progression, requirement
  tracking, underwriting-status, document collection, pipeline reporting.
- **Phase 3** — In-force servicing *(skeleton)*: reviews state machine + obligation calendar.
- **Phase 4** — Producer licensing & CE **records** *(skeleton)* + expiry reminders.
- **Phase 5** — Commissions: split-aware expected/received ledger, adjustments/reversals/
  chargebacks, carrier-statement import + reconciliation, ledger-derived revenue rollup.
- **Phase 6** — Exceptions, work management & scheduled scanning: one `run_insurance_scan()` on
  the shared Exception Engine, registered on the existing scheduler; insurance work queues +
  auto-assignment.
- **Phase 7** — Policyholder portal: read-only, opt-in, scoped policy view (no compensation/
  exceptions exposed).
- **Phase 8** — Reporting & dashboards: consolidated firm-internal operations dashboard,
  proportional to the viewer's capabilities and record scope.
- **Phase 9** — Integration ports: six vendor-neutral, **disabled** extension-point stubs.
- Reuses the platform throughout (exception engine, work management, scheduler, portal,
  documents, audit/timeline, reporting) — **no parallel subsystems**.

## 2. Explicit exclusions (NOT built / NOT enabled)

- Suitability determination · replacement / 1035 recommendation · licensing/CE **validation** ·
  sale/issue **blocking** · compliance approval · any regulated decision engine.
- Any **client-facing exception visibility** (commission/compensation and firm-internal
  exceptions stay off client surfaces).
- Any live integration I/O (all integration ports are disabled stubs — no credentials,
  endpoints, or scheduled jobs).

## 3. AD-5 statement

All regulated insurance functionality is **blocked by AD-5** and is intentionally excluded from
this release. A qualified, named compliance reviewer plus an approved, dated sign-off are
required before any regulated capability may be built or enabled; this is **not resolvable in
code**. Business (operational) approval of this release is **not** regulatory certification.
**AD-5-regulated functionality remains intentionally excluded pending compliance review.**

## 4. RC validation summary

Per [`docs/RC_0.10.0_VALIDATION.md`](RC_0.10.0_VALIDATION.md), all gates passed:
full regression suite; compile; ruff ratchet; `git diff --check`; clean working tree; single
Alembic head; linear + reversible migration chain; schema at head (dev + test); CHANGELOG
structural lint; startup/shutdown (health + readiness 200); `release.sh` dry-run (only the
intentional pre-tag CHANGELOG dating + cut-from-`main` gates remain). Security: authorization /
capability enforcement (no read-gated mutations), record-scope, organization boundaries,
client/staff separation, audit coverage, no information leakage. No secrets, credentials,
endpoints, or debug artifacts committed.

## 5. Test summary

**717 passed, 5 skipped, 0 failed** via `scripts/test.sh run` (standard isolated-DB harness).

## 6. Approval checklist

- [x] RC validation complete and passed (`docs/RC_0.10.0_VALIDATION.md`)
- [x] Full regression suite green (717 / 5 / 0)
- [x] Single Alembic head `d0l1n2o3i4k5`; migration chain linear & reversible; schema at head
- [x] Authorization, record-scope, organization boundaries, client/staff separation verified
- [x] Audit coverage verified; no information leakage
- [x] No secrets, credentials, endpoints, or debug artifacts committed
- [x] CHANGELOG structural lint passes; Phases 0–9 documented; version/phase references consistent
- [x] Draft PR #27 complete and current
- [x] AD-5-regulated functionality confirmed excluded and unbuilt
- [ ] **Final release authorization granted** *(pending — §8)*
- [ ] CHANGELOG `[0.10.0]` entry dated *(post-authorization)*
- [ ] PR #27 merged into `main` *(post-authorization)*
- [ ] `v0.10.0` tag created *(post-authorization)*

## 7. Release recommendation

**Release 0.10.0 (non-regulated implementation) has successfully passed RC validation and is
recommended for release. AD-5-regulated functionality remains intentionally excluded pending
compliance review.**

## 8. Release approval

| Approval | Status | Approver | Date |
|---|---|---|---|
| Phase 10 RC validation (non-regulated scope) | **Approved** | Michael Shelton (business owner) | 2026-07-16 |
| **Final release authorization** (date CHANGELOG · merge PR #27 · tag `v0.10.0`) | **PENDING** | _(awaiting sign-off)_ | _(to be recorded)_ |

No irreversible release action (CHANGELOG dating, PR merge, or `v0.10.0` tag) is performed until
**Final release authorization** above is granted.

## 9. Reviewer

**Michael Shelton** — business owner (workflow/operational scope). Business approval of the
non-regulated release scope only; **not** regulatory certification (see the AD-5 statement, §3).

## 10. Notes

- This approval covers **only** the implemented **non-regulated** surface (Phases 0–9). It is
  **not** a statement that the entire insurance platform is release-ready.
- The release is cut from `main`: the approved sequence is (1) date the CHANGELOG `[0.10.0]`
  entry, (2) merge PR #27 into `main`, (3) tag `v0.10.0` (`scripts/release.sh 0.10.0`). These are
  performed only after Final release authorization (§8).
- The compliance-reviewer role for AD-5 remains **unfilled**; regulated insurance functionality
  stays blocked and out of scope until it is filled and an approved sign-off exists.
