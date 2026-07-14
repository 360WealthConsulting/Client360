# Release 0.9.9 Phase 6 — Dead Code Removal

Removes residual dead/debug code beyond the Phase 2 Graph connector
(`PRODUCTION_ARCHITECTURE.md` §11/§4; progress review §11). Every removal is
proven unused: no import, no call site, no test, no template references it. No
database, migration, or public-API change.

## WP6.1 — remove `POST /timeline/test` debug endpoint

`app/routes/timeline.py` — deleted `create_test_timeline_event()` (`POST
/timeline/test`), which wrote a synthetic event into `person_id=1`. The now-unused
`add_timeline_event` import was removed with it; `get_person_timeline` and the
`GET /timeline/person/{person_id}` view are retained.

**Evidence:** `grep` found zero references to `/timeline/test` /
`create_test_timeline_event` in code, tests, or templates. Route count 178 → 177;
OpenAPI paths 164 → 163; the person-timeline route still present.

## WP6.2 — remove verified unused imports

Removed unused imports across 18 files, each confirmed unused by an AST
import-usage analysis and re-checked by `grep` (and confirmed not re-exported):

| File | Removed |
|---|---|
| `app/importers/schwab.py` | `json` |
| `app/matching/matcher.py` | `re` |
| `app/portal/signatures.py` | `uuid` |
| `app/routes/portal.py` | `Form`, `RedirectResponse`, `portal_accounts`, `portal_threads`, `notify` |
| `app/routes/tax_intake.py` | `Optional` |
| `app/routes/work.py` | `Query` |
| `app/security/audit.py` | `Any` |
| `app/security/policy.py` | `and_` |
| `app/security/service.py` | `and_`, `func` |
| `app/services/client_alerts.py` | `date` |
| `app/services/documents.py` | `shutil` |
| `app/services/identity.py` | `and_`, `or_` |
| `app/services/microsoft_identity.py` | `select` |
| `app/services/tax_domain.py` | `and_`, `func` |
| `app/services/tax_intake.py` | `and_`, `documents` |
| `app/services/tax_return_lifecycle.py` | `date`, `case`, `func`, `workflow_instances` |
| `app/services/work_management.py` | `activities`, `audit_events` |
| `app/services/workflow_automation.py` | `and_` |

An AST re-scan reports **0** unused imports remaining across `app/`. All modules
compile (`compileall`) and import cleanly.

## Preserved (not removed)

Intentionally reserved extension points were left in place per the Phase 3
decision: `app/services/tax_filing_providers.py` (reserved for Epic 5 Sprint 5.6)
and `app/portal/signatures.py` (signature port; test-covered). These are
documented, functional, and referenced — not dead.

## Regression tests

`tests/test_phase6_dead_code.py`:
- asserts `POST /timeline/test` is not registered and the person-timeline route
  is;
- imports every `app/**` module (a stale reference to a removed import would fail
  here);
- re-runs the unused-import check and asserts none remain.

## Validation

- Full suite: **286 passed, 4 skipped** on a fresh DB at head `o5f36c4d3e2a`
  (Phase 6 tests added; the 4 skips are the deferred `app.models` scaffold below).
- Startup/shutdown clean; **route count 177** (−1 debug endpoint); OpenAPI 163
  paths; single Alembic head unchanged (no migration); `git diff --check` clean.
- Net change: 19 files, +17 / −47.

## Remaining deferred items (for reviewer decision)

**`app/models/` orphaned ORM scaffold — recommend removal in a follow-up.**
The directory (`client.py`, `household.py`, `source_link.py`, empty `person.py`)
is an early SQLAlchemy **ORM** scaffold that predates and contradicts the
established **SQLAlchemy Core** architecture (`app/db.py`). Evidence it is dead:

- **Zero references** — nothing in `app/` or `tests/` imports `app.models.*`.
- **Broken** — `app/models/client.py` uses PEP 604 `str | None` annotations that
  raise `TypeError` on this project's Python 3.9; it cannot even be imported.
- `app/models/person.py` is a 0-byte empty file.

It was **not** removed in this phase because it is outside the plan's Phase 6
scope (debug endpoint + unused imports) and deleting a whole package is a separate
decision. The import-graph regression test skips `app.models.*` with an explicit
reason pointing here. Recommend approving its removal as a small follow-up.
