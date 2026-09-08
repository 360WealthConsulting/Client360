"""What legal subject a Drake identifier denotes — decided by the return, never by the name.

THE DEFECTS THESE PIN

Drake identifiers were all routed to ``people``. 150 of the 1,802 production identifiers are not
natural persons, and 46 of those sit on a person row today. The routing that put them there leaned on
contact evidence: a shared firm phone matched two business identifiers and one person identifier onto
a single unrelated contact, because an owner's phone IS the business's phone.

Two shapes matter beyond the ordinary cases, and both are real in production:

* four identifiers carry 1040 history with a date of birth AND later 1041 history — a decedent and
  the estate that succeeds them. Two legal subjects on one identifier. Collapsing them because the
  hash matches destroys one of the two histories.
* one identifier carries both a person return and an entity return. That is not a recognised shape,
  and the classifier must refuse rather than pick one.

Pure tests: the classifier reads no database.
"""
import pytest

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
    normalize_return_type,
)


def obs(return_type, tax_year=2021, has_dob=False):
    return Observation(return_type=return_type, tax_year=tax_year, has_dob=has_dob)


# ==================================================================================================
# 1-4. The ordinary cases, one subject each.
# ==================================================================================================

@pytest.mark.parametrize("return_type,expected", [
    ("1040", NATURAL_PERSON),
    ("1040NR", NATURAL_PERSON),
    ("1120S", BUSINESS_ENTITY),
    ("1120", BUSINESS_ENTITY),
    ("1065", BUSINESS_ENTITY),
    ("990", BUSINESS_ENTITY),
    ("1041", ESTATE_OR_TRUST),
])
def test_a_single_return_type_classifies_to_one_subject(return_type, expected):
    result = classify([obs(return_type, has_dob=return_type.startswith("1040"))])

    assert result.outcome == SINGLE_SUBJECT
    assert result.subject_types == (expected,)
    assert not result.requires_review


def test_an_ordinary_person_is_the_only_thing_that_may_be_written_unattended():
    assert classify([obs("1040", has_dob=True)]).is_single_natural_person
    assert not classify([obs("1120S")]).is_single_natural_person
    assert not classify([obs("1041")]).is_single_natural_person


def test_year_bounds_and_counts_come_from_the_observations():
    result = classify([obs("1120S", 2021), obs("1120S", 2022), obs("1120S", 2023)])
    subject = result.subjects[0]

    assert (subject.first_year, subject.last_year, subject.return_count) == (2021, 2023, 3)
    assert subject.return_types == ("1120S",)


def test_a_1040_without_a_dob_is_still_a_natural_person():
    """A missing DOB is a gap in the source, not evidence that the filer is a company."""
    result = classify([obs("1040", has_dob=False)])

    assert result.subject_types == (NATURAL_PERSON,)
    assert result.subjects[0].dob_observations == 0


def test_an_entity_that_changed_return_form_is_still_one_subject():
    """A production identifier files 1065 then 1120S. A form change is not a subject change."""
    result = classify([obs("1065", 2021), obs("1120S", 2022)])

    assert result.outcome == SINGLE_SUBJECT
    assert result.subject_types == (BUSINESS_ENTITY,)
    assert result.subjects[0].return_types == ("1065", "1120S")


# ==================================================================================================
# The name is not evidence.
# ==================================================================================================

def test_the_return_decides_and_the_name_is_never_consulted():
    """"DAVID KEETER LLC" is a name-token superset of the human "DAVID KEETER"; only the return
    separates them. The classifier takes no name argument at all, which is the point."""
    assert classify([obs("1040", has_dob=True)]).subject_types == (NATURAL_PERSON,)
    assert classify([obs("1120S")]).subject_types == (BUSINESS_ENTITY,)


def test_return_types_are_matched_case_insensitively():
    """Drake writes ``1120S``; canonical_population lowercases it."""
    assert normalize_return_type("1120s") == "1120S"
    assert classify([obs("1120s")]).subject_types == (BUSINESS_ENTITY,)
    assert classify([obs(" 1041 ")]).subject_types == (ESTATE_OR_TRUST,)


