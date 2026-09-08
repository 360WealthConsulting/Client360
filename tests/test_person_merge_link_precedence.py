"""Source-link collision precedence — provenance strength is compared before age.

THE DEFECT THIS PINS

``_link_rank`` used to be ``(confirmed, -match_score, created_at)``. When two people link the same
source contact and both rows are confirmed with the same score, the ONLY remaining signal was age, so
the older row won regardless of what established it.

Production holds twelve such collisions. Each pair is one SSN-derived identity promotion
(``drake_identity_promotion`` -> ``identifier_verified``) against a canonical-repair row
(``canonical_repair_promotion`` -> ``canonical_repair``), both ``confirmed``, both scored 100, with
the canonical-repair row roughly seventeen hours older. Ranking by age kept the weaker row every
time and rewrote the survivor's ``match_method`` to the weaker claim -- and ``canonical_repair`` is
defined in ``link_trust`` as asserting provenance was matched WITHOUT recording on what.

The strength order is not redefined here. It is ``link_trust.TRUST_STRENGTH_ORDER``, so the merge
path and the trust vocabulary cannot drift apart.
"""
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from app.services.link_trust import (
    CANONICAL_REPAIR,
    HUMAN_APPROVED,
    IDENTIFIER_VERIFIED,
    MACHINE_CONTACT,
    MACHINE_EXACT_NAME,
    SOURCE_HUMAN,
    TRUST_STRENGTH_ORDER,
    UNKNOWN_LEGACY,
    trust_strength,
)
from app.services.person_merge import _link_rank

_OLDER = "2026-08-08 17:36:44+00"
_NEWER = "2026-08-09 10:02:11+00"


def link(*, method=None, score=100, confirmed=True, created_at=_NEWER, trust_level=None,
         confirmation_source=None, row_id=1):
    return {"id": row_id, "match_method": method, "match_score": score, "confirmed": confirmed,
            "created_at": created_at, "trust_level": trust_level,
            "confirmation_source": confirmation_source}


def stronger(a, b):
    """Which of two colliding links wins, using the real ranking."""
    return min((a, b), key=_link_rank)


# ==================================================================================================
# 1. The exact production case.
# ==================================================================================================

def test_identity_promotion_beats_older_canonical_repair():
    identity = link(method="drake_identity_promotion", created_at=_NEWER, row_id=10007)
    repair = link(method="canonical_repair_promotion", created_at=_OLDER, row_id=9872)

    assert stronger(identity, repair) is identity


def test_the_two_production_methods_classify_as_expected():
    """If these ever stop classifying this way the ranking above proves nothing."""
    assert trust_strength(link(method="drake_identity_promotion"))[0] \
        == TRUST_STRENGTH_ORDER.index(IDENTIFIER_VERIFIED)
    assert trust_strength(link(method="canonical_repair_promotion"))[0] \
        == TRUST_STRENGTH_ORDER.index(CANONICAL_REPAIR)


# ==================================================================================================
# 2. Stronger-but-newer beats weaker-but-older, generally.
# ==================================================================================================

@pytest.mark.parametrize("strong,weak", [
    ("drake_identity_review", "drake_identity_promotion"),      # human approved > identifier
    ("drake_identity_promotion", "auto_promote"),               # identifier > contact
    ("auto_promote", "unique_exact_name"),                      # contact > bare name
    ("unique_exact_name", "canonical_repair_promotion"),        # name > unrecorded repair
    ("canonical_repair_promotion", "something_unrecognised"),   # repair > nothing recorded
])
def test_stronger_provenance_wins_even_when_newer(strong, weak):
    newer_strong = link(method=strong, created_at=_NEWER, row_id=2)
    older_weak = link(method=weak, created_at=_OLDER, row_id=1)

    assert stronger(newer_strong, older_weak) is newer_strong


# ==================================================================================================
# 3. Equal strength still falls back to the existing deterministic behaviour.
# ==================================================================================================

def test_equal_provenance_keeps_the_higher_score():
    high = link(method="auto_promote", score=90, created_at=_NEWER, row_id=2)
    low = link(method="auto_promote", score=40, created_at=_OLDER, row_id=1)

    assert stronger(high, low) is high


def test_equal_provenance_and_score_keeps_the_earlier_link():
    older = link(method="auto_promote", created_at=_OLDER, row_id=2)
    newer = link(method="auto_promote", created_at=_NEWER, row_id=1)

    assert stronger(older, newer) is older


def test_a_total_tie_resolves_by_row_id_and_never_by_anything_meaningful():
    """The last fallback exists only to make the order total; it must not decide anything real."""
    first = link(method="auto_promote", created_at=_OLDER, row_id=1)
    second = link(method="auto_promote", created_at=_OLDER, row_id=2)

    assert stronger(first, second) is first
    assert _link_rank(first)[:-1] == _link_rank(second)[:-1], "only the id may differ here"


# ==================================================================================================
# 4. Human approval is never downgraded by machine evidence.
# ==================================================================================================

@pytest.mark.parametrize("machine", ["drake_identity_promotion", "auto_promote",
                                     "canonical_repair_promotion", "unique_exact_name"])
