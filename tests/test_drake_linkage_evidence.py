"""Drake linkage semantics — the rules a Drake identity must obey before it may claim a person.

Every case here comes from the read-only replay of all 1,802 production Drake identities. The three
deterministic wrong links it found are reproduced as fixtures (names and person ids only, as
evidence for the rule); NO production row is read, written or corrected by this suite, and the
evaluator is pure, so none of these tests touches a database at all.

The defects being pinned:

* identity ``6e4b8ada0e03`` linked taxpayer *Titus Glick* to his spouse *Clara Glick*, because the
  joint return's unattributed ``Email`` was scored as if it identified the taxpayer;
* 147 linked spouse identities rest on a name alone, because Drake has no spouse-attributed contact
  field and the importer wrote ``email: None, phone: None``;
* the review scorer pooled ``taxpayer_name`` and ``spouse_name`` into one set, so the wrong role's
  name scored an exact-name hit;
* 12 "corrections" were really pairs of duplicate person records created a day apart, where a
  contact point picked one arbitrarily;
* ties were resolved by lowest ``person_id``.
"""
import pytest

from app.services.drake_linkage_evidence import (
    AMBIGUOUS,
    AUTO_LINK,
    METHOD_CONTACT,
    METHOD_CONTACT_NAME,
    METHOD_NAME,
    NO_MATCH,
    REVIEW_CANDIDATE,
    SPOUSE,
    TAXPAYER,
    Roster,
    RosterPerson,
    build_identity_evidence,
    evaluate,
    name_tokens,
    normalize_name,
)
from app.services.link_trust import (
    MACHINE_CONTACT,
    MACHINE_EXACT_NAME,
    MACHINE_NAME_LOCATION,
    is_trusted_for_tax_return_visibility,
)

HOUSEHOLD_EMAIL = "glickfamily@example.com"
TAXPAYER_CELL = "5550101234"


def person(person_id, name, **kwargs):
    kwargs.setdefault("emails", frozenset())
    kwargs.setdefault("phones", frozenset())
    return RosterPerson(person_id=person_id, full_name=name, **kwargs)


# ==================================================================================================
# A. The joint-return case: a taxpayer identity must not land on the spouse.
# ==================================================================================================

def test_joint_return_email_pointing_at_the_spouse_does_not_link_the_taxpayer():
    """Reproduces identity 6e4b8ada0e03: Titus Glick (taxpayer) was linked to Clara Glick."""
    roster = Roster([
        person(2920, "Clara Glick", emails={HOUSEHOLD_EMAIL}),
        person(7860, "Titus Glick"),
    ])
    evidence = build_identity_evidence(
        "6e4b8ada0e03", TAXPAYER, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        emails=[HOUSEHOLD_EMAIL], has_spouse=True)

    decision = evaluate(evidence, roster)

    assert decision.outcome != AUTO_LINK
    assert decision.person_id is None
    assert 2920 not in decision.candidates          # the spouse is never a taxpayer candidate
    assert any("household_contact_ignored" in reason for reason in decision.reasons)


def test_the_joint_email_is_retained_as_household_context_not_discarded():
    evidence = build_identity_evidence(
        "6e4b8ada0e03", TAXPAYER, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        emails=[HOUSEHOLD_EMAIL], has_spouse=True)

    assert evidence.household_contacts == frozenset({HOUSEHOLD_EMAIL})
    assert evidence.attributed_emails == frozenset()
    assert evidence.joint_return is True


# ==================================================================================================
# B / 5. A spouse identity may never consume taxpayer contact evidence.
# ==================================================================================================

def test_spouse_identity_is_given_no_contact_evidence_at_all():
    evidence = build_identity_evidence(
        "spouse-hash", SPOUSE, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        emails=[HOUSEHOLD_EMAIL], phones=[TAXPAYER_CELL], has_spouse=True)

    assert evidence.attributed_emails == frozenset()
    assert evidence.attributed_phones == frozenset()
    assert evidence.household_contacts == frozenset({HOUSEHOLD_EMAIL, TAXPAYER_CELL})


def test_spouse_identity_cannot_auto_link_on_the_taxpayers_phone():
    roster = Roster([person(7860, "Titus Glick", phones={TAXPAYER_CELL}),
                     person(2920, "Clara Glick")])
    evidence = build_identity_evidence(
        "spouse-hash", SPOUSE, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        phones=[TAXPAYER_CELL], has_spouse=True)

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE     # the spouse's own name, and nothing more
    assert decision.person_id is None
    assert decision.trust_level == MACHINE_EXACT_NAME


