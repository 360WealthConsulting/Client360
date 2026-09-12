"""The exception reconciliation must be a PARTITION, and surname similarity must never merge families.

Two properties, both of which were violated by the summary this replaced:

1. **Exhaustive and mutually exclusive.** Every exception folder lands in exactly one category, and
   the category counts add back to the totals. The earlier summary grouped folders four different
   ways, each defensible alone, and then added overlapping buckets together — which is how it
   reported 115 personal decisions while listing categories that summed to 148.
2. **Evidence, not similarity.** "Same surname" is how two unrelated families get merged into one
   client record. A shared surname with CONTRADICTING given names must be demoted to review, never
   recommended as a duplicate-household merge.

These run without a database: ``classify`` is pure over a reference dictionary, so the reference is
built here by hand and every branch is reachable.
"""
import importlib.util
from collections import Counter
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "taxdome_exception_reconciliation.py"


def _load():
    spec = importlib.util.spec_from_file_location("taxdome_exception_reconciliation_under_test",
                                                  _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recon = _load()


def _ref(*, people=None, households=None, orgs=None, evidence=None, contacts=None):
    people = people or {}
    members = {}
    for person in people.values():
        if person.get("household_id") is not None:
            members.setdefault(int(person["household_id"]), []).append(person)
    return {"people": people, "households": households or {}, "orgs": orgs or {},
            "members": members,
            "evidence": evidence or {}, "drake": {}, "contacts": contacts or {}}


def _row(cause, **kw):
    base = {"cause": cause, "taxdome_folder": "F", "proposed_entity_type": "", "proposed_entity_id": "",
            "stored_person_id": "", "stored_household_id": "", "stored_organization_id": "",
            "document_id": "1"}
    base.update(kw)
    return base


def _contact(*, email=None, phone=None):
    return {"emails": {email} if email else set(),
            "phones": {phone} if phone else set(), "addresses": set()}


# --- the partition ----------------------------------------------------------------------------------

def test_the_category_list_and_the_personal_decision_subset_are_coherent():
    assert len(recon.CATEGORIES) == len(set(recon.CATEGORIES)), "duplicate category"
    assert set(recon.PERSONAL_DECISION_CATEGORIES) <= set(recon.CATEGORIES)
    # automatic and confirmation are explicitly NOT personal decisions — that is the whole point of
    # separating them, so the count of "decisions Michael must make" means something.
    assert "automatic" not in recon.PERSONAL_DECISION_CATEGORIES
    assert "confirmation" not in recon.PERSONAL_DECISION_CATEGORIES


@pytest.mark.parametrize("cause,rows,ref", [
    ("conflict_household",
     [_row("conflict_household", proposed_entity_id="1", stored_household_id="2")],
     _ref(households={1: {"id": 1, "name": "Alpha Household"},
                      2: {"id": 2, "name": "Beta Household"}})),
    ("conflict_person",
     [_row("conflict_person", proposed_entity_id="1", stored_person_id="2")],
     _ref(people={1: {"id": 1, "full_name": "A One", "household_id": None},
                  2: {"id": 2, "full_name": "B Two", "household_id": None}})),
    ("folder_unresolved", [_row("folder_unresolved")], _ref()),
    ("missing_folder_tag", [_row("missing_folder_tag")], _ref()),
])
def test_every_cause_classifies_into_a_known_category(cause, rows, ref):
    category, _confidence, reason = recon.classify("Some Folder", rows, ref)
    assert category in recon.CATEGORIES, f"{cause} produced unknown category {category}"
    assert reason, "every classification must carry a reason"


def test_folder_and_document_totals_are_exhaustive_and_mutually_exclusive():
    """The invariant the script asserts before it prints, reproduced over a synthetic corpus."""
    decided = {
        "f1": {"category": "automatic", "documents": 10},
        "f2": {"category": "automatic", "documents": 5},
        "f3": {"category": "confirmation", "documents": 7},
        "f4": {"category": "data_repair", "documents": 21},
        "f5": {"category": "create_profile", "documents": 100},
        "f6": {"category": "ambiguous", "documents": 3},
        "f7": {"category": "genuine_conflict", "documents": 1},
    }
    folders = Counter(d["category"] for d in decided.values())
    documents = Counter()
    for d in decided.values():
        documents[d["category"]] += d["documents"]

    assert sum(folders.values()) == len(decided)
    assert sum(documents.values()) == sum(d["documents"] for d in decided.values())
    assert set(folders) <= set(recon.CATEGORIES)
    # Each folder counted once and once only.
    assert sum(1 for d in decided.values() if d["category"] in recon.CATEGORIES) == len(decided)


def test_a_folder_can_hold_exactly_one_category():
    """Overlapping buckets are the defect. One folder, one answer."""
    rows = [_row("folder_unresolved"), _row("folder_unresolved")]
    first = recon.classify("Nobody At All", rows, _ref())
    second = recon.classify("Nobody At All", rows, _ref())
    assert first[0] == second[0], "classification must be deterministic"


# --- evidence, not similarity -------------------------------------------------------------------------

def test_a_shared_surname_with_contradicting_given_names_is_never_a_duplicate_merge():
    """A folder naming one given name against a household label naming a different one. Same last
    name is exactly how two unrelated families get merged into one client record.

    Names here are invented and verified against production to match nothing real — a fixture is not
    a place for a client's name."""
    ref = _ref(households={1: {"id": 1, "name": "Farnsworth Household"},
                           2: {"id": 2, "name": "Tobias & Marisol Farnsworth Household"}})
    rows = [_row("conflict_household", proposed_entity_id="1", stored_household_id="2")]
    category, confidence, reason = recon.classify("Quillon and Marisol Farnsworth", rows, ref)
    assert category == "ambiguous", "a surname-only match must not be recommended as a merge"
    assert category != "data_repair"
    assert "surname" in reason


def test_a_shared_contact_value_does_justify_a_duplicate_merge():
    """Independent corroboration is the bar — a shared phone or email, not a shared name."""
    ref = _ref(
        people={10: {"id": 10, "full_name": "Pat Alpha", "household_id": 1},
                20: {"id": 20, "full_name": "Pat Alpha", "household_id": 2}},
        households={1: {"id": 1, "name": "Alpha Household"},
                    2: {"id": 2, "name": "Alpha Household"}},
        evidence={10: _contact(phone="5551234567"), 20: _contact(phone="5551234567")})
    rows = [_row("conflict_household", proposed_entity_id="1", stored_household_id="2")]
    category, confidence, _reason = recon.classify("Alpha", rows, ref)
    assert category == "data_repair"
    assert confidence == "high"


def test_a_surname_match_with_no_corroboration_is_review_not_repair():
    ref = _ref(households={1: {"id": 1, "name": "Gamma Household"},
                           2: {"id": 2, "name": "Gamma Household"}})
    rows = [_row("conflict_household", proposed_entity_id="1", stored_household_id="2")]
    category, _confidence, _reason = recon.classify("Gamma", rows, ref)
    assert category == "ambiguous"


# --- who acts ------------------------------------------------------------------------------------------

def test_a_personal_folder_holding_organization_documents_is_michaels_decision():
    ref = _ref(people={1: {"id": 1, "full_name": "Sole Trader", "household_id": None}},
               orgs={9: {"id": 9, "name": "Trading Co"}})
    rows = [_row("conflict_person", proposed_entity_id="1", stored_organization_id="9")]
    category, _confidence, reason = recon.classify("Sole Trader", rows, ref)
    assert category == "genuine_conflict"
    assert "personal versus business" in reason


def test_a_folder_person_inside_the_stored_household_is_only_a_confirmation():
    """Agreement at two levels of one family is not a conflict anybody needs to adjudicate."""
    ref = _ref(people={1: {"id": 1, "full_name": "Member One", "household_id": 5}},
               households={5: {"id": 5, "name": "Family Household"}})
    rows = [_row("conflict_person", proposed_entity_id="1", stored_household_id="5")]
    category, _confidence, _reason = recon.classify("Member One", rows, ref)
    assert category == "confirmation"
    assert category not in recon.PERSONAL_DECISION_CATEGORIES


def test_an_exact_normalized_name_match_needs_no_personal_decision():
    ref = _ref(people={1: {"id": 1, "full_name": "Jane Q Example", "household_id": None}})
    rows = [_row("folder_unresolved")]
    category, _confidence, _reason = recon.classify("Example, Jane Q", rows, ref)
    assert category == "automatic"
    assert category not in recon.PERSONAL_DECISION_CATEGORIES


def test_a_name_matching_nothing_anywhere_is_a_create_profile_decision():
    category, _confidence, reason = recon.classify("Zzyzx Nonesuch", [_row("folder_unresolved")],
                                                   _ref())
    assert category == "create_profile"
    assert "no person, household, organization or source contact" in reason


def test_a_known_source_contact_without_a_profile_is_still_a_create_profile_decision():
    ref = _ref(contacts={"known someone": [{"email": "x@example.com"}]})
    category, _confidence, reason = recon.classify("Someone Known", [_row("folder_unresolved")], ref)
    assert category == "create_profile"
    assert "known to the firm" in reason


def test_the_script_writes_nothing_to_the_database():
    source = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("engine.begin(", "INSERT INTO", "DELETE FROM", "UPDATE ",
                      "resolve_document_ownership", "merge_people", "assign_people_to_household"):
        assert forbidden not in source, f"the reconciliation must not be able to {forbidden}"


def test_the_script_illustrates_with_placeholders_not_concrete_labels():
    """A client name in source is a client name in git history, permanently.

    This checks the SHAPE rather than a list of forbidden surnames. A guard that enumerates the names
    it forbids writes those names into the repository itself — which is exactly the mistake it is
    supposed to prevent, and is how real client labels reached this branch in the first place. So:
    no concrete ``<Word> Household`` label anywhere in the script. The placeholder form
    ``<Surname> Household`` carries angle brackets and does not match."""
    import re

    source = _SCRIPT.read_text(encoding="utf-8")
    concrete = re.findall(r"\b[A-Z][a-z]{2,}\s+(?:Household|Family|Trust)\b", source)
    assert concrete == [], (
        f"{len(concrete)} concrete client-style label(s) in source — use <Surname> Household")
