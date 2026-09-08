"""Where a Drake identifier's evidence is written — the one boundary that decides.

WHY THIS IS ONE MODULE

Forward Drake ingestion has several writers, and before this they all funnelled into ``people``:

* ``scripts/link_drake_to_people.py`` builds Drake ``source_contacts`` and, on AUTO_LINK, writes
  ``person_source_links``;
* ``app/services/drake_identity_rebuild.py`` derives ``drake_identity`` from those source contacts;
* ``app/routes/matches.py::approve_drake_identity`` sets ``primary_person_id`` on human approval.

If subject typing lived in only one of them, a business return could still reach a person through
another. So classification is done here, from the shared classifier, and each writer asks this module
rather than deciding for itself.

WHAT DECIDES

``app.services.drake_return_subject`` — the filed return, never the name. The evidence needed is
already carried on every Drake source contact: ``raw_data`` holds ``return_type``, ``tax_year`` and
``role``, written by ``build_drake_contacts``. No new schema and no new source field is required.

WHAT IS DELIBERATELY NOT DONE HERE

No entity is created or matched. A newly derived business identity is written with
``relationship_entity_id = NULL`` — "not yet adjudicated" — because a name is not evidence of entity
identity, and the D7 review found all 18 candidate matches rested on name alone with no EIN anywhere
in production to corroborate them. Auto-linking on a normalised name is exactly the defect that put
two businesses and an unrelated person on one record in the first place.

Nothing here backfills. Identities already sitting in ``drake_identity`` that classify as non-natural
are reported, never moved or deleted; relocating them is a later, separately authorised phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.services.drake_return_subject import (
    BUSINESS_ENTITY,
    CONFLICTING_SUBJECTS,
    ESTATE_OR_TRUST,
    NATURAL_PERSON,
    PERSON_THEN_ESTATE,
    SINGLE_SUBJECT,
    UNKNOWN,
    Observation,
    classify,
)

#: Route to ``drake_identity`` / ``people``.
ROUTE_PERSON = "route_person"
#: Route to ``drake_business_identity`` / ``relationship_entities``.
ROUTE_BUSINESS_IDENTITY = "route_business_identity"
#: Route nowhere. The caller must not write to either authoritative path.
ROUTE_REVIEW = "route_review"

#: Deterministic reason codes for a refusal. Surfaced to the caller, never swallowed.
CONFLICTING_SUBJECTS_CODE = "CONFLICTING_SUBJECTS"
UNKNOWN_RETURN_TYPE = "UNKNOWN_RETURN_TYPE"
INSUFFICIENT_YEAR_SEPARATION = "INSUFFICIENT_YEAR_SEPARATION"
REVIEW_REQUIRED = "REVIEW_REQUIRED"

#: Fields recomputed from Drake source evidence on every ingestion.
DERIVED_COLUMNS = ("subject_type", "first_year", "last_year", "return_count", "subject_name",
                   "return_types", "decedent_identifier_hash")

#: Never written by ingestion. Entity linkage, adjudication and first-observed history.
PERSISTENT_COLUMNS = ("relationship_entity_id", "trust_level", "confirmation_source",
                      "evidence_method", "confirmed_by_user_id", "confirmed_at", "created_at")


@dataclass(frozen=True)
class Route:
    """Where one identifier's evidence may be written, and why."""

    identifier_hash: str
    destination: str
    subject_type: str | None = None
    first_year: int | None = None
    last_year: int | None = None
    return_count: int = 0
    return_types: tuple = ()
    reason_code: str | None = None
    reason: str = ""
    #: Set on an ESTATE route that succeeds a natural person on the SAME identifier. Recorded from
    #: the classification, never guessed from a name.
    decedent_identifier_hash: str | None = None
    #: For a decedent-then-estate identifier: the sibling route for the other legal subject.
    companion: Route | None = None

    @property
    def is_person(self) -> bool:
        return self.destination == ROUTE_PERSON

    @property
    def is_business_identity(self) -> bool:
        return self.destination == ROUTE_BUSINESS_IDENTITY

    @property
    def needs_review(self) -> bool:
        return self.destination == ROUTE_REVIEW


