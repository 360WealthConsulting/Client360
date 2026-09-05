"""Drake link trust — what ``confirmed = true`` is allowed to buy, and what it is not.

``person_source_links.confirmed`` is one boolean meaning seven things. It is hardcoded ``True`` by
automated code (``app/matching/promote.py::_link``; ``scripts/link_drake_to_people.py``), all 3,607
production Drake links carry it, and 41.1% of them rest on name-derived evidence — 1,404
``unique_exact_name`` plus 79 ``exact_name_city_state``. A resolver that reads ``confirmed`` and calls
the result "by identifier, never by name" is asserting something the data does not support.

These tests pin the replacement: trust is explicit, name-derived evidence never reaches a tax return
on its own, and the legacy escape hatch has to be asked for by name.

Temp/test rows only, all tagged and torn down.
"""
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, insert

from app.db import engine, metadata, people, person_source_links, source_contacts
from app.services.drake_return_identity import IDENTIFIED, compute_return_identity_key
from app.services.drake_return_resolution import (
    POLICY_LEGACY_DERIVED,
    POLICY_STRICT,
    resolved_drake_returns_for_person,
    trusted_hash_owners,
)
from app.services.link_trust import (
    CANONICAL_REPAIR,
    HUMAN_APPROVED,
    IDENTIFIER_VERIFIED,
    MACHINE_CONTACT,
    MACHINE_EXACT_NAME,
    MACHINE_NAME_LOCATION,
    SOURCE_HUMAN,
    SOURCE_MACHINE,
    UNKNOWN_LEGACY,
    derive_legacy_trust,
    is_trusted_for_tax_return_visibility,
    link_trust,
)

drake_client_returns = metadata.tables["drake_client_returns"]
users = metadata.tables["users"]


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


# ==================================================================================================
# Classification — the six evidence classes, plus legacy. No database needed.
# ==================================================================================================

# --- 9. identifier-grade --------------------------------------------------------------------------

@pytest.mark.parametrize("method", [
    "confirmed_identifier_hash",
    "drake_identity_promotion",
    "repeated_drake_1040_taxpayer_promotion",
    "canonical_repair_exact_drake_taxpayer",
    "manual_drake_ssn_hash_continuity",
])
def test_identifier_grade_methods_classify_as_identifier_verified(method):
    assert derive_legacy_trust(method) == IDENTIFIER_VERIFIED


# --- 10. human-approved ---------------------------------------------------------------------------

def test_identity_review_approval_classifies_as_human_approved():
    """The one method written by a reviewer approving THIS link in the identity-review UI."""
    assert derive_legacy_trust("drake_identity_review") == HUMAN_APPROVED
    assert link_trust({"match_method": "drake_identity_review"})["confirmation_source"] == SOURCE_HUMAN


def test_a_manual_prefix_is_human_sourced_but_not_human_approved():
    """A human RUNNING a repair script is not a human APPROVING a specific link."""
    trust = link_trust({"match_method": "manual_drake_household_provenance"})
    assert trust["confirmation_source"] == SOURCE_HUMAN
    assert trust["trust_level"] != HUMAN_APPROVED
    assert not is_trusted_for_tax_return_visibility(
        {"match_method": "manual_drake_household_provenance"}, accept_derived_legacy=True)


# --- 11. machine exact-name / 12. name+location / 13. contact / 14. canonical repair ---------------

@pytest.mark.parametrize("method,expected", [
    ("unique_exact_name", MACHINE_EXACT_NAME),
    ("exact_name_city_state", MACHINE_NAME_LOCATION),
    ("exact_name_city_state+exact_phone", MACHINE_CONTACT),
    ("exact_email", MACHINE_CONTACT),
    ("exact_phone", MACHINE_CONTACT),
    ("exact_email+exact_phone", MACHINE_CONTACT),
    ("auto_promote", MACHINE_CONTACT),
    ("canonical_repair_exact_person_provenance", CANONICAL_REPAIR),
    ("canonical_repair_promote", CANONICAL_REPAIR),
])
def test_machine_methods_classify_below_the_visibility_bar(method, expected):
    assert derive_legacy_trust(method) == expected
    assert link_trust({"match_method": method})["confirmation_source"] == SOURCE_MACHINE
    assert not is_trusted_for_tax_return_visibility({"match_method": method},
                                                    accept_derived_legacy=True), \
        f"{method} must never grant tax-return visibility on its own"


@pytest.mark.parametrize("method", [None, "", "something_nobody_recorded"])
def test_unrecorded_or_unrecognised_methods_are_unknown_legacy(method):
    assert derive_legacy_trust(method) == UNKNOWN_LEGACY
    assert not is_trusted_for_tax_return_visibility({"match_method": method},
                                                    accept_derived_legacy=True)


