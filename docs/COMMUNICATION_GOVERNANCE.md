# Communication Governance (Phase D.44)

`app/services/communications/engagement/governance.py` is a read-only checker that verifies the unified
communications layer stays a **composition** over authoritative services and never becomes a second
messaging / timeline / notification / document / scheduling / audit / event system. It returns a structured
`{ok, issue_count, findings}` report and never raises into normal use. See
[`ADR-049`](adr/ADR-049-unified-communications.md).

## Invariants enforced
1. **No second store / no writes.** No engagement module defines a table (`Table(` / `define_*_tables`),
   writes the DB (`insert` / `update` / `delete`), or otherwise mutates.
2. **No second event bus.** No module publishes to the outbox (`publish` / `publish_safe`).
3. **No second audit system.** No module writes audit events (`write_audit_event`).
4. **No direct projection reads.** No module reads `rm_*` projection tables directly — it composes through
   the authoritative reads.
5. **Reuses the authoritative reads.** The composition references `activity_timeline` and the D.43 portal
   reads (no shadow timeline, no raw domain fan-out); the advisor spine specifically composes the
   authoritative projection.
6. **Registry completeness.** Every interaction type declares an authoritative owner, source service,
   retention class, deep link, a valid lifecycle, and a valid visibility.
7. **External safety.** The external portal composition never emits an internal-only interaction type.
8. **Governed gating.** Every gate is a runtime flag in the `GATES` registry — no raw environment fallback.

The checker excludes `governance.py` from its own source scan (it holds the detection string-literals and
would self-match) — it enforces by checking, not by doing.

## How it runs
`validate_engagement()` returns `{ok, issue_count, findings}`. It is surfaced through the internal
diagnostics (`app/services/communications/engagement/diagnostics.py`) on the `observability.audit` surface
(`GET /engagement/diagnostics`) and asserted clean by
`tests/test_unified_communications.py::test_governance_clean`.

## Diagnostics
`engagement_diagnostics()` composes the gate snapshot, in-process counters (low-cardinality — no client
identifiers, subjects, or previews), registry coverage, adapter availability, interaction counts by type,
adapter failures by source, suppression/duplicate counts, average composition latency, and the governance
report. Internal-only.

## Analytics
Three low-cardinality metrics are registered in the platform Analytics registry:
`engagement_interactions_composed`, `engagement_searches`, `engagement_adapter_failures` — all sourced from
in-process counters, never client data.

## Relationship to platform governance
The layer reuses the authoritative Runtime Engine (sole evaluator), the transactional outbox (sole event
bus — D.44 adds none), the audit ledger, the activity timeline (sole timeline), and the
document/communication/scheduling/portal services (sole mutation layers). Governance is the executable proof
that these boundaries hold.

## References
`app/services/communications/engagement/governance.py`,
`app/services/communications/engagement/diagnostics.py`, `app/services/analytics/{sources,metrics}.py`,
`tests/test_unified_communications.py`, ADR-049.
