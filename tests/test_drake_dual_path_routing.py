"""Forward Drake ingestion routes each identifier to the table its filed return says it belongs in.

THE DEFECT THESE PIN

Every Drake identifier used to land on a ``people`` row. 150 of the 1,802 production identifiers are
not natural persons, and 46 of those sit on a person today. The routing that put them there leaned on
contact evidence: person 1314 collected two S-corp identifiers and an unrelated person's identifier
because the firm's phone number matched all three, and a phone match was allowed to decide.

Routing is now decided at one shared boundary — ``app.services.drake_subject_routing``, over the
Phase A classifier — so a business identifier cannot reach a person through whichever writer runs.
The two writers that could are the auto-linker (via the shared evaluator) and the identity-approval
route; both are covered here.

Nothing in this module backfills. Rows are seeded inside a transaction and rolled back.
"""
import json
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from app.services.drake_identity_rebuild import rebuild_drake_identities
from app.services.drake_linkage_evidence import (
    NO_MATCH,
    TAXPAYER,
    Roster,
    RosterPerson,
    build_identity_evidence,
    evaluate,
)
from app.services.drake_return_subject import Observation
from app.services.drake_subject_routing import (
    CONFLICTING_SUBJECTS_CODE,
    ROUTE_BUSINESS_IDENTITY,
    ROUTE_PERSON,
    ROUTE_REVIEW,
    UNKNOWN_RETURN_TYPE,
    route_identifier,
    upsert_business_identity,
)


@pytest.fixture()
def conn():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


class Seeder:
    def __init__(self, connection):
        self.connection = connection
        self.tag = f"route-{uuid.uuid4().hex[:8]}"

    def contacts(self, identifier_hash, observations, *, name="SUBJECT"):
        """One Drake source contact per (return type, year), as the importer writes them."""
        for return_type, year in observations:
            self.connection.execute(text(
                "INSERT INTO source_contacts "
                "  (source_system, source_file, source_hash, full_name, raw_data) "
                "VALUES ('Drake', :f, :sh, :n, CAST(:raw AS json))"),
                {"f": f"{self.tag}.csv", "sh": uuid.uuid4().hex, "n": name,
                 "raw": json.dumps({"identifier_hash": identifier_hash, "role": "taxpayer",
                                    "tax_year": str(year), "return_type": return_type})})
        return identifier_hash

    def entity(self, name="Routed Entity LLC"):
        return self.connection.execute(text(
            "INSERT INTO relationship_entities (entity_type, name) "
            "VALUES ('business', :n) RETURNING id"), {"n": f"{self.tag} {name}"}).scalar_one()

    def identity_row(self, identifier_hash):
        return self.connection.execute(text(
            "SELECT * FROM drake_identity WHERE identifier_hash = :h"),
            {"h": identifier_hash}).mappings().one_or_none()

    def business_rows(self, identifier_hash):
        return self.connection.execute(text(
            "SELECT * FROM drake_business_identity WHERE identifier_hash = :h "
            "ORDER BY subject_type"), {"h": identifier_hash}).mappings().all()


@pytest.fixture()
def seed(conn):
    return Seeder(conn)


def obs(*pairs):
    return [Observation(return_type=t, tax_year=y) for t, y in pairs]


# ==================================================================================================
# 1-4. Each subject reaches its own table, and only its own.
# ==================================================================================================

def test_a_1040_routes_to_drake_identity_and_creates_no_business_row(seed):
    h = seed.contacts(uuid.uuid4().hex, [("1040", 2021), ("1040", 2022)], name="A PERSON")

    rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is not None
    assert seed.business_rows(h) == []


@pytest.mark.parametrize("return_type", ["1120S", "1120", "1065", "990"])
def test_a_business_return_routes_to_drake_business_identity_only(seed, return_type):
    h = seed.contacts(uuid.uuid4().hex, [(return_type, 2021)], name="A COMPANY LLC")

    rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is None, "a business identifier must never enter drake_identity"
    rows = seed.business_rows(h)
    assert len(rows) == 1 and rows[0]["subject_type"] == "business_entity"
    assert rows[0]["return_types"] == [return_type]


