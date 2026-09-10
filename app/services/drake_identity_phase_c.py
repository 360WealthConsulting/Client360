"""D7 Phase C — relocate a mis-filed non-natural identity out of ``drake_identity``.

WHAT THIS FIXES
---------------
``drake_identity`` holds natural-person Drake identities. Its own rebuild says so — *"Only a natural
person belongs in this table"* — and the review route refuses to approve anything else onto a person
record. But 133 rows predating D7 Phase B routing describe businesses, estates and trusts:
HANDPICKED WINE WAREHOUSE LLC, TNT LYNCHBURG INC, ANNETTE SMITH ESTATE. Phase B stopped creating
them and reports the backlog as ``pending_d7_migration``; it deliberately relocates nothing. This is
that later phase.

The rows are not merely untidy. :func:`drake_machine_attribution._check_identifier_is_free` refuses
to attribute any identifier that ``drake_identity`` mentions, so every one of these rows blocks the
attribution of the business it misdescribes.

WHY RELOCATION AND NOT A TOMBSTONE
----------------------------------
The obvious design — mark the row retired and keep it — cannot work. That guard is
``SELECT identifier_hash FROM drake_identity WHERE identifier_hash = :hash`` with no other
predicate, and it is not being weakened. A retained row still blocks however it is marked. So the
row must leave the table, and no schema change is needed for that.

WHAT A RELOCATED IDENTITY LOOKS LIKE
------------------------------------
One ``drake_business_identity`` row with ``relationship_entity_id``, ``trust_level``,
``confirmation_source`` and ``evidence_method`` all NULL: **typed, not yet attributed**. That is not
a new shape — it is exactly what ``drake_subject_routing._DBI_UPSERT`` already writes at ingestion.
Phase C decides WHAT an identifier is. Deciding WHICH entity owns it belongs to
:mod:`app.services.drake_machine_attribution` or to human adjudication, and this module never does
it: it writes no ``entity_source_links`` and sets no entity.

TYPED FROM THE FILED RETURNS, NEVER FROM THE SOURCE CONTACTS
------------------------------------------------------------
``drake_subject_routing`` types identifiers from ``source_contacts.raw_data.return_type``, which is
NULL on 109 Drake contacts whose returns are plainly 1120S — so it holds 14 of these 133 for review
that the filed returns type unambiguously. This module classifies from ``drake_client_returns``
through the same helper the attribution service uses, so the two agree. Repairing those 109 contacts
is a separate lane and is not attempted here.

WHAT IT REFUSES
---------------
Fail-closed, with a code per reason. Two are worth naming:

``PERSON_LINK_WITHOUT_PSL``  the row carries ``primary_person_id`` and NOTHING else records that
                             association — no ``person_source_links`` row reaches the same
                             identifier. Relocating would erase the only live evidence that someone
                             connected that person to that business, leaving it in an audit payload
                             and nowhere a query would find it. Eight rows are held for this.
``UNEXPECTED_DEPENDENCY``    a pending ``drake_identity_match_candidates`` row proposes linking this
                             identifier to a PERSON — the very assertion being retired. It is a
                             logical reference with no foreign key, so it cannot dangle
                             structurally, but approving it afterwards is impossible (the review
                             route returns 409 for a non-natural subject). Candidate cleanup is a
                             separate lane, so this module refuses rather than strand it.

IDEMPOTENCY
-----------
The relocation is not idempotent by repetition — the second run finds no row to move. It is
idempotent by REFUSAL: :data:`ALREADY_RELOCATED` is returned when the ``drake_identity`` row is gone
and a matching ``drake_business_identity`` row exists, with no write of any kind. A second
invocation can neither delete another row nor create a second identity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.security.audit import write_audit_event
from app.services.drake_machine_attribution import _RETURNS_FOR_HASH
from app.services.drake_return_subject import NATURAL_PERSON, SINGLE_SUBJECT, Observation, classify

BATCH_ID = "D7-PHASE-C"

#: One entry per relocated identifier. ``actor_user_id`` is NULL — the established contract for
#: unattended work, the same one the attribution service uses.
AUDIT_ACTION = "drake.identity_relocated_phase_c"

#: Phase C types an identifier. It never attributes one, so every attribution column stays NULL.
UNATTRIBUTED: dict[str, Any] = {"relationship_entity_id": None, "trust_level": None,
                                "confirmation_source": None, "evidence_method": None}

# --- refusal codes --------------------------------------------------------------------------------

NOT_TYPEABLE = "NOT_TYPEABLE"
REQUIRES_REVIEW = "REQUIRES_REVIEW"
MIXED_SUBJECT = "MIXED_SUBJECT"
SUBJECT_IS_NATURAL_PERSON = "SUBJECT_IS_NATURAL_PERSON"
PERSON_LINK_WITHOUT_PSL = "PERSON_LINK_WITHOUT_PSL"
ROW_DRIFTED = "ROW_DRIFTED"
ROW_NOT_FOUND = "ROW_NOT_FOUND"
CONFLICTING_DBI = "CONFLICTING_DBI"
UNEXPECTED_DEPENDENCY = "UNEXPECTED_DEPENDENCY"
ALREADY_RELOCATED = "ALREADY_RELOCATED"

#: The columns of ``drake_identity``, in table order. The frozen snapshot carries all of them so a
#: rollback can restore the row losslessly, ``created_at`` and NULLs included.
IDENTITY_COLUMNS = ("identifier_hash", "primary_person_id", "first_year", "last_year",
                    "return_count", "taxpayer_name", "spouse_name", "confidence", "created_at")


class RelocationRefused(RuntimeError):
    """One relocation was refused, with a stable machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class RelocationRequest:
    """One frozen identifier. Every field is compared against production before anything moves."""

    identifier_hash: str
    #: The complete ``drake_identity`` row as frozen, keyed by column name.
    frozen_row: dict[str, Any]
    #: ``person_source_links`` ids that evidence the person association (cohort B only).
    expected_psl_ids: tuple[int, ...] = ()
    request_id: str | None = None


