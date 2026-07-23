# Event Governance Guide (Phase D.34)

`app/services/events/governance.py::validate()` is a **read-only** validator of the domain-event model.
It reads the contract + subscription registries and the in-code contracts read-only (the outbox remains
the sole bus; the runtime engine the sole evaluator; the policy engine the sole decision engine) and
returns `{ok, issue_count, findings, coverage}`. It never raises and never edits anything.

## Finding types

| Finding | Meaning |
|---|---|
| `unregistered_contract` | An in-code contract with no active registry row (a producer could publish an event the registry doesn't record). |
| `orphan_contract` | An active registry row with no in-code contract (nothing can validate/publish it). |
| `orphan_subscription` | An active subscription whose event type has no active contract. |
| `producer_without_consumer` | An active contract with no active subscription (an event nobody consumes). |
| `schema_violation` | A contract whose payload schema declares an unknown field type (a malformed contract). |
| `version_drift` | A contract `schema_version` the envelope does not support, or a registry/code version mismatch. |
| `deprecated_reference` | An active contract depends on — or a subscription targets — a deprecated/retired event. |
| `invalid_ownership` | An active contract with neither an owner nor a producer. |
| `governance_check_error` | The validator caught an unexpected error (never raised). |

## Coverage in the report

`registry.coverage()` reports domain coverage (event domains with a contract ÷ identified domains =
**100%**), consumer coverage (active contracts with a subscription ÷ active = **100%**), and producer
coverage (active contracts with a producer ÷ active = **100%**).

## Outbox + envelope integration

Governance consumes the registries and the `app/platform/events.py` supported-version set read-only — it
never triggers delivery, never mutates the outbox, and never makes a business decision. It ensures the
typed model stays coherent with the one bus.

## Recording

`governance.record_validation()` runs validation and records a `domain_event.governance_validated` event
to the shared audit hash-chain (a major lifecycle action). Routine published events are never recorded
to the audit chain — the outbox is their log.

## Routes

`GET /events/governance` (`observability.audit`) — the report.
`POST /events/governance/validate` (`observability.execute`) — run + record.

## Current state

0 governance issues · domain coverage 100% · consumer coverage 100% · producer coverage 100%.
