"""What legal subject does a Drake identifier denote — a person, a business, or an estate?

WHY THIS EXISTS

Drake identifiers are grouped by ``identifier_hash`` and, until now, every one of them was routed to a
``people`` row. That is wrong for 150 of the 1,802 production identifiers, which denote businesses,
estates and trusts rather than natural persons. Worse, the routing that put them there leaned on
contact evidence — a shared firm phone number matched a business identifier to a person, because an
owner's phone IS the business's phone.

So subject typing is decided **by the filed return**, never by the name and never by a contact point:

    1040 / 1040NR                individual income tax return  -> a natural person
    1065 / 1120 / 1120S / 990    entity returns                -> a business entity
    1041                         fiduciary return              -> an estate or trust

The name is not evidence. "DAVID KEETER LLC" is a name-token superset of the human "DAVID KEETER", so
any name-based test merges them; the return types (1120S against 1040) separate them correctly. A
corporate suffix is not consulted here at all.

ONE IDENTIFIER IS NOT ALWAYS ONE LEGAL SUBJECT

Four production identifiers carry 1040 history with a date of birth AND later 1041 history: a
decedent's identifier continues into the estate's fiduciary return. Those are two legal subjects, and
collapsing them because the hash matches would destroy one of the two histories. This module returns
BOTH, each bounded to the years in which the identifier acted as that subject, and marks the result
for review — which of the two an entity is created for is a human decision, not a routing rule.

A different pairing — a person return and a business return on one identifier — is not a recognised
shape at all. It is a data error or a filing irregularity, and it fails closed: no subject is
proposed, and the caller is told to review it. Nothing here silently picks one.

This module is deliberately pure: it reads no database and writes nothing. The later dual-path
ingestion phase supplies the observations and acts on the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: A natural person. Routes to ``drake_identity``.
NATURAL_PERSON = "natural_person"
#: A trading entity. Routes to ``drake_business_identity`` with this ``subject_type``.
BUSINESS_ENTITY = "business_entity"
#: A non-natural legal person. Routes to ``drake_business_identity`` with this ``subject_type``.
ESTATE_OR_TRUST = "estate_or_trust"

#: Exactly one subject, unambiguous.
SINGLE_SUBJECT = "single_subject"
#: 1040 history followed by 1041 history: a decedent and the estate that succeeds them.
PERSON_THEN_ESTATE = "person_then_estate"
#: A person return and an entity return on one identifier. Not a recognised shape.
CONFLICTING_SUBJECTS = "conflicting_subjects"
#: Nothing to classify from.
UNKNOWN = "unknown"

PERSON_RETURNS = frozenset({"1040", "1040NR"})
BUSINESS_RETURNS = frozenset({"1065", "1120", "1120S", "990"})
FIDUCIARY_RETURNS = frozenset({"1041"})

_BY_RETURN = (
    {value: NATURAL_PERSON for value in PERSON_RETURNS}
    | {value: BUSINESS_ENTITY for value in BUSINESS_RETURNS}
    | {value: ESTATE_OR_TRUST for value in FIDUCIARY_RETURNS}
)

#: ``relationship_entities.entity_type`` for each non-natural subject. Production stores estates as
#: ``trust`` and this must not diverge from it: ``canonical_population._DRAKE_ENTITY_BY_RETURN``
#: already maps 1041 -> "trust", and all five production estate rows are stored that way. The
#: estate/trust distinction belongs in ``organization_profiles.entity_form``, not in the bucket.
ENTITY_TYPE_FOR_SUBJECT = {
    BUSINESS_ENTITY: "business",
    ESTATE_OR_TRUST: "trust",
}


def normalize_return_type(value) -> str | None:
    """Drake writes ``1120S``; other paths lowercase it. Compare case-insensitively."""
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    return cleaned or None


@dataclass(frozen=True)
class Observation:
    """One role a Drake identifier held on one return."""

    return_type: str | None
    tax_year: int | None = None
    has_dob: bool = False


@dataclass(frozen=True)
class Subject:
    """One legal subject an identifier denotes, bounded to the years it acted as that subject."""

    subject_type: str
    first_year: int | None
    last_year: int | None
    return_count: int
    return_types: tuple[str, ...]
    dob_observations: int = 0

    @property
    def entity_type(self) -> str | None:
        """The ``relationship_entities.entity_type`` this subject belongs under, if any."""
        return ENTITY_TYPE_FOR_SUBJECT.get(self.subject_type)

    @property
    def routes_to_drake_identity(self) -> bool:
        return self.subject_type == NATURAL_PERSON


@dataclass(frozen=True)
class Classification:
    """The outcome for one identifier."""

    outcome: str
    subjects: tuple[Subject, ...] = field(default_factory=tuple)
    requires_review: bool = False
    reason: str = ""
    unrecognised_return_types: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_single_natural_person(self) -> bool:
        """True only when this identifier may be written to ``drake_identity`` unattended."""
        return (self.outcome == SINGLE_SUBJECT
                and not self.requires_review
                and self.subjects[0].subject_type == NATURAL_PERSON)

    @property
    def subject_types(self) -> tuple[str, ...]:
        return tuple(subject.subject_type for subject in self.subjects)


def _subject(subject_type, observations) -> Subject:
    years = sorted({o.tax_year for o in observations if o.tax_year is not None})
    return Subject(
        subject_type=subject_type,
        first_year=years[0] if years else None,
        last_year=years[-1] if years else None,
        return_count=len(observations),
        return_types=tuple(sorted({normalize_return_type(o.return_type) for o in observations})),
        dob_observations=sum(1 for o in observations if o.has_dob),
    )


def classify(observations) -> Classification:
    """Classify one identifier from the returns it appears on.

    ``observations`` is any iterable of :class:`Observation` (or of mappings carrying
    ``return_type``, ``tax_year`` and ``has_dob``). The result never guesses: an identifier whose
    returns do not describe a single recognised shape is returned for review with no subject.
    """
    rows = [o if isinstance(o, Observation) else Observation(
        return_type=o.get("return_type"), tax_year=o.get("tax_year"),
        has_dob=bool(o.get("has_dob"))) for o in observations]

    by_subject: dict[str, list[Observation]] = {}
    unrecognised: set[str] = set()
    for row in rows:
        normalized = normalize_return_type(row.return_type)
        if normalized is None:
            continue
        subject_type = _BY_RETURN.get(normalized)
        if subject_type is None:
            unrecognised.add(normalized)
            continue
        by_subject.setdefault(subject_type, []).append(row)

    unrecognised_types = tuple(sorted(unrecognised))

    if not by_subject:
        listed = ", ".join(unrecognised_types)
        return Classification(
            outcome=UNKNOWN, requires_review=True,
            unrecognised_return_types=unrecognised_types,
            reason=("no recognised return type on any row; the name is not evidence and is not "
                    "consulted" if not unrecognised_types else
                    f"only unrecognised return types: {listed}"))

    if len(by_subject) == 1:
        subject_type, rows_for_subject = next(iter(by_subject.items()))
        subject = _subject(subject_type, rows_for_subject)
        review = bool(unrecognised_types)
        listed = ", ".join(unrecognised_types)
        held = (f"; also carries unrecognised return type(s) {listed}, so it is held for review"
                if review else "")
        return Classification(
            outcome=SINGLE_SUBJECT, subjects=(subject,), requires_review=review,
            unrecognised_return_types=unrecognised_types,
            reason=f"{subject_type} from return type(s) {', '.join(subject.return_types)}{held}")

    present = set(by_subject)

    if present == {NATURAL_PERSON, ESTATE_OR_TRUST}:
        person = _subject(NATURAL_PERSON, by_subject[NATURAL_PERSON])
        estate = _subject(ESTATE_OR_TRUST, by_subject[ESTATE_OR_TRUST])
        return Classification(
            outcome=PERSON_THEN_ESTATE, subjects=(person, estate), requires_review=True,
            unrecognised_return_types=unrecognised_types,
            reason=(
                "one identifier, two legal subjects: a natural person on "
                f"{', '.join(person.return_types)} ({person.first_year}-{person.last_year}) and an "
                f"estate or trust on {', '.join(estate.return_types)} "
                f"({estate.first_year}-{estate.last_year}). The estate succeeds the decedent and "
                "must not be collapsed into them merely because the identifier matches."))

    conflict = "; ".join(
        f"{subject_type} from "
        f"{', '.join(sorted({normalize_return_type(o.return_type) for o in rows_for_subject}))}"
        for subject_type, rows_for_subject in sorted(by_subject.items()))
    return Classification(
        outcome=CONFLICTING_SUBJECTS, subjects=(), requires_review=True,
        unrecognised_return_types=unrecognised_types,
        reason=(f"return types describe subjects that cannot coexist on one identifier: {conflict}."
                " This is a data error or a filing irregularity, not a routing decision; no subject"
                " is proposed."))
