"""Attach a Drake entity identity to an EXISTING entity that stored provenance already corroborates.

WHY THIS IS NOT THE ADJUDICATION SERVICE
----------------------------------------
:mod:`app.services.drake_entity_adjudication` exists for the case where evidence reaches the system
only because a person read it — an EIN off a filed 1041 that the client export never carried. It
records ``human_approved`` and demands a named approver, because a named human really did decide.

A different population needs no such decision. Its identifier is present in the export, the filed
returns type it unambiguously, and the target entity's own stored provenance already cites the very
Drake source contacts that carry the identifier. Nothing is being judged; a link that the data
already asserts is simply being written down. Recording that as ``human_approved`` would misstate the
evidence, and inventing an approver to satisfy the human path would misstate who decided. Production
agrees: all 26 pre-existing identity rows and all 65 pre-existing links carry
``identifier_verified`` / ``machine`` / ``drake_entity_provenance`` with no confirming user, and this
module writes exactly that shape.

So the two writers are deliberately separate, and neither can produce the other's trust level. There
is no parameter here to raise attribution to ``human_approved``, and the adjudication service is
untouched.

WHAT MAKES A CASE MACHINE-RESOLVABLE
------------------------------------
Every one of these must hold, and each has its own refusal code:

    the entity exists, is active, and its type matches the classified subject
    the filed returns classify as ONE non-natural subject with no review required
    the identifier is an EIN, derived here, and owned by nobody else
    every named source contact is Drake-origin and carries THIS identifier
    the named contacts are ALL of that identifier's contacts, not a chosen subset
    no contact is already attributed to a different entity
    the entity's STORED PROVENANCE independently corroborates those contacts

THE PROVENANCE RULE IS THE WHOLE POINT
--------------------------------------
This module must never become a name matcher. It reads no name, no address, no phone and no email,
and there is no similarity scoring anywhere in it. The only thing that authorises an unattended bind
is structured provenance Client360 already stored on the entity, in one of the shapes production
actually writes (see :data:`PROVENANCE_FORMS`). A target that merely shares a name is refused with
``PROVENANCE_NAME_ONLY``, which is precisely the defect the D7 review found in all 18 of its
name-based candidates.

TRANSACTION
-----------
Writes happen in the caller's transaction, including the audit entry, so the attribution and its
record commit or roll back together. Row writes are idempotent: re-running creates nothing and never
upgrades an existing trust level. One audit entry is written per call, which is stated rather than
hidden, matching the convention for unattended work elsewhere in the system.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
from app.services.link_trust import IDENTIFIER_VERIFIED

#: ``confirmation_source`` for everything written here. No person decided; the data did.
MACHINE = "machine"

#: ``evidence_method`` and ``match_method``. The same string production already carries on all 26
#: machine identity rows and all 65 machine links, so a reader cannot tell this writer's output from
#: the earlier phases' — which is correct, because the evidence is the same kind.
EVIDENCE_METHOD = "drake_entity_provenance"

#: One entry per call. ``actor_user_id`` is NULL: the established contract for unattended work, used
#: by 21,743 existing audit rows across OCR runs, ingestion, imports and person merges. There is no
#: system user row to attribute to, and inventing one would be worse than recording none.
AUDIT_ACTION = "drake.entity_attribution_machine"

#: Only an EIN. An SSN denotes a natural person; binding one to an entity is the mistake the whole
#: Drake identity design exists to prevent.
ENTITY_IDENTIFIER_TYPES = frozenset({"ein"})

#: The structured provenance shapes that permit an unattended bind, in the forms production stores.
#: Nothing derived from a name, an address or a contact point appears here, and nothing may be added
#: to this list that is not a stored, deterministic reference to the same source records.
PROVENANCE_FORMS = (
    "details.identifier_hash",
    "details.source_contact_ids",
    "details.canonical_repair.source_contact_ids",
    "details.source_record_ids",
    "details.drake_return_ids",
)

# --- refusal codes ------------------------------------------------------------------------------

MISSING_SOURCE_RECORDS = "MISSING_SOURCE_RECORDS"
UNSUPPORTED_IDENTIFIER_TYPE = "UNSUPPORTED_IDENTIFIER_TYPE"
UNUSABLE_IDENTIFIER = "UNUSABLE_IDENTIFIER"
ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ENTITY_INACTIVE = "ENTITY_INACTIVE"
ENTITY_TYPE_MISMATCH = "ENTITY_TYPE_MISMATCH"
SOURCE_RECORD_NOT_FOUND = "SOURCE_RECORD_NOT_FOUND"
SOURCE_RECORD_NOT_DRAKE = "SOURCE_RECORD_NOT_DRAKE"
SOURCE_RECORD_WRONG_IDENTIFIER = "SOURCE_RECORD_WRONG_IDENTIFIER"
SOURCE_RECORDS_INCOMPLETE = "SOURCE_RECORDS_INCOMPLETE"
SOURCE_RECORD_BOUND_ELSEWHERE = "SOURCE_RECORD_BOUND_ELSEWHERE"
NO_RETURN_EVIDENCE = "NO_RETURN_EVIDENCE"
SUBJECT_IS_NATURAL_PERSON = "SUBJECT_IS_NATURAL_PERSON"
SUBJECT_REQUIRES_REVIEW = "SUBJECT_REQUIRES_REVIEW"
SUBJECT_HAS_NO_YEAR = "SUBJECT_HAS_NO_YEAR"
IDENTIFIER_BOUND_TO_PERSON = "IDENTIFIER_BOUND_TO_PERSON"
IDENTIFIER_BOUND_TO_OTHER_ENTITY = "IDENTIFIER_BOUND_TO_OTHER_ENTITY"
PROVENANCE_MISSING = "PROVENANCE_MISSING"
PROVENANCE_NAME_ONLY = "PROVENANCE_NAME_ONLY"
PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"


class AttributionRefused(RuntimeError):
    """One attribution was refused, with a stable machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AttributionRequest:
    """A candidate for unattended attribution. Note what is absent.

    There is no trust level, no approver and no evidence list: the trust level is fixed, the actor is
    nobody, and the evidence is the entity's own stored provenance, which this module reads rather
    than accepts. There is also no identifier hash — it is derived here.
    """

    relationship_entity_id: int
    identifier: str
    identifier_type: str
    source_contact_ids: tuple[int, ...]
    reason: str = ""
    request_id: str | None = None