@dataclass
class RelocationResult:
    """What one relocation established."""

    identifier_hash: str
    subject_type: str
    subject_name: str
    first_year: int
    last_year: int
    return_count: int
    return_types: tuple[str, ...]
    cohort: str = ""
    business_identity_id: int = 0
    removed_row: dict[str, Any] = field(default_factory=dict)
    source_contact_ids: tuple[int, ...] = ()
    source_record_ids: tuple[str, ...] = ()
    drake_return_ids: tuple[int, ...] = ()
    audit_event_id: int = 0


# --- SQL ------------------------------------------------------------------------------------------

_IDENTITY_ROW = ("SELECT identifier_hash, primary_person_id, first_year, last_year, return_count, "
                 "taxpayer_name, spouse_name, confidence, created_at "
                 "FROM drake_identity WHERE identifier_hash = :hash FOR UPDATE")

_DBI_FOR_HASH = ("SELECT id, subject_type, relationship_entity_id, trust_level "
                 "FROM drake_business_identity WHERE identifier_hash = :hash")

_CONTACTS_FOR_HASH = """
    SELECT id, source_record_id, raw_data
      FROM source_contacts
     WHERE source_system = 'Drake' AND raw_data->>'identifier_hash' = :hash
     ORDER BY id
"""

_PSL_FOR_CONTACTS = """
    SELECT id, person_id, source_contact_id, match_method, match_score, confirmed
      FROM person_source_links WHERE source_contact_id = ANY(:ids) ORDER BY id
"""

_PENDING_CANDIDATES = """
    SELECT id, person_id, score, status FROM drake_identity_match_candidates
     WHERE identifier_hash = :hash ORDER BY id
"""

_INSERT_DBI = """
    INSERT INTO drake_business_identity (
        identifier_hash, subject_type, relationship_entity_id, first_year, last_year,
        return_count, subject_name, return_types, trust_level, confirmation_source, evidence_method
    )
    VALUES (
        :identifier_hash, :subject_type, NULL, :first_year, :last_year,
        :return_count, :subject_name, :return_types, NULL, NULL, NULL
    )
    RETURNING id
"""

_DELETE_IDENTITY = "DELETE FROM drake_identity WHERE identifier_hash = :hash RETURNING identifier_hash"