# ==================================================================================================
# 5. A decedent and the estate that succeeds them: two subjects, one identifier.
# ==================================================================================================

def test_person_history_then_estate_history_yields_two_subjects():
    """The production shape: 1040 as spouse 2021-2022 with a DOB, then a 1041 estate return."""
    result = classify([
        obs("1040", 2021, has_dob=True),
        obs("1040", 2022, has_dob=True),
        obs("1041", 2022),
    ])

    assert result.outcome == PERSON_THEN_ESTATE
    assert result.subject_types == (NATURAL_PERSON, ESTATE_OR_TRUST)
    assert result.requires_review, "which subject gets an entity is a human decision"


def test_the_two_subjects_keep_their_own_year_bounds():
    result = classify([
        obs("1040", 2021, has_dob=True), obs("1040", 2022, has_dob=True), obs("1041", 2022),
    ])
    person, estate = result.subjects

    assert (person.first_year, person.last_year) == (2021, 2022)
    assert (estate.first_year, estate.last_year) == (2022, 2022)
    assert person.dob_observations == 2 and estate.dob_observations == 0


def test_the_estate_is_never_collapsed_into_the_person_just_because_the_hash_matches():
    result = classify([obs("1040", 2021, has_dob=True), obs("1041", 2022)])

    assert len(result.subjects) == 2, "one identifier, two legal subjects"
    assert not result.is_single_natural_person
    assert "must not be collapsed" in result.reason


def test_the_estate_subject_targets_the_trust_bucket_not_business():
    """Production stores estates as entity_type='trust'; 1041 must not become a business."""
    result = classify([obs("1041")])

    assert result.subjects[0].entity_type == "trust"
    assert classify([obs("1120S")]).subjects[0].entity_type == "business"
    assert classify([obs("1040", has_dob=True)]).subjects[0].entity_type is None


# ==================================================================================================
# 6. A person return and an entity return cannot coexist. Fail closed.
# ==================================================================================================

@pytest.mark.parametrize("entity_return", ["1120S", "1120", "1065", "990"])
def test_a_person_return_with_an_entity_return_refuses_to_classify(entity_return):
    result = classify([obs("1040", 2021, has_dob=True), obs(entity_return, 2022)])

    assert result.outcome == CONFLICTING_SUBJECTS
    assert result.subjects == (), "no subject may be proposed for a shape this contradictory"
    assert result.requires_review
    assert not result.is_single_natural_person


def test_a_conflicting_identifier_is_never_routed_to_drake_identity():
    result = classify([obs("1040", has_dob=True), obs("1120S")])

    assert not result.is_single_natural_person
    assert all(not s.routes_to_drake_identity for s in result.subjects)


def test_three_way_conflict_also_fails_closed():
    result = classify([obs("1040", has_dob=True), obs("1120S"), obs("1041")])

    assert result.outcome == CONFLICTING_SUBJECTS
    assert result.subjects == ()


# ==================================================================================================
# Unknown and unrecognised input.
# ==================================================================================================

def test_no_return_type_is_unknown_and_reviewed_not_guessed():
    result = classify([obs(None), obs("")])

    assert result.outcome == UNKNOWN
    assert result.subjects == ()
    assert result.requires_review
    assert "the name is not evidence" in result.reason


def test_no_observations_at_all_is_unknown():
    result = classify([])

    assert result.outcome == UNKNOWN and result.requires_review


def test_an_unrecognised_return_type_is_reported_and_held_for_review():
    result = classify([obs("1040", has_dob=True), obs("706")])

    assert result.unrecognised_return_types == ("706",)
    assert result.requires_review, "an unknown form must not pass unattended"
    assert not result.is_single_natural_person


def test_only_unrecognised_return_types_is_unknown():
    result = classify([obs("706"), obs("709")])

    assert result.outcome == UNKNOWN
    assert result.unrecognised_return_types == ("706", "709")


def test_mappings_are_accepted_as_well_as_observations():
    result = classify([{"return_type": "1041", "tax_year": 2021, "has_dob": False}])

    assert result.subject_types == (ESTATE_OR_TRUST,)
