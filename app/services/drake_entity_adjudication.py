"""Human-approved adjudication of a non-natural Drake identity onto an EXISTING entity.

WHY THIS PATHWAY HAS TO EXIST
-----------------------------
Drake's client export is the only evidence the unattended import ever sees, and for a fiduciary
return it does not carry the filing entity's EIN. Measured against production: for every estate and
trust client, the EIN held in Drake's own client index differs from the value in the export, and none
of those EINs appears anywhere in the exported file. What the export's taxpayer-identifier column
actually holds on a 1041 is the DECEDENT's SSN — proven on two clients whose exported value belongs
to a separate individual client record for the deceased person.

That has two consequences the identity model must respect:

* An estate with no decedent SSN recorded, and a living trust which has no decedent at all, export a
  blank identifier. ``app.services.drake_return_identity`` refuses to key on nothing and quarantines
  the row as ``unidentified_no_taxpayer_identifier``. **That behaviour is correct and this module
  does not change it.** Two unrelated estates keyed on "no identifier" would merge into one return.
* Re-exporting cannot fix it, and neither can typing something into the export's identifier column.
  Entering the decedent's SSN would populate the field, but it would key the filing entity to a
  natural person: the wrong legal subject, and for a living trust simply a fiction.

So the EIN can only ever reach the system as EVIDENCE A HUMAN READ — from the filed return, from an
IRS notice, from Drake's client index — and the identity it establishes is an adjudication, not an
import. This module is that pathway, and it is deliberately separate from ingestion: nothing here
runs unattended, and nothing in ingestion calls it.

WHAT IT WRITES, AND NOTHING ELSE
--------------------------------
    drake_business_identity   one row per (identifier hash, subject type), carrying the entity link
                              and the ``human_approved`` trust fields
    entity_source_links       one row per adjudicated Drake source contact
    audit_events              one ``drake.entity_identity_adjudicated`` entry

``drake_client_returns`` is NOT touched. The quarantined rows keep their null identifier hash, their
``unidentified_no_taxpayer_identifier`` status and their raw export row exactly as Drake wrote them,
because the export genuinely did not contain an EIN and the database must not claim otherwise. The
link to those returns runs through their Drake source contacts, which is what ``entity_source_links``
is for. The evidence chain is therefore explicit end to end::

    filed return / Drake client index  ->  EIN read by a human
        ->  hash derived HERE, never supplied  ->  drake_business_identity (human_approved)
        ->  existing relationship_entities row  ->  entity_source_links  ->  Drake source contacts
        ->  the quarantined drake_client_returns rows they were built from

NO ENTITY IS EVER CREATED
-------------------------
The caller names an entity that already exists and has already been adjudicated to be the right one.
Creating one here would re-introduce exactly the defect the D7 review found: every one of its 18
candidate matches rested on a name alone, and a name is not evidence of entity identity. This module
has no name-matching, no address-matching and no fallback of any kind. If the named entity is wrong,
the answer is a different entity id from a human, not a lookup.

THE HASH IS DERIVED, NEVER ACCEPTED
-----------------------------------
The caller passes the identifier as read from the evidence and this module hashes it through
:func:`app.services.drake_identifier.identifier_hash` — the same expression the import uses. A
caller-supplied hash is not part of the interface at all, so no call site can bind an entity to a
value that corresponds to no real identifier.

FAIL CLOSED
-----------
Every refusal below raises :class:`AdjudicationRefused` with a stable ``code`` and writes nothing.
There is no partial application: the caller's transaction either carries the whole adjudication or
none of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text

from app.security.audit import write_audit_event
from app.services.drake_identifier import identifier_hash as derive_identifier_hash
from app.services.drake_return_subject import (
    ENTITY_TYPE_FOR_SUBJECT,
    NATURAL_PERSON,
    SINGLE_SUBJECT,
    Observation,
    classify,
)
from app.services.link_trust import HUMAN_APPROVED

#: ``confirmation_source`` for everything this module writes. A person decided; nothing else does.
HUMAN = "human"

#: ``evidence_method`` recorded on both tables. Names the pathway, so a reader can tell an
#: adjudicated identity from one the import derived (``drake_entity_provenance``).
EVIDENCE_METHOD = "human_adjudicated_entity_identifier"

#: The audit action. One entry per adjudication, in the caller's transaction.
AUDIT_ACTION = "drake.entity_identity_adjudicated"

#: Identifier kinds a non-natural entity may be adjudicated on. An SSN is deliberately absent: it
#: denotes a natural person, and binding one to an entity is the decedent-SSN mistake this module
#: exists to avoid.
ENTITY_IDENTIFIER_TYPES = frozenset({"ein"})

# --- refusal codes ----------------------------------------------------------------------------------

MISSING_ACTOR = "MISSING_ACTOR"
MISSING_EVIDENCE = "MISSING_EVIDENCE"
MISSING_SOURCE_RECORDS = "MISSING_SOURCE_RECORDS"
UNSUPPORTED_IDENTIFIER_TYPE = "UNSUPPORTED_IDENTIFIER_TYPE"
UNUSABLE_IDENTIFIER = "UNUSABLE_IDENTIFIER"
ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ENTITY_INACTIVE = "ENTITY_INACTIVE"
ENTITY_TYPE_MISMATCH = "ENTITY_TYPE_MISMATCH"
SOURCE_RECORD_NOT_FOUND = "SOURCE_RECORD_NOT_FOUND"
SOURCE_RECORD_NOT_DRAKE = "SOURCE_RECORD_NOT_DRAKE"
SOURCE_RECORD_BOUND_ELSEWHERE = "SOURCE_RECORD_BOUND_ELSEWHERE"
SUBJECT_IS_NATURAL_PERSON = "SUBJECT_IS_NATURAL_PERSON"
SUBJECT_REQUIRES_REVIEW = "SUBJECT_REQUIRES_REVIEW"
SUBJECT_HAS_NO_YEAR = "SUBJECT_HAS_NO_YEAR"
IDENTIFIER_BOUND_TO_PERSON = "IDENTIFIER_BOUND_TO_PERSON"
IDENTIFIER_BOUND_TO_OTHER_ENTITY = "IDENTIFIER_BOUND_TO_OTHER_ENTITY"


class AdjudicationRefused(RuntimeError):
    """One adjudication was refused, with a stable machine-readable reason.

    Carries ``code`` so a caller (a route, a script, a test) can branch on the reason without
    parsing prose, and a human-readable message for the operator.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- the request -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    """One artefact a human read in order to decide.

    Deliberately generic. ``kind`` and ``reference`` are opaque strings recorded verbatim in the
    audit entry: this module never resolves, validates or follows a reference, and it hard-codes no
    document id, path or entity of any kind. A filed return, an IRS notice, an EIN assignment letter
    and a line in Drake's client index are all just evidence here.
    """

    kind: str
    reference: str
    detail: str = ""

    def as_record(self) -> dict:
        record = {"kind": self.kind, "reference": self.reference}
        if self.detail:
            record["detail"] = self.detail
        return record


