"""Durable source-to-owner mappings, and one review per source client.

THE TWO ACTIVATION BLOCKERS, PINNED
-----------------------------------
1. **A verified source identity is resolved once.** Drake identifies a client by client id, TaxDome
   by account. Once either resolves, that fact is persisted against the stable key and every future
   document carrying it inherits the owner — never re-derived from a name, filename or OCR result,
   because re-deriving is how a rename silently moves a client's documents.

2. **A conflict is one question about a client, not one per document.** On the production corpus, 474
   conflicts come from 18 folders and one folder accounts for 121 documents. A reviewer shown the
   same question 121 times stops reading it.

Fixture names here are invented and never match a real client.
"""
import hashlib
import uuid

import pytest
import sqlalchemy as sa

from app.db import documents, engine, households, people
from app.services.document_pipeline_continuous import ownership, source_authority
from app.services.document_pipeline_continuous.model import (
    OUTCOME_ALREADY_OWNED,
    OUTCOME_LINKED,
    OUTCOME_REVIEW,
)

_TAG = uuid.uuid4().hex[:8]


def _fresh() -> str:
    """A subject-key suffix unique to ONE test.

    A module-level tag made several tests share one Drake client id, so the second test's mapping was
    refused exactly as designed — and the test blamed the code for the guard working."""
    return uuid.uuid4().hex[:10]


_DOCS: list[int] = []
_PEOPLE: list[int] = []
_HH: list[int] = []
_SUBJECTS: list[tuple] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            c.execute(sa.text("DELETE FROM document_pipeline_source_review_documents "
                              "WHERE document_id = ANY(:ids)"), {"ids": ids})
            for t in ("document_pipeline_tasks", "document_pipeline_blockers",
                      "document_pipeline_ownership_reviews", "document_sources", "document_ocr"):
                c.execute(sa.text(f"DELETE FROM {t} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        for system, stype, key in _SUBJECTS:
            c.execute(sa.text("DELETE FROM document_pipeline_source_review_documents m "
                              "USING document_pipeline_source_reviews r "
                              "WHERE m.review_id = r.id AND r.subject_key = :k"), {"k": key})
            c.execute(sa.text("DELETE FROM document_pipeline_source_reviews WHERE subject_key = :k"),
                      {"k": key})
            c.execute(sa.text("DELETE FROM folder_resolution_decisions "
                              "WHERE subject_system = :s AND subject_type = :t AND subject_key = :k"),
                      {"s": system, "t": stype, "k": key})
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    for bucket in (_DOCS, _PEOPLE, _HH, _SUBJECTS):
        bucket.clear()


def _person(name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=name, active=True, contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _doc(*, source_system=None, external_id=None, tags=None, person_id=None, household_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id,
            original_name=f"d-{_TAG}.pdf", stored_name=f"sa-{uuid.uuid4().hex}",
            storage_path="x", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", archived=False, tags=tags or {}).returning(documents.c.id)).scalar_one()
        if source_system:
            c.execute(sa.text("INSERT INTO document_sources "
                              "(document_id, source_system, source_uri, source_external_id) "
                              "VALUES (:d, :s, '', :e)"),
                      {"d": did, "s": source_system, "e": external_id})
    _DOCS.append(did)
    return did


def _track(identity):
    _SUBJECTS.append(identity.triple)
    return identity


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(sa.select(
            documents.c.person_id, documents.c.household_id, documents.c.organization_id)
            .where(documents.c.id == did)).first())


# --- stable keys are not display names ----------------------------------------------------------

def test_a_drake_document_is_identified_by_client_id_not_by_name():
    external = f"DRK{_fresh()}"
    did = _doc(source_system="Drake", external_id=external)
    with engine.connect() as c:
        identity = source_authority.source_identity(c, did)
    assert identity is not None
    assert identity.subject_type == source_authority.SUBJECT_DRAKE_CLIENT
    assert identity.key == source_authority.normalise_key(external)


def test_a_taxdome_account_id_is_preferred_over_the_folder_name():
    """An account id survives a folder rename. A folder name does not."""
    did = _doc(source_system="TaxDome Drive", external_id=f"ACC{_TAG}",
               tags={"taxdome_folder": "Farnsworth, Tobias & Marisol"})
    with engine.connect() as c:
        identity = source_authority.source_identity(c, did)
    assert identity.subject_type == source_authority.SUBJECT_TAXDOME_ACCOUNT


def test_the_folder_key_is_used_only_when_no_account_id_exists():
    folder = f"Farnsworth {_fresh()}"
    did = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.connect() as c:
        identity = source_authority.source_identity(c, did)
    assert identity.subject_type == source_authority.SUBJECT_TAXDOME_FOLDER
    assert identity.key == source_authority.normalise_key(folder)


def test_normalisation_unifies_spelling_variants_but_not_different_clients():
    same = {source_authority.normalise_key("WHITE, MICHAEL AND DEBRA"),
            source_authority.normalise_key("White, Michael & Debra  ")}
    assert len(same) == 1, "case and punctuation must not create two identities"
    assert (source_authority.normalise_key("Farnsworth, Tobias")
            != source_authority.normalise_key("Farnsworth, Quillon")), \
        "two different clients must never normalise together"


def test_sharepoint_has_no_stable_identity_and_stays_evidence_based():
    did = _doc(source_system="SharePoint")
    with engine.connect() as c:
        assert source_authority.source_identity(c, did) is None


def test_a_blank_key_is_not_an_identity():
    """Rule 9: a blank name is not a client."""
    did = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": "   "})
    with engine.connect() as c:
        assert source_authority.source_identity(c, did) is None
    assert source_authority.normalise_key(None) == ""
    assert source_authority.normalise_key("") == ""


# --- mappings persist and are inherited -----------------------------------------------------------

def test_a_taxdome_folder_is_mapped_once_and_future_documents_inherit_the_owner():
    household = _household(f"Farnsworth Household {_TAG}")
    folder = f"Farnsworth {_fresh()}"
    first = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, first))
        source_authority.persist_mapping(c, identity, entity_type="household",
                                         entity_id=household, match_reason="test")

    later = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.begin() as c:
        verdict = ownership.resolve(c, later, proposal={}, request_id="test")

    assert verdict["outcome"] == OUTCOME_LINKED
    assert verdict.get("from_persisted_mapping") is True
    assert _owner(later) == (None, household, None)


def test_a_drake_identity_is_mapped_once_and_future_documents_inherit_the_owner():
    person = _person(f"Quillon Farnsworth {_TAG}")
    external = f"DRK{_fresh()}"
    first = _doc(source_system="Drake", external_id=external)
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, first))
        source_authority.persist_mapping(c, identity, entity_type="person", entity_id=person,
                                         match_reason="test")

    later = _doc(source_system="Drake", external_id=external)
    with engine.begin() as c:
        verdict = ownership.resolve(c, later, proposal={}, request_id="test")
    assert verdict["outcome"] == OUTCOME_LINKED
    assert _owner(later) == (person, None, None)