def classify_identifier(connection, identifier_hash: str):
    """Type an identifier from its FILED RETURNS, exactly as the attribution service does."""
    rows = connection.execute(text(_RETURNS_FOR_HASH), {"hash": identifier_hash}).mappings().all()
    if not rows:
        raise RelocationRefused(
            NOT_TYPEABLE, "no Drake return carries this identifier, so nothing types it.")
    result = classify([Observation(return_type=r["return_type"], tax_year=r["tax_year"],
                                   has_dob=r["has_dob"]) for r in rows])
    if result.outcome != SINGLE_SUBJECT:
        raise RelocationRefused(
            MIXED_SUBJECT,
            f"the filed returns do not describe one subject ({result.outcome}): "
            f"{result.reason or 'no single subject'}.")
    if result.requires_review:
        raise RelocationRefused(
            REQUIRES_REVIEW,
            f"the filed returns need human review: {result.reason or 'held for review'}.")
    subject = result.subjects[0]
    if subject.subject_type == NATURAL_PERSON:
        raise RelocationRefused(
            SUBJECT_IS_NATURAL_PERSON,
            "the filed returns describe a natural person, which belongs in drake_identity.")
    if subject.first_year is None or subject.last_year is None:
        raise RelocationRefused(NOT_TYPEABLE, "the filed returns carry no tax year.")
    name = connection.execute(text(
        "SELECT taxpayer_normalized_name FROM drake_client_returns "
        "WHERE taxpayer_identifier_hash = :hash AND taxpayer_normalized_name IS NOT NULL "
        "ORDER BY tax_year DESC LIMIT 1"), {"hash": identifier_hash}).scalar()
    return subject, (name or "(unnamed)")


def _comparable(row: dict) -> dict:
    """A frozen row and a live row compared as strings, so a manifest round-trip cannot differ."""
    return {c: ("" if row.get(c) is None else str(row.get(c))) for c in IDENTITY_COLUMNS}


