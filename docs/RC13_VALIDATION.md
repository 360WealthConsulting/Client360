# RC13 — Release Candidate Validation (Release 0.9.10 · Sprint 5.5 — Exception Engine)

**Scope:** Independent, adversarial validation of the platform-wide Exception Engine
(ADR-17), implemented **tax-domain only** across Phases 1–8 on branch
`feature/exception-engine` (PR #21). **Baseline:** `main` @ v0.9.9 head `o5f36c4d3e2a`.
**Candidate head:** `q7b58f6c5d4e`.
**Validator:** release engineering (adversarial pass — attempts to falsify each claim,
not merely confirm it).
**Date:** 2026-07-14.

> This validation is a **merge gate**. ADR-17 and Sprint 5.5 are marked *implemented*
> only because every check below passed. PR #21 remains a **draft** and is **not merged**;
> v0.9.10 is **not tagged**.

---

## 0. Final recommendation

## ✅ SAFE TO MERGE

Every RC13 check passed. No defect was found during validation. The change is
additive and reversible (one new schema migration + one data-only migration), the
platform contract (SQLAlchemy Core, capability RBAC, record-scope authorization,
append-only audit, single Alembic head) is preserved, and no non-tax exception domain
is implemented. Recommendation is conditioned on merging PR #21 as-is; any further
commit requires re-running §1 (automated suite) and §3 (migration lifecycle).

---

## 1. Build, suite, and static gates

| Check | Method | Result |
|---|---|---|
| Full automated test suite | `pytest -q` | **421 passed, 5 skipped** |
| Exception-area suites | 8 files (engine/schema/detectors/sla/work/api/portal/reporting) | **98 passed** |
| Regression suites (tax/portal/work/auth/audit/microsoft) | 12 files | **95 passed** |
| Python compilation | `python -m compileall app tests migrations` | clean |
| Startup / shutdown | FastAPI `lifespan` enter/exit (validate config + scheduler) | clean |
| OpenAPI generation | `app.openapi()` | **196 paths** |
| Route registration | `len(app.routes)` | **211 routes**; `/exceptions/reporting`, `/api/v1/exceptions/report`, `/portal/action-needed`, `/api/v1/portal/exceptions[/{id}]` present |
| Template loading/rendering | Jinja render of new + modified templates | clean (reporting, portal action-needed, exception_summary partial with/without data) |

The 5 skips are pre-existing and unrelated to the Exception Engine (environment-gated
Microsoft/live-integration tests).

---

## 2. Migration integrity

| Check | Method | Result |
|---|---|---|
| Exactly one Alembic head | `alembic heads` | **`q7b58f6c5d4e` (single head)** |
| Clean base → head | fresh DB `alembic upgrade head` from zero | success; head `q7b58f6c5d4e` |
| No schema drift | `alembic revision --autogenerate` | no exception-related add/remove; only pre-existing reflection-vs-metadata noise (this app reflects, it has no declarative target metadata). **No drift introduced by Phase 8** (no migration added). |

## 3. Migration lifecycle (v0.9.9 ⇄ head) & sentinel preservation

Performed on a dedicated scratch database `client360_rc13`:

1. **v0.9.9 → head:** `upgrade o5f36c4d3e2a` → `upgrade head` → head `q7b58f6c5d4e`. ✅
2. **Downgrade to v0.9.9:** `downgrade o5f36c4d3e2a` → exception tables removed
   (`to_regclass('exceptions')` = NULL); the exception engine is additive in 0.9.10, so
   its tables are correctly created/dropped. ✅
3. **Re-upgrade to head:** `upgrade head` → head `q7b58f6c5d4e`. ✅
4. **Sentinel preservation:** a pre-0.9.10 `households` row and a **custom `work_queues`
   row that the data-only migration `q7b58f6c5d4e` does not touch** were seeded at head,
   then survived the full down→v0.9.9→up cycle unchanged; after re-upgrade the three tax
   queues (`tax_exceptions`, `tax_exceptions_critical`, `compliance_exceptions`) were
   correctly re-applied. ✅

Evidence (counts `tax_queues | custom | household`): before `3|1|1` → at v0.9.9
`1(custom)|1(household)|exceptions=NULL` → after re-upgrade `3|1|1`.

---

## 4. Schema constraints & event immutability

Direct SQL probes on the migrated scratch DB:

| Check | Probe | Result |
|---|---|---|
| `domain` CHECK | insert `domain='bogus'` | rejected — `ck_exceptions_domain` |
| `category` CHECK | insert `category='bogus'` | rejected — `ck_exceptions_category` |
| `severity` CHECK | insert `severity='bogus'` | rejected — `ck_exceptions_severity` |
| `status` CHECK | insert `status='bogus'` | rejected — `ck_exceptions_status` |
| `source` CHECK | insert `source='bogus'` | rejected — `ck_exceptions_source` |
| Event ledger UPDATE | `UPDATE exception_events …` | rejected — trigger `exception_events are append-only`; row intact |
| Event ledger DELETE | `DELETE FROM exception_events …` | rejected — same trigger; row intact |
| Dedupe partial-unique | second **open** row with same `dedupe_key` | rejected — `ix_exceptions_dedupe_active` |

Corresponding automated coverage: `test_exception_engine_schema.py` (7).

---

## 5. Core engine behavior

| Check | Coverage | Result |
|---|---|---|
| Dedupe + concurrent raise (idempotent replay / reopen / IntegrityError re-fetch) | `test_exception_engine_service.py`, schema dedupe probe (§4) | pass |
| Valid & invalid state transitions | `test_exception_engine_service.py` (TRANSITIONS) | pass |
| Stale-action rejection (`expected_status` + `SELECT … FOR UPDATE`) | `test_exception_engine_service.py`, `test_exception_api.py` (409) | pass |
| Record-scope authorization (domain-aware) | `test_exception_engine_service.py`, `test_exception_api.py`, `test_exception_reporting.py` | pass |
| Out-of-scope → 404 (hide existence) | `test_exception_api.py`, `test_portal_exceptions.py` | pass |
| Least-privilege `exception.*` capabilities | `p6a47e5d4f3b` grants; `test_exception_api.py` (capability dep) | pass — read/write/resolve/compliance; resolve+compliance sensitive; no new `record.read_all` |
| Blocker / compliance resolution segregation | `test_exception_api.py`, `test_exception_engine_service.py` (403 without `exception.resolve`/`.compliance`) | pass |

---

## 6. Detectors, lifecycle, SLA

| Check | Coverage | Result |
|---|---|---|
| Detector idempotency (stable dedupe keys) | `test_tax_exception_detectors.py` (15) | pass |
| Source-condition resolve + recurrence reopen | `test_tax_exception_detectors.py` (`_reconcile` auto-resolve→reopen) | pass |
| Lifecycle blocker gating (`blocks_lifecycle`) | `test_tax_return_lifecycle.py` | pass |
| Force-path authorization + audit | `test_tax_return_lifecycle.py` (`transition_return(..., force=True)`) | pass |
| SLA sweep idempotency (cadence-gated, replay-safe) | `test_exception_sla.py` (13) | pass |
| Duplicate escalation / notification prevention | `test_exception_sla.py` (`last_notified_at` + cadence gate) | pass |
| Honest notification outcomes (email/SMS stubbed → `disabled`, never fabricated) | `test_exception_sla.py` (asserts `disabled`) | pass |

---

## 7. Work Management integration

| Check | Coverage | Result |
|---|---|---|
| Work Management projection (single `work_items()` point) | `test_exception_work.py` (11), `test_work_management.py` | pass |
| Queue count / detail parity | `test_work_management.py` | pass |
| Capacity & bottleneck calculations (deterministic, explainable) | `test_work_management.py` | pass |
| No second assignment model (reuses `record_assignments`) | `test_exception_work.py` | pass |

---

## 8. API / UI / portal

| Check | Coverage | Result |
|---|---|---|
| API/UI parity (console list == API list) | `test_exception_api.py` | pass |
| Staff console thin-route / canonical-service only | `test_exception_api.py` | pass |
| Portal client-visible allowlist | `test_portal_exceptions.py` (13) | pass — only `CLIENT_VISIBLE_CODES` |
| Portal isolation (account/person/household scope) + out-of-scope 404 | `test_portal_exceptions.py`, `test_client_portal.py` | pass |
| No internal-field leakage (codes, owners, escalation, dedupe, events, audit) | `test_portal_exceptions.py` | pass |
| Client cannot directly resolve exceptions (read-only portal) | `test_portal_exceptions.py` | pass |

---

## 9. Dashboards & reporting (Phase 8)

| Check | Coverage | Result |
|---|---|---|
| Authorization filtering **before** aggregation | `test_exception_reporting.py` (scoped principal aggregates only in-scope) | pass |
| Aggregation accuracy (summary, category, aging, escalation, owner/team, client/return) | `test_exception_reporting.py` | pass |
| MTTA / MTTR / reopen rate / SLA-compliance from **real** stored data | `test_exception_reporting.py` (events + timestamps) | pass |
| Trend derived from real `opened_at`/`resolved_at` (no fabricated points) | `test_exception_reporting.py` | pass |
| Role-appropriate audiences (advisor/operations/tax/compliance/management) | `test_exception_reporting.py` | pass |
| Dashboard embedding is capability-gated (`exception.read`) | `test_exception_reporting.py` (`dashboard_summary` returns None without cap) | pass |
| No fabricated revenue/productivity/history | design review — only stored fields aggregated | pass |

---

## 10. Guardrails

| Check | Result |
|---|---|
| No implementation of non-tax exception domains | Confirmed — `SUPPORTED_DOMAINS = {"tax"}`; detectors, SLA sweep, reporting all tax-only; other domains schema-ready but inert. |
| No role widened / no new `record.read_all` grants | Confirmed — capability grants unchanged since Phase 1. |
| Regression: existing tax, portal, work, auth, audit, Microsoft | **95 passed** across the 12 regression suites; no regressions. |

---

## 11. Defects

**None found.** No remediation was required during RC13. (Had a genuine defect been
found, the protocol was: fix only that defect, rerun the affected checks, and document
the remediation here.)

---

## 12. Verdict

**SAFE TO MERGE.** Do not merge PR #21 or tag/publish v0.9.10 until release approval.
On merge, re-run §1 and §3 against the merge commit before tagging.