@dataclass
class AttributionResult:
    """What one attribution established."""

    relationship_entity_id: int
    identifier_hash: str
    subject_type: str
    subject_name: str
    first_year: int
    last_year: int
    return_count: int
    return_types: tuple[str, ...]
    provenance_form: str = ""
    business_identity_id: int = 0
    identity_created: bool = False
    source_links_created: tuple[int, ...] = field(default_factory=tuple)
    source_links_unchanged: tuple[int, ...] = field(default_factory=tuple)
    audit_event_id: int = 0

    @property
    def changed(self) -> bool:
        return self.identity_created or bool(self.source_links_created)


# --- SQL ------------------------------------------------------------------------------------------

_ENTITY = "SELECT id, entity_type, name, active, details FROM relationship_entities WHERE id = :e"

_CONTACTS = """
    SELECT id, source_system, source_record_id, full_name, raw_data
    FROM source_contacts WHERE id = ANY(:ids) ORDER BY id
"""

_CONTACTS_FOR_HASH = """
    SELECT id FROM source_contacts
    WHERE source_system = 'Drake' AND raw_data->>'identifier_hash' = :hash ORDER BY id
"""

#: The filed returns are the evidence for typing, not the contact rows: a Drake contact's
#: ``raw_data.return_type`` is NULL for some years, while the return itself always carries it.
_RETURNS_FOR_HASH = """
    SELECT id, tax_year, return_type, (taxpayer_dob IS NOT NULL) AS has_dob
    FROM drake_client_returns WHERE taxpayer_identifier_hash = :hash
    UNION ALL
    SELECT id, tax_year, return_type, (spouse_dob IS NOT NULL)
    FROM drake_client_returns WHERE spouse_identifier_hash = :hash
    ORDER BY 2, 1
"""

