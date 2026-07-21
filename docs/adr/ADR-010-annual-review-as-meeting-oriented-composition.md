# ADR-010 — Annual Review as meeting-oriented composition

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Annual Review); Business Operations Owner (Michael Shelton).

## Context
Advisors need a single place to run an annual client meeting: "what do I need to review with this
client today?". This could be misbuilt as a new system of record for planning, or as a place where
checking a box implies compliance approval. It needed to stay a meeting-oriented composition.

## Decision
Annual Review **must** be a **meeting-oriented composition layer** anchored to a **person**:
- It **composes** Client Snapshot, Advisor Intelligence, Advisor Work, Activity Timeline,
  Compliance, Portfolio, and Meeting Prep, each **gated on its owning capability**.
- It **owns only** `annual_review_sessions` (a mutable advisor-activity record: notes + a
  presentation-only checklist).
- It **must not** become the source of truth for any underlying domain, **must not** duplicate
  business planning, and **must not** treat checklist completion as compliance approval.
- Session lifecycle is `draft → in_progress → completed → archived`; "start review" is
  **idempotent** (at most one OPEN session per advisor per client, enforced by a partial-unique
  index).

## Alternatives considered
1. **Annual Review owns copies of portfolio/tax/benefits data** for the meeting. Rejected:
   duplicates source data and drifts; composition reads keep it live.
2. **Checklist completion marks items "compliant/approved".** Rejected: conflates advisor activity
   with regulatory approval (ADR-008); the checklist is presentation-only.

## Reasons for the decision
A thin session record plus live composition gives a useful meeting surface without a new system of
record or a compliance-approval shortcut.

## Consequences
### Positive consequences
- One meeting surface reusing live domain data; idempotent sessions; no duplication.

### Negative consequences and tradeoffs
- The "advisor" shown is the current principal (no per-client servicing-advisor field).
- Sessions are mutable (not an append-only ledger) — intentional for a working checklist.

## Enforcement
- `app/services/annual_review.py` (`compose_workspace`, session lifecycle, `_compliance_summary`
  counts-only); owned table `annual_review_sessions` + partial-unique OPEN guard (migration
  `i9a1n2r3e4v5`).
- Per-section capability gating and no-source-mutation verified in `tests/test_annual_review.py`.

## Exceptions
None currently approved.

## Revisit conditions
If a per-client servicing-advisor field is introduced, update the snapshot. Any move toward owning
planning data would require a superseding ADR.

## References
- `app/services/annual_review.py`; migration `i9a1n2r3e4v5_annual_review_workspace.py`
- `docs/PLATFORM_ARCHITECTURE.md` §16 (Annual Review architecture)
- `tests/test_annual_review.py`, `docs/PHASE_D11_ANNUAL_REVIEW_WORKSPACE.md`
