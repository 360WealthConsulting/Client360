# 3CX Phone System v20 — Client360 custom CRM connector

Client360 answers two questions for the phone system: **who is calling this number**, so 3CX can
open the client's profile on the answering advisor's screen, and **this call just ended**, so it
lands in the client's communication history alongside their email and text.

The connector is the **server half only**. 3CX calls Client360; Client360 never calls 3CX, holds
no 3CX credential, and reads no recording or transcript. Installing the template is a deliberate
act performed by an administrator in the 3CX management console — nothing in this repository
uploads it.

---

## What was built

| Piece | Where |
| --- | --- |
| Configuration (all switches default to off) | [`app/integrations/threecx/config.py`](../app/integrations/threecx/config.py) |
| Authentication gate | [`app/integrations/threecx/auth.py`](../app/integrations/threecx/auth.py) |
| Exact phone lookup | [`app/integrations/threecx/lookup.py`](../app/integrations/threecx/lookup.py) |
| Template renderer | [`app/integrations/threecx/template.py`](../app/integrations/threecx/template.py) |
| Call journaling (PBX-neutral) | [`app/services/communications/call_journal.py`](../app/services/communications/call_journal.py) |
| Phone normalize / mask / dial helpers | [`app/services/communications/phone_numbers.py`](../app/services/communications/phone_numbers.py) |
| HTTP endpoints | [`app/routes/threecx.py`](../app/routes/threecx.py) |
| Template to upload | [`deploy/3cx/client360-3cx-crm-template.xml`](../deploy/3cx/client360-3cx-crm-template.xml) |
| Tests | [`tests/test_threecx_connector.py`](../tests/test_threecx_connector.py) |

No database migration was needed. `phone_log` has been a legal `communication_messages.channel`
since the communications platform shipped, and `communication_message_sources` already enforces
`UNIQUE (source_system, source_external_id)`, which is what makes journaling idempotent.

---

## Endpoints

Both are `POST`, and both are called only by the PBX.

### `POST /api/integrations/3cx/lookup`

```json
{ "number": "+1 (555) 010-0000" }
```

Answers with a `contacts` array holding **exactly one** entry when the number matches one active
client, and an **empty** array for both no match and several matches:

```json
{ "found": true, "match_count": 1, "contacts": [ {
    "person_id": 4211, "entity_id": "4211", "entity_type": "person",
    "display_name": "Bill Carter", "first_name": "William", "last_name": "Carter",
    "email": "bill@example.com",
    "contact_url": "https://client360.example.com/people/4211" } ] }
```

### `POST /api/integrations/3cx/calls`

```json
{ "call_type": "Inbound", "number": "+15550100000", "agent": "101",
  "duration": "00:03:21", "started_at_utc": "2026-09-11T14:03:00Z", "entity_id": "4211" }
```

Answers `{"journaled": true, "created": true, "duplicate": false, "message_id": 91,
"person_id": 4211}`. A retry of the same call answers `created: false, duplicate: true` with the
original `message_id`.

---

## Authentication

A **dedicated integration secret**, and nothing else.

* 3CX presents it as `Authorization: Bearer <secret>`. The comparison is constant-time.
* It is **not** a Client360 login, an MCP token, or any staff credential. 3CX stores template
  parameters as readable configuration, so whatever goes there is disclosed to every PBX
  administrator and replayed on every call. A normal credential there would hand the whole
  application to the phone system.
* Revoking it is one environment variable, with no effect on any person's account.
* Authority is bounded by what the two endpoints can do at all — read one person's name and
  profile URL for a number the PBX already has, and append one call record. Widening that means
  adding an endpoint, which is a visible code change rather than a configuration change.

Both paths are listed in `PUBLIC_EXACT` so the staff session middleware does not intercept them,
the same arrangement `/mcp` and the SharePoint webhook already use. "Public" there means *no
session cookie required*, not *unauthenticated*: the route authenticates every request before any
query runs. Because the endpoints honour no ambient credential, a browser cannot be made to call
them with a signed-in user's authority, so they are not CSRF-reachable.

