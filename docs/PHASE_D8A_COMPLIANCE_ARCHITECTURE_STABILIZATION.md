# Phase D.8A — Compliance Architecture Stabilization Audit

A stabilization checkpoint over the D.5–D.8 Advisor-Intelligence + compliance stack.
**Zero functional expansion**: no new recommendation/review/decision/authority types,
no new workflow, no automated compliance decisions, no reviewer fabrication, no
authority seeding. Only behavior-preserving deduplication and guardrails were applied.
Implemented on `release/0.13.0`.

## Audited architecture & dependency direction
```
Advisor Intelligence (advisor_intelligence.py)          — rule execution
        │ get_client_signals / list_registered_signals   (one-way read)
        ▼
Rule Catalog (compliance/rule_catalog.py)                — governance metadata (D.6)
        │ RuleCatalog / compare_versions
        ▼
Compliance Review (compliance/reviews.py)                — review + append-only decision ledger (D.7)
        │ reviewer_authority() lookup
        ▼
Reviewer Authority (reviewer_authority.py lookup + authority_admin.py admin, D.8)
```
Routes: `routes/compliance.py`. Persistence: `database/compliance_tables.py` + `app.db`
reflection + migrations `e7c8…` (D.7) and `f8a9…` (D.8). D.6 shipped no migration.

**The dependency direction points downward only, with no violations found.** Advisor
Intelligence imports no compliance; Rule Catalog imports only Advisor Intelligence;
Reviewer Authority administration never executes AI rules; the authority *lookup* is a
leaf; services never import routes/templates; database modules import neither. These are
now asserted by a single consolidated test (previously scattered across three files).

## Domain ownership (confirmed correct — nothing moved)
- **Rule production** → Advisor Intelligence. **Governance metadata** → Rule Catalog.
  **Review creation / transitions / decision validation** → `reviews.py`. **Authority
  lookup** → `reviewer_authority.py`. **Authority administration** → `authority_admin.py`.
  **Authorization** → server-side `require_capability` on every route. **Persistence** →
  the tables/migrations. **Rendering** → templates.
- Lifecycle/transition rules already live in services, not routes or templates; routes
  only translate HTTP; templates render server-prepared data and gate controls but never
  as the *only* enforcement. No ownership was objectively misplaced, so **no behavior was
  moved between layers**.

## Duplication findings & disposition
| Finding | Sites | Disposition |
|---|---|---|
| `now()` (`datetime.now(UTC)`) | reviews, authority_admin | **Extracted** → `compliance/_common.py::now` |
| Stale-load (`FOR UPDATE` + expected_status) | reviews, authority_admin | **Extracted** → `_common.py::load_for_update` (exact domain error types/messages preserved) |
| Pagination envelope (clamp + ceil-div) | reviews, authority_admin | **Extracted** → `_common.py::clamp_page` / `page_count` (exact math) |
| Form field reader (`parse_qs` + trimmed `_one`) | routes ×5 | **Extracted** → `compliance.py::_read_form` (exact strip/default) |
| Dependency-direction assertions | 3 test files, AI-only | **Consolidated** → `tests/test_compliance_dependency_direction.py` (full chain) |

## Abstractions introduced (each: ≥2 real consumers, one responsibility, no behavior change)
- **`compliance/_common.py`** — `now()`, `load_for_update(...)`, `clamp_page()`,
  `page_count()`. Consumed by `reviews.py` and `authority_admin.py`.
- **`compliance.py::_read_form`** — one urlencoded-body field reader; five route consumers.

## Abstractions deliberately rejected
- **Generalized workflow / state-machine engine** — the review and authority lifecycles
  have genuinely different domain rules (authority-gated decide vs authority lifecycle);
  a generic engine would hide those rules. Only the mechanical stale-load is shared;
  the transition *sets* stay in each service.
- **Shared snapshot utility** — the recommendation snapshot (`Signal.to_dict`), authority
  evidence snapshot (date→str), and decision evidence (list passthrough) have
  domain-specific shapes with no identical-output two-consumer case; a forced util would
  risk serialized-output drift.
- **Append-only migration/SQL helper** — the trigger idiom recurs across 12 already-merged
  migrations, but this phase adds no new schema migration (0 new consumers) and merged
  migrations must not be rewritten. Deferred until the next real migration needs it.
- **Capability-string constants module** — capabilities are inline literals, not a
  defined-constant duplication; centralizing them is cosmetic and would touch auth wiring.