def test_a_persisted_mapping_bypasses_name_and_ocr_inference_entirely():
    """The mapping must win even when content evidence points somewhere else."""
    mapped = _person(f"Mapped Farnsworth {_TAG}")
    decoy = _person(f"Decoy Vexley {_TAG}")
    folder = f"Farnsworth {_fresh()}"
    seed = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, seed))
        source_authority.persist_mapping(c, identity, entity_type="person", entity_id=mapped)

    later = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.begin() as c:
        verdict = ownership.resolve(c, later, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": decoy,
            "evidence": ["a very confident but irrelevant content match"]}, request_id="test")
    assert _owner(later) == (mapped, None, None), "content evidence must not beat a verified mapping"
    assert verdict.get("from_persisted_mapping") is True


def test_an_already_correct_owner_is_a_no_op():
    household = _household(f"Farnsworth Household {_TAG}")
    folder = f"Farnsworth {_fresh()}"
    seed = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, seed))
        source_authority.persist_mapping(c, identity, entity_type="household", entity_id=household)

    owned = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder},
                 household_id=household)
    with engine.begin() as c:
        verdict = ownership.resolve(c, owned, proposal={}, request_id="test")
    assert verdict["outcome"] == OUTCOME_ALREADY_OWNED
    assert _owner(owned) == (None, household, None)


