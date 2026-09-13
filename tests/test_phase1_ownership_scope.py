"""Phase 1: Drake and TaxDome own documents; SharePoint is discovered and enriched but never owned.

The rollout is phased for a reason that is not code readiness. Drake and TaxDome identify a client by
a stable key — a client id, an account — so a mapping recorded against that key stays true through a
rename. SharePoint identifies a client by evidence in the document, and evidence is worth more AFTER
the authoritative mappings exist, because a folder already resolved to a client stops being a guess.

So Phase 1 must be complete, not partial: an excluded lane assigns no owner, opens no review, and
persists no mapping. A lane that is "off" but still files rows in a reviewer's queue is not off.

These also pin the second half of Phase 1: an identity whose existing documents already agree gets
that agreement recorded ONCE, even when it needs no ownership write at all.

Fixture names are invented and match no real client.
"""
import hashlib
import uuid

import pytest
import sqlalchemy as sa

from app.config import OWNERSHIP_LANES, document_pipeline_ownership_sources
from app.db import documents, engine, households, people
from app.services.document_pipeline_continuous import ownership, source_authority
from app.services.document_pipeline_continuous.model import (
    OUTCOME_ALREADY_OWNED,
    OUTCOME_LINKED,
)

_DOCS: list[int] = []
_PEOPLE: list[int] = []
_HH: list[int] = []
_KEYS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            c.execute(sa.text("DELETE FROM document_pipeline_source_review_documents "
                              "WHERE document_id = ANY(:ids)"), {"ids": ids})
            for t in ("document_pipeline_ownership_reviews", "document_sources", "document_ocr"):
                c.execute(sa.text(f"DELETE FROM {t} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        for key in _KEYS:
            c.execute(sa.text("DELETE FROM document_pipeline_source_review_documents m "
                              "USING document_pipeline_source_reviews r "
                              "WHERE m.review_id = r.id AND r.subject_key = :k"), {"k": key})
            c.execute(sa.text("DELETE FROM document_pipeline_source_reviews WHERE subject_key = :k"),
                      {"k": key})
            c.execute(sa.text("DELETE FROM folder_resolution_decisions WHERE subject_key = :k"),
                      {"k": key})
        if _PEOPLE:
            c.execute(sa.text("DELETE FROM household_relationships WHERE person_id = ANY(:p)"),
                      {"p": _PEOPLE})
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    for bucket in (_DOCS, _PEOPLE, _HH, _KEYS):
        bucket.clear()


@pytest.fixture
def phase1(monkeypatch):
    """The exact production setting Phase 1 runs under."""
    monkeypatch.setenv("DOCUMENT_PIPELINE_OWNERSHIP_SOURCES", "drake,taxdome")
    return document_pipeline_ownership_sources()


def _fresh():
    return uuid.uuid4().hex[:10]


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name)
                        .returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _person(name, household_id=None):
    """A person, and — when given a household — the membership row that makes them a member.

    Setting people.household_id alone produces a state real data does not have: the denormalised
    column says one thing and the relationship table says nothing. The household policy reads both,
    so a fixture that writes only one would test the fixture's gap rather than the policy.
    """
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=name, active=True, contact_type="Client",
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id:
            c.execute(sa.text("INSERT INTO household_relationships"
                              " (household_id, person_id, relationship_type)"
                              " VALUES (:h, :p, 'member')"), {"h": household_id, "p": pid})
    _PEOPLE.append(pid)
    return pid


def _doc(*, source, folder=None, external_id=None, household_id=None, person_id=None):
    tags = {"taxdome_folder": folder} if folder else {}
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, original_name="d.pdf",
            stored_name=f"p1-{uuid.uuid4().hex}", storage_path="x", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags=tags).returning(documents.c.id)).scalar_one()
        c.execute(sa.text(
            "INSERT INTO document_sources (document_id, source_system, source_external_id, source_uri)"
            " VALUES (:d, :s, :e, '')"), {"d": did, "s": source, "e": external_id})
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.begin() as c:
        r = c.execute(sa.select(documents.c.person_id, documents.c.household_id,
                                documents.c.organization_id)
                      .where(documents.c.id == did)).first()
    return tuple(r)