@dataclass(frozen=True)
class AdjudicationRequest:
    """Everything one human decision needs, and nothing it must not be trusted with."""

    relationship_entity_id: int
    #: The identifier exactly as read from the evidence. Hashed here; never stored.
    identifier: str
    #: What kind of identifier that is. Must be in :data:`ENTITY_IDENTIFIER_TYPES`.
    identifier_type: str
    #: The Drake ``source_contacts`` rows this entity's returns were built from.
    source_contact_ids: tuple[int, ...]
    #: What the approver read. At least one item is required.
    evidence: tuple[Evidence, ...]
    #: The person accountable for the decision. Required: an unattributed human approval is refused
    #: by this module and, independently, by a database check constraint.
    actor_user_id: int
    reason: str = ""
    request_id: str | None = None


@dataclass
class AdjudicationResult:
    """What one adjudication established. Returned on success; nothing is returned on refusal."""

    relationship_entity_id: int
    identifier_hash: str
    subject_type: str
    subject_name: str
    first_year: int
    last_year: int
    return_count: int
    return_types: tuple[str, ...]
    business_identity_id: int = 0
    identity_created: bool = False
    source_links_created: tuple[int, ...] = field(default_factory=tuple)
    source_links_unchanged: tuple[int, ...] = field(default_factory=tuple)
    audit_event_id: int = 0

    @property
    def changed(self) -> bool:
        """False when a re-run found everything already in place."""
        return self.identity_created or bool(self.source_links_created)


# --- SQL ----------------------------------------------------------------------------------------------

_ENTITY = """
    SELECT id, entity_type, name, active FROM relationship_entities WHERE id = :entity_id
"""