@dataclass
class RoutingReport:
    """What one ingestion pass routed, and what it refused."""

    person: list = field(default_factory=list)
    business: list = field(default_factory=list)
    review: list = field(default_factory=list)

    def add(self, route: Route) -> Route:
        (self.person if route.is_person
         else self.business if route.is_business_identity
         else self.review).append(route)
        return route

    def lines(self) -> list[str]:
        out = [
            f"routed to drake_identity          {len(self.person)}",
            f"routed to drake_business_identity {len(self.business)}",
            f"held for review                   {len(self.review)}",
        ]
        for route in self.review:
            out.append(f"  {route.reason_code}: {route.identifier_hash[:12]} — {route.reason}")
        return out


def _route_for(identifier_hash, subject, name_by_subject) -> Route:
    return Route(
        identifier_hash=identifier_hash,
        destination=(ROUTE_PERSON if subject.subject_type == NATURAL_PERSON
                     else ROUTE_BUSINESS_IDENTITY),
        subject_type=subject.subject_type,
        first_year=subject.first_year,
        last_year=subject.last_year,
        return_count=subject.return_count,
        return_types=subject.return_types,
        reason=f"{subject.subject_type} from return type(s) "
               f"{', '.join(subject.return_types)}",
    )


def route_identifier(identifier_hash, observations, *, names=None) -> Route:
    """Decide where one identifier's evidence may be written.

    ``observations`` are :class:`~app.services.drake_return_subject.Observation` values (or mappings)
    for every return row this identifier appears on. Returns exactly one :class:`Route`; for a
    decedent-then-estate identifier the person route carries the estate route as ``companion``, so
    the caller receives both legal subjects and cannot collapse them.
    """
    names = names or {}
    result = classify(observations)

    if result.outcome == SINGLE_SUBJECT and not result.requires_review:
        return _route_for(identifier_hash, result.subjects[0], names)

    if result.outcome == PERSON_THEN_ESTATE:
        person, estate = result.subjects
        # The two subjects must be separable by tax year to be written apart. They overlap in
        # production (a 1040 and a 1041 both covering 2022), which is legitimate -- the decedent's
        # final personal return and the estate's first fiduciary return share a year -- so overlap
        # alone is not a refusal. What is refused is an estate with no year of its own at all.
        if estate.first_year is None or person.first_year is None:
            return Route(identifier_hash=identifier_hash, destination=ROUTE_REVIEW,
                         reason_code=INSUFFICIENT_YEAR_SEPARATION,
                         return_types=tuple(sorted(set(person.return_types)
                                                   | set(estate.return_types))),
                         reason="a decedent-then-estate identifier carries no tax year on one of "
                                "its two subjects, so its observations cannot be attributed")
        estate_route = _route_for(identifier_hash, estate, names)
        estate_route = Route(**{**estate_route.__dict__,
                                "decedent_identifier_hash": identifier_hash})
        person_route = _route_for(identifier_hash, person, names)
        return Route(**{**person_route.__dict__, "companion": estate_route})

    if result.outcome == CONFLICTING_SUBJECTS:
        code = CONFLICTING_SUBJECTS_CODE
    elif result.outcome == UNKNOWN:
        code = UNKNOWN_RETURN_TYPE
    else:
        code = REVIEW_REQUIRED

    return Route(identifier_hash=identifier_hash, destination=ROUTE_REVIEW, reason_code=code,
                 return_types=result.unrecognised_return_types, reason=result.reason)


# --- reading the evidence that already exists on a Drake source contact ------------------------------

_SOURCE_OBSERVATIONS = """
    SELECT
        sc.raw_data->>'identifier_hash'          AS identifier_hash,
        sc.raw_data->>'return_type'              AS return_type,
        (sc.raw_data->>'tax_year')::integer      AS tax_year,
        sc.raw_data->>'role'                     AS role,
        sc.full_name                             AS full_name,
        CASE WHEN sc.raw_data->>'role' = 'taxpayer' THEN r.taxpayer_dob ELSE r.spouse_dob END AS dob
    FROM source_contacts sc
    LEFT JOIN drake_client_returns r
        ON r.id = (sc.raw_data->>'drake_return_id')::integer
    WHERE sc.source_system = 'Drake'
      AND sc.raw_data->>'identifier_hash' IS NOT NULL
"""


