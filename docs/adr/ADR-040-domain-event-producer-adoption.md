# ADR-040 — Enterprise Domain Event Producer Adoption: authoritative write services publish completed, references-only business facts through the existing model

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owners (People/Households, Opportunity/Referral, Operations, Documents,
Compliance, Tax, Insurance/Benefits); Reliability / Operations (governance); Security / Authorization
(RBAC + payload safety); Business Operations Owner (Michael Shelton). Authorized compliance reviewer:
Not yet designated.

## Context
Phase D.34 established a governed, typed domain-event model over the existing transactional outbox, but
only orchestration + the pre-existing workflow/runtime flows published. The major business domains still
wrote directly to their authoritative tables and ledgers without emitting typed domain facts, so other
modules (timeline, notifications, automation, integrations, analytics, audit, reporting, future plugins)
could not react asynchronously without direct inter-module dependencies. A repository audit (D.35)
identified the authoritative write boundaries across seven domains and confirmed which flows already
publish, which are regulatory/append-only and must stay unchanged, and which listed actions do **not**
exist (a document e-signature flow, a compliance "completed" status, a compliance exception domain).

## Decision
Phase D.35 adopts the D.34 model at the audited authoritative write boundaries: the major domains now
publish typed, past-tense, references-only domain FACTS through the existing standardized publisher +
transactional outbox. It is **strictly additive** and preserves every D.34 invariant:

- **Additive, behavior-preserving.** Each publish is at the authoritative write boundary, AFTER the
  business mutation succeeds, using the caller's transaction connection where available, via
  `publish_safe` so a failed publish can never corrupt the mutation (and is counted, observable).
  Consumers remain dark-launched (the outbox dispatcher is off by default), so runtime behavior is
  unchanged. No adoption site changes existing behavior.
- **Authoritative write services remain unchanged.** No append-only or regulatory ledger
  (`compliance_decisions`, `tax_return_lifecycle_events`, `tax_filing_events`, the exception ledger,
  etc.) is modified — events are added *alongside/after* the authoritative write. Runtime remains the
  sole behavior evaluator; policy the sole decision engine; orchestration the process coordinator.
- **Events describe completed business FACTS, not commands.** An event names something that already
  happened (`opportunity.won`, `tax.filing_submitted`); a consumer decides what to do with it. Events
  are published only on a genuine mutation (idempotent no-ops do not publish).
- **Payloads are references-only.** Ids, codes, status transitions, timestamps, actor references —
  never PII, secrets, tax figures, account/policy values, health data, or document contents. A
  payload-safety layer (by field name) rejects any prohibited field at publish time AND in governance.
- **No new bus, no new event log, no message broker, no duplicate domain behavior, no new direct
  service-to-service dependencies.** A domain event is a contract-validated `Envelope` in
  `outbox_events`; producers publish only through the standardized publisher; the outbox remains the
  sole internal event bus and the sole event log.
- **Event consumers may not become hidden sources of business truth.** The authoritative record stays
  in the owning domain's tables/ledgers; events are a decoupled notification of a fact, never the
  system of record. Consumers project/react; they do not own truth.
- **Governance is extended** to detect audited write paths missing a registered event, registered
  producers with no actual publishing site, publish sites using unregistered event types, contract /
  version drift, payload schema violations, **sensitive-field violations**, duplicate semantic
  contracts, orphan subscriptions, and deprecated contracts still being published.

31 typed contracts are added across 11 business domains, each with a dark-launched read-model
subscription and an actual publishing site (100% producer adoption).

## Alternatives considered
1. **Publish commands / rich payloads.** Rejected: events are facts, not commands; rich payloads would
   leak sensitive data and make consumers a shadow system of record.
2. **Move logic into consumers now.** Rejected: business logic stays in the authoritative services;
   consumers stay dark-launched. Adoption is about emitting facts, not rewiring behavior.
3. **Invent the missing flows (e-signature, compliance "completed"/exception).** Rejected: those
   boundaries do not exist in the codebase; inventing events for them would be fiction. Omitted +
   documented.
4. **Publish post-commit everywhere.** Rejected where a transaction is available: transactional publish
   (same `conn`) gives atomicity; `publish_safe` keeps a failed publish from corrupting the mutation.
5. **One event table per domain / a new bus.** Rejected: the outbox is the sole bus and log (ADR-039).

## Reasons for the decision
Decoupling the major domains through typed, governed, references-only facts — without a new bus, without
touching authoritative writes, without changing behavior, and without leaking sensitive data — is the
natural completion of the event model and preserves ADR-013/033/037/038/039.

## Consequences
### Positive consequences
- 11 business domains publish typed domain facts (100% producer adoption over the audited boundaries);
  36 total contracts, 0 governance issues. Consumers can be enabled later without producer changes.
  Analytics/observability expose producer adoption, per-domain event flow, and awaiting/dead-lettered
  events.

### Negative consequences and tradeoffs
- Some domains (exceptions, tax engagement create) publish post-commit rather than transactionally,
  where the authoritative flow already commits before its side effects — a deliberate, documented
  trade for not disturbing those flows.
- Producer adoption covers the *audited* boundaries; non-existent flows (e-signature) and lower-value
  writes are intentionally not adopted (documented as remaining).
- Every adopted write now emits an outbox row; the dispatcher stays off by default so there is no
  delivery cost until a consumer is enabled.

## Enforcement
- Contracts + subscriptions: `app/database/event_seed.py` (`D35_CONTRACTS_SEED`, `ADOPTION_MODULES`),
  migration `migrations/versions/zc2d3e4f5a6b_event_producer_adoption.py`. Payload safety:
  `app/services/events/payload_safety.py` (+ publisher + contract + governance enforcement). Publishing
  sites (15 modules): people (`people.py`, `matching/promote.py`, `person_merge.py`, `routes/households.py`),
  opportunity/referral, operations (`tasks.py`, `projects.py`), exception engine, documents, compliance
  reviews, tax (`tax_domain.py`, `tax_return_lifecycle.py`), insurance, benefits enrollment. Governance
  scan + new finding types in `app/services/events/governance.py`. Diagnostics/analytics
  (`registry.producer_adoption`, `diagnostics.events_by_domain`, `sources.py`/`metrics.py`), route
  `app/routes/events.py::/events/producers`. The outbox, the envelope, the authoritative ledgers, the
  runtime/policy/orchestration engines, RBAC, and infrastructure config are untouched. Tests:
  `tests/test_domain_event_adoption.py`; manifest / platform-architecture / route-count / ADR-count
  guards updated.

## Exceptions
Post-commit publishing for the exception engine and tax engagement creation (their authoritative flows
commit before side effects). Non-adopted / non-existent flows (document e-signature, a compliance
"completed" status, a compliance exception domain) are omitted by design. `administrator`/
`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Enabling a live consumer for a domain event, adopting additional write boundaries, adding an
e-signature or other net-new flow, or changing the references-only payload policy would each warrant a
new or superseding ADR.

## References
- `app/database/event_seed.py`, `app/services/events/{payload_safety,publisher,contracts,registry,
  governance,diagnostics}.py`, the 15 adoption modules, migration
  `migrations/versions/zc2d3e4f5a6b_event_producer_adoption.py`, `docs/DOMAIN_EVENT_PRODUCER_ADOPTION.md`,
  `docs/DOMAIN_EVENT_CATALOG.md`, `docs/DOMAIN_EVENT_PAYLOAD_SAFETY.md`,
  `docs/DOMAIN_EVENT_OPERATIONS_RUNBOOK.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_domain_event_adoption.py`; relates to ADR-013, ADR-033, ADR-037, ADR-038, ADR-039