def test_a_1041_routes_to_estate_or_trust(seed):
    h = seed.contacts(uuid.uuid4().hex, [("1041", 2021)], name="SOMEONE ESTATE")

    rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is None
    rows = seed.business_rows(h)
    assert len(rows) == 1 and rows[0]["subject_type"] == "estate_or_trust"


# ==================================================================================================
# 5. A decedent and the estate that succeeds them: both subjects, neither collapsed.
# ==================================================================================================

def test_person_then_estate_writes_both_subjects_and_collapses_neither(seed):
    h = seed.contacts(uuid.uuid4().hex,
                      [("1040", 2021), ("1040", 2022), ("1041", 2022)], name="A DECEDENT")

    rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is not None, "the person half stays in drake_identity"
    rows = seed.business_rows(h)
    assert [r["subject_type"] for r in rows] == ["estate_or_trust"]
    assert rows[0]["decedent_identifier_hash"] == h, "the succession is recorded, not inferred"


def test_the_two_subjects_keep_their_own_years(seed):
    route = route_identifier("h", obs(("1040", 2021), ("1040", 2022), ("1041", 2022)))

    assert route.destination == ROUTE_PERSON
    assert (route.first_year, route.last_year) == (2021, 2022)
    assert route.companion is not None
    assert route.companion.destination == ROUTE_BUSINESS_IDENTITY
    assert (route.companion.first_year, route.companion.last_year) == (2022, 2022)


# ==================================================================================================
# 6-7. Conflicting and unknown fail closed, with a reason code and no authoritative write.
# ==================================================================================================

def test_a_person_and_business_identifier_is_written_nowhere(seed):
    h = seed.contacts(uuid.uuid4().hex, [("1040", 2021), ("1120S", 2022)], name="CONTRADICTORY")

    report = rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is None
    assert seed.business_rows(h) == []
    assert any(r.identifier_hash == h and r.reason_code == CONFLICTING_SUBJECTS_CODE
               for r in report.routing.review)


def test_an_unknown_return_type_is_written_nowhere(seed):
    h = seed.contacts(uuid.uuid4().hex, [("706", 2021)], name="UNKNOWN FORM")

    report = rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is None
    assert seed.business_rows(h) == []
    assert any(r.identifier_hash == h and r.reason_code == UNKNOWN_RETURN_TYPE
               for r in report.routing.review)


def test_a_missing_return_type_is_written_nowhere(seed):
    h = seed.contacts(uuid.uuid4().hex, [(None, 2021)], name="NO FORM")

    rebuild_drake_identities(seed.connection)

    assert seed.identity_row(h) is None
    assert seed.business_rows(h) == []


@pytest.mark.parametrize("observations,code", [
    (obs(("1040", 2021), ("1120S", 2022)), CONFLICTING_SUBJECTS_CODE),
    (obs(("706", 2021)), UNKNOWN_RETURN_TYPE),
    ([], UNKNOWN_RETURN_TYPE),
])
def test_refusals_carry_a_deterministic_reason_code(observations, code):
    route = route_identifier("h", observations)

    assert route.destination == ROUTE_REVIEW
    assert route.reason_code == code
    assert route.reason, "a refusal must say why"


# ==================================================================================================
# 8-10. Re-ingestion refreshes derived fields and preserves adjudication.
# ==================================================================================================

def test_repeated_business_ingestion_makes_one_row_and_refreshes_it(seed):
    h = seed.contacts(uuid.uuid4().hex, [("1120S", 2021)], name="REPEATED LLC")
    rebuild_drake_identities(seed.connection)
    first = seed.business_rows(h)[0]

    seed.contacts(h, [("1120S", 2022), ("1120S", 2023)], name="REPEATED LLC")
    rebuild_drake_identities(seed.connection)
    rows = seed.business_rows(h)

    assert len(rows) == 1, "re-ingestion must not duplicate the identity"
    assert (rows[0]["first_year"], rows[0]["last_year"], rows[0]["return_count"]) == (2021, 2023, 3)
    assert rows[0]["created_at"] == first["created_at"], "first-observed history survives"


