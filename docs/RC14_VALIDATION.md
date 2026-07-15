# RC14 — Release Candidate Validation (Release 0.9.11 · Employer Operations / Employee Benefits)

**Scope:** Independent, adversarial validation of Release 0.9.11 — Employer Operations &
Employee Benefits (ADR-18), delivered across Phases 1–8 on branch `feature/employee-benefits`
(PR #22). **Baseline:** `main` @ v0.9.10 head `q7b58f6c5d4e`. **Candidate head:**
`u1f9c0i9h8g7`.
**Validator:** release engineering (adversarial pass). **Date:** 2026-07-15.

> Merge gate. PR #22 remains a **draft** and is **not merged**; v0.9.11 is **not tagged**.

---

## 0. Final recommendation

## ✅ SAFE TO MERGE

Every RC14 check passed with no defect surviving validation. The change is additive and
reversible (5 migrations: `r8c69f7e6d5c`, `s9d7a8g7f6e5`, `t0e8b9h8g7f6`, `u1f9c0i9h8g7`,
plus the data-only queue migration), reuses existing platform systems (no duplicate engine,
scheduler, portal, workflow, reporting framework, or data model), preserves Organization
record scope and employer-portal privacy, and leaves tax untouched. Recommendation is
conditioned on merging PR #22 as-is; any further commit requires re-running §1 and §6.

---

## 1. Build, suite, and static gates

| Check | Method | Result |
|---|---|---|
| Full automated test suite | `pytest -q` | **520 passed, 5 skipped** |
| Benefits-area suites | schema/services/detectors/work/obligations/console/employer-portal/reporting | **87 passed** |
| Python compilation | `compileall app tests migrations` | clean |
| Startup / shutdown | FastAPI `lifespan` (config + scheduler) | clean; `benefits-detector-scan` + `exception-sla-sweep` registered |
| OpenAPI generation | `app.openapi()` | **222 paths** |
| Route registration | `len(app.routes)` | **245 routes**; benefits/organization/employer-portal/reporting routes present |
| Dead-code / unused-import | `test_phase6_dead_code.py` | passed |
| `git diff --check` | whitespace/conflict markers | clean |

The 5 skips are pre-existing environment-gated Microsoft/live-integration tests, unrelated
to benefits.

## 2. Migration integrity

| Check | Result |
|---|---|
| Exactly one Alembic head | **`u1f9c0i9h8g7` (single head)** |
| Clean base → head | success from zero |
| Down → v0.9.10 (`q7b58f6c5d4e`) → up | reversible; benefit tables removed on downgrade and restored on re-upgrade |
| Sentinel preservation | a pre-0.9.11 `households` row survived the full down→v0.9.10→up cycle; 13 obligation templates re-seeded |
| No schema drift introduced by Phase 8 | Phase 8 adds **no migration** (reporting is read-only) |

## 3. Domain model & guardrails

- **Reuse, not rebuild:** Organizations are `relationship_entities` (+`organization_profiles`);
  ownership is a typed detail on the existing `relationships` edge; work uses
  `record_assignments`; exceptions use the platform Exception Engine (`domain='benefits'`);
  SLA uses the shared sweep; portal reuses the existing accounts/documents/messages/notify;
  reporting reuses `exception_reporting`. **No second engine/scheduler/portal/workflow/
  reporting framework/data model.**
- **Nothing inferred:** obligation dates are explicitly entered/verified; contribution-deposit
  lateness and other unsupported detectors remain inert; reporting aggregates stored data only.
- **Tax untouched:** `tax_engagements` and all tax modules unchanged; tax queues narrowed to
  `domain='tax'` (data-only) so benefits never leaks in.

## 4. Authorization & isolation

| Check | Coverage | Result |
|---|---|---|
| Organization record scope on staff reads/writes | `test_benefits_services`, `test_benefits_console` | pass |
| Cross-organization isolation (staff) | services + console + reporting tests | pass — out-of-scope → 404 |
| Staff-capability gating | console require_capability + service `_require` | pass — `organization.*` / `benefits.*` / `benefits.enroll` / `benefits.compliance` / `benefits.sensitive.read` |
| Benefits exception authorization branch | `test_benefits_services`, `test_benefits_detectors` | pass — org anchor + employee person/household; out-of-scope hidden |
| Reporting: authorization **before** aggregation | `test_benefits_reporting` | pass — scoped principal aggregates only in-scope orgs |
| No new `record.read_all` grant / no role widened | Phase 1 grants unchanged | confirmed |

## 5. Employer-portal privacy

| Check | Coverage | Result |
|---|---|---|
| Organization-scoped employer access; out-of-scope 404 | `test_employer_portal` | pass |
| Strict employer allowlist (`EMPLOYER_VISIBLE_CODES`) | `test_employer_portal` | pass — internal/compliance/renewal/retirement/document exceptions never shown |
| No EIN / employee identity / compensation / deferral / internal notes / codes / owners / escalation / queue data | `test_employer_portal`, `test_benefits_reporting` | pass — projections carry only allowlisted org-level fields |
| Employer notifications auditable + honest (existing provider/outcome) | `test_employer_portal` | pass — delivered/disabled recorded; idempotent; no sensitive data |
| Employer read-only on exceptions (resolution via real action) | census upload clears census-overdue | pass |
| Portal isolation regression (individual clients) | `test_client_portal`, `test_portal_exceptions` | pass |

## 6. Exceptions, SLA, scheduler, work management

| Check | Result |
|---|---|
| Detector idempotency / auto-resolve / reopen (health + retirement + obligations) | pass (`test_benefits_detectors`, `test_benefits_obligations`) |
| Immutable exception events + audit | pass |
| SLA escalation (shared sweep, benefits) — breach/at-risk/cooldown, internal-only | pass (`test_benefits_obligations`) |
| Scheduler — single scheduler, overlap prevention (`max_instances=1`/`coalesce`), idempotent, per-org failure isolation, honest metrics | pass (`test_benefits_work_integration`, `test_benefits_obligations`) |
| Work Management projection + benefits queues; queue access does not bypass scope | pass (`test_benefits_work_integration`) |

## 7. Dashboards / reporting (Phase 8)

| Check | Result |
|---|---|
| Proportional, decision-oriented (book, participation, compliance/renewal calendar, exceptions) | pass — no decorative panels; every panel supports a staff/management decision |
| Authorization-filtered before aggregation; stored data only | pass |
| Reuses `exception_reporting` (`domain='benefits'`) — no second reporting framework | pass |
| Names/labels not raw IDs (plan names, obligation titles) | pass |
| No sensitive-data exposure (EIN/comp/deferral/employee identity) | pass |
| Query paths use `organization_id` / `due_date` indexes | `benefit_plans` index-only scan confirmed; `benefit_obligations` indexes present (planner uses seq scan only on the tiny test table) |

## 8. Defects

**None found.** No remediation required during RC14.

## 9. Verdict

**SAFE TO MERGE.** Do not merge PR #22 or tag v0.9.11 until release approval. On merge,
re-run §1 and §2 against the merge commit before tagging.
