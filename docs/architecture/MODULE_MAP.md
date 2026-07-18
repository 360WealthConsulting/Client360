# Client360 Module Map — Bounded-Context Mapping (E1.1)

**Status:** Living implementation-support document. Introduced by E1.1 under
**ADR-013** (incremental, in-place reconciliation).
**Purpose:** Map the *existing* `app/` code into the target bounded contexts
**without moving code**. This is ADR-013 step 1 ("map existing code into bounded
contexts") and the reference for gradual import-linter tightening (step 3).

> This map describes the system that **exists** while pointing toward the target
> architecture. It does not authorize relocation or rewrites. Working software
> has precedence over theoretical structure.

## Application root & entry point
- **Application root:** `app/` (canonical per ADR-013 — **not** `backend/app/`).
- **Entry point:** `app/main.py` — module-level `app = FastAPI(lifespan=…)`,
  `AuthenticationMiddleware` + `SessionMiddleware`, ~40 routers, 280 registered
  routes. Startup work (`validate_startup_configuration`, scheduler) runs inside
  the **lifespan**, so importing the module is side-effect-free.
- **Config:** `app/config.py` (`validate_startup_configuration()`).
- **Migrations:** `migrations/` + root `alembic.ini` (authoritative history; 41
  revisions).

## Existing code → target bounded context

| Target context | Existing modules (today) | Notes |
|---|---|---|
| **Client / People** | `app/models/person.py`, `app/models/client.py`, `app/routes/people.py` | Core identity |
| **Household** | `app/models/household.py`, `app/routes/households.py`, `app/routes/relationships.py` | Grouping |
| **Source & Links (ingestion)** | `app/models/source_link.py`, `app/routes/source.py`, `app/importers/*` (schwab, assetmark, wealthbox, dave_ramsey) | Importers = protected scope |
| **Matching / Merge** | `app/matching/*` (matcher, plan_matches, apply_safe_matches, audit_matches, verify_merge_plan), `app/routes/matches.py` | Match engine, merge planning, match review = protected scope |
| **Search** | `app/routes/search.py` | Protected scope |
| **Portfolio / Wealth (existing)** | `app/portfolio/*`, `app/routes/portfolio.py` | Existing; future Wealth Ops (E8) reconciles here, SME-gated |
| **Tax (existing)** | `app/routes/tax*.py`, `app/templates/tax/` | Existing; future Tax Ops (E9) reconciles here, SME-gated |
| **Documents / Portal** | `app/routes/documents.py`, `app/portal/*`, `app/routes/portal.py` | Reference-based document handling |
| **Workflow / Tasks / Activities** | `app/routes/workflows.py`, `app/routes/tasks.py`, `app/routes/task_dashboard.py`, `app/routes/activities.py` | Future Workflow Engine (E4) reconciles here |
| **Admin / Ops / Session / Auth** | `app/routes/admin.py`, `app/routes/ops.py`, `app/routes/session.py`, `app/routes/auth.py`, `app/security/*` | Identity/Administration |
| **Integrations (legacy, approved)** | `app/connectors/microsoft365/*`, `app/jobs/microsoft_*_sync.py`, `app/integrations/*` | **Existing** operational integrations; the "no assumed integrations" rule applies to **new** integrations only (ADR-013) |
| **Insurance (LEGACY bounded context)** | `app/routes/insurance.py` (53 routes), `app/templates/insurance/` | **Frozen legacy** per ADR-013: preserve behavior, **no** new features/workflows/compliance/regulatory logic; exists for historical compatibility until explicitly retired. AD-5 boundary preserved. |

## Boundary enforcement roadmap (import-linter, gradual — ADR-013 step 3)
- **In force now (E1.1):** *Domain and ingestion layers must not import the web
  layer* — `app.models`, `app.matching`, `app.importers` must **not** import
  `app.routes`. Verified passing; encoded as an import-linter `forbidden`
  contract.
- **Planned (later items, incremental):** freeze the Insurance legacy context
  (no new inbound coupling); isolate `app.matching`/`app.importers` from each
  other where valuable; introduce kernel/platform seams as future contexts are
  built. Each new contract is added only when it currently passes, then held.

## Rules (from ADR-013)
1. No relocation of `app/`; no cosmetic restructuring.
2. Protected functionality (FastAPI app, SQLAlchemy models, Alembic history,
   importers, matching, merge planning, match review, search, existing routes,
   templates, schema, startup) must keep working — breaking any requires an ADR.
3. Migrate to stricter boundaries only when there is measurable value.