# ==================================================================================================
# C. An unattributed joint-return email cannot create a link for anyone.
# ==================================================================================================

def test_unattributed_joint_email_creates_no_link_even_when_it_matches_exactly_one_person():
    roster = Roster([person(4001, "Someone Else", emails={HOUSEHOLD_EMAIL})])
    evidence = build_identity_evidence(
        "joint", TAXPAYER, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        emails=[HOUSEHOLD_EMAIL], has_spouse=True)

    decision = evaluate(evidence, roster)

    assert decision.outcome == NO_MATCH
    assert decision.person_id is None


def test_the_same_email_does_identify_the_taxpayer_when_the_return_has_no_spouse():
    """Attribution is the whole question: with no spouse on the return the email IS the taxpayer's."""
    roster = Roster([person(4001, "Solo Filer", emails={"solo@example.com"})])
    evidence = build_identity_evidence(
        "single", TAXPAYER, taxpayer_name="SOLO FILER", emails=["solo@example.com"],
        has_spouse=False)

    decision = evaluate(evidence, roster)

    assert decision.outcome == AUTO_LINK
    assert decision.person_id == 4001


# ==================================================================================================
# D. Taxpayer name + taxpayer-attributed phone is deterministic when nothing contradicts it.
# ==================================================================================================

def test_role_name_plus_attributed_phone_auto_links():
    roster = Roster([person(7860, "Titus Glick", phones={TAXPAYER_CELL}),
                     person(2920, "Clara Glick")])
    evidence = build_identity_evidence(
        "tp", TAXPAYER, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        phones=[TAXPAYER_CELL], has_spouse=True)

    decision = evaluate(evidence, roster)

    assert decision.outcome == AUTO_LINK
    assert decision.person_id == 7860
    assert decision.method == METHOD_CONTACT_NAME
    assert decision.trust_level == MACHINE_CONTACT
    assert decision.confidence == 95


def test_attributed_contact_without_a_name_on_the_identity_links_at_lower_confidence():
    roster = Roster([person(7860, "Titus Glick", phones={TAXPAYER_CELL})])
    evidence = build_identity_evidence("tp", TAXPAYER, phones=[TAXPAYER_CELL], has_spouse=False)

    decision = evaluate(evidence, roster)

    assert decision.outcome == AUTO_LINK
    assert decision.method == METHOD_CONTACT
    assert decision.confidence == 85


# ==================================================================================================
# E / 7. Exact name alone never auto-links, however unique it looks.
# ==================================================================================================

def test_a_globally_unique_exact_name_is_a_review_candidate_not_a_link():
    roster = Roster([person(7862, "Dwight Moretz")])
    evidence = build_identity_evidence("2aed88564ce0", TAXPAYER, taxpayer_name="DWIGHT MORETZ")

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE
    assert decision.person_id is None
    assert decision.trust_level == MACHINE_EXACT_NAME
    assert any("CURRENT roster only" in reason for reason in decision.reasons)


@pytest.mark.parametrize("level", [MACHINE_EXACT_NAME, MACHINE_NAME_LOCATION, MACHINE_CONTACT])
def test_machine_evidence_never_reaches_tax_return_visibility(level):
    """The 155 name-only replay proposals must not become trusted links.

    Nothing this evaluator can emit is trusted for a tax-return read surface: an automatic link is a
    proposal about identity, not a human decision, and the trust policy already says so.
    """
    row = {"trust_level": level, "confirmation_source": "machine",
           "evidence_method": METHOD_NAME, "confirmed": True}

    assert is_trusted_for_tax_return_visibility(row) is False
    assert is_trusted_for_tax_return_visibility(row, accept_derived_legacy=True) is False


# ==================================================================================================
# F / 15. Duplicate person records fail closed instead of being chosen between.
# ==================================================================================================

def test_duplicate_same_name_person_records_refuse_to_auto_link():
    """The Aug-08/Aug-09 duplicate import: 7536 and 7436 both 'DONALD WRIGHT'."""
    roster = Roster([person(7536, "DONALD WRIGHT"),
                     person(7436, "DONALD WRIGHT", phones={TAXPAYER_CELL})])
    evidence = build_identity_evidence(
        "20d371c1fe13", TAXPAYER, taxpayer_name="DONALD WRIGHT", phones=[TAXPAYER_CELL])

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE
    assert decision.person_id is None
    assert set(decision.candidates) >= {7436, 7536}
    assert any("duplicate person records" in reason for reason in decision.reasons)