def test_the_1404_unique_exact_name_links_are_not_trusted_under_any_policy():
    """The single largest slice of production "confirmed" Drake linkage, and it is a NAME match."""
    row = {"match_method": "unique_exact_name", "trust_level": None}
    assert not is_trusted_for_tax_return_visibility(row, accept_derived_legacy=False)
    assert not is_trusted_for_tax_return_visibility(row, accept_derived_legacy=True)


# --- recorded vs derived --------------------------------------------------------------------------

def test_a_recorded_trust_level_wins_over_the_method_string():
    row = {"trust_level": HUMAN_APPROVED, "confirmation_source": SOURCE_HUMAN,
           "match_method": "unique_exact_name"}
    trust = link_trust(row)
    assert trust["recorded"] is True
    assert trust["trust_level"] == HUMAN_APPROVED
    assert is_trusted_for_tax_return_visibility(row)


def test_a_derived_level_is_never_trusted_without_the_explicit_legacy_switch():
    """Derived identifier-grade is real evidence — but honouring it is a deployment decision."""
    row = {"trust_level": None, "match_method": "drake_identity_promotion"}
    assert link_trust(row)["recorded"] is False
    assert not is_trusted_for_tax_return_visibility(row)
    assert is_trusted_for_tax_return_visibility(row, accept_derived_legacy=True)


# ==================================================================================================
# Database-backed: what the resolver actually shows.
# ==================================================================================================

@pytest.fixture
def world():
    """One person per evidence class, each holding one identifier hash with one 1040 behind it."""
    tag = uuid.uuid4().hex[:8]
    year = 2100 + (int(tag, 16) % 800)
    w = {"tag": tag, "year": year, "person": {}, "contact": [], "ret": [], "user": None}

    cases = {
        "identifier": "drake_identity_promotion",
        "review": "drake_identity_review",
        "exact_name": "unique_exact_name",
        "name_location": "exact_name_city_state",
        "contact": "exact_email+exact_phone",
        "repair": "canonical_repair_exact_person_provenance",
        "recorded_human": "unique_exact_name",     # weak METHOD, strong RECORDED trust
        "quarantined": "drake_identity_promotion",
    }

    with engine.begin() as c:
        w["user"] = c.execute(insert(users).values(
            email=f"reviewer-{tag}@example.test", normalized_email=f"reviewer-{tag}@example.test",
            display_name=f"Reviewer {tag}").returning(users.c.id)).scalar_one()

        for key, method in cases.items():
            pid = c.execute(insert(people).values(
                first_name=f"Ada{tag}", last_name=f"Lovelace{tag}",
                full_name=f"Ada{tag} Lovelace{tag}", active=True,
            ).returning(people.c.id)).scalar_one()
            w["person"][key] = pid

            identifier = _hash(tag + key)
            sid = c.execute(insert(source_contacts).values(
                source_system="Drake", source_file=f"DRKTRUST {tag}",
                source_hash=uuid.uuid4().hex, source_record_id=uuid.uuid4().hex,
                raw_data={"identifier_hash": identifier, "role": "taxpayer", "tax_year": year},
            ).returning(source_contacts.c.id)).scalar_one()
            w["contact"].append(sid)

            link = {"person_id": pid, "source_contact_id": sid, "match_method": method,
                    "match_score": 100, "confirmed": True}
            if key == "recorded_human":
                link |= {"trust_level": HUMAN_APPROVED, "confirmation_source": SOURCE_HUMAN,
                         "evidence_method": method, "confirmed_by_user_id": w["user"],
                         "confirmed_at": datetime.now(UTC)}
            c.execute(insert(person_source_links).values(**link))

            identified = key != "quarantined"
            key_value = compute_return_identity_key(year, identifier, None, "1040", "1")
            rid = c.execute(insert(drake_client_returns).values(
                tax_year=year, source_row_number=len(w["ret"]) + 1,
                taxpayer_identifier_hash=identifier, return_type="1040", filing_status="1",
                taxpayer_first_name=f"Ada{tag}", taxpayer_last_name=f"Lovelace{tag}",
                return_identity_key=key_value if identified else None,
                identity_status=IDENTIFIED if identified else "unidentified_ambiguous_collision",
                source_updated_at=datetime.now(UTC), raw_data={},
            ).returning(drake_client_returns.c.id)).scalar_one()
            w["ret"].append(rid)
            w[f"return_{key}"] = rid

    yield w

    with engine.begin() as c:
        c.execute(delete(drake_client_returns).where(drake_client_returns.c.id.in_(w["ret"])))
        c.execute(delete(person_source_links).where(
            person_source_links.c.source_contact_id.in_(w["contact"])))
        c.execute(delete(source_contacts).where(source_contacts.c.id.in_(w["contact"])))
        c.execute(delete(people).where(people.c.id.in_(list(w["person"].values()))))
        c.execute(delete(users).where(users.c.id == w["user"]))