def test_an_established_entity_link_survives_re_ingestion(seed):
    entity = seed.entity()
    h = seed.contacts(uuid.uuid4().hex, [("1065", 2021)], name="LINKED LLC")
    rebuild_drake_identities(seed.connection)
    seed.connection.execute(text(
        "UPDATE drake_business_identity SET relationship_entity_id = :e WHERE identifier_hash = :h"),
        {"e": entity, "h": h})

    seed.contacts(h, [("1065", 2022)], name="LINKED LLC")
    rebuild_drake_identities(seed.connection)

    row = seed.business_rows(h)[0]
    assert row["relationship_entity_id"] == entity, "ingestion must never wipe entity adjudication"
    assert row["last_year"] == 2022, "derived fields still refresh"


def test_human_adjudication_on_a_business_identity_survives_re_ingestion(seed):
    user = seed.connection.execute(text("SELECT id FROM users LIMIT 1")).scalar()
    if user is None:
        pytest.skip("no user row available in this database")
    h = seed.contacts(uuid.uuid4().hex, [("1120", 2021)], name="ADJUDICATED INC")
    rebuild_drake_identities(seed.connection)
    seed.connection.execute(text(
        "UPDATE drake_business_identity SET trust_level='human_approved', "
        "  confirmation_source='human', evidence_method='manual', confirmed_by_user_id=:u, "
        "  confirmed_at=now() WHERE identifier_hash = :h"), {"u": user, "h": h})

    seed.contacts(h, [("1120", 2022)], name="ADJUDICATED INC")
    rebuild_drake_identities(seed.connection)

    row = seed.business_rows(h)[0]
    assert row["trust_level"] == "human_approved"
    assert row["confirmed_by_user_id"] == user and row["confirmed_at"] is not None
    assert row["last_year"] == 2022


# ==================================================================================================
# 11-15. Nothing that already worked is disturbed.
# ==================================================================================================

def test_ordinary_person_ingestion_is_unchanged(seed):
    person = seed.connection.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"{seed.tag} person"}).scalar_one()
    h = uuid.uuid4().hex
    seed.connection.execute(text(
        "INSERT INTO drake_identity (identifier_hash, primary_person_id, first_year, last_year, "
        "  return_count, taxpayer_name, confidence) VALUES (:h,:p,1990,1990,1,'stale',77)"),
        {"h": h, "p": person})
    seed.contacts(h, [("1040", 2021), ("1040", 2022)], name="ORDINARY PERSON")

    rebuild_drake_identities(seed.connection)

    row = seed.identity_row(h)
    assert row["primary_person_id"] == person, "the link survives, as it did before Phase B"
    assert row["confidence"] == 77
    assert (row["first_year"], row["last_year"]) == (2021, 2022)
    assert seed.business_rows(h) == []


def test_person_source_links_is_untouched_by_routing(seed):
    before = seed.connection.execute(text(
        "SELECT md5(string_agg(t::text, chr(10) ORDER BY t.id)), count(*) "
        "FROM person_source_links t")).one()
    seed.contacts(uuid.uuid4().hex, [("1120S", 2021)], name="NO LINKS LLC")
    seed.contacts(uuid.uuid4().hex, [("1040", 2021)], name="A PERSON")

    rebuild_drake_identities(seed.connection)

    assert seed.connection.execute(text(
        "SELECT md5(string_agg(t::text, chr(10) ORDER BY t.id)), count(*) "
        "FROM person_source_links t")).one() == before


def test_routing_creates_no_relationship_entities(seed):
    before = seed.connection.execute(text("SELECT count(*) FROM relationship_entities")).scalar()
    seed.contacts(uuid.uuid4().hex, [("1120S", 2021)], name="UNRESOLVED HOLDINGS LLC")
    seed.contacts(uuid.uuid4().hex, [("1041", 2021)], name="UNRESOLVED ESTATE")

    rebuild_drake_identities(seed.connection)

    assert seed.connection.execute(text(
        "SELECT count(*) FROM relationship_entities")).scalar() == before


def test_a_new_business_identity_is_left_unresolved(seed):
    """No entity is invented from a name. 18 of the D7 candidates matched on name alone."""
    entity = seed.entity(name="Exactly Named LLC")
    name = seed.connection.execute(text(
        "SELECT name FROM relationship_entities WHERE id = :e"), {"e": entity}).scalar_one()
    h = seed.contacts(uuid.uuid4().hex, [("1120S", 2021)], name=name)

    rebuild_drake_identities(seed.connection)

    assert seed.business_rows(h)[0]["relationship_entity_id"] is None, \
        "an exact name match must not auto-link an entity"