_CONTACTS = """
    SELECT id, source_system, full_name, raw_data
    FROM source_contacts WHERE id = ANY(:ids) ORDER BY id
"""

_IDENTITY_BY_HASH = """
    SELECT id, subject_type, relationship_entity_id, trust_level
    FROM drake_business_identity WHERE identifier_hash = :hash
"""

_PERSON_IDENTITY_BY_HASH = """
    SELECT identifier_hash FROM drake_identity WHERE identifier_hash = :hash
"""

_LINKS_FOR_CONTACTS = """
    SELECT source_contact_id, relationship_entity_id
    FROM entity_source_links WHERE source_contact_id = ANY(:ids)
"""

# The adjudication columns ARE written here, unlike the ingestion upsert in
# ``drake_subject_routing``, which lists only source-derived columns so that a later import cannot
# wipe a human decision. This is the writer that decision is allowed to come from.
_IDENTITY_UPSERT = """
    INSERT INTO drake_business_identity (
        identifier_hash, subject_type, relationship_entity_id, first_year, last_year,
        return_count, subject_name, return_types, trust_level, confirmation_source,
        evidence_method, confirmed_by_user_id, confirmed_at
    )
    VALUES (
        :identifier_hash, :subject_type, :relationship_entity_id, :first_year, :last_year,
        :return_count, :subject_name, :return_types, :trust_level, :confirmation_source,
        :evidence_method, :confirmed_by_user_id, :confirmed_at
    )
    ON CONFLICT ON CONSTRAINT uq_drake_business_identity DO UPDATE SET
        relationship_entity_id = EXCLUDED.relationship_entity_id,
        first_year             = EXCLUDED.first_year,
        last_year              = EXCLUDED.last_year,
        return_count           = EXCLUDED.return_count,
        subject_name           = EXCLUDED.subject_name,
        return_types           = EXCLUDED.return_types,
        trust_level            = EXCLUDED.trust_level,
        confirmation_source    = EXCLUDED.confirmation_source,
        evidence_method        = EXCLUDED.evidence_method,
        confirmed_by_user_id   = EXCLUDED.confirmed_by_user_id,
        confirmed_at           = EXCLUDED.confirmed_at,
        updated_at             = now()
    RETURNING id, (xmax = 0) AS inserted
"""

_LINK_UPSERT = """
    INSERT INTO entity_source_links (
        relationship_entity_id, source_contact_id, match_method, match_score, confirmed,
        trust_level, confirmation_source, evidence_method, confirmed_by_user_id, confirmed_at
    )
    VALUES (
        :relationship_entity_id, :source_contact_id, :match_method, 100.00, true,
        :trust_level, :confirmation_source, :evidence_method, :confirmed_by_user_id, :confirmed_at
    )
    ON CONFLICT ON CONSTRAINT uq_entity_source_link DO UPDATE SET
        match_method         = EXCLUDED.match_method,
        match_score          = EXCLUDED.match_score,
        confirmed            = EXCLUDED.confirmed,
        trust_level          = EXCLUDED.trust_level,
        confirmation_source  = EXCLUDED.confirmation_source,
        evidence_method      = EXCLUDED.evidence_method,
        confirmed_by_user_id = EXCLUDED.confirmed_by_user_id,
        confirmed_at         = EXCLUDED.confirmed_at
    RETURNING id, (xmax = 0) AS inserted
"""


# --- the operation -------------------------------------------------------------------------------------

