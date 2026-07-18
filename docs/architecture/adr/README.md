# Architecture Decision Records (ADRs)

ADRs are the **only** sanctioned way to change Client360's architecture or the
frozen baseline documents (Engineering Constitution §3). Each ADR records a
decision, its alternatives, rationale, and consequences.

| ADR | Title | Status |
|---|---|---|
| ADR-001…012 | Founding architectural decisions (event-driven, PostgreSQL, workflow engine, evidence-first, reference documents, domain boundaries, connector framework, security model, data ownership, compliance boundaries, modular monolith, CQRS-lite) | Baselined in the Enterprise Architecture Package |
| [ADR-013](ADR-013-repository-reconciliation.md) | Reconcile the Enterprise Architecture with the existing in-place application | Accepted |
| [ADR-015](ADR-015-tamper-evident-audit-architecture.md) | Tamper-evident audit architecture (in-database per-chain SHA-256 hash chain) | Accepted |

> ADR-001…012 are catalogued in the frozen Enterprise Architecture Package. This
> directory holds ADRs recorded from ADR-013 onward; earlier ADRs may be
> transcribed here later without changing their content.