def test_a_conflicting_existing_owner_is_never_overwritten_by_a_mapping():
    mapped = _household(f"Farnsworth Household {_TAG}")
    stored = _household(f"Vexley Household {_TAG}")
    folder = f"Farnsworth {_fresh()}"
    seed = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder})
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, seed))
        source_authority.persist_mapping(c, identity, entity_type="household", entity_id=mapped)

    conflicted = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": folder},
                      household_id=stored)
    with engine.begin() as c:
        verdict = ownership.resolve(c, conflicted, proposal={}, request_id="test")
    assert verdict["outcome"] == OUTCOME_REVIEW
    assert _owner(conflicted) == (None, stored, None), "the stored owner must be untouched"


# --- mapping lifecycle is guarded ------------------------------------------------------------------

def test_source_key_uniqueness_is_enforced_and_a_second_mapping_is_refused():
    a, b = _person(f"First Farnsworth {_TAG}"), _person(f"Second Vexley {_TAG}")
    did = _doc(source_system="Drake", external_id=f"DRK{_fresh()}")
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, did))
        first = source_authority.persist_mapping(c, identity, entity_type="person", entity_id=a)
        second = source_authority.persist_mapping(c, identity, entity_type="person", entity_id=b)
    assert first is not None
    assert second is None, "an automated writer must never replace an established mapping"
    with engine.connect() as c:
        assert source_authority.lookup_mapping(c, identity)["entity_id"] == a


def test_changing_a_mapping_is_explicit_versioned_and_audited():
    """A correction supersedes; it never overwrites. History survives."""
    from app.services.resolution_knowledge import get_decision_history, record_decision

    a, b = _person(f"Before Farnsworth {_TAG}"), _person(f"After Vexley {_TAG}")
    did = _doc(source_system="Drake", external_id=f"DRK{_fresh()}")
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, did))
        source_authority.persist_mapping(c, identity, entity_type="person", entity_id=a)
        record_decision(subject_system=identity.system, subject_type=identity.subject_type,
                        subject_key=identity.key, display_name=identity.display,
                        decision="link_person", resulting_entity_type="person",
                        resulting_entity_id=b, supersede=True, reviewed_by="a human", conn=c)
        history = get_decision_history(*identity.triple, conn=c)
        current = source_authority.lookup_mapping(c, identity)
    assert current["entity_id"] == b
    assert len(history) == 2, "the prior decision must be retained as history"
    assert sum(1 for h in history if h["active"]) == 1
    superseded = next(h for h in history if not h["active"])
    assert superseded["superseded_at"] is not None
    assert superseded["superseded_by"] is not None


def test_a_mapping_can_never_point_at_an_entity_that_does_not_exist():
    did = _doc(source_system="Drake", external_id=f"DRK{_fresh()}")
    with engine.begin() as c:
        identity = _track(source_authority.source_identity(c, did))
        assert source_authority.persist_mapping(
            c, identity, entity_type="person", entity_id=2_147_000_003) is None


def test_the_pipeline_never_records_a_create_decision():
    """The ledger can express 'create this client'. The pipeline must never be the one saying it."""
    import inspect

    src = inspect.getsource(source_authority)
    assert "create_person" not in src.replace("create_person`` and", "")
    assert set(source_authority._LINK_DECISION.values()) == {
        "link_person", "link_household", "link_business"}


def test_no_entity_creation_anywhere_in_the_module():
    import inspect

    src = inspect.getsource(source_authority)
    for forbidden in ("people.insert(", "households.insert(", "relationship_entities.insert(",
                      "assign_people_to_household", "person_creation", "merge_people"):
        assert forbidden not in src


def test_a_blank_full_name_never_creates_or_matches_a_person():
    blank = _person("")
    did = _doc(source_system="TaxDome Drive", tags={"taxdome_folder": "   "})
    with engine.connect() as c:
        assert source_authority.source_identity(c, did) is None
    with engine.connect() as c:
        before = c.execute(sa.select(sa.func.count()).select_from(people)).scalar()
    with engine.begin() as c:
        ownership.resolve(c, did, proposal={}, request_id="test")
    with engine.connect() as c:
        after = c.execute(sa.select(sa.func.count()).select_from(people)).scalar()
    assert after == before, "resolution must never create a person"
    assert blank in _PEOPLE