def _counts(c, ids):
    return (
        c.execute(sa.text("SELECT count(*) FROM document_pipeline_ownership_reviews "
                          "WHERE document_id = ANY(:ids)"), {"ids": ids}).scalar(),
        c.execute(sa.text("SELECT count(*) FROM document_pipeline_source_review_documents "
                          "WHERE document_id = ANY(:ids)"), {"ids": ids}).scalar(),
    )


# --- the configuration switch ---------------------------------------------------------------------

def test_unset_means_every_lane_so_the_switch_changes_nothing_until_asked(monkeypatch):
    monkeypatch.delenv("DOCUMENT_PIPELINE_OWNERSHIP_SOURCES", raising=False)
    assert document_pipeline_ownership_sources() == frozenset(OWNERSHIP_LANES)


def test_the_phase_1_production_setting_selects_drake_and_taxdome_only(phase1):
    assert phase1 == frozenset({"drake", "taxdome"})
    assert "sharepoint" not in phase1


@pytest.mark.parametrize("raw,expected", [
    ("drake,taxdome", {"drake", "taxdome"}),
    (" Drake , TaxDome ", {"drake", "taxdome"}),
    ("drake", {"drake"}),
    ("", set()),
    ("drake,nonsense", {"drake"}),
    ("nonsense", set()),
])
def test_the_switch_parses_and_refuses_to_invent_a_lane(monkeypatch, raw, expected):
    monkeypatch.setenv("DOCUMENT_PIPELINE_OWNERSHIP_SOURCES", raw)
    assert document_pipeline_ownership_sources() == frozenset(expected)


# --- SharePoint is completely out of scope in Phase 1 ---------------------------------------------

@pytest.mark.parametrize("confidence", ["HIGH", "MEDIUM", "AMBIGUOUS", "HOLD", "NO_MATCH"])
def test_sharepoint_assigns_nothing_and_queues_nothing_at_any_confidence(phase1, confidence):
    target = _person(f"Tobias Brill {_fresh()}")
    did = _doc(source="SharePoint")
    proposal = {"confidence": confidence, "entity_type": "person", "entity_id": target,
                "evidence": ["content evidence"]}

    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal=proposal)
        per_doc, aggregated = _counts(c, [did])

    assert verdict["outcome"] == ownership.OUTCOME_OUT_OF_SCOPE
    assert verdict["lane"] == ownership.LANE_SHAREPOINT
    assert verdict["reason_code"] == "lane_not_in_ownership_scope"
    assert _owner(did) == (None, None, None), "SharePoint assigned an owner in Phase 1"
    assert per_doc == 0, "SharePoint opened a review in Phase 1"
    assert aggregated == 0


def test_sharepoint_persists_no_authoritative_mapping_in_phase_1(phase1):
    did = _doc(source="SharePoint")
    with engine.begin() as c:
        before = c.execute(sa.text("SELECT count(*) FROM folder_resolution_decisions")).scalar()
        ownership.resolve(c, did, proposal={"confidence": "HIGH", "entity_type": "person",
                                            "entity_id": _person(f"Zzyzx {_fresh()}")})
        after = c.execute(sa.text("SELECT count(*) FROM folder_resolution_decisions")).scalar()
    assert after == before


def test_sharepoint_still_owns_when_the_scope_includes_it(monkeypatch):
    """Phase 2, in one test: the gate is scope, not a permanent disabling of the lane."""
    monkeypatch.setenv("DOCUMENT_PIPELINE_OWNERSHIP_SOURCES", "drake,taxdome,sharepoint")
    target = _person(f"Ondrasik {_fresh()}")
    did = _doc(source="SharePoint")

    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": target})

    assert verdict["outcome"] == OUTCOME_LINKED
    assert _owner(did) == (target, None, None)


# --- Drake and TaxDome are unaffected by the Phase 1 scope ----------------------------------------

