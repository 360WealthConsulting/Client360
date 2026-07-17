# Client360 — Project Status

_Living status snapshot. Updated at each phase/hygiene checkpoint. Last updated:
**2026-07-16** (Release 0.10.0 — **Phase 10 RC validation complete** (`docs/RC_0.10.0_VALIDATION.md`).
**The non-regulated Release 0.10.0 implementation (Phases 0–9) is complete and ready to enter
Phase 10 RC validation. AD-5 remains an external release blocker for any regulated insurance
functionality.** Recommended for release — awaiting approval to date the CHANGELOG, merge PR #27,
and tag `v0.10.0`.)_

| Field | Value |
|---|---|
| **Current release** | **0.10.0 — Insurance Operations** (in progress; not tagged). Last tagged release: **0.9.13**. |
| **Branch** | `feature/insurance-operations` (base: `main`) |
| **Active PR** | [Draft PR #27](https://github.com/360WealthConsulting/Client360/pull/27) — *Draft, do not merge* |
| **Current Alembic head** | `d0l1n2o3i4k5` — single head; **dev `client360` and test `client360_test` both at head** |
| **Tests** | **717 passed, 5 skipped, 0 failed** via `scripts/test.sh run` (standard harness). Ruff ratchet clean; single head `d0l1n2o3i4k5` (Phases 7–9 have no migration); compile OK; disabled-by-default + no-external-I/O + safe-failure + audit + authorization verified; no secrets/endpoints committed; startup/shutdown clean; `git diff --check` clean. |
| **Documentation status** | CHANGELOG `[Unreleased]` documents Phases 0–9; architecture doc header updated (implemented / deferred-regulated / AD-5 gate); company-wide Confluence crosswalk with Insurance Operations pages (all **draft**, unpublished); Insurance SOPs drafted (commissions, exceptions & work queues, policyholder portal — all draft-only under `docs/confluence/`). |

## Completed phases (0.10.0)

Phases 2–4 shipped as **non-regulated operational skeletons**; **Phases 5–9 (commissions;
exceptions / work-management / scheduled scanning; policyholder portal; reporting & dashboards;
integration ports as disabled stubs) are non-regulated and complete for their scope**. Regulated
logic remains deferred — see AD-5:

- **Phase 0** — Schema foundation: product catalog, `insurance_case`, policy/party/producer; `insurance.*` caps/roles; exception-engine + work-management registration. Migration `v2b3d4f5a6c7`.
- **Phase 1** — Policies core + coverages/riders/parties/values; product-version evolution; CRUD API, book/detail UI; lifecycle statuses + shared Timeline/Audit events. Migrations `w3c4e5g6b7d8`, `x4d5f6h7c8e9`.
- **Phase 2** — New-business pipeline (skeleton): case progression, requirement tracking, underwriting-status, document collection, workflow orchestration, pipeline reporting, UI/APIs. Migration `y5e6g7i8d9f0`.
- **Phase 3** — In-force servicing (skeleton): reviews state machine, obligation calendar, `INS_REVIEW_OVERDUE` via shared Exception Engine, review metrics, reviews board UI/APIs. Migration `z6f7h8j9e0g1`.
- **Phase 4** — Producer licensing & CE **records** (skeleton): `insurance_licenses`, `insurance_ce_records`, expiry detectors (`INS_LICENSE_EXPIRING` / `INS_CE_PERIOD_ENDING`), licensing dashboard UI/APIs. Migration `a7g8i9k0f1h2`.
- **Phase 5** — Commissions (non-regulated, complete): split-aware expected/received ledger (`insurance_commissions`), adjustments/reversals/chargebacks + write-off, carrier-statement import + reconciliation (`insurance_commission_statements` / `_statement_lines`), **firm-internal** variance/outstanding exceptions (`INS_COMMISSION_VARIANCE` / `INS_COMMISSION_OUTSTANDING`; kept off the client Timeline), uncapped ledger-derived revenue rollup with producer-payout / agency-retained breakdown, full audit coverage, commissions console + APIs, `insurance.commissions.write` capability. Migration `b8i9k1l2g3j4`. Includes the audit & revenue-validation pass.
- **Phase 6** — Exceptions, Work Management & Scheduled Scanning (non-regulated, complete): single `run_insurance_scan()` orchestrating all detectors through the **shared** Exception Engine (idempotent, failure-isolated, honest reporting); registered on the **existing** scheduler (`insurance-detector-scan`); insurance **work queues** via the existing criteria framework; **auto-assignment** via existing assignment rules (`insurance_work.py`); organization-based record scope kept off the client Timeline; manual `POST /api/v1/insurance/scan`. Migration `c9k0m1n2h3j4` (data-only queues). Reuses shared subsystems — no insurance-specific engine/queue/scheduler.
- **Phase 7** — Policyholder portal surface (non-regulated, complete): read-only policy view via the **existing** portal framework (`insurance_portal.py` + portal routes/template), opt-in `insurance` grant permission, person/household/org scope, out-of-scope 404, dashboard slice. Proportional disclosure only — **no producers/commissions/licensing/exceptions** exposed; insurance exceptions cannot reach the client action surface. No migration (read-only). Pre-cleanup of review items #1–#3 also applied.
- **Phase 8** — Reporting & dashboards (non-regulated, complete): consolidated firm-internal `operations_dashboard` (`insurance_reporting.py` + `/api/v1/insurance/dashboard` + `/insurance/dashboard`) composing pipeline/reviews/commissions/licensing plus new exception, work-queue, and portal-adoption summaries; **proportional to the viewer's capabilities**; record scope applied before aggregation; reuses the shared exception/work-queue/reporting primitives — no parallel engine. Staff-only (not the client portal); no compliance metrics (AD-5). No migration (read-only).
- **Phase 9** — Integration ports as disabled stubs (non-regulated, complete): six vendor-neutral, **disabled-by-default** extension-point stubs (`insurance_integrations.py`) — carrier policy/in-force, case status, commission statements, licensing/appointments, document intake (inbound), outbound export; inert (no I/O, credentials, endpoints, or scheduled jobs); config never enables them; read-only registry/status + inert audit-safe invoke routes. Reuses the shared disabled-provider idiom — no parallel framework. No migration.

## Remaining phases (0.10.0)

- **Phase 10** — RC validation + release v0.10.0 (tag). *(Next task.)*
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
- 🟠 **Release 0.10.0 RC-validated (`docs/RC_0.10.0_VALIDATION.md`), not yet tagged.** Non-regulated Phases 0–9 passed RC validation and are recommended for release; the tag step (date CHANGELOG `[0.10.0]` + merge PR #27 + tag `v0.10.0`) is **awaiting approval**.
- 🟡 **Confluence Insurance pages are draft/unpublished** — must not be published until the corresponding phase functionality is complete and (for regulated content) AD-5-cleared.
- 🟡 **Benefits (0.9.11) Confluence pages still in draft** — awaiting page-owner approval.

## Next task

**Release (approval-gated).** RC validation is complete and passed (`docs/RC_0.10.0_VALIDATION.md`).
Awaiting approval to: (1) date the CHANGELOG `[0.10.0]` entry, (2) merge draft PR #27 into `main`,
(3) tag `v0.10.0` (`scripts/release.sh 0.10.0`). **No AD-5-gated content is in the release** — only
the non-regulated Phases 0–9 surface.

---

_Phase 10 RC validation is complete and stopped for review. Do not date the CHANGELOG, merge PR
#27, or create the `v0.10.0` tag until final approval. AD-5 remains an open, non-code blocker for all
regulated insurance logic._
