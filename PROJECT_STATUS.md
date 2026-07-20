# Client360 — Project Status (historical release log through 0.10.0)

> **⚠️ For the CURRENT project state (Version 1.0 / Sprint 2), see
> [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) and [`docs/V1_RELEASE_PLAN.md`](docs/V1_RELEASE_PLAN.md).**
> This file is retained as the historical release-status log; entries below stop at Release 0.10.0.

_Living status snapshot. Updated at each phase/hygiene checkpoint. Last updated:
**2026-07-17** (**Release 0.10.0 RELEASED** — tag `v0.10.0` on `main` (release commit `5ba60a2`);
PR #27 merged; [GitHub Release](https://github.com/360WealthConsulting/Client360/releases/tag/v0.10.0)
published.
**Release 0.10.0 contains the completed non-regulated Insurance Operations implementation
(Phases 0–9). AD-5-regulated functionality remains intentionally excluded pending compliance
review and approval.**)_

| Field | Value |
|---|---|
| **Current release** | **0.10.0 — Insurance Operations — RELEASED** (tag `v0.10.0`, 2026-07-17; release commit `5ba60a2`; [GitHub Release](https://github.com/360WealthConsulting/Client360/releases/tag/v0.10.0)). Prior tagged release: 0.9.13. |
| **Branch** | Released from `main` (PR #27 merged). |
| **PR** | [#27](https://github.com/360WealthConsulting/Client360/pull/27) — **MERGED** (2026-07-17). |
| **Current Alembic head** | `d0l1n2o3i4k5` — single head; **dev `client360` and test `client360_test` both at head** |
| **Tests** | **717 passed, 5 skipped, 0 failed** via `scripts/test.sh run` (standard harness). Ruff ratchet clean; single head `d0l1n2o3i4k5` (Phases 7–9 have no migration); compile OK; disabled-by-default + no-external-I/O + safe-failure + audit + authorization verified; no secrets/endpoints committed; startup/shutdown clean; `git diff --check` clean. |
| **Documentation status** | CHANGELOG `[0.10.0]` dated and released; architecture doc header updated (implemented / deferred-regulated / AD-5 gate). **5 in-scope non-regulated Confluence pages PUBLISHED** (Commissions `28803073`, Exceptions & Work Queues `28835841`, Policyholder Portal `28868609`, Reporting & Dashboard `28901377`, Integrations reference `28901397`) under parent **Insurance Operations — Release 0.10.0** (`28770305`). **7 register pages remain DRAFT/deferred** (Overview, Policy Management, New Business, In-Force Servicing, Reviews & Obligations, Producer Licensing/CE, Roles & Responsibilities — Phase 0–4 skeleton and/or AD-5-gated). Full 26-area Publication Register remains a **Phase A roadmap** deliverable — not expanded in this release. |

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

- **Phase 10** — RC validation + release v0.10.0 (tag). ✅ **Complete** — released 2026-07-17.
- **Regulated portions of Phases 2–4** — blocked pending AD-5 (see below).

All ten phases of Release 0.10.0 are complete; the non-regulated scope (Phases 0–9) is
released. The regulated remainder stays out of scope under AD-5.

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
- ✅ **Release 0.10.0 RELEASED** (tag `v0.10.0`, 2026-07-17) — non-regulated Phases 0–9. RC-validated (`docs/RC_0.10.0_VALIDATION.md`), approved (`docs/RELEASE_0.10.0_APPROVAL.md`), PR #27 merged, GitHub Release published.
- 🟢 **In-scope Insurance Confluence pages published** (5 non-regulated: Commissions, Exceptions & Work Queues, Policyholder Portal, Reporting & Dashboard, Integrations reference). The remaining 7 Insurance register pages (Phase 0–4 skeleton and/or AD-5-gated) **stay draft/unpublished** and must not be published until their phase is RC-validated and, for regulated content, AD-5-cleared.
- 🟡 **Benefits (0.9.11) Confluence pages still in draft** — awaiting page-owner approval.

## Next task

**Release 0.10.0 is closed.** All closeout is done: released to `main` (tag `v0.10.0`), status
updated, and the 5 in-scope non-regulated Insurance Confluence pages published (AD-5-gated and
future-functionality pages held as draft).

Planning transitions to the next roadmap milestone: **Phase A — Foundation & governance** of the
360 Wealth Consulting Operations Manual (`docs/documentation-framework/05-IMPLEMENTATION-ROADMAP.md`)
— provision the Confluence space skeleton, load the template library, add the `governance/` tree,
and **promote `DOCUMENTATION_CROSSWALK.md` to the full 26-area Publication Register** (explicitly
deferred out of Release 0.10.0). Product-side, the next development release picks up from `main`
at `v0.10.0`. **AD-5-regulated insurance functionality remains out of scope pending a qualified,
named compliance reviewer and approved sign-off.**

---

_Release 0.10.0 (non-regulated Phases 0–9) is shipped. AD-5 remains an open, non-code blocker for all
regulated insurance logic._