def adjudicate_entity_identity(connection, request: AdjudicationRequest) -> AdjudicationResult:
    """Bind a verified entity identifier to an EXISTING relationship entity, on a human's authority.

    Runs entirely inside ``connection``'s transaction, including the audit entry, so the decision and
    its record commit or roll back together. Raises :class:`AdjudicationRefused` and writes nothing
    if any precondition fails. Re-running an identical request is a no-op that returns the same
    result with ``changed`` False.
    """
    _require_attribution(request)
    identifier_hash = _derive_hash(request)
    entity = _load_entity(connection, request.relationship_entity_id)
    contacts = _load_contacts(connection, request.source_contact_ids)
    subject = _classify_subject(contacts)
    _check_entity_type(entity, subject)
    _check_identifier_is_free(connection, identifier_hash, request)
    _check_contacts_are_free(connection, request)

    approved_at = datetime.now(UTC)
    subject_name = _subject_name(contacts, entity)

    identity_id, identity_created = connection.execute(text(_IDENTITY_UPSERT), {
        "identifier_hash": identifier_hash,
        "subject_type": subject.subject_type,
        "relationship_entity_id": entity["id"],
        "first_year": subject.first_year,
        "last_year": subject.last_year,
        "return_count": subject.return_count,
        "subject_name": subject_name,
        "return_types": list(subject.return_types),
        "trust_level": HUMAN_APPROVED,
        "confirmation_source": HUMAN,
        "evidence_method": EVIDENCE_METHOD,
        "confirmed_by_user_id": request.actor_user_id,
        "confirmed_at": approved_at,
    }).one()

    created, unchanged = [], []
    for contact in contacts:
        _, inserted = connection.execute(text(_LINK_UPSERT), {
            "relationship_entity_id": entity["id"],
            "source_contact_id": contact["id"],
            "match_method": EVIDENCE_METHOD,
            "trust_level": HUMAN_APPROVED,
            "confirmation_source": HUMAN,
            "evidence_method": EVIDENCE_METHOD,
            "confirmed_by_user_id": request.actor_user_id,
            "confirmed_at": approved_at,
        }).one()
        (created if inserted else unchanged).append(contact["id"])

    audit_id = write_audit_event(
        action=AUDIT_ACTION,
        entity_type="relationship_entity",
        entity_id=entity["id"],
        actor_user_id=request.actor_user_id,
        request_id=request.request_id or f"adjudicate-entity-{entity['id']}",
        conn=connection,
        metadata=_audit_record(request, entity, subject, subject_name, identifier_hash,
                               approved_at, created, unchanged),
    )

    return AdjudicationResult(
        relationship_entity_id=entity["id"],
        identifier_hash=identifier_hash,
        subject_type=subject.subject_type,
        subject_name=subject_name,
        first_year=subject.first_year,
        last_year=subject.last_year,
        return_count=subject.return_count,
        return_types=subject.return_types,
        business_identity_id=identity_id,
        identity_created=bool(identity_created),
        source_links_created=tuple(created),
        source_links_unchanged=tuple(unchanged),
        audit_event_id=audit_id,
    )


# --- preconditions -------------------------------------------------------------------------------------

def _require_attribution(request: AdjudicationRequest) -> None:
    if not request.actor_user_id:
        raise AdjudicationRefused(
            MISSING_ACTOR,
            "a human-approved adjudication needs the approving user; there is no anonymous "
            "human approval.")
    if not request.evidence:
        raise AdjudicationRefused(
            MISSING_EVIDENCE,
            "a human-approved adjudication needs at least one evidence reference: the point of "
            "this pathway is that a person read something.")
    if not request.source_contact_ids:
        raise AdjudicationRefused(
            MISSING_SOURCE_RECORDS,
            "no Drake source records were named, so there is nothing to attribute to the entity "
            "and no evidence from which to derive the identity's year range.")


def _derive_hash(request: AdjudicationRequest) -> str:
    if request.identifier_type not in ENTITY_IDENTIFIER_TYPES:
        raise AdjudicationRefused(
            UNSUPPORTED_IDENTIFIER_TYPE,
            f"{request.identifier_type!r} is not an entity identifier. Supported: "
            f"{sorted(ENTITY_IDENTIFIER_TYPES)}. An SSN denotes a natural person and must never be "
            "bound to a filing entity.")
    derived = derive_identifier_hash(request.identifier)
    if derived is None:
        raise AdjudicationRefused(
            UNUSABLE_IDENTIFIER,
            "the identifier carries no digits, so it denotes nothing and cannot be hashed.")
    return derived


def _load_entity(connection, entity_id: int) -> dict:
    row = connection.execute(text(_ENTITY), {"entity_id": entity_id}).mappings().one_or_none()
    if row is None:
        raise AdjudicationRefused(
            ENTITY_NOT_FOUND,
            f"relationship entity {entity_id} does not exist. This pathway never creates one: a "
            "name is not evidence of entity identity.")
    if not row["active"]:
        raise AdjudicationRefused(
            ENTITY_INACTIVE,
            f"relationship entity {entity_id} is inactive; adjudicating an identity onto it would "
            "resurrect it as a side effect.")
    return dict(row)