def relocate_identity(connection, request: RelocationRequest) -> RelocationResult:
    """Move one non-natural identity into ``drake_business_identity``. Fail-closed.

    Writes in the caller's transaction: one DBI insert, one ``drake_identity`` delete, one audit
    entry. Never touches ``person_source_links``, ``people``, ``relationship_entities``,
    ``drake_client_returns``, ``source_contacts`` or ``drake_identity_match_candidates``.
    """
    identifier_hash = request.identifier_hash
    live = connection.execute(text(_IDENTITY_ROW), {"hash": identifier_hash}).mappings().first()
    existing_dbi = connection.execute(
        text(_DBI_FOR_HASH), {"hash": identifier_hash}).mappings().all()

    if live is None:
        if existing_dbi:
            raise RelocationRefused(
                ALREADY_RELOCATED,
                f"identifier already relocated to drake_business_identity "
                f"{[r['id'] for r in existing_dbi]}; nothing to move and nothing written.")
        raise RelocationRefused(ROW_NOT_FOUND, "no drake_identity row carries this identifier.")

    if _comparable(dict(live)) != _comparable(request.frozen_row):
        raise RelocationRefused(
            ROW_DRIFTED,
            "the drake_identity row no longer matches the frozen manifest; refusing to relocate a "
            "row that changed after review.")

    if existing_dbi:
        raise RelocationRefused(
            CONFLICTING_DBI,
            f"drake_business_identity already holds this identifier as {[r['id'] for r in existing_dbi]}; "
            "Phase C creates the row and will not merge into one it did not write.")

    pending = [r for r in connection.execute(text(_PENDING_CANDIDATES),
                                             {"hash": identifier_hash}).mappings()
               if (r["status"] or "") == "pending"]
    if pending:
        raise RelocationRefused(
            UNEXPECTED_DEPENDENCY,
            f"pending match candidate(s) {[r['id'] for r in pending]} propose linking this "
            "identifier to a person. Candidate cleanup is a separate lane; refusing to strand them.")

    subject, subject_name = classify_identifier(connection, identifier_hash)

    contacts = connection.execute(text(_CONTACTS_FOR_HASH),
                                  {"hash": identifier_hash}).mappings().all()
    contact_ids = tuple(int(c["id"]) for c in contacts)
    record_ids = tuple(c["source_record_id"] or "" for c in contacts)
    return_ids = tuple(sorted({int(r["id"]) for r in connection.execute(
        text(_RETURNS_FOR_HASH), {"hash": identifier_hash}).mappings()}))

    # Cohort. A person link is allowed ONLY where person_source_links independently records the same
    # association, so relocation removes a duplicate assertion rather than the only one.
    cohort = "A"
    if live["primary_person_id"] is not None:
        cohort = "B"
        psl = connection.execute(text(_PSL_FOR_CONTACTS),
                                 {"ids": list(contact_ids) or [0]}).mappings().all()
        backing = tuple(sorted(int(r["id"]) for r in psl
                               if int(r["person_id"]) == int(live["primary_person_id"])))
        if not backing:
            raise RelocationRefused(
                PERSON_LINK_WITHOUT_PSL,
                f"primary_person_id {live['primary_person_id']} is recorded here and nowhere else "
                "(no person_source_links row reaches this identifier). Relocating would erase the "
                "only live record of that association.")
        if request.expected_psl_ids and backing != tuple(sorted(request.expected_psl_ids)):
            raise RelocationRefused(
                ROW_DRIFTED,
                f"the backing person_source_links changed: expected "
                f"{sorted(request.expected_psl_ids)}, found {list(backing)}.")

    removed = {c: live[c] for c in IDENTITY_COLUMNS}
    business_identity_id = connection.execute(text(_INSERT_DBI), {
        "identifier_hash": identifier_hash,
        "subject_type": subject.subject_type,
        "first_year": subject.first_year,
        "last_year": subject.last_year,
        "return_count": subject.return_count,
        "subject_name": subject_name,
        "return_types": list(subject.return_types),
    }).scalar_one()

    deleted = connection.execute(text(_DELETE_IDENTITY), {"hash": identifier_hash}).scalar_one()
    if deleted != identifier_hash:
        raise RelocationRefused(ROW_DRIFTED, "the delete removed a different row than intended.")

    audit_id = write_audit_event(
        action=AUDIT_ACTION,
        entity_type="drake_business_identity",
        entity_id=business_identity_id,
        actor_user_id=None,                       # unattended: the established NULL-actor contract
        request_id=request.request_id or f"{BATCH_ID}-{identifier_hash[:12]}",
        conn=connection,
        metadata={
            "batch": BATCH_ID,
            "cohort": cohort,
            "identifier_hash": identifier_hash,
            "business_identity_id": business_identity_id,
            "subject_type": subject.subject_type,
            "subject_name": subject_name,
            "first_year": subject.first_year,
            "last_year": subject.last_year,
            "return_count": subject.return_count,
            "return_types": list(subject.return_types),
            "source_contact_ids": list(contact_ids),
            "source_record_ids": list(record_ids),
            "drake_return_ids": list(return_ids),
            # The whole removed row, losslessly, so a rollback can restore it exactly.
            "removed_drake_identity": {c: (removed[c].isoformat()
                                           if hasattr(removed[c], "isoformat") else removed[c])
                                       for c in IDENTITY_COLUMNS},
            "former_primary_person_id": removed["primary_person_id"],
            "former_confidence": removed["confidence"],
        },
    )

    return RelocationResult(
        identifier_hash=identifier_hash, subject_type=subject.subject_type,
        subject_name=subject_name, first_year=subject.first_year, last_year=subject.last_year,
        return_count=subject.return_count, return_types=tuple(subject.return_types),
        cohort=cohort, business_identity_id=business_identity_id, removed_row=removed,
        source_contact_ids=contact_ids, source_record_ids=record_ids,
        drake_return_ids=return_ids, audit_event_id=audit_id or 0)


def plan_digest(rows) -> str:
    """Content hash over the frozen identifiers, order-independent of dict layout."""
    import hashlib
    payload = json.dumps(sorted(
        [{"identifier_hash": r["identifier_hash"], "frozen_row": _comparable(r["frozen_row"]),
          "expected_psl_ids": sorted(r.get("expected_psl_ids") or ())} for r in rows],
        key=lambda r: r["identifier_hash"]), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["ALREADY_RELOCATED", "AUDIT_ACTION", "BATCH_ID", "CONFLICTING_DBI", "IDENTITY_COLUMNS",
           "MIXED_SUBJECT", "NOT_TYPEABLE", "PERSON_LINK_WITHOUT_PSL", "REQUIRES_REVIEW",
           "ROW_DRIFTED", "ROW_NOT_FOUND", "SUBJECT_IS_NATURAL_PERSON", "UNATTRIBUTED",
           "UNEXPECTED_DEPENDENCY", "RelocationRefused", "RelocationRequest", "RelocationResult",
           "classify_identifier", "plan_digest", "relocate_identity"]