def test_a_reordered_duplicate_name_is_still_recognised_as_a_duplicate():
    """7385 'SPANGLER, ROBERT' and 3202 'Robert Spangler' are one human in two records."""
    assert name_tokens("SPANGLER, ROBERT") == name_tokens("Robert Spangler")
    assert normalize_name("SPANGLER, ROBERT") != normalize_name("Robert Spangler")

    roster = Roster([person(7385, "SPANGLER, ROBERT"),
                     person(3202, "Robert Spangler", phones={TAXPAYER_CELL})])
    evidence = build_identity_evidence(
        "f5732080bafa", TAXPAYER, taxpayer_name="ROBERT SPANGLER", phones=[TAXPAYER_CELL])

    assert evaluate(evidence, roster).outcome == REVIEW_CANDIDATE


# ==================================================================================================
# G / 11. Conflicting signals fail closed.
# ==================================================================================================

def test_contact_and_name_pointing_at_different_people_resolves_to_nothing():
    """Identity 3b13e6d4a561: the link said 'Craig Hixson', the source name says 'Craig Hixon'."""
    roster = Roster([person(5575, "Craig Hixson", phones={TAXPAYER_CELL}),
                     person(7367, "Craig Hixon")])
    evidence = build_identity_evidence(
        "3b13e6d4a561", TAXPAYER, taxpayer_name="CRAIG HIXON", phones=[TAXPAYER_CELL])

    decision = evaluate(evidence, roster)

    assert decision.outcome == AMBIGUOUS
    assert decision.person_id is None
    assert any("disagree" in reason for reason in decision.reasons)


def test_one_contact_point_held_by_two_people_resolves_to_nothing():
    roster = Roster([person(1, "A Person", phones={TAXPAYER_CELL}),
                     person(2, "B Person", phones={TAXPAYER_CELL})])
    evidence = build_identity_evidence("shared", TAXPAYER, phones=[TAXPAYER_CELL])

    assert evaluate(evidence, roster).outcome == AMBIGUOUS


# ==================================================================================================
# H / 12. No tie-break by person id.
# ==================================================================================================

@pytest.mark.parametrize("ids", [(11, 12), (12, 11)])
def test_two_equally_named_candidates_never_resolve_to_the_lower_id(ids):
    first, second = ids
    roster = Roster([person(first, "Same Name"), person(second, "Same Name")])
    evidence = build_identity_evidence("tie", TAXPAYER, taxpayer_name="Same Name")

    decision = evaluate(evidence, roster)

    assert decision.outcome == AMBIGUOUS
    assert decision.person_id is None


def test_evaluation_is_independent_of_roster_insertion_order():
    people = [person(9001, "Order Test", phones={TAXPAYER_CELL}), person(9002, "Other Person")]
    evidence = build_identity_evidence("order", TAXPAYER, taxpayer_name="ORDER TEST",
                                       phones=[TAXPAYER_CELL])

    forward = evaluate(evidence, Roster(people))
    backward = evaluate(evidence, Roster(list(reversed(people))))

    assert forward == backward


# ==================================================================================================
# I / 9. City and state are context, never identity.
# ==================================================================================================

def test_city_and_state_alone_identify_nobody():
    roster = Roster([person(3001, "Someone Local", city="Roanoke", state="VA")])
    evidence = build_identity_evidence("loc", TAXPAYER, city="Roanoke", state="VA")

    decision = evaluate(evidence, roster)

    assert decision.outcome == NO_MATCH
    assert decision.candidates == ()


def test_city_and_state_only_corroborate_a_name_and_stay_below_auto_link():
    roster = Roster([person(3001, "Local Person", city="Roanoke", state="VA")])
    evidence = build_identity_evidence("loc", TAXPAYER, taxpayer_name="LOCAL PERSON",
                                       city="Roanoke", state="VA")

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE
    assert decision.trust_level == MACHINE_NAME_LOCATION
    assert decision.confidence == 60


# ==================================================================================================
# J / 8. DOB is role-correct and only used when both sides carry one.
# ==================================================================================================