def test_human_approved_is_never_lost_to_machine_evidence(machine):
    human = link(method="x", trust_level=HUMAN_APPROVED, confirmation_source=SOURCE_HUMAN,
                 created_at=_NEWER, score=1, row_id=99)
    machine_link = link(method=machine, created_at=_OLDER, score=100, row_id=1)

    assert stronger(human, machine_link) is human


# ==================================================================================================
# 5. Recorded trust, and legacy rows with none.
# ==================================================================================================

def test_recorded_trust_beats_trust_merely_derived_from_a_method_string():
    recorded = link(method="canonical_repair_promotion", trust_level=IDENTIFIER_VERIFIED,
                    created_at=_NEWER, row_id=2)
    derived = link(method="drake_identity_promotion", created_at=_OLDER, row_id=1)

    assert trust_strength(recorded)[0] == trust_strength(derived)[0], "same level, different origin"
    assert stronger(recorded, derived) is recorded


def test_a_null_trust_level_falls_back_to_the_method_string():
    assert trust_strength(link(method="drake_identity_promotion", trust_level=None))[0] \
        == TRUST_STRENGTH_ORDER.index(IDENTIFIER_VERIFIED)


def test_an_unrecognised_method_is_the_weakest_thing_there_is():
    assert trust_strength(link(method="who_knows"))[0] \
        == TRUST_STRENGTH_ORDER.index(UNKNOWN_LEGACY)
    assert trust_strength(link(method=None))[0] == TRUST_STRENGTH_ORDER.index(UNKNOWN_LEGACY)


def test_strength_order_is_a_ranking_not_the_declaration_order():
    """TRUST_LEVELS lists exact-name above contact; strength must not, because a name is not an id."""
    order = list(TRUST_STRENGTH_ORDER)
    assert order.index(HUMAN_APPROVED) < order.index(IDENTIFIER_VERIFIED)
    assert order.index(IDENTIFIER_VERIFIED) < order.index(MACHINE_CONTACT)
    assert order.index(MACHINE_CONTACT) < order.index(MACHINE_EXACT_NAME)
    assert order.index(MACHINE_EXACT_NAME) < order.index(CANONICAL_REPAIR)
    assert order.index(CANONICAL_REPAIR) < order.index(UNKNOWN_LEGACY)


# ==================================================================================================
# 6. Confirmed still leads, so the pre-existing behaviour is untouched.
# ==================================================================================================

def test_a_confirmed_weak_link_still_beats_an_unconfirmed_strong_one():
    """Unchanged from before this fix: an explicit non-confirmation is still respected first."""
    confirmed_weak = link(method="canonical_repair_promotion", confirmed=True, row_id=1)
    unconfirmed_strong = link(method="drake_identity_promotion", confirmed=False, row_id=2)

    assert stronger(confirmed_weak, unconfirmed_strong) is confirmed_weak


# ==================================================================================================
# 7. The ranking is what the merge path actually uses, end to end.
# ==================================================================================================

@pytest.fixture()
def collision():
    """Two people linking one source contact, mirroring the production shape. Torn down after."""
    tag = f"linkprec-{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        contact = conn.execute(text(
            "INSERT INTO source_contacts (source_system, source_file, source_hash, raw_data) "
            "VALUES ('SyntheticCRM', :f, :h, '{}'::jsonb) RETURNING id"),
            {"f": tag + ".csv", "h": uuid.uuid4().hex}).scalar_one()
        survivor = conn.execute(text(
            "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
            {"n": tag + " survivor"}).scalar_one()
        duplicate = conn.execute(text(
            "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
            {"n": tag + " duplicate"}).scalar_one()
        rows = {}
        for person, method, created in ((survivor, "drake_identity_promotion", _NEWER),
                                        (duplicate, "canonical_repair_promotion", _OLDER)):
            rows[method] = conn.execute(text(
                "INSERT INTO person_source_links "
                "  (person_id, source_contact_id, match_method, match_score, confirmed, created_at) "
                "VALUES (:p, :c, :m, 100, true, :t) RETURNING id"),
                {"p": person, "c": contact, "m": method, "t": created}).scalar_one()
    yield {"contact": contact, "survivor": survivor, "duplicate": duplicate, "links": rows}
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM person_source_links WHERE source_contact_id = :c"),
                     {"c": contact})
        conn.execute(text("DELETE FROM people WHERE id = ANY(:i)"),
                     {"i": [survivor, duplicate]})
        conn.execute(text("DELETE FROM source_contacts WHERE id = :c"), {"c": contact})


def test_the_collision_resolver_keeps_the_identity_row_not_the_older_repair_row(collision):
    from app.services.person_merge import _resolve_source_link_collisions

    # An explicit transaction that is rolled back, rather than engine.begin() with an inner
    # rollback: engine.begin() commits on exit, so rolling back inside it leaves the pooled
    # connection in a state that upsets later count-snapshot tests in the same session.
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            resolved = _resolve_source_link_collisions(
                conn, collision["survivor"], collision["duplicate"])
            kept = conn.execute(text(
                "SELECT match_method FROM person_source_links WHERE person_id = :p"),
                {"p": collision["survivor"]}).scalar_one()
        finally:
            transaction.rollback()

    assert resolved and resolved[0]["kept_from"] == "survivor"
    assert kept == "drake_identity_promotion", "the older canonical-repair row was copied over"
    assert resolved[0]["reason"] == "stronger recorded/derived provenance"