def _ids(person_id, policy):
    with engine.connect() as c:
        return {r["id"] for r in resolved_drake_returns_for_person(c, person_id, policy=policy)}


# --- 15. fails closed on untrusted linkage --------------------------------------------------------

@pytest.mark.parametrize("case", ["exact_name", "name_location", "contact", "repair"])
def test_untrusted_linkage_resolves_nothing_under_either_policy(world, case):
    assert _ids(world["person"][case], POLICY_STRICT) == set()
    assert _ids(world["person"][case], POLICY_LEGACY_DERIVED) == set()


def test_strict_policy_trusts_nothing_that_was_never_recorded(world):
    """The honest starting position: production holds no recorded trust, so STRICT resolves nothing."""
    for case in ("identifier", "review", "exact_name", "contact", "repair"):
        assert _ids(world["person"][case], POLICY_STRICT) == set()


# --- 9 / 10 at the resolver -----------------------------------------------------------------------

def test_identifier_grade_link_resolves_under_the_legacy_policy(world):
    assert _ids(world["person"]["identifier"], POLICY_LEGACY_DERIVED) \
        == {world["return_identifier"]}


def test_human_reviewed_link_resolves_under_the_legacy_policy(world):
    assert _ids(world["person"]["review"], POLICY_LEGACY_DERIVED) == {world["return_review"]}


def test_recorded_human_approval_resolves_even_under_the_strict_policy(world):
    """Recorded trust is the destination: a weak METHOD with a real, attributed approval passes."""
    assert _ids(world["person"]["recorded_human"], POLICY_STRICT) \
        == {world["return_recorded_human"]}


def test_resolution_reports_the_drake_role(world):
    with engine.connect() as c:
        rows = resolved_drake_returns_for_person(c, world["person"]["identifier"],
                                                 policy=POLICY_LEGACY_DERIVED)
    assert rows[0]["drake_role"] == "taxpayer"
    assert rows[0]["resolved_person_id"] == world["person"]["identifier"]


# --- identity gate at the resolver ----------------------------------------------------------------

def test_a_return_without_a_stable_identity_never_resolves(world):
    """The link is identifier-grade; the RETURN is quarantined. Quarantine wins."""
    assert _ids(world["person"]["quarantined"], POLICY_LEGACY_DERIVED) == set()
    assert _ids(world["person"]["quarantined"], POLICY_STRICT) == set()


# --- ambiguity ------------------------------------------------------------------------------------

def test_a_hash_claimed_by_two_trusted_people_reaches_neither(world):
    """Two claimants and the return is withheld from BOTH, never tie-broken."""
    tag = world["tag"]
    shared = _hash(tag + "identifier")
    with engine.begin() as c:
        rival = c.execute(insert(people).values(
            first_name=f"Rival{tag}", last_name=f"Claimant{tag}",
            full_name=f"Rival{tag} Claimant{tag}", active=True,
        ).returning(people.c.id)).scalar_one()
        sid = c.execute(insert(source_contacts).values(
            source_system="Drake", source_file=f"DRKTRUST {tag}",
            source_hash=uuid.uuid4().hex, source_record_id=uuid.uuid4().hex,
            raw_data={"identifier_hash": shared, "role": "taxpayer"},
        ).returning(source_contacts.c.id)).scalar_one()
        c.execute(insert(person_source_links).values(
            person_id=rival, source_contact_id=sid, match_method="drake_identity_promotion",
            match_score=100, confirmed=True))
    try:
        assert _ids(world["person"]["identifier"], POLICY_LEGACY_DERIVED) == set()
        assert _ids(rival, POLICY_LEGACY_DERIVED) == set()
        with engine.connect() as c:
            _sole, contested = trusted_hash_owners(c, policy=POLICY_LEGACY_DERIVED)
        assert shared in contested
    finally:
        with engine.begin() as c:
            c.execute(delete(person_source_links).where(
                person_source_links.c.source_contact_id == sid))
            c.execute(delete(source_contacts).where(source_contacts.c.id == sid))
            c.execute(delete(people).where(people.c.id == rival))


def test_an_unknown_policy_is_refused_rather_than_defaulted(world):
    with engine.connect() as c, pytest.raises(ValueError, match="unknown policy"):
        trusted_hash_owners(c, policy="whatever_seems_reasonable")
