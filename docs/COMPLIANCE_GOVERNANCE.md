# Compliance Intelligence Governance (Phase D.47)

`app/services/compliance_intelligence/governance.py` is a read-only checker that verifies the supervisory
layer stays a **composition** over the authoritative compliance/review/exception/audit/approval services and
never becomes a second compliance rules engine, approval engine, audit log, or workflow. It returns
`{ok, issue_count, findings}` and never raises into normal use. See
[`ADR-052`](adr/ADR-052-compliance-intelligence.md).

## Invariants enforced
1. **No second store / no writes.** No module defines a table (`Table(` / `define_*_tables`) or writes the
   DB (`insert`/`update`/`delete`).
2. **No second approval / audit engine.** No module calls a mutation/approval entry point —
   `submit_review` / `assign_reviewer` / `record_decision` / `write_audit_event` / exception
   raise-resolve-acknowledge-escalate / `set_status`. Approvals, decisions, and audit entries stay with
   their authoritative owner.
3. **No second event bus.** No module publishes to the outbox.
4. **No direct projection reads.** No module reads `rm_*` tables directly.
5. **Composes the authoritative sources.** The engine reuses `compliance.reviews`, `exception_engine`,
   `insurance_licensing`, and `portfolio` — no second compliance engine.
6. **Supervisor-vs-advisor separation is enforced.** The `compliance.supervise` gate is present
   (`gate.supervisor_authorized`) and applied in the engine; the advisor task projection
   (`advisor_compliance_tasks`) never returns supervisory items/exceptions or calls the review adapter, so
   supervisory-only findings can never leak to an advisor.
7. **Explainability enforced.** `is_explainable` (explanation + evidence + deep link) is present in the
   model AND applied in the engine — non-explainable items are never emitted.
8. **Registry completeness + single ownership.** Every review + exception type is fully declared; no
   duplicate keys.
9. **Governed gating.** Every gate is a runtime flag in the `GATES` registry; no raw environment fallback.

The checker excludes `governance.py` from its own source scan (it holds the detection string-literals).

## Additional guarantees proven by tests
- **Supervisory information never leaks** — advisors get `None` on every supervisory surface, a suppressed
  Compliance Oversight section, and no supervisory AI facts.
- **Every supervisory item deep-links** to an authoritative workflow and **carries supporting evidence**.
- **No mutation, no policy bypass, no runtime bypass** — verified by the architecture-invariant + gate tests.
- **AI never approves/waives/suppresses/invents** — it only summarizes counts this layer emits.

## How it runs
`validate_compliance_intelligence()` returns `{ok, issue_count, findings}`, surfaced through the internal
diagnostics (`app/services/compliance_intelligence/diagnostics.py`) on the `observability.audit` surface
(`GET /supervision/diagnostics`) and asserted clean by
`tests/test_compliance_intelligence.py::test_governance_clean`.

## Diagnostics & analytics
`compliance_diagnostics()` composes gate snapshot + in-process counters (low-cardinality — no client
identifiers, reviewer names, or supervisory evidence) + registry coverage + adapter availability +
governance: reviews/exceptions composed, overdue reviews, suppressed, missing-evidence, authorization
failures, adapter failures by source, counts by review type/severity, and average composition latency. Four
low-cardinality metrics (`supervisory_reviews_composed`, `supervisory_exceptions_composed`,
`supervisory_dashboards`, `supervisory_authorization_failures`) are registered in the platform Analytics
registry (category `compliance`).

## Observability
Following the platform's established instrumentation pattern (no span/trace API), the layer instruments
supervisory composition, dashboard generation, registry lookups, authorization, and diagnostics with an
in-process counter module (`stats.py`). It never logs client-sensitive supervisory evidence.

## References
`app/services/compliance_intelligence/governance.py`, `app/services/compliance_intelligence/diagnostics.py`,
`app/services/compliance_intelligence/stats.py`, `app/services/analytics/{sources,metrics}.py`,
`tests/test_compliance_intelligence.py`, ADR-052.