def _load_contacts(connection, contact_ids) -> list[dict]:
    rows = [dict(r) for r in connection.execute(
        text(_CONTACTS), {"ids": list(contact_ids)}).mappings()]
    found = {row["id"] for row in rows}
    missing = sorted(set(contact_ids) - found)
    if missing:
        raise AdjudicationRefused(
            SOURCE_RECORD_NOT_FOUND,
            f"source contact(s) {missing} do not exist.")
    foreign = sorted(row["id"] for row in rows if row["source_system"] != "Drake")
    if foreign:
        raise AdjudicationRefused(
            SOURCE_RECORD_NOT_DRAKE,
            f"source contact(s) {foreign} are not Drake records; this pathway adjudicates Drake "
            "tax identity only.")
    return rows


def _observations(contacts) -> list[Observation]:
    """The return evidence each Drake contact already carries, as the classifier wants it."""
    out = []
    for contact in contacts:
        raw = contact["raw_data"] or {}
        year = raw.get("tax_year")
        out.append(Observation(
            return_type=raw.get("return_type"),
            tax_year=int(year) if year is not None else None,
            has_dob=False,
        ))
    return out


def _classify_subject(contacts):
    """What legal subject the named Drake rows describe — decided by the filed return, as ever.

    The subject type is NOT taken from the caller. A human names the entity and the identifier; what
    kind of subject the returns describe is still read from the returns themselves, so an operator
    cannot adjudicate a 1040 history onto a trust by asserting it.
    """
    result = classify(_observations(contacts))

    if result.outcome != SINGLE_SUBJECT or result.requires_review:
        raise AdjudicationRefused(
            SUBJECT_REQUIRES_REVIEW,
            "the named Drake rows do not describe one unambiguous subject "
            f"({result.outcome}): {result.reason or 'held for review'}. Adjudicate a set of rows "
            "that belong to a single legal subject.")

    subject = result.subjects[0]
    if subject.subject_type == NATURAL_PERSON:
        raise AdjudicationRefused(
            SUBJECT_IS_NATURAL_PERSON,
            "the named Drake rows describe a natural person. A person identity belongs in "
            "drake_identity, not on a relationship entity.")
    if subject.first_year is None or subject.last_year is None:
        raise AdjudicationRefused(
            SUBJECT_HAS_NO_YEAR,
            "the named Drake rows carry no tax year, so the identity's year range cannot be "
            "derived from evidence.")
    return subject


def _check_entity_type(entity: dict, subject) -> None:
    expected = ENTITY_TYPE_FOR_SUBJECT.get(subject.subject_type)
    if entity["entity_type"] != expected:
        raise AdjudicationRefused(
            ENTITY_TYPE_MISMATCH,
            f"entity {entity['id']} is {entity['entity_type']!r} but the returns describe "
            f"{subject.subject_type!r}, which belongs under {expected!r}.")


def _check_identifier_is_free(connection, identifier_hash: str, request) -> None:
    """The identifier must not already denote someone else.

    A hash already bound to a natural person means the digits are somebody's SSN, which is the
    decedent-identifier mistake this module exists to prevent. A hash bound to a different entity
    means two canonical entities would claim one taxpayer.
    """
    if connection.execute(text(_PERSON_IDENTITY_BY_HASH),
                          {"hash": identifier_hash}).scalar() is not None:
        raise AdjudicationRefused(
            IDENTIFIER_BOUND_TO_PERSON,
            "this identifier already denotes a natural person in drake_identity. Binding it to an "
            "entity would merge a person and a filing entity onto one taxpayer identity.")

    for row in connection.execute(text(_IDENTITY_BY_HASH), {"hash": identifier_hash}).mappings():
        bound = row["relationship_entity_id"]
        if bound is not None and bound != request.relationship_entity_id:
            raise AdjudicationRefused(
                IDENTIFIER_BOUND_TO_OTHER_ENTITY,
                f"this identifier is already adjudicated to relationship entity {bound} as "
                f"{row['subject_type']!r}. One taxpayer identifier denotes one entity.")


def _check_contacts_are_free(connection, request) -> None:
    """A Drake source record must not already be attributed to a different entity."""
    conflicting = [
        (row["source_contact_id"], row["relationship_entity_id"])
        for row in connection.execute(
            text(_LINKS_FOR_CONTACTS), {"ids": list(request.source_contact_ids)}).mappings()
        if row["relationship_entity_id"] != request.relationship_entity_id
    ]
    if conflicting:
        raise AdjudicationRefused(
            SOURCE_RECORD_BOUND_ELSEWHERE,
            f"source contact(s) already linked to another entity: {sorted(conflicting)}. "
            "Resolve the contradiction before adjudicating.")