**Fail closed.** Both endpoints return `404` — not `401` — unless the connector is switched on
*and* a secret of at least 32 characters is configured. A probe learns nothing about whether this
deployment has a telephony surface, and a half-finished rollout cannot leave an unauthenticated
surface live.

---

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLIENT360_3CX_ENABLED` | `false` | Master switch. Off means both endpoints 404. |
| `CLIENT360_3CX_INTEGRATION_SECRET` | *(unset)* | The dedicated bearer secret. Under 32 characters counts as unset. |
| `CLIENT360_3CX_INSTANCE` | `default` | Which PBX these records came from, for provenance. |
| `CLIENT360_3CX_DIAL_SCHEME` | `tel` | URI scheme for the click-to-call link. `tel` or `callto`. |
| `PUBLIC_BASE_URL` | *(unset)* | Already required elsewhere. Without it the lookup still returns the client's name, but no screen-pop URL. |

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

---

## Lookup behaviour

**Exact normalized-phone equality only.** No prefix, suffix, substring, fuzzy or last-N-digits
matching, and no fallback to name or email. Comparison is against `people.normalized_phone` under
the repository's one normalization convention — digits, with a leading US `1` dropped — which is
also what the AssetMark, Schwab and Wealthbox importers wrote.

**One match, or none.** A screen-pop is acted on before anyone speaks, so a wrong answer is worse
than no answer: an advisor who opens Jane Smith's profile and greets the caller by her name has
disclosed that Jane is a client of this firm to whoever actually rang.

| Situation | `contacts` | What 3CX does |
| --- | --- | --- |
| One active client on the number | one entry | Opens that client's profile |
| No client on the number | empty | Nothing |
| Two or more people share the number | empty, `ambiguous: true` | Nothing |
| The matching record is inactive | empty | Nothing |

A couple sharing a mobile is **not** resolved to their household here, even though
`sms_ingest` may file a text that way: a household has no single profile to pop and no single name
to greet.

This is enforced by the *response shape*, not by 3CX-side configuration. The template's
`<Rule Type="Any">contacts</Rule>` does not fire on an empty array, so there is nothing for anyone
to misconfigure.

---

## Journaling

Only **completed inbound and outbound** calls. `Missed` and `Notanswered` are refused with `422`
and the template skips them; they are real events, but admitting them would silently turn "calls
with this client" into "call attempts".

Each journaled call writes one `communication_messages` row on channel `phone_log`, into the same
conversation ledger as the client's email and text, with:

| Field | Source |
| --- | --- |
| `direction` | `inbound` / `outbound`, from `[CallType]` |
| `sender_ref` | the caller's number (inbound) or the agent extension (outbound) |
| `subject` | `"Inbound call from ***0000"` — **masked** |
| `body` | always `null` |
| `message_metadata.agent_extension` | `[Agent]` |
| `message_metadata.duration_seconds` | parsed from `[Duration]` (`hh:mm:ss`) |
| `message_metadata.started_at` | `[CallStartTimeUTC]`, ISO 8601 |
| `message_metadata.identity_kind` | how the dedup key was established |

plus a `communication_recipients` row, a `communication_message_sources` row carrying the
provenance and dedup key, and an append-only `communication_events` row (`call_journaled`). No
timeline event is written, for the same reason email and SMS ingestion write none: the D.44
registry classifies by `(source, event_type)` and a second row would double-count the exchange.

**No recording, transcript, summary or sentiment** is requested or stored. 3CX can offer all four
to `ReportCall`; the template asks for none. A call journal records *that* a call happened — the
moment it holds what was said it is a different artefact under a different retention rule.

**A client id the PBX hands back is checked, never trusted.** `entity_id` is compared against a
fresh lookup of the number; a mismatch is refused with `422` and nothing is written. Without that
check, anyone holding the integration secret could file a call against any client in the book.

**An unmatched or ambiguous caller is accepted and not filed** (`journaled: false`, HTTP 200).
There is nothing for the PBX to retry, so a 4xx would only make it keep trying. The gap is
recorded in the audit chain.

---

## De-duplication

`UNIQUE (source_system, source_external_id)` on `communication_message_sources`. The in-code
lookup is the fast path; the constraint is what makes a duplicate impossible under concurrency,
which is exactly when a PBX retry arrives.

* `source_system` is `call:3cx-<instance>`, so two PBXs can never collide.
* `source_external_id` is the PBX's own call id when there is one.

### The limitation, stated plainly

**3CX v20 exposes no call identifier to call journaling.** The documented `ReportCall` variables
are `[CallType]`, `[Number]`, `[Name]`, `[Agent]`, `[Duration]`, `[DateTime]`,
`[CallStartTimeLocal]` and `[CallStartTimeUTC]`. 3CX support has stated that a call id is
available from the CDR, not from `ReportCall`. Writing `[CallID]` into a template would render as
an empty string and quietly destroy de-duplication.

So when no call id is supplied, the key is a SHA-256 over the tuple that identifies a call anyway:

```
start instant (UTC, to the second) | agent extension | normalized number | direction
```

Re-reporting the same call renders the same tuple and writes nothing. Two genuinely different
calls would have to share an agent, a counterparty, a direction *and* a start second to collide —
which is the same call reported twice. It is hashed rather than concatenated so the stored
identifier, which appears in query output and exports, does not itself contain a phone number.

Every row records which rule produced its key (`identity_kind`: `vendor_call_id` or
`derived_call_tuple`), so nobody later mistakes a derived identity for a vendor-guaranteed one.

The endpoint **already accepts** a `call_id` field and prefers it whenever present. If a future
3CX release exposes one, or the firm adds a CDR-driven poster, only the template changes.

---

## Privacy

Full phone numbers never reach a log line, an audit entry, a subject line, an error body, or the
stored dedup key. Everything human-readable carries the last-four form (`***0000`) produced by
`mask_number`. The audit chain records the masked number, the match count and the outcome — an
audit trail holding every number the PBX ever asked about would *be* a call-detail record, which
is not what it is for.

---

## Click to call

The client profile's phone number is a link that dials through the installed 3CX handler. The
`href` is built by the `dial` Jinja filter rather than by concatenating `tel:` with the stored
number: records hold whatever punctuation the importer or a member of staff typed, URI handlers
disagree about what they strip, and 3CX dials against outbound rules that need an unambiguous
country code. The filter emits punctuation-free digits, `+1`-prefixed for a 10-digit NANP number,
and renders plain text rather than a dead link when the record holds no dialable digits.

---

## Installing the template (operator steps)

Nothing below has been performed. It requires 3CX credentials and a running PBX.

1. Provision the secret and switch the connector on in the Client360 environment:
   `CLIENT360_3CX_ENABLED=true`, `CLIENT360_3CX_INTEGRATION_SECRET=<48+ random chars>`,
   `CLIENT360_3CX_INSTANCE=<a short name for this PBX>`. Confirm `PUBLIC_BASE_URL` is set to the
   HTTPS origin staff browsers use. Restart the service.
2. In 3CX: **Management Console → Settings → CRM → Server side → Add**, and upload
   `deploy/3cx/client360-3cx-crm-template.xml`.
3. Fill in the three parameters: **Client360 base URL** (origin only, no trailing slash),
   **Client360 integration secret**, and **Enable call journaling**.
4. Test with a number belonging to exactly one active client. The profile should open on answer.
5. End the call and confirm one `phone_log` entry appears in that client's communication history.
   Call the same client again and confirm a second, separate entry appears.

If no profile opens, check in this order: the connector is enabled and the secret matches; the
number on the client record normalizes to the same digits as the number 3CX reports; exactly one
**active** person holds it.

---

## Scope

**Not built, deliberately:** outbound dialling from Client360, live call state or presence,
recording or transcript retrieval, queue and agent statistics, CDR import, missed-call handling,
contact creation from 3CX, and chat (`ReportChat`).

**The XML contract was verified, not invented.** Every element, attribute and variable used was
taken from 3CX's published server-side CRM template documentation and from a shipping vendor
template, and `tests/test_threecx_connector.py` fails if the checked-in file drifts from the
endpoints or if a token outside the documented variable set appears in the journaling scenario.
