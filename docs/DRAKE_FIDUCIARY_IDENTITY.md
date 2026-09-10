# Drake fiduciary identity — why 1041 rows quarantine, and how a human resolves one

## The problem in one sentence

Drake's client export does not carry the filing entity's EIN on a fiduciary return, so some estate
and trust rows arrive with no usable taxpayer identifier and are quarantined; the EIN can only reach
Client360 as evidence a person read, through an explicit adjudication.

## What the export actually contains

The client export (`CLIENT.CSV`, or `<year>.CSV`) has two identifier columns, `TP_Social` and
`SP_Social`. On an individual return `TP_Social` is the taxpayer's SSN. On a business return
(1065 / 1120 / 1120S / 990) it is the entity's EIN.

On a **1041 it is the decedent's SSN**, not the estate or trust EIN. Two facts establish this:

- For every estate and trust client, the identifier Drake holds in its own client index differs from
  the value the export emits, and none of those EINs appears anywhere in the exported file.
- Where the export does emit a value for an estate, that value belongs to a separate individual
  client record — the deceased person.

So two shapes legitimately export a blank identifier:

| Shape | Why it is blank |
|---|---|
| A decedent's estate with no decedent SSN recorded | the column's source field was never filled in |
| A living trust | there is no decedent, so there is no SSN for the column to hold |

The blank is not a Drake data error and not a parsing fault. The rows are full width and correctly
aligned; the export simply has no column for what identifies the filing entity.

## Why the row is quarantined, and why that stays

`app/services/drake_return_identity.py` refuses to derive an identity with no taxpayer identifier and
marks the row `unidentified_no_taxpayer_identifier`. Keying such rows on "no identifier" would merge
two unrelated estates into one return, so the refusal is correct.

**Unattended import behaviour is unchanged by anything in this document.** No name fallback, no
address fallback, no relaxation. `tests/test_drake_entity_adjudication.py` pins that a full-width
1041 row with a blank identifier still quarantines, both on its own and after the entity behind it
has been adjudicated.

## Why the export must not be "fixed"

Re-exporting changes nothing: the EIN is already correct in Drake and the export does not emit it.

Typing a value into the identifier column is worse than the quarantine. Entering the decedent's SSN
would populate the field, but the importer would then derive a **person** hash and key the filing
entity to a natural person — the wrong legal subject, and for a living trust an outright fiction.
Entering the EIN into the individual-SSN field would misrepresent what Drake holds.

## The supported resolution: human-approved adjudication

`app/services/drake_entity_adjudication.py` binds a verified entity identifier to an entity that
already exists.

```
filed return / IRS notice / Drake client index
    -> EIN read by a person
    -> hash derived inside the service, never supplied by the caller
    -> drake_business_identity, trust_level = human_approved
    -> the existing relationship_entities row
    -> entity_source_links -> Drake source contacts -> the quarantined returns
```

### What it writes

| Table | What |
|---|---|
| `drake_business_identity` | one row per identifier hash and subject type, carrying the entity link and the human-approval fields |
| `entity_source_links` | one row per adjudicated Drake source contact |
| `audit_events` | one `drake.entity_identity_adjudicated` entry |

`drake_client_returns` is never touched. The quarantined rows keep their null identifier hash, their
status and the raw export row exactly as Drake wrote it, because the export genuinely did not contain
an EIN and the database must not claim otherwise. Attribution runs through the source contacts.

### What it refuses

The entity must already exist, be active, and be of the type the returns describe. The service never
creates an entity and offers no lookup by name or address: the caller names the entity id, because a
name is not evidence of entity identity.

Refusals carry a stable code: a missing approver, no evidence, no source records, an identifier with
no digits, an SSN identifier type, an identifier already denoting a natural person or another entity,
a source record already attributed elsewhere, a person or mixed return history, or returns with no
tax year. Every refusal writes nothing.

### Provenance requirements

At least one evidence reference is required, and the approving user and timestamp are required. The
`Evidence` record is generic — a kind, a reference and an optional detail — and the service never
resolves or validates a reference, so no document id or path is hard-coded anywhere in the logic.
The database enforces attribution independently: a check constraint on both tables refuses
`human_approved` without a confirming user and timestamp.

### Audit requirements

The audit entry answers, without reading any other table: who approved it, when, which entity was
selected, what identifier type was verified, the identifier hash used, which source records were
linked and which were already present, which evidence supported the decision, the trust level, and
that this was a human adjudication.

The raw EIN is deliberately absent from the audit entry. The non-reversible hash identifies the
taxpayer, and an audit trail is not a place to accumulate plaintext tax identifiers.

### Idempotency

Re-running an identical adjudication creates nothing and reports `changed` as false. Re-running with
an extra source record adds only that record's link. Both are pinned by tests.

## The identifier hash

`app/services/drake_identifier.py` is the single definition of the salted `SHA-256(KEY : digits)`
used across the system. The import driver imports it rather than defining its own, because two
implementations that drift by one character produce two identities for one taxpayer. Only the digits
are hashed, so formatting is not identity, and a value with no digits has no hash rather than a hash
of the empty string.

An adjudicated entity is therefore findable by exactly the hash a future export would produce, if one
ever carried the EIN.

## Related code

- `app/services/drake_return_identity.py` — the quarantine rule
- `app/services/drake_return_subject.py` — which legal subject a return describes
- `app/services/drake_subject_routing.py` — unattended ingestion's write boundary
- `app/services/link_trust.py` — the trust vocabulary
- `tests/test_drake_entity_adjudication.py` — the tests for everything above