def observations_by_identifier(connection) -> dict:
    """Every Drake identifier's return observations, from the source contacts already written.

    ``return_type`` is carried in ``source_contacts.raw_data`` by ``build_drake_contacts``, so no new
    source field and no schema change is needed to classify at this boundary.
    """
    grouped: dict[str, dict] = {}
    for row in connection.execute(text(_SOURCE_OBSERVATIONS)).mappings():
        entry = grouped.setdefault(row["identifier_hash"], {"observations": [], "names": {}})
        entry["observations"].append(Observation(
            return_type=row["return_type"], tax_year=row["tax_year"], has_dob=row["dob"] is not None))
        if row["full_name"]:
            entry["names"].setdefault(row["role"], row["full_name"])
    return grouped


def route_all(connection) -> tuple[dict, RoutingReport]:
    """Route every Drake identifier present in ``source_contacts``. Reads only."""
    report = RoutingReport()
    routes = {}
    for identifier_hash, entry in observations_by_identifier(connection).items():
        route = report.add(route_identifier(identifier_hash, entry["observations"],
                                            names=entry["names"]))
        routes[identifier_hash] = route
    return routes, report


# --- writing a non-natural identity ------------------------------------------------------------------

_DBI_UPSERT = """
    INSERT INTO drake_business_identity (
        identifier_hash, subject_type, first_year, last_year, return_count,
        subject_name, return_types, decedent_identifier_hash
    )
    VALUES (:identifier_hash, :subject_type, :first_year, :last_year, :return_count,
            :subject_name, :return_types, :decedent_identifier_hash)
    ON CONFLICT ON CONSTRAINT uq_drake_business_identity DO UPDATE SET
        first_year               = EXCLUDED.first_year,
        last_year                = EXCLUDED.last_year,
        return_count             = EXCLUDED.return_count,
        subject_name             = EXCLUDED.subject_name,
        return_types             = EXCLUDED.return_types,
        decedent_identifier_hash = EXCLUDED.decedent_identifier_hash,
        updated_at               = now()
    RETURNING id
"""


def upsert_business_identity(connection, route: Route, *, subject_name) -> int:
    """Write one non-natural identity, refreshing derived fields and preserving adjudication.

    Mirrors ``drake_identity_rebuild``: the ``DO UPDATE`` lists only source-derived columns, so
    ``relationship_entity_id``, the trust and confirmation fields and ``created_at`` survive
    re-ingestion. An established entity linkage is never wiped by a later import.
    """
    if not route.is_business_identity:
        raise ValueError(f"{route.identifier_hash[:12]} does not route to a business identity")
    return connection.execute(text(_DBI_UPSERT), {
        "identifier_hash": route.identifier_hash,
        "subject_type": route.subject_type,
        "first_year": route.first_year,
        "last_year": route.last_year,
        "return_count": route.return_count,
        "subject_name": subject_name or "(unnamed)",
        "return_types": list(route.return_types),
        # Set only where the classifier proved this estate succeeds a person identity on the same
        # identifier; never guessed from a name.
        "decedent_identifier_hash": route.decedent_identifier_hash,
    }).scalar_one()


def is_person_routed(route: Route | None) -> bool:
    """A writer targeting ``people`` may proceed only for a route that is unambiguously a person."""
    return bool(route and route.is_person)


__all__ = [
    "BUSINESS_ENTITY", "ESTATE_OR_TRUST", "NATURAL_PERSON",
    "ROUTE_BUSINESS_IDENTITY", "ROUTE_PERSON", "ROUTE_REVIEW",
    "CONFLICTING_SUBJECTS_CODE", "INSUFFICIENT_YEAR_SEPARATION", "REVIEW_REQUIRED",
    "UNKNOWN_RETURN_TYPE", "DERIVED_COLUMNS", "PERSISTENT_COLUMNS",
    "Route", "RoutingReport", "is_person_routed", "observations_by_identifier",
    "route_all", "route_identifier", "upsert_business_identity",
]