def test_dob_is_ignored_when_the_roster_holds_none():
    """people.birth_date is populated on 1 of 7,794 rows, so this is the normal case."""
    roster = Roster([person(5001, "Dated Person")])
    evidence = build_identity_evidence("dob", TAXPAYER, taxpayer_name="DATED PERSON",
                                       dob="1970-01-01")

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE
    assert decision.confidence == 55
    assert any("no_canonical_dob" in reason for reason in decision.reasons)


def test_matching_role_dob_strengthens_a_name_candidate_but_still_does_not_auto_link():
    roster = Roster([person(5001, "Dated Person", dob="1970-01-01")])
    evidence = build_identity_evidence("dob", TAXPAYER, taxpayer_name="DATED PERSON",
                                       dob="1970-01-01")

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE
    assert decision.confidence == 75
    assert decision.person_id is None


def test_a_spouse_dob_is_never_read_for_a_taxpayer_identity():
    roster = Roster([person(5001, "Titus Glick", dob="1980-05-05"),
                     person(5002, "Clara Glick", dob="1982-06-06")])
    taxpayer = build_identity_evidence(
        "roles", TAXPAYER, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        dob="1980-05-05", has_spouse=True)

    assert taxpayer.dob == "1980-05-05"
    assert evaluate(taxpayer, roster).candidates == (5001,)


# ==================================================================================================
# K / 1 / 2 / L. Roles stay distinct through construction and scoring.
# ==================================================================================================

@pytest.mark.parametrize("role,expected", [(TAXPAYER, "TITUS GLICK"), (SPOUSE, "CLARA GLICK")])
def test_each_identity_carries_only_its_own_roles_name(role, expected):
    evidence = build_identity_evidence("roles", role, taxpayer_name="TITUS GLICK",
                                       spouse_name="CLARA GLICK", has_spouse=True)

    assert evidence.role_name == expected


def test_the_spouses_name_is_never_a_candidate_for_a_taxpayer_identity():
    """The pooled-name defect: {taxpayer_name, spouse_name} as one set."""
    roster = Roster([person(2920, "Clara Glick"), person(7860, "Titus Glick")])

    taxpayer = evaluate(build_identity_evidence(
        "roles", TAXPAYER, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        has_spouse=True), roster)
    spouse = evaluate(build_identity_evidence(
        "roles", SPOUSE, taxpayer_name="TITUS GLICK", spouse_name="CLARA GLICK",
        has_spouse=True), roster)

    assert taxpayer.candidates == (7860,)
    assert spouse.candidates == (2920,)


def test_an_unknown_role_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError):
        build_identity_evidence("bad", "preparer", taxpayer_name="X Y")


# ==================================================================================================
# 13. Confidence describes the evidence, and is never a constant.
# ==================================================================================================

def test_confidence_varies_with_the_evidence_actually_present():
    roster = Roster([person(6001, "Conf Person", phones={TAXPAYER_CELL}, city="Roanoke",
                            state="VA")])
    name_only = evaluate(build_identity_evidence("c", TAXPAYER, taxpayer_name="CONF PERSON"),
                         roster)
    with_location = evaluate(build_identity_evidence(
        "c", TAXPAYER, taxpayer_name="CONF PERSON", city="Roanoke", state="VA"), roster)
    with_contact = evaluate(build_identity_evidence(
        "c", TAXPAYER, taxpayer_name="CONF PERSON", phones=[TAXPAYER_CELL]), roster)

    assert name_only.confidence == 55
    assert with_location.confidence == 60
    assert with_contact.confidence == 95
    assert len({name_only.confidence, with_location.confidence, with_contact.confidence}) == 3


def test_no_evidence_scores_zero_rather_than_a_default_hundred():
    decision = evaluate(build_identity_evidence("empty", TAXPAYER), Roster([]))

    assert decision.outcome == NO_MATCH
    assert decision.confidence == 0
    assert decision.trust_level is None


# ==================================================================================================
# 14. A Drake-derived link may not be evidence for itself.
# ==================================================================================================

def test_the_roster_is_built_by_the_caller_so_drake_contacts_can_be_excluded():
    """The evaluator can only see what the caller puts in the roster; it never queries for more."""
    drake_only_email = "from-the-drake-record@example.com"
    roster = Roster([person(8001, "Circular Person")])         # Drake contact points NOT loaded
    evidence = build_identity_evidence("circ", TAXPAYER, taxpayer_name="CIRCULAR PERSON",
                                       emails=[drake_only_email])

    decision = evaluate(evidence, roster)

    assert decision.outcome == REVIEW_CANDIDATE
    assert decision.method == METHOD_NAME
