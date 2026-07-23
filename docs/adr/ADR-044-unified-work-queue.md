# ADR-044 — Unified Work Queue: a cross-domain composition surface over the authoritative work services; not a second engine

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Work Management); Reliability / Operations; Security /
Authorization (RBAC ownership); Business Operations Owner (Michael Shelton). Authorized compliance
reviewer: Not yet designated.

## Context
D.38 built the personalized Advisor Workspace, which surfaces *what* needs attention. Advisors and
operations staff still lacked ONE governed place to *work through* those items — actionable work lives
in many authoritative services (tasks, workflow steps, exceptions, compliance reviews, documents, tax
returns, insurance cases, opportunities, meetings), each with its own list, statuses, assignment, SLA,
and deep link. `work_management.work_items` already merges the three legacy sources (tasks, workflow
steps, exceptions) with record-scope pushed into SQL, and `work_intelligence` already owns SLA/priority
math — but there was no single, filterable, paginated, cross-domain queue with saved views and safe
actions.

## Decision
Phase D.39 adds a **Unified Work Queue** at `GET /work` — a read-only COMPOSITION surface
(`app/services/work_queue/`) that normalizes actionable work from the authoritative services into one
governed queue. **It is not a task/workflow/exception/assignment engine and is never the source of
truth.**

- **Composition, not a new engine.** Adapters read bounded, record-scoped, actionable sets from the
  existing authoritative services and map them to a normalized, references-only ``UnifiedWorkItem``
  (presentation + routing metadata only; the original source status is preserved, ``status_group`` is a
  display/filter normalization). The three legacy sources are read through the existing
  ``work_management.work_items`` — the queue builds **no second task/workflow/exception engine, no
  second assignment engine, no second event bus/log, no CQRS write model, no event sourcing, and no
  shadow business logic.**
- **Actions delegate to the owning service.** Every state-changing action goes through an
  action-dispatch layer that validates the domain + action + capability + record scope and then calls
  the authoritative owning service, which enforces scope, appends its own ledger/audit, and publishes
  any domain event through the existing outbox. The dispatch layer holds **no business-state mutation
  of its own, records no duplicate audit event, and publishes no domain event.** Assignment reuses the
  single authoritative assignment engine (``work_management.assign_work`` + ``authorize_assignment_target``).
- **No new projection.** The audit found no cross-domain work projection is justified — authoritative
  composition is affordable with bounded per-adapter fetches, and count widgets reuse the D.37
  adoption-backed sources (projection when healthy+fresh, else authoritative fallback). The queue never
  reads an ``rm_*`` table directly (governance flags any such reference).
- **Saved views are presentation state only.** Built-in views are immutable constants; per-user saved
  views + default view + remembered filters live in ``work_queue_saved_views`` /
  ``work_queue_preferences`` (self-service, one row/user, gated by ``work_queue.saved_views``). They
  never alter a source record.
- **Bulk is constrained.** Only proven-safe, semantically-identical actions (claim, assign,
  acknowledge) are bulk-eligible; each item is delegated individually and partial success is reported
  honestly. No bulk-complete of heterogeneous items, no bulk-approve of compliance.
- **RBAC / scope preserved, fail closed.** A tab, filter, or item appears only where the principal has
  the capability (never shown-then-403); an adapter that cannot determine scope returns nothing. Counts
  served from a projection stay on the firm-wide path; scoped principals get the authoritative scoped
  read.

Adopted sources: tasks, workflow steps, exceptions, advisor work, compliance reviews, documents (review),
tax returns, insurance cases, opportunity follow-ups, meetings. Excluded (no safe firm-wide scoped list
of actionable items): insurance requirements, benefits enrollments/obligations, annual-review sessions,
meeting follow-ups, notifications.

## Alternatives considered
1. **A dedicated unified-work projection (`rm_unified_work`).** Rejected: authoritative composition with
   bounded fetches is affordable, a cross-domain projection would need per-domain scope anchors it does
   not have, and it risks becoming a shadow source of truth.
2. **A generic "complete/assign" mutation in the queue.** Rejected: domain semantics differ; a generic
   mutator would be shadow business logic. The queue only exposes actions the owning service supports and
   delegates to it.