_PERSON_IDENTITY = "SELECT identifier_hash FROM drake_identity WHERE identifier_hash = :hash"

_IDENTITY_BY_HASH = """
    SELECT id, subject_type, relationship_entity_id FROM drake_business_identity
    WHERE identifier_hash = :hash
"""

_LINKS_FOR_CONTACTS = """
    SELECT source_contact_id, relationship_entity_id FROM entity_source_links
    WHERE source_contact_id = ANY(:ids)
"""

_IDENTITY_UPSERT = """
    INSERT INTO drake_business_identity (
        identifier_hash, subject_type, relationship_entity_id, first_year, last_year,
        return_count, subject_name, return_types, trust_level, confirmation_source, evidence_method
    )
    VALUES (
        :identifier_hash, :subject_type, :relationship_entity_id, :first_year, :last_year,
        :return_count, :subject_name, :return_types, :trust_level, :confirmation_source,
        :evidence_method
    )
    ON CONFLICT ON CONSTRAINT uq_drake_business_identity DO UPDATE SET
        relationship_entity_id = EXCLUDED.relationship_entity_id,
        first_year             = EXCLUDED.first_year,
        last_year              = EXCLUDED.last_year,
        return_count           = EXCLUDED.return_count,
        subject_name           = EXCLUDED.subject_name,
        return_types           = EXCLUDED.return_types,
        updated_at             = now()
    RETURNING id, (xmax = 0) AS inserted
"""

# ``trust_level``, ``confirmation_source`` and ``evidence_method`` are absent from the DO UPDATE on
# purpose: a re-run refreshes derived fields but must never restate — or downgrade — a trust level
# that a human adjudication may since have raised on the same identifier.
_LINK_UPSERT = """
    INSERT INTO entity_source_links (
        relationship_entity_id, source_contact_id, match_method, match_score, confirmed,
        trust_level, confirmation_source, evidence_method
    )
    VALUES (
        :relationship_entity_id, :source_contact_id, :match_method, 100.00, true,
        :trust_level, :confirmation_source, :evidence_method
    )
    ON CONFLICT ON CONSTRAINT uq_entity_source_link DO UPDATE SET
        match_method = EXCLUDED.match_method,
        match_score  = EXCLUDED.match_score
    RETURNING id, (xmax = 0) AS inserted
"""


# --- the operation --------------------------------------------------------------------------------

