# RC12 — Release 0.9.9 (Platform Consolidation) Final Validation

Independent release-candidate validation of `feature/platform-consolidation`
(Phases 1–7) against `main`. Alembic head `o5f36c4d3e2a`; baseline `main`
(v0.9.8) at head `l2c03f1e0d9b`. No application defects were discovered; no code
was changed during this validation.

## Scope validated

7 phase commits on the branch:

| Phase | Commit | Summary |
|---|---|---|
| 1 | `5af14ac` | Microsoft 365 token security (encrypted MSAL cache, read-only scopes) |
| 2 | `0b2d1b6` | Microsoft Graph connector consolidation |
| 3 | `8830be5` | Provider-registry consolidation |
| 4 | `ed79b35` | Hot-path foreign-key indexes (24, CONCURRENTLY) |
| 5 | `28aefb9` | N+1 / query-optimization |
| 6 | `2b82747` | Dead debug endpoint + unused-import removal |
| 7 | `7d44384` | Deployment readiness (`/readiness`, config, CSRF, runbook) |

## Validation results

| # | Check | Result |
|---|---|---|
| 1 | Python compilation (`compileall` app + tests + scripts) | **PASS** — clean |
| 2 | Git whitespace (`git diff main..HEAD --check`) | **PASS** — none |
| 3 | Single Alembic head | **PASS** — `o5f36c4d3e2a` |
| 4 | Dead-code / unused-import AST scan | **PASS** — 0 unused imports |
| 5 | Clean base → head on an empty database | **PASS** — 25 migrations, head `o5f36c4d3e2a` |
| 6 | Startup/shutdown, OpenAPI, route registration | **PASS** — lifespan clean; 178 routes; 164 OpenAPI paths; `/readiness` + `/health` present; `POST /timeline/test` gone |
| 7 | v0.9.8 up → down → re-up + sentinel preservation | **PASS** — 3 up / 3 down / 3 re-up; Phase 1+4 columns/indexes removed then restored; sentinel checksums identical |
| 8 | Full automated test suite | **PASS** — 297 passed, 4 skipped |
| 9 | Authorization regression (integration + hardening) | **PASS** — 4 + 20 |
| 10 | H13 deterministic document matching | **PASS** — 14 |
| 11 | RC-series tax document remediation (RC11 lineage) | **PASS** — 25 |
| 12 | Microsoft document sync / matching | **PASS** — 8 |
| 13 | Microsoft token security (Phase 1) | **PASS** — 9 |
| 14 | Graph / provider / index / N+1 / dead-code / readiness guards | **PASS** — 13 / 9 / 2 / 9 / 109(+4 skip) / 10 |
| 15 | Client portal isolation | **PASS** — 11 |
| 16 | Append-only audit immutability (DB trigger) | **PASS** — UPDATE and DELETE both rejected |
| 17 | No plaintext Microsoft token in DB | **PASS** — 0 rows with non-null `access_token`/`refresh_token`; `token_cache_encrypted` present |
| 18 | Token crypto fail-closed + read-only scopes | **PASS** — `TokenKeyMissing` on missing key; scopes `User.Read, Mail.Read, Calendars.Read, Files.Read.All, Sites.Read.All` (no Write/Send) |
| 19 | Readiness endpoint (live) | **PASS** — 200 `ready`; db `ok`; migrations in-sync; head `o5f36c4d3e2a` |
| 20 | Performance: indexes present + planner selection | **PASS** — 24/24 valid; planner selects the FK index |
| 21 | Performance: N+1 bounded query counts | **PASS** — Phase 5 query-count-independent-of-N tests green |
| 22 | Backup / restore rehearsal | **PASS** — dump → restore → upgrade → single head → sentinel counts → 297 passed / 4 skipped |
| 23 | Deployment-gate documentation + mechanisms | **PASS** — gate/runbook docs present; config warns on missing key; reconnect enforcement present; legacy plaintext columns retained nullable for rollback |
| 24 | PR #19 state | **PASS** — draft, base `main`, not merged |

**Defects found: 0.** No remediation was required.

## Release readiness

All automated gates, adversarial/authorization harnesses, migration
reversibility with sentinel preservation, security invariants (immutable audit,
no plaintext tokens, least-privilege scopes, fail-closed crypto), performance
verification, readiness endpoint, and a recorded backup/restore rehearsal are
**green**. The branch is a clean 7-commit sequence over v0.9.8 with a single
Alembic head. The code is validated and ready to merge pending owner approval and
the operational gates below.

## Remaining deferred items (unchanged, documented)

1. **WP7.5 `person_notes` migration** off flat files — business-data/schema change,
   excluded from this release.
2. **`app/models/` orphaned ORM scaffold** — broken (PEP 604 on Python 3.9),
   zero-reference; retained as documented technical debt (import test skips it).
3. **`MICROSOFT_TOKEN_KEY` rotation** — no rotation mechanism yet.
4. **Legacy plaintext token columns** (`access_token`, `refresh_token`) — retained
   nullable one release for rollback; removal deferred.

## Production deployment gates (binding — must be satisfied at deploy time)

1. `MICROSOFT_TOKEN_KEY` configured in every environment.
2. Key backed up **separately from the database**.
3. Existing Microsoft 365 connections must **reconnect once** after deploy.
4. **Live Microsoft tenant test** (manual) verifying OAuth authorization, encrypted
   MSAL cache persistence, access-token expiration, `acquire_token_silent` refresh,
   mail/calendar/document sync, and sync-health status. *Not executable in CI —
   remains an operator gate.*
5. Run migrations to head `o5f36c4d3e2a`; gate traffic on `/readiness`; run the
   Phase 4 `CONCURRENTLY` index builds in a low-traffic, monitored window.

## Post-approval release step (not done in RC12)

Release-note finalization (`CHANGELOG.md`, `README.md`, `docs/ROADMAP.md`,
marking `[Planned: 0.9.9]` → `[Implemented]`, `docs/RELEASE_0.9.9.md`) is the
release step performed **after** RC12 approval and immediately before/at merge.

## Recommendation

**SAFE TO MERGE** — subject to owner approval and satisfaction of the production
deployment gates above (notably the manual live-tenant verification, which cannot
be executed in automated validation). PR #19 remains a **draft** and is **not
merged**.