def test_taxdome_still_links_in_phase_1(phase1):
    tag = _fresh()
    person = _person(f"Marisol Quillon {tag}")
    folder = f"Marisol Quillon {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    did = _doc(source="TaxDome Drive", folder=folder)

    with engine.begin() as c:
        verdict = ownership.resolve(c, did)

    assert verdict["outcome"] == OUTCOME_LINKED
    assert verdict["lane"] == ownership.LANE_TAXDOME
    assert _owner(did) == (person, None, None)


def test_drake_is_in_scope_in_phase_1(phase1):
    target = _person(f"Nonesuch {_fresh()}")
    did = _doc(source="Drake", external_id=f"DK{_fresh()}")
    _KEYS.append(source_authority.normalise_key(f"DK{_fresh()}"))

    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": target})

    assert verdict["outcome"] == OUTCOME_LINKED
    assert verdict["lane"] == ownership.LANE_DRAKE


# --- mappings established from agreement that already exists --------------------------------------

def test_an_already_owned_agreeing_folder_records_its_mapping_without_any_write(phase1):
    """The gap this closes: a correctly filed client needing no write stayed unmapped forever."""
    tag = _fresh()
    folder = f"Established Folder {tag}"
    key = source_authority.normalise_key(folder)
    _KEYS.append(key)
    household = _household(f"Quillon Household {tag}")
    owned = [_doc(source="TaxDome Drive", folder=folder, household_id=household)
             for _ in range(3)]

    with engine.begin() as c:
        verdict = ownership.resolve(c, owned[0])
        identity = source_authority.source_identity(c, owned[0])
        mapping = source_authority.lookup_mapping(c, identity)

    assert verdict["outcome"] == OUTCOME_ALREADY_OWNED
    assert verdict["mapping_status"] == "established"
    assert mapping is not None
    assert (mapping["entity_type"], mapping["entity_id"]) == ("household", household)
    for did in owned:
        assert _owner(did) == (None, household, None), "an existing owner changed"


def test_a_later_unowned_document_under_that_identity_inherits_it(phase1):
    tag = _fresh()
    folder = f"Established Folder {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    household = _household(f"Quillon Household {tag}")
    _doc(source="TaxDome Drive", folder=folder, household_id=household)
    first = _DOCS[-1]

    with engine.begin() as c:
        ownership.resolve(c, first)

    later = _doc(source="TaxDome Drive", folder=folder)
    with engine.begin() as c:
        verdict = ownership.resolve(c, later)

    assert verdict["outcome"] == OUTCOME_LINKED
    assert verdict.get("from_persisted_mapping") is True
    assert _owner(later) == (None, household, None)


def test_a_person_plus_their_household_needs_the_policy_and_is_held_without_it(phase1):
    """Person-plus-household no longer resolves on structure alone.

    It looks like one client at two levels and usually is — but sometimes it is two clients sharing a
    folder, and nothing structural separates those. Phase 1 therefore requires Drake to confirm the
    couple files jointly (see tests/test_household_mapping_policy.py for all six conditions and the
    passing path). Absent that confirmation the folder is one review, not a guess.
    """
    tag = _fresh()
    folder = f"Established Folder {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    household = _household(f"Quillon Household {tag}")
    member = _person(f"Quillon Member {tag}", household_id=household)
    _doc(source="TaxDome Drive", folder=folder, person_id=member, household_id=household)
    _doc(source="TaxDome Drive", folder=folder, household_id=household)

    with engine.begin() as c:
        result = source_authority.establish_mapping_from_existing_owners(
            c, source_authority.source_identity(c, _DOCS[-1]))

    assert result["status"] == "ambiguous"
    assert result["reason"] == "household_is_not_a_couple"
    assert "entity_id" not in result, "a refused folder must carry nothing to write"


def test_a_person_from_a_different_household_is_ambiguous_not_a_mapping(phase1):
    """Two clients in one folder is a question. It must never become a mapping."""
    tag = _fresh()
    folder = f"Established Folder {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    household = _household(f"Quillon Household {tag}")
    other = _household(f"Vexley Household {tag}")
    outsider = _person(f"Vexley Member {tag}", household_id=other)
    _doc(source="TaxDome Drive", folder=folder, household_id=household)
    _doc(source="TaxDome Drive", folder=folder, person_id=outsider)

    with engine.begin() as c:
        identity = source_authority.source_identity(c, _DOCS[-1])
        result = source_authority.establish_mapping_from_existing_owners(c, identity)
        mapping = source_authority.lookup_mapping(c, identity)

    assert result["status"] == "ambiguous"
    assert mapping is None