def attribute_entity_by_provenance(connection, request: AttributionRequest) -> AttributionResult:
    """Bind an identifier to an existing entity that stored provenance already corroborates.

    Raises :class:`AttributionRefused` and writes nothing unless every condition in the module
    docstring holds. Re-running an identical request is a no-op for rows.
    """
    if not request.source_contact_ids:
        raise AttributionRefused(
            MISSING_SOURCE_RECORDS, "no Drake source records were named.")
    identifier_hash = _derive_hash(request)
    entity = _load_entity(connection, request.relationship_entity_id)
    contacts = _load_contacts(connection, request, identifier_hash)
    subject, subject_name = _classify(connection, identifier_hash)
    _check_entity_type(entity, subject)
    _check_identifier_is_free(connection, identifier_hash, request)
    _check_contacts_are_free(connection, request)
    form = _check_provenance(entity, identifier_hash, contacts)

    identity_id, created = connection.execute(text(_IDENTITY_UPSERT), {
        "identifier_hash": identifier_hash,
        "subject_type": subject.subject_type,
        "relationship_entity_id": entity["id"],
        "first_year": subject.first_year,
        "last_year": subject.last_year,
        "return_count": subject.return_count,
        "subject_name": subject_name,
        "return_types": list(subject.return_types),
        "trust_level": IDENTIFIER_VERIFIED,
        "confirmation_source": MACHINE,
        "evidence_method": EVIDENCE_METHOD,
    }).one()

    made, unchanged = [], []
    for contact in contacts:
        _, inserted = connection.execute(text(_LINK_UPSERT), {
            "relationship_entity_id": entity["id"],
            "source_contact_id": contact["id"],
            "match_method": EVIDENCE_METHOD,
            "trust_level": IDENTIFIER_VERIFIED,
            "confirmation_source": MACHINE,
            "evidence_method": EVIDENCE_METHOD,
        }).one()
        (made if inserted else unchanged).append(contact["id"])

    audit_id = write_audit_event(
        action=AUDIT_ACTION,
        entity_type="relationship_entity",
        entity_id=entity["id"],
        actor_user_id=None,                       # unattended: the established NULL-actor contract
        request_id=request.request_id or f"attribute-entity-{entity['id']}",
        conn=connection,
        metadata={
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
            "source_record_ids": sorted(c["source_record_id"] or "" for c in contacts),
            "source_links_created": sorted(made),
            "source_links_unchanged": sorted(unchanged),
            "provenance_form": form,
            "evidence_method": EVIDENCE_METHOD,
            "trust_level": IDENTIFIER_VERIFIED,
            "confirmation_source": MACHINE,
            "human_adjudication": False,
            "unattended": True,
            "reason": request.reason,
            "attributed_at": datetime.now(UTC).isoformat(),
        })

    return AttributionResult(
        relationship_entity_id=entity["id"],
        identifier_hash=identifier_hash,
        subject_type=subject.subject_type,
        subject_name=subject_name,
        first_year=subject.first_year,
        last_year=subject.last_year,
        return_count=subject.return_count,
        return_types=subject.return_types,
        provenance_form=form,
        business_identity_id=identity_id,
        identity_created=bool(created),
        source_links_created=tuple(made),
        source_links_unchanged=tuple(unchanged),
        audit_event_id=audit_id,
    )


# --- conditions -----------------------------------------------------------------------------------

def _derive_hash(request: AttributionRequest) -> str:
    if request.identifier_type not in ENTITY_IDENTIFIER_TYPES:
        raise AttributionRefused(
            UNSUPPORTED_IDENTIFIER_TYPE,
            f"{request.identifier_type!r} is not an entity identifier. An SSN denotes a natural "
            "person and must never be bound to a filing entity.")
    derived = derive_identifier_hash(request.identifier)
    if derived is None:
        raise AttributionRefused(
            UNUSABLE_IDENTIFIER, "the identifier carries no digits, so it denotes nothing.")
    return derived


def _load_entity(connection, entity_id: int) -> dict:
    row = connection.execute(text(_ENTITY), {"e": entity_id}).mappings().one_or_none()
    if row is None:
        raise AttributionRefused(
            ENTITY_NOT_FOUND,
            f"relationship entity {entity_id} does not exist. This pathway never creates one.")
    if not row["active"]:
        raise AttributionRefused(
            ENTITY_INACTIVE, f"relationship entity {entity_id} is inactive.")
    return dict(row)