def _subject_name(contacts, entity) -> str:
    """The name Drake carries for these rows, falling back to the entity's own name.

    Taken from the source evidence rather than from the caller, so the stored name says what the
    export said. It is a label on the identity, never part of matching.
    """
    for contact in sorted(contacts, key=lambda c: c["id"], reverse=True):
        if (contact.get("full_name") or "").strip():
            return contact["full_name"].strip()
    return entity["name"]


def _audit_record(request, entity, subject, subject_name, identifier_hash, approved_at,
                  created, unchanged) -> dict:
    """Everything needed to answer "who decided what, on which evidence, and when".

    The raw identifier is deliberately absent: the non-reversible hash is what identifies the
    taxpayer here, and an audit trail is not a place to accumulate plaintext tax identifiers. Keys
    avoid the substrings ``redact_metadata`` masks, so the record survives redaction intact.
    """
    return {
        "relationship_entity_id": entity["id"],
        "entity_name": entity["name"],
        "entity_type": entity["entity_type"],
        "identifier_type": request.identifier_type,
        "identifier_hash": identifier_hash,
        "subject_type": subject.subject_type,
        "subject_name": subject_name,
        "first_year": subject.first_year,
        "last_year": subject.last_year,
        "return_count": subject.return_count,
        "return_types": list(subject.return_types),
        "source_contact_ids": sorted(request.source_contact_ids),
        "source_links_created": sorted(created),
        "source_links_unchanged": sorted(unchanged),
        "evidence": [item.as_record() for item in request.evidence],
        "evidence_method": EVIDENCE_METHOD,
        "trust_level": HUMAN_APPROVED,
        "confirmation_source": HUMAN,
        "human_adjudication": True,
        "approved_by_user_id": request.actor_user_id,
        "approved_at": approved_at.isoformat(),
        "reason": request.reason,
    }


# --- reading back what an adjudication established ------------------------------------------------------

_ATTRIBUTED_RETURNS = """
    SELECT DISTINCT (sc.raw_data->>'drake_return_id')::bigint AS drake_return_id
    FROM entity_source_links esl
    JOIN source_contacts sc ON sc.id = esl.source_contact_id
    WHERE esl.relationship_entity_id = :entity_id
      AND sc.source_system = 'Drake'
      AND sc.raw_data->>'drake_return_id' IS NOT NULL
    ORDER BY 1
"""


def attributed_return_ids(connection, relationship_entity_id: int) -> tuple[int, ...]:
    """The ``drake_client_returns`` ids attributed to one entity, in id order.

    The join is deliberately indirect — entity, to source link, to Drake source contact, to the
    return the contact was built from — because that is the whole point: the return itself was never
    rewritten to claim an identifier its export did not contain. This is the supported way to ask
    "which Drake returns does this entity own?" without inferring it from a name.
    """
    return tuple(row[0] for row in connection.execute(
        text(_ATTRIBUTED_RETURNS), {"entity_id": relationship_entity_id}))


__all__ = [
    "AUDIT_ACTION", "ENTITY_IDENTIFIER_TYPES", "EVIDENCE_METHOD", "HUMAN",
    "attributed_return_ids",
    "AdjudicationRefused", "AdjudicationRequest", "AdjudicationResult", "Evidence",
    "adjudicate_entity_identity",
    "ENTITY_INACTIVE", "ENTITY_NOT_FOUND", "ENTITY_TYPE_MISMATCH",
    "IDENTIFIER_BOUND_TO_OTHER_ENTITY", "IDENTIFIER_BOUND_TO_PERSON", "MISSING_ACTOR",
    "MISSING_EVIDENCE", "MISSING_SOURCE_RECORDS", "SOURCE_RECORD_BOUND_ELSEWHERE",
    "SOURCE_RECORD_NOT_DRAKE", "SOURCE_RECORD_NOT_FOUND", "SUBJECT_HAS_NO_YEAR",
    "SUBJECT_IS_NATURAL_PERSON", "SUBJECT_REQUIRES_REVIEW", "UNSUPPORTED_IDENTIFIER_TYPE",
    "UNUSABLE_IDENTIFIER",
]
