# Client360 — Project Status

_Living status snapshot. Updated at each phase/hygiene checkpoint. Last updated:
**2026-07-16** (Release 0.10.0 Phase 6 — Insurance Exceptions, Work Management & Scheduled
Scanning — complete; Phase 5 approved)._

| Field | Value |
|---|---|
| **Current release** | **0.10.0 — Insurance Operations** (in progress; not tagged). Last tagged release: **0.9.13**. |
| **Branch** | `feature/insurance-operations` (base: `main`) |
| **Active PR** | [Draft PR #27](https://github.com/360WealthConsulting/Client360/pull/27) — *Draft, do not merge* |
| **Current Alembic head** | `c9k0m1n2h3j4` — single head; **dev `client360` and test `client360_test` both at head** |
| **Tests** | **692 passed, 5 skipped, 0 failed** via `scripts/test.sh run` (standard harness). Ruff ratchet clean; migrations reversible; single head `c9k0m1n2h3j4`; compile OK; scheduler registration verified; startup/shutdown clean; `git diff --check` clean. |
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
- **Phase 6** — Exceptions, Work Management & Scheduled Scanning (non-regulated, complete): single `run_insurance_scan()` orchestrating all detectors through the **shared** Exception Engine (idempotent, failure-isolated, honest reporting); registered on the **existing** scheduler (`insurance-detector-scan`); insurance **work queues** via the existing criteria framework; **auto-assignment** via existing assignment rules (`insurance_work.py`); organization-based record scope kept off the client Timeline; manual `POST /api/v1/insurance/scan`. Migration `c9k0m1n2h3j4` (data-only queues). Reuses shared subsystems — no insurance-specific engine/queue/scheduler.

## Remaining phases (0.10.0)

- **Phase 7** — Policyholder portal surface. *(Next task.)*
- **Phase 8** — Reporting + dashboards.
- **Phase 9** — Integration ports (disabled stubs).
- **Phase 10** — RC validation + release v0.10.0 (tag).
- **Regulated portions of Phases 2–4** — blocked pending AD-5 (see below).

## Documentation standard (Definition of Done)

The **360 Wealth Consulting Operations Manual documentation framework** (approved) is the
permanent, company-wide documentation standard: `docs/documentation-framework/` (information
architecture, templates + area profiles, capability map, gap analysis, roadmap, and the Git ↔
Confluence sync + Definition of Done). **Documentation is required for every completed feature,
phase, and change — mandatory, not advisory.** Per phase/release: update the Change Log, the
module Release Notes, every Git-canonical doc type touched, and the publication register
(`docs/DOCUMENTATION_CROSSWALK.md`); flag Confluence-canonical follow-ups. One canonical home per
page (Git technical, Confluence operational) — no duplication.

## Open risks

- 🔴 **AD-5 — compliance reviewer NOT YET NAMED.** All regulated insurance logic (suitability, replacement/1035, licensing/CE validation, compliance approvals) is **blocked** and cannot pass an RC gate without a qualified, named reviewer + approved sign-off artifact. Michael Shelton is the **business** owner (operational scope) only — not regulatory certification. **Not resolvable in code.**
- 🟠 **Release 0.10.0 not yet RC-validated / not tagged.** Phases 5–10 outstanding.
- 🟡 **Confluence Insurance pages are draft/unpublished** — must not be published until the corresponding phase functionality is complete and (for regulated content) AD-5-cleared.
- 🟡 **Benefits (0.9.11) Confluence pages still in draft** — awaiting page-owner approval.

## Next task

**Phase 7 — Policyholder portal surface.** Expose an org/person-scoped policyholder view via
the existing portal framework (out-of-scope requests 404). **Client-facing exception visibility
remains deliberately out of scope** — commission/compensation and firm-internal exceptions stay
off client surfaces. Begin only after Phase 6 is reviewed/accepted.

---

_Phase 6 is complete and stopped for review. Do not begin Phase 7 until this checkpoint is
reviewed/accepted per the project cadence. AD-5 remains an open, non-code blocker for all
regulated insurance logic._