def _load_contacts(connection, request, identifier_hash: str) -> list[dict]:
    """The named contacts, proven to be Drake-origin AND to be exactly this identifier's set."""
    rows = [dict(r) for r in connection.execute(
        text(_CONTACTS), {"ids": list(request.source_contact_ids)}).mappings()]
    missing = sorted(set(request.source_contact_ids) - {r["id"] for r in rows})
    if missing:
        raise AttributionRefused(SOURCE_RECORD_NOT_FOUND, f"source contact(s) {missing} not found.")

    foreign = sorted(r["id"] for r in rows if r["source_system"] != "Drake")
    if foreign:
        raise AttributionRefused(
            SOURCE_RECORD_NOT_DRAKE, f"source contact(s) {foreign} are not Drake records.")

    wrong = sorted(r["id"] for r in rows if _raw(r).get("identifier_hash") != identifier_hash)
    if wrong:
        raise AttributionRefused(
            SOURCE_RECORD_WRONG_IDENTIFIER,
            f"source contact(s) {wrong} do not carry this identifier. A contact may only be "
            "attributed on the strength of the identifier it actually holds.")

    every = connection.execute(text(_CONTACTS_FOR_HASH),
                               {"hash": identifier_hash}).scalars().all()
    absent = sorted(set(every) - set(request.source_contact_ids))
    if absent:
        raise AttributionRefused(
            SOURCE_RECORDS_INCOMPLETE,
            f"this identifier also appears on source contact(s) {absent}, which were not named. "
            "Attributing a subset would split one taxpayer's provenance across two states.")
    return rows


def _raw(contact) -> dict[str, Any]:
    raw = contact.get("raw_data") or {}
    return raw if isinstance(raw, dict) else json.loads(raw)


def _classify(connection, identifier_hash: str):
    """Type the identifier from its FILED RETURNS. The caller does not get to assert a subject."""
    rows = connection.execute(text(_RETURNS_FOR_HASH),
                              {"hash": identifier_hash}).mappings().all()
    if not rows:
        raise AttributionRefused(
            NO_RETURN_EVIDENCE,
            "no Drake return carries this identifier, so there is nothing to classify from.")

    result = classify([Observation(return_type=r["return_type"], tax_year=r["tax_year"],
                                   has_dob=r["has_dob"]) for r in rows])
    if result.outcome != SINGLE_SUBJECT or result.requires_review:
        raise AttributionRefused(
            SUBJECT_REQUIRES_REVIEW,
            f"the filed returns do not describe one unambiguous subject ({result.outcome}): "
            f"{result.reason or 'held for review'}. Unattended attribution is not available.")

    subject = result.subjects[0]
    if subject.subject_type == NATURAL_PERSON:
        raise AttributionRefused(
            SUBJECT_IS_NATURAL_PERSON,
            "the filed returns describe a natural person, which belongs in drake_identity.")
    if subject.first_year is None or subject.last_year is None:
        raise AttributionRefused(
            SUBJECT_HAS_NO_YEAR, "the filed returns carry no tax year.")

    name = connection.execute(text(
        "SELECT taxpayer_normalized_name FROM drake_client_returns "
        "WHERE taxpayer_identifier_hash = :hash AND taxpayer_normalized_name IS NOT NULL "
        "ORDER BY tax_year DESC LIMIT 1"), {"hash": identifier_hash}).scalar()
    return subject, (name or "(unnamed)")


def _check_entity_type(entity: dict, subject) -> None:
    expected = ENTITY_TYPE_FOR_SUBJECT.get(subject.subject_type)
    if entity["entity_type"] != expected:
        raise AttributionRefused(
            ENTITY_TYPE_MISMATCH,
            f"entity {entity['id']} is {entity['entity_type']!r} but the returns describe "
            f"{subject.subject_type!r}, which belongs under {expected!r}.")


def _check_identifier_is_free(connection, identifier_hash: str, request) -> None:
    if connection.execute(text(_PERSON_IDENTITY),
                          {"hash": identifier_hash}).scalar() is not None:
        raise AttributionRefused(
            IDENTIFIER_BOUND_TO_PERSON,
            "this identifier already denotes a natural person in drake_identity.")
    for row in connection.execute(text(_IDENTITY_BY_HASH),
                                  {"hash": identifier_hash}).mappings():
        bound = row["relationship_entity_id"]
        if bound is not None and bound != request.relationship_entity_id:
            raise AttributionRefused(
                IDENTIFIER_BOUND_TO_OTHER_ENTITY,
                f"this identifier is already attributed to relationship entity {bound}.")


