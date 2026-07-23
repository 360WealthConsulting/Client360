# Domain Event Payload Safety (Phase D.35)

Domain-event payloads carry **references only** — ids, codes, status transitions, timestamps, actor
references, and non-sensitive metadata. They must **never** carry PII, secrets, tax figures,
account/policy values, health data, or document contents. Event payloads are stored in the outbox and
delivered to consumers, so a sensitive value would leak beyond its authoritative, capability-gated home,
and an event consumer could become a shadow copy of regulated data. This is a hard invariant.

## What is allowed

- Entity **ids** (`person_id`, `opportunity_id`, `document_id`, `enrollment_id`, …) — references, not
  values; resolving them still requires the appropriate capability + record scope.
- **Codes** and **statuses** (`status`, `from_status`, `to_status`, `stage`, `case_type`, `code`,
  `filing_status`, `coverage_tier`, `resolution_code`, `classification`) — controlled vocabularies.
- **Timestamps** and **actor references** (an actor user id), correlation/causation references.
- Field **names** (e.g. `changed_fields` on `people.person_updated`) — never the field *values*.

## What is prohibited (rejected by name)

`app/services/events/payload_safety.py` rejects a payload (or a declared schema) whose field name
contains a prohibited substring — enforced at publish time (the publisher raises/`publish_safe` drops)
AND in governance (`sensitive_field_violation`). Prohibited categories:

- **Identifiers of a person / tax:** ssn, social security, ein, tin, tax_id, national id, passport.
- **Direct contact PII:** first/last/full name, email, phone, fax, address, street, city, zip, postal.
- **Dates of birth:** dob, date_of_birth, birth.
- **Financial values:** amount, balance, aum, account value/number, market value, premium, salary,
  income, wage, compensation, revenue, valuation, price, cost, fee, commission, payment, deposit.
- **Policy/account sensitive numbers:** policy number, certificate/member number, routing, iban, card.
- **Health / benefits PHI:** phi, diagnosis, medical, medication, health condition, disability,
  coverage amount.
- **Secrets:** password, secret, token, credential, api key, private/access key.
- **Free text / document contents:** content, body, plaintext, note(s), comment(s), file data,
  attachment, raw.

Reference field names that could trip a substring (e.g. `state`, `stage`, `status`, `from_status`,
`to_status`) are on an explicit allow-list. `"age"` is intentionally NOT a bare prohibited substring (it
false-matches cover**age** / st**age** / eng**age**ment); a standalone age field is caught by
`dob`/`date_of_birth`/`birth`.

## Detection is by NAME, never by value

The payload-safety layer inspects only field **names** — it never reads a value. This keeps the model
itself from becoming a place sensitive data flows through, and makes enforcement deterministic and
testable.

## Enforcement points

1. **Publisher** — `publish()` rejects a payload containing a prohibited field (`EventError`);
   `publish_safe()` swallows + counts it. Every business publish is validated before it can reach the
   outbox.
2. **Contract catalog** — `EventContract.sensitive_schema_fields()` flags a declared schema field that
   is prohibited.
3. **Governance** — `sensitive_field_violation` reports any registered contract whose schema declares a
   prohibited field, so a bad contract can never ship.

Tests (`tests/test_domain_event_adoption.py`) assert every D.35 contract is references-only and that a
sensitive payload is rejected at publish time.