def test_two_households_in_one_folder_is_ambiguous(phase1):
    tag = _fresh()
    folder = f"Established Folder {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    _doc(source="TaxDome Drive", folder=folder,
         household_id=_household(f"Quillon Household {tag}"))
    _doc(source="TaxDome Drive", folder=folder,
         household_id=_household(f"Vexley Household {tag}"))

    with engine.begin() as c:
        identity = source_authority.source_identity(c, _DOCS[-1])
        result = source_authority.establish_mapping_from_existing_owners(c, identity)
        mapping = source_authority.lookup_mapping(c, identity)

    assert result["status"] == "ambiguous"
    assert mapping is None


def test_an_identity_with_no_owned_documents_establishes_nothing(phase1):
    folder = f"Unowned Folder {_fresh()}"
    _KEYS.append(source_authority.normalise_key(folder))
    _doc(source="TaxDome Drive", folder=folder)

    with engine.begin() as c:
        result = source_authority.establish_mapping_from_existing_owners(
            c, source_authority.source_identity(c, _DOCS[-1]))

    assert result["status"] == "no_owned_documents"


def test_establishing_a_mapping_creates_no_person_household_or_organization(phase1):
    tag = _fresh()
    folder = f"Established Folder {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    household = _household(f"Quillon Household {tag}")
    did = _doc(source="TaxDome Drive", folder=folder, household_id=household)

    with engine.begin() as c:
        before = {t: c.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar()
                  for t in ("people", "households", "relationship_entities")}
    with engine.begin() as c:
        ownership.resolve(c, did)
    with engine.begin() as c:
        after = {t: c.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar()
                 for t in ("people", "households", "relationship_entities")}

    assert before == after


# --- documents with no provenance at all -----------------------------------------------------------

def _orphan_doc(folder, **owner):
    """A live document carrying a TaxDome folder tag and NO document_sources row of any kind."""
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name="d.pdf", stored_name=f"orph-{uuid.uuid4().hex}", storage_path="x",
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", archived=False, tags={"taxdome_folder": folder},
            **owner).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def test_a_document_with_no_source_row_is_held_for_provenance_repair(phase1):
    """SharePoint is detect_lane's fallback, not a finding. Absent provenance must not become one."""
    folder = f"Orphan Folder {_fresh()}"
    _KEYS.append(source_authority.normalise_key(folder))
    did = _orphan_doc(folder)

    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HIGH", "entity_type": "person",
            "entity_id": _person(f"Decoy {_fresh()}")})
        per_doc, aggregated = _counts(c, [did])

    assert verdict["outcome"] == ownership.OUTCOME_PROVENANCE_REPAIR
    assert verdict["reason_code"] == "no_document_source_rows"
    assert verdict["apparent_taxdome_folder"] == folder
    assert verdict["lane"] is None, "it must not be labelled SharePoint"
    assert _owner(did) == (None, None, None)
    assert per_doc == 0 and aggregated == 0


def test_a_provenance_less_document_persists_no_mapping(phase1):
    folder = f"Orphan Folder {_fresh()}"
    _KEYS.append(source_authority.normalise_key(folder))
    did = _orphan_doc(folder, household_id=_household(f"Quillon Household {_fresh()}"))

    with engine.begin() as c:
        before_n = c.execute(sa.text("SELECT count(*) FROM folder_resolution_decisions")).scalar()
        verdict = ownership.resolve(c, did)
        after_n = c.execute(sa.text("SELECT count(*) FROM folder_resolution_decisions")).scalar()

    assert verdict["outcome"] == ownership.OUTCOME_PROVENANCE_REPAIR
    assert after_n == before_n, "a document with no provenance must not establish a mapping"