def _check_contacts_are_free(connection, request) -> None:
    conflicting = [
        (r["source_contact_id"], r["relationship_entity_id"])
        for r in connection.execute(
            text(_LINKS_FOR_CONTACTS), {"ids": list(request.source_contact_ids)}).mappings()
        if r["relationship_entity_id"] != request.relationship_entity_id
    ]
    if conflicting:
        raise AttributionRefused(
            SOURCE_RECORD_BOUND_ELSEWHERE,
            f"source contact(s) already linked to another entity: {sorted(conflicting)}.")


def _check_provenance(entity: dict, identifier_hash: str, contacts) -> str:
    """The entity's OWN stored provenance must already reference this identifier's source records.

    Returns the provenance form that satisfied the check, for the audit record. A name, an address
    or a contact point is never consulted and can never satisfy it.
    """
    details = entity.get("details") or {}
    if not isinstance(details, dict):
        details = json.loads(details)
    if not details:
        raise AttributionRefused(
            PROVENANCE_MISSING,
            f"entity {entity['id']} stores no provenance, so nothing corroborates this identifier "
            "except its name — which is not evidence.")

    named = {c["id"] for c in contacts}
    records = {c["source_record_id"] for c in contacts if c["source_record_id"]}
    returns = {int(_raw(c)["drake_return_id"]) for c in contacts if _raw(c).get("drake_return_id")}

    if str(details.get("identifier_hash") or "") == identifier_hash:
        return "details.identifier_hash"
    if named & set(details.get("source_contact_ids") or []):
        return "details.source_contact_ids"
    nested = details.get("canonical_repair") or {}
    if isinstance(nested, dict) and named & set(nested.get("source_contact_ids") or []):
        return "details.canonical_repair.source_contact_ids"
    if records & set(details.get("source_record_ids") or []):
        return "details.source_record_ids"
    if returns & {int(x) for x in (details.get("drake_return_ids") or [])}:
        return "details.drake_return_ids"

    if any(key in details for key in ("source_contact_ids", "source_record_ids",
                                      "drake_return_ids", "identifier_hash")) or nested:
        raise AttributionRefused(
            PROVENANCE_MISMATCH,
            f"entity {entity['id']} stores provenance, but it references different source records "
            f"than this identifier's {sorted(named)}. It corroborates a different taxpayer.")

    raise AttributionRefused(
        PROVENANCE_NAME_ONLY,
        f"entity {entity['id']} carries no structured source provenance ({sorted(details)}). A "
        "shared name is not evidence of entity identity; this bind needs human adjudication.")


__all__ = [
    "AUDIT_ACTION", "ENTITY_IDENTIFIER_TYPES", "EVIDENCE_METHOD", "MACHINE", "PROVENANCE_FORMS",
    "AttributionRefused", "AttributionRequest", "AttributionResult",
    "attribute_entity_by_provenance",
    "ENTITY_INACTIVE", "ENTITY_NOT_FOUND", "ENTITY_TYPE_MISMATCH",
    "IDENTIFIER_BOUND_TO_OTHER_ENTITY", "IDENTIFIER_BOUND_TO_PERSON", "MISSING_SOURCE_RECORDS",
    "NO_RETURN_EVIDENCE", "PROVENANCE_MISMATCH", "PROVENANCE_MISSING", "PROVENANCE_NAME_ONLY",
    "SOURCE_RECORD_BOUND_ELSEWHERE", "SOURCE_RECORD_NOT_DRAKE", "SOURCE_RECORD_NOT_FOUND",
    "SOURCE_RECORD_WRONG_IDENTIFIER", "SOURCE_RECORDS_INCOMPLETE", "SUBJECT_HAS_NO_YEAR",
    "SUBJECT_IS_NATURAL_PERSON", "SUBJECT_REQUIRES_REVIEW", "UNSUPPORTED_IDENTIFIER_TYPE",
    "UNUSABLE_IDENTIFIER",
]