def test_routing_creates_no_entity_source_links(seed):
    before = seed.connection.execute(text("SELECT count(*) FROM entity_source_links")).scalar()
    seed.contacts(uuid.uuid4().hex, [("1065", 2021)], name="DORMANT LLC")

    rebuild_drake_identities(seed.connection)

    assert seed.connection.execute(
        text("SELECT count(*) FROM entity_source_links")).scalar() == before


def test_existing_non_natural_rows_in_drake_identity_are_retained_untouched(seed):
    """The D7 backlog. This phase reports it; a later, authorised phase relocates it."""
    person = seed.connection.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"{seed.tag} holder"}).scalar_one()
    h = uuid.uuid4().hex
    seed.connection.execute(text(
        "INSERT INTO drake_identity (identifier_hash, primary_person_id, first_year, last_year, "
        "  return_count, taxpayer_name) VALUES (:h,:p,2021,2021,1,'LEGACY LLC')"),
        {"h": h, "p": person})
    seed.contacts(h, [("1120S", 2021)], name="LEGACY LLC")

    report = rebuild_drake_identities(seed.connection)

    row = seed.identity_row(h)
    assert row is not None, "an existing row is never deleted by this phase"
    assert row["primary_person_id"] == person, "and never unlinked"
    assert h in report.pending_d7_migration


# ==================================================================================================
# 13. The other writers cannot bypass the boundary.
# ==================================================================================================

def roster_with(person_id, phone):
    return Roster([RosterPerson(person_id=person_id, full_name="Shared Phone Owner", dob=None,
                                emails=frozenset(), phones=frozenset({phone}),
                                city=None, state=None)])


def test_the_evaluator_refuses_to_link_a_business_identifier_to_a_person():
    """The person-1314 pattern: the firm's phone matched, and that used to be enough."""
    phone = "5555550123"
    evidence = build_identity_evidence(
        "hash", TAXPAYER, taxpayer_name="360 FINANCIAL SOLUTIONS", phones=[phone],
        return_observations=obs(("1120S", 2021), ("1120S", 2022)))

    decision = evaluate(evidence, roster_with(7, phone))

    assert decision.outcome == NO_MATCH
    assert not decision.is_auto_link
    assert any("non_natural_subject" in r for r in decision.reasons)


def test_the_evaluator_refuses_a_conflicting_identifier_too():
    phone = "5555550124"
    evidence = build_identity_evidence(
        "hash", TAXPAYER, taxpayer_name="CONTRADICTORY", phones=[phone],
        return_observations=obs(("1040", 2021), ("1120S", 2022)))

    assert evaluate(evidence, roster_with(8, phone)).outcome == NO_MATCH


def test_the_evaluator_still_links_an_ordinary_person():
    phone = "5555550125"
    evidence = build_identity_evidence(
        "hash", TAXPAYER, taxpayer_name="Shared Phone Owner", phones=[phone],
        return_observations=obs(("1040", 2021)))

    assert evaluate(evidence, roster_with(9, phone)).is_auto_link


def test_evidence_without_return_observations_behaves_exactly_as_before():
    """Callers that supply no return evidence are unaffected; the gate is opt-in by evidence."""
    phone = "5555550126"
    evidence = build_identity_evidence(
        "hash", TAXPAYER, taxpayer_name="Shared Phone Owner", phones=[phone])

    assert evidence.subject_type is None
    assert evaluate(evidence, roster_with(10, phone)).is_auto_link


# ==================================================================================================
# 18. No backfill function is introduced.
# ==================================================================================================

def test_the_routing_module_exposes_no_backfill():
    import app.services.drake_subject_routing as routing

    assert not [n for n in dir(routing) if "backfill" in n.lower() or "migrate" in n.lower()]


def test_upsert_refuses_a_person_route():
    route = route_identifier("h", obs(("1040", 2021)))

    with pytest.raises(ValueError, match="does not route to a business identity"):
        upsert_business_identity(None, route, subject_name="X")
