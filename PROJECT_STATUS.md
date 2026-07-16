# Client360 — Project Status

_Living status snapshot. Updated at each phase/hygiene checkpoint. Last updated:
**2026-07-16** (Release 0.10.0 Phase 5 — Insurance Commissions — **complete & approved**,
incl. the audit & revenue-validation pass; Phase 6 in progress)._

| Field | Value |
|---|---|
| **Current release** | **0.10.0 — Insurance Operations** (in progress; not tagged). Last tagged release: **0.9.13**. |
| **Branch** | `feature/insurance-operations` (base: `main`) |
| **Active PR** | [Draft PR #27](https://github.com/360WealthConsulting/Client360/pull/27) — *Draft, do not merge* |
| **Current Alembic head** | `b8i9k1l2g3j4` — single head; **dev `client360` and test `client360_test` both at head** |
| **Tests** | **682 passed, 5 skipped, 0 failed** via `scripts/test.sh run` (standard harness). Ruff clean on Phase 5 files; single head `b8i9k1l2g3j4`; compile OK; `git diff --check` clean; startup/shutdown clean. |
| **Documentation status** | CHANGELOG `[Unreleased]` documents Phases 0–5; architecture doc header updated (implemented / deferred-regulated / AD-5 gate); company-wide Confluence crosswalk with Insurance Operations pages (all **draft**, unpublished); Insurance Commissions SOP drafted (`docs/confluence/INSURANCE_COMMISSIONS_SOP_DRAFT.md`, draft-only). |

## Completed phases (0.10.0)

Phases 2–4 shipped as **non-regulated operational skeletons**; **Phase 5 (commissions) is
non-regulated and complete for its scope**. Regulated logic remains deferred — see AD-5:

- **Phase 0** — Schema foundation: product catalog, `insurance_case`, policy/party/producer; `insurance.*` caps/roles; exception-engine + work-management registration. Migration `v2b3d4f5a6c7`.
- **Phase 1** — Policies core + coverages/riders/parties/values; product-version evolution; CRUD API, book/detail UI; lifecycle statuses + shared Timeline/Audit events. Migrations `w3c4e5g6b7d8`, `x4d5f6h7c8e9`.
- **Phase 2** — New-business pipeline (skeleton): case progression, requirement tracking, underwriting-status, document collection, workflow orchestration, pipeline reporting, UI/APIs. Migration `y5e6g7i8d9f0`.
- **Phase 3** — In-force servicing (skeleton): reviews state machine, obligation calendar, `INS_REVIEW_OVERDUE` via shared Exception Engine, review metrics, reviews board UI/APIs. Migration `z6f7h8j9e0g1`.
- **Phase 4** — Producer licensing & CE **records** (skeleton): `insurance_licenses`, `insurance_ce_records`, expiry detectors (`INS_LICENSE_EXPIRING` / `INS_CE_PERIOD_ENDING`), licensing dashboard UI/APIs. Migration `a7g8i9k0f1h2`.
- **Phase 5** — Commissions (non-regulated, complete): split-aware expected/received ledger (`insurance_commissions`), adjustments/reversals/chargebacks + write-off, carrier-statement import + reconciliation (`insurance_commission_statements` / `_statement_lines`), **firm-internal** variance/outstanding exceptions (`INS_COMMISSION_VARIANCE` / `INS_COMMISSION_OUTSTANDING`; kept off the client Timeline), uncapped ledger-derived revenue rollup with producer-payout / agency-retained breakdown, full audit coverage, commissions console + APIs, `insurance.commissions.write` capability. Migration `b8i9k1l2g3j4`. Includes the audit & revenue-validation pass.

## Remaining phases (0.10.0)

- **Phase 6** — Exceptions + detectors + queues + `run_insurance_scan` scheduled job (live cron wiring of the Phase 3/4/5 scan callables). *(Next task.)*
- **Phase 7** — Policyholder portal surface.
- **Phase 8** — Reporting + dashboards.
- **Phase 9** — Integration ports (disabled stubs).
- **Phase 10** — RC validation + release v0.10.0 (tag).
- **Regulated portions of Phases 2–4** — blocked pending AD-5 (see below).

## Open risks

- 🔴 **AD-5 — compliance reviewer NOT YET NAMED.** All regulated insurance logic (suitability, replacement/1035, licensing/CE validation, compliance approvals) is **blocked** and cannot pass an RC gate without a qualified, named reviewer + approved sign-off artifact. Michael Shelton is the **business** owner (operational scope) only — not regulatory certification. **Not resolvable in code.**
- 🟠 **Release 0.10.0 not yet RC-validated / not tagged.** Phases 5–10 outstanding.
- 🟡 **Confluence Insurance pages are draft/unpublished** — must not be published until the corresponding phase functionality is complete and (for regulated content) AD-5-cleared.
- 🟡 **Benefits (0.9.11) Confluence pages still in draft** — awaiting page-owner approval.

## Next task

**Phase 6 — Exceptions, detectors, queues, and live scan cron.** Wire the Phase 3/4/5 scan
callables (`run_insurance_review_scan`, `run_insurance_licensing_scan`,
`run_insurance_commission_scan`) into the scheduler as `run_insurance_scan`, and complete the
insurance exception queues. Fully operational (no AD-5 dependency). Begin only after the
Phase 5 work is committed and the draft PR is updated — **stop for review before Phase 6.**

---

_Phase 5 is complete and stopped for review. Do not begin Phase 6 until this checkpoint is
reviewed/accepted per the project cadence. AD-5 remains an open, non-code blocker for all
regulated insurance logic._
