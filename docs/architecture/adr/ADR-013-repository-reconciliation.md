# ADR-013 — Reconcile the Enterprise Architecture with the existing in-place Client360 application

- **Status:** Accepted (approved with amendments)
- **Date:** 2026-07-18
- **Supersedes:** any earlier planning assumption that treated Client360 as a
  greenfield repository.

## Context
The frozen planning artifacts (Enterprise Architecture Package, Sprint 0/1
design) assumed a greenfield `backend/app/` modular monolith with an SPA
frontend. Repository inspection (E1.1, Section 1) found a **mature, working
FastAPI application** rooted at `app/`: server-rendered Jinja UI, SQLAlchemy
models, 41 Alembic migrations, importers, a matching/merge/match-review engine,
search, ~280 routes, live Microsoft 365 integrations, an existing Insurance
surface, and 55 test files with a disposable-test-DB safety guard. The approved
layout conflicts with reality on foundational points (application root, frontend
model, internal organization, migrations location, pre-existing Insurance, and
live integrations).

## Decision
The **inspected repository is the canonical implementation baseline.** The
architecture reconciles to the repository, not the reverse. Specifically:

1. **Repository:** keep `app/` as the application root; **no** relocation to
   `backend/app/`; no cosmetic restructuring; no rewrite of working code to match
   earlier diagrams.
2. **Protected functionality (architectural scope):** FastAPI app, SQLAlchemy
   models, Alembic history, importers, matching engine, merge planning, match
   review, search, existing routes, existing templates, existing schema, and
   existing startup behavior must keep working. No backlog item may intentionally
   break them **without an approved ADR**.
3. **Modular-monolith migration:** incremental only — map code into contexts,
   establish public interfaces, introduce import-linter gradually, reduce
   cross-context dependencies over time, migrate only when there is measurable
   value. Repository stability > architectural purity.
4. **UI:** the server-rendered UI remains production. **Do not** scaffold an
   unused SPA. A future ADR may introduce a frontend app if justified.
5. **Database:** existing Alembic history is authoritative. Do not rename,
   rewrite, relocate, or reset migrations/databases. Backward compatibility is
   mandatory.
6. **Insurance module:** becomes a **Legacy Bounded Context** — preserve
   behavior; no new features, architectural expansion, regulatory enhancement,
   compliance logic, or workflows. Exists for historical compatibility until
   explicitly retired. AD-5 boundary preserved.
7. **Existing integrations:** Microsoft 365 and other operational integrations
   are **approved legacy integrations**. The "no assumed integrations" rule
   applies only to **new** integrations.
8. **Quality strategy:** existing Ruff findings are accepted technical debt
   (already baselined via `scripts/ruff_gate.py` + `docs/ruff-baseline.json`);
   do not block on pre-existing lint. Require **new** code to pass; reduce
   existing violations incrementally. Same philosophy for MyPy and import-linter.

## Engineering principle
Working software has precedence over theoretical structure. The approved
architecture describes the system that exists while guiding it toward the target
architecture through incremental evolution.

## Alternatives considered
- **Big-bang relocate + reorganize into bounded contexts + build SPA** —
  rejected: high risk; contradicts "no broad rewrite"; breaks imports,
  migrations, and the working UI.
- **New parallel `backend/` beside legacy `app/`** — rejected: duplication and
  two sources of truth.

## Consequences
Preserves working functionality, migration history, and data. Delivers the
*intent* of the modular-monolith target (consistent structure + gradual boundary
enforcement) via a compatibility path. Accepts documented debt: directory-name
reconciliation (map, not move), incremental boundary tightening, and the existing
Ruff/MyPy/import-linter baselines. Frozen architecture documents are updated only
through approved ADRs.