- **DB-level conflicting-active-scope constraint** — jsonb-scope overlap isn't a simple
  constraint; the transactional service check stands. Deferred.

## Authorization boundaries (unchanged)
Six distinct capabilities (`compliance.review.read/submit/assign/decide`,
`compliance.authority.read/manage`) each enforced server-side via `require_capability`.
Templates hide manage/decide controls but never as the sole enforcement (proven by the
D.7/D.8 gating tests). `audit.read`/`decide`/admin-role-alone are not used for authority
administration. No capability merged, no server-side check weakened.

## Snapshot ownership
Recommendation snapshots are `Signal.to_dict()` (owned by Advisor Intelligence and pinned
by the D.5 golden); decision/authority evidence snapshots are owned by their respective
services. Snapshots remain immutable copies (never live references). Unchanged.

## Lifecycle ownership
Review lifecycle (`reviews.py`: `OPEN_STATUSES`, `_ASSIGN_FROM`, `_DECIDE_FROM`,
`_DECIDED_STATES` + the approval double-gate) and authority lifecycle
(`authority_admin.py`: `_TRANSITIONS` + supersede rules) remain fully owned by their
services. Only the shared stale-load mechanics were extracted.

## Append-only ledger patterns
`compliance_decisions` and `reviewer_authority_events` each use the platform's
`prevent_*_mutation()` + `BEFORE UPDATE OR DELETE` trigger idiom (shared by
`audit_events`/`exception_events`/`workflow_events`). They stay separate tables (never
combined); update/delete blocking is test-covered for both. A reusable migration helper
is deferred to the next schema migration.

## Migration consistency
Chain `d4c5o6m7d8i9 → e7c8o9m1p2q3 (D.7) → f8a9u1t2h3r4 (D.8)` — revision order correct,
upgrade/downgrade complete and **verified round-trip** (downgraded one revision at a time
to pre-D.7 and re-upgraded to head), constraints/indexes named, FK behaviors sane, status
CHECKs present, triggers cleaned on downgrade, capability seeding + administrator
superuser invariant + compliance-role composition honored, **empty authority-catalog
invariant preserved, no fabricated backfills**. **No defect found → no forward migration
was created** (this phase is code-only).

## Data-integrity invariants (verified, all server-side)
Open-review partial-unique · decision + authority-event append-only · stale-write
protection · decision & authority superseding references · no circular superseding · no
active authority with empty scope · no self-administration · inactive principals cannot
exercise authority · expired/suspended/revoked/superseded authority cannot approve ·
exact Rule-Catalog version required (no silent latest-version substitution). None of these
lives UI-only.

## Security
Sort fields whitelisted (dict lookup) · page sizes bounded (≤200) · scope-first preserved
(queue book-scoped; authority admin firm-level by capability) · no mass-assignment
(explicit fields) · Jinja autoescape on evidence/comments · domain-string errors (no
leakage) · UI-control hiding is never the sole enforcement. No firm-wide broadening.

## Testing strategy
Behavioral coverage retained; the D.5 golden regression is preserved and re-run green.
Dependency guards consolidated into one file. Append-only leftovers handled by the
documented shared-DB convention. The route-count guard remains an intentional exact
invariant. No behavioral test was replaced by a source-text assertion.

## Invariants preserved (behavior equivalence)
Serialized Advisor-Intelligence signals · rendered AI HTML · Rule Catalog contents ·
review eligibility · review lifecycle outcomes · decision validation outcomes · authority
lookup outcomes · authority lifecycle outcomes · capability assignments · route paths ·
default sorting · pagination · authorization denials · final-approval blocking reasons —
all unchanged (D.5 golden + full D.5–D.8 suites green; 1419 passed).

## Remaining technical debt (deferred, not blocking)
- Append-only trigger idiom would benefit from a migration helper — introduce it with the
  next schema migration (no consumer this phase).
- A DB-level conflicting-active-scope guard (beyond the transactional service check).
- The route-count guard is exact/fragile by design; a structural alternative is optional.

## Readiness recommendation for D.9
**D.5–D.8 are stable and ready for D.9.** The dependency direction is clean and now
guarded by tests; domain ownership is correct; authorization, append-only history,
scope-first, and final-approval blocking are enforced server-side and test-covered; the
migration chain round-trips; and the compliance services now share small, single-purpose
helpers with no behavior change. No blocking defects were found.
