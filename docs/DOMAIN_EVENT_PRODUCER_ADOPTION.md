# Domain Event Producer Adoption (Phase D.35)

Phase D.35 expands the governed D.34 domain-event model beyond orchestration: the major business domains
now publish typed, past-tense, **references-only** domain FACTS at their authoritative write boundaries,
through the existing standardized publisher and transactional outbox. It is **strictly additive** — no
new bus, no new event log, no message broker, no duplicate domain behavior, no new direct
service-to-service dependencies, and no behavior change while the dispatcher is disabled (the default).

## Principles

- **Facts, not commands.** An event names something that already happened (`opportunity.won`,
  `tax.filing_submitted`). Consumers decide what to do with it.
- **Published after the mutation, on a genuine change.** The event is emitted after the business write
  succeeds, only when a real mutation occurred (idempotent no-ops do not publish).
- **Transactional where a connection is available; safe always.** Publishing uses the caller's `conn`
  (atomic with the business change) via `publish_safe`, so a failed publish can never corrupt the
  mutation and is counted (observable).
- **References-only payloads.** Ids, codes, status transitions, timestamps, actor references — never
  PII, secrets, tax figures, account/policy values, health data, or document contents (see
  `docs/DOMAIN_EVENT_PAYLOAD_SAFETY.md`).
- **Authoritative writes unchanged.** No append-only/regulatory ledger is modified; events are added
  alongside/after the authoritative write. Runtime stays the sole evaluator, policy the sole decision
  engine, orchestration the coordinator.
- **Consumers are not sources of truth.** The system of record stays in the owning domain; consumers
  project/react. Consumers stay dark-launched until explicitly enabled.

## Adopted domains & publishing sites

| Domain | Events | Publishing site(s) |
|---|---|---|
| People | `people.person_created`, `people.person_updated`, `people.identity_merged` | `matching/promote.py::_create_person`, `people.py::update_person_contact`, `person_merge.py::merge_source_contacts` |
| Households | `households.household_created`, `households.membership_changed` | `routes/households.py` (create / save member) |
| Opportunity | `opportunity.created`, `.stage_changed`, `.won`, `.lost` | `opportunity/service.py` (create / change_stage / close) |
| Referral | `referral.recorded` | `referral/service.py::create_referral_source` |
| Operations | `operations.task_created`, `.task_completed`, `.project_created`, `.project_status_changed` | `operations/tasks.py`, `operations/projects.py` |
| Exceptions | `exception.opened`, `exception.resolved` (covers tax/benefits/insurance/operations) | `exception_engine.py::raise_exception` / `resolve` |
| Documents | `document.registered`, `.status_changed`, `.archived` | `document_platform/service.py` (create / _transition) |
| Compliance | `compliance.review_opened`, `.approval_granted`, `.approval_denied` | `compliance/reviews.py` (submit / record_decision) — AFTER the append-only ledger |
| Tax | `tax.engagement_created`, `.return_status_changed`, `.filing_submitted`, `.filing_acknowledged` | `tax_domain.py`, `tax_return_lifecycle.py` |
| Insurance | `insurance.case_created`, `.application_status_changed`, `.policy_issued` | `insurance.py` (create_case / update_case_status / update_policy_status) |
| Benefits | `benefits.enrollment_created`, `.enrollment_status_changed` | `benefits_enrollment.py` |

**31 contracts across 11 business domains, 100% producer adoption** (every registered contract has an
actual publishing site — governance verifies this by scanning the adoption modules).

## Deliberately NOT adopted (do not exist — never invented)

- **Document e-signature / signature-requested / document-signed** — no e-signature flow, table, or
  `signed` status exists in the platform.
- **Compliance "review completed" status** — no such status; a completed review IS a recorded decision,
  represented by `compliance.approval_granted` / `approval_denied`.
- **Compliance exception created/resolved** — compliance is not an exception-engine domain; the shared
  `exception.opened`/`resolved` events (carrying the exception `code`) cover tax/benefits/insurance/
  operations exceptions.

## Governance & diagnostics

Governance (`app/services/events/governance.py`) scans the adoption modules and flags
producer-without-publishing-site, unregistered-publish-site, deprecated-contract-published,
sensitive-field-violation, duplicate-semantic-contract, plus the D.34 checks. Producer-adoption
diagnostics (`registry.producer_adoption`, `diagnostics.events_by_domain`, `GET /events/producers`)
report active vs stale producers, adoption coverage, and the per-domain event flow.

## Enabling a consumer (later)

Add the consumer's `outbox.subscribe(event_type, handler)` to a `register_*_consumers()` in the
scheduler's gated outbox block (dark-launched with the dispatcher). Handlers must be idempotent
(at-least-once delivery; the outbox tracks processed events). See `docs/DOMAIN_EVENT_OPERATIONS_RUNBOOK.md`.