3. **A second task table to hold the merged queue.** Rejected: the authoritative sources remain the sole
   record; the queue references them.
4. **Bulk-complete/approve across domains.** Rejected: heterogeneous and high-stakes; only claim/assign/
   acknowledge are bulk-eligible, per-item, with honest partial results.
5. **A new frontend framework for the queue UI.** Rejected: the app is pure SSR with progressive
   enhancement; the queue uses GET filter forms + POST-redirect-GET, no framework.

## Reasons for the decision
Advisors and operations need one execution surface without weakening any invariant: no second engine,
no second event log, no RBAC bypass, no shadow mutation, no behavior change to the owning domains.
Composing bounded, record-scoped reads into a normalized queue and delegating every action to the
authoritative owning service delivers exactly that, and preserves ADR-004/013/041/042/043.

## Consequences
### Positive consequences
- One governed, filterable, paginated cross-domain queue (10 adopted sources) with deterministic
  ordering (overdue → SLA-breached → priority → due → age → stable key), built-in + personal saved
  views, safe single + bulk actions delegated to owning services, SLA/priority presentation reusing
  authoritative logic, read-only diagnostics + governance, and an AI-ready summary. D.38 workspace
  widgets (My Work, Overdue, Due Today, Unassigned Team, SLA Breaches) deep-link into filtered views via
  the shared summary service.

### Negative consequences and tradeoffs
- Cross-domain totals are bounded by per-adapter candidate caps (federated merge); very large books show
  a bounded set, not an unbounded scan (documented; deterministic). Sources without a safe firm-wide
  scoped actionable list are excluded (documented) rather than adapted with parallel logic. Some
  sources are read/open-only in the queue (their mutations stay on their own surface).

## Enforcement
- `app/services/work_queue/` (contract, adapters, service, views, dispatch, summary, diagnostics,
  governance); `app/database/work_queue_tables.py` (registered in `schema.py`); `app/db.py` exposes the
  two tables; migration `migrations/versions/l3q4v5w6x7y8_unified_work_queue.py` (seeds
  `work_queue.saved_views`). Routes in `app/routes/work.py` (`GET /work`, `POST /work/action`,
  `/work/bulk-action`, `/work/views`, `/work/views/default`, `/work/views/delete`, `GET /work/summary`,
  `/work/diagnostics`). Template `app/templates/work/queue_unified.html`, `app/static/css/work_queue.css`.
  Workspace widgets in `app/services/workspace/{registry,widgets}.py`. The authoritative work services,
  their tables/ledgers, the outbox, the event/projection model, the runtime/policy engines, and RBAC are
  untouched. Tests: `tests/test_unified_work_queue.py`; manifest / platform-architecture / route-count /
  ADR-count guards updated.

## Exceptions
Viewing the queue reuses `work.read`; team/unassigned views reuse `capacity.read`; actions reuse the
owning services' capabilities (`work.write` for assignment via the authoritative engine, `exception.write`
for acknowledge/resolve, `documents.write` for approve); diagnostics reuse `observability.audit`. Only
`work_queue.saved_views` is new (self-service presentation state). Counts are served from a projection
only on the firm-wide (`record.read_all`) path. Excluded sources are documented, not adapted.

## Revisit conditions
Adopting an excluded source (once it gains a safe firm-wide scoped actionable list), adding a dedicated
unified-work projection (if authoritative composition becomes materially too expensive), introducing
drag-and-drop or a client-side framework, or expanding bulk beyond claim/assign/acknowledge would each
warrant a new or superseding ADR.

## References
- `app/services/work_queue/*`, `app/routes/work.py`, `app/database/work_queue_tables.py`,
  migration `migrations/versions/l3q4v5w6x7y8_unified_work_queue.py`,
  `app/templates/work/queue_unified.html`, `app/static/css/work_queue.css`
- `docs/UNIFIED_WORK_QUEUE.md`, `docs/WORK_QUEUE_ADAPTER_GUIDE.md`, `docs/WORK_QUEUE_ACTIONS.md`,
  `docs/WORK_QUEUE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`,
  `docs/platform_architecture_manifest.yaml`
- `tests/test_unified_work_queue.py`; relates to ADR-004, ADR-013, ADR-041, ADR-042, ADR-043
