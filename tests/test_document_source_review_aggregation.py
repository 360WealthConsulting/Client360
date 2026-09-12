"""One review per source client — the fix for reviewer flooding.

On the production corpus 474 ownership conflicts come from 18 TaxDome folders, and ONE folder
accounts for 121 documents. Document-level review rows would put 474 items in front of a reviewer
for 18 decisions, 121 of them the same decision restated. A reviewer shown the same question 121
times stops reading it, which is how a real conflict gets approved by reflex.

So a conflict raised by a stable source identity opens ONE review and the documents hang off it.
These tests pin that, and pin the things that make it safe: resolving once applies only to documents
still genuinely unowned, existing owners are untouched, and closing and reopening are deterministic.

Fixture names are invented and match no real client.
"""
import hashlib
import uuid

import pytest
import sqlalchemy as sa

from app.db import documents, engine, households, people
from app.services.document_pipeline_continuous import ownership, source_authority
from app.services.document_pipeline_continuous.model import OUTCOME_REVIEW

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
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    for bucket in (_DOCS, _PEOPLE, _HH, _KEYS):
        bucket.clear()


def _fresh():
    return uuid.uuid4().hex[:10]


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


def _doc(folder, *, household_id=None, person_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, original_name="d.pdf",
            stored_name=f"ag-{uuid.uuid4().hex}", storage_path="x", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active", archived=False,
            tags={"taxdome_folder": folder}).returning(documents.c.id)).scalar_one()
        c.execute(sa.text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                          "VALUES (:d, 'TaxDome Drive', '')"), {"d": did})
    _DOCS.append(did)
    return did


def _identity(conn, did):
    identity = source_authority.source_identity(conn, did)
    if identity and identity.key not in _KEYS:
        _KEYS.append(identity.key)
    return identity


def _open_reviews(conn, key):
    return conn.execute(sa.text(
        "SELECT id, status FROM document_pipeline_source_reviews "
        "WHERE subject_key = :k AND status = 'open'"), {"k": key}).mappings().all()


def _count(conn, review_id):
    return conn.execute(sa.text("SELECT count(*) FROM document_pipeline_source_review_documents "
                                "WHERE review_id = :r"), {"r": review_id}).scalar()


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(sa.select(
            documents.c.person_id, documents.c.household_id, documents.c.organization_id)
            .where(documents.c.id == did)).first())


# --- the flooding fix -----------------------------------------------------------------------------

def test_one_hundred_and_twenty_one_conflicts_from_one_folder_create_ONE_review():
    """The production shape, at production size. 121 documents, one decision."""
    folder = f"Farnsworth {_fresh()}"
    stored = _household(f"Stored Household {_fresh()}")
    mapped = _household(f"Mapped Household {_fresh()}")

    first = _doc(folder, household_id=stored)
    with engine.begin() as c:
        identity = _identity(c, first)
        source_authority.persist_mapping(c, identity, entity_type="household", entity_id=mapped)

    docs = [first] + [_doc(folder, household_id=stored) for _ in range(120)]
    with engine.begin() as c:
        for did in docs:
            verdict = ownership.resolve(c, did, proposal={}, request_id="test")
            assert verdict["outcome"] == OUTCOME_REVIEW
        reviews = _open_reviews(c, identity.key)
        members = _count(c, reviews[0]["id"])

    assert len(docs) == 121
    assert len(reviews) == 1, f"121 documents produced {len(reviews)} reviews; expected exactly one"
    assert members == 121, "every affected document must be aggregated onto the one review"
    for did in docs:
        assert _owner(did) == (None, stored, None), "no stored owner may be touched"


def test_an_additional_document_joins_the_existing_review():
    folder = f"Farnsworth {_fresh()}"
    stored = _household(f"Stored {_fresh()}")
    mapped = _household(f"Mapped {_fresh()}")
    first = _doc(folder, household_id=stored)
    with engine.begin() as c:
        identity = _identity(c, first)
        source_authority.persist_mapping(c, identity, entity_type="household", entity_id=mapped)
        ownership.resolve(c, first, proposal={}, request_id="test")
        review_id = _open_reviews(c, identity.key)[0]["id"]

    later = _doc(folder, household_id=stored)
    with engine.begin() as c:
        ownership.resolve(c, later, proposal={}, request_id="test")
        reviews = _open_reviews(c, identity.key)
        members = _count(c, review_id)
    assert len(reviews) == 1
    assert reviews[0]["id"] == review_id, "a new document must join, not open another review"
    assert members == 2


def test_separate_folders_create_separate_reviews():
    stored = _household(f"Stored {_fresh()}")
    ids = []
    for _ in range(3):
        folder = f"Farnsworth {_fresh()}"
        mapped = _household(f"Mapped {_fresh()}")
        did = _doc(folder, household_id=stored)
        with engine.begin() as c:
            identity = _identity(c, did)
            source_authority.persist_mapping(c, identity, entity_type="household",
                                             entity_id=mapped)
            ownership.resolve(c, did, proposal={}, request_id="test")
            ids.append(_open_reviews(c, identity.key)[0]["id"])
    assert len(set(ids)) == 3, "three folders are three decisions"


def test_the_review_stores_the_client_label_once_not_per_document():
    """Aggregating is also what stops a client's name being copied onto 121 rows."""
    folder = f"Farnsworth {_fresh()}"
    stored = _household(f"Stored {_fresh()}")
    mapped = _household(f"Mapped {_fresh()}")
    first = _doc(folder, household_id=stored)
    with engine.begin() as c:
        identity = _identity(c, first)
        source_authority.persist_mapping(c, identity, entity_type="household", entity_id=mapped)
        for _ in range(4):
            ownership.resolve(c, _doc(folder, household_id=stored), proposal={}, request_id="test")
        ownership.resolve(c, first, proposal={}, request_id="test")
        rows = c.execute(sa.text(
            "SELECT count(*) FROM document_pipeline_source_reviews WHERE subject_key = :k"),
            {"k": identity.key}).scalar()
        member_columns = [r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'document_pipeline_source_review_documents'"))]
    assert rows == 1, "the display name exists on exactly one row"
    assert "display_name" not in member_columns
    assert "subject_key" not in member_columns


# --- resolving once -------------------------------------------------------------------------------

def test_resolving_a_review_applies_only_to_still_unowned_documents():
    folder = f"Farnsworth {_fresh()}"
    stored = _household(f"Stored {_fresh()}")
    target = _household(f"Target {_fresh()}")
    mapped = _household(f"Mapped {_fresh()}")

    owned = _doc(folder, household_id=stored)
    with engine.begin() as c:
        identity = _identity(c, owned)
        source_authority.persist_mapping(c, identity, entity_type="household", entity_id=mapped)
        ownership.resolve(c, owned, proposal={}, request_id="test")
        review_id = _open_reviews(c, identity.key)[0]["id"]
        # An unowned document joins the same review.
        free = _doc(folder)
        source_authority.open_source_review(c, identity, document_id=free,
                                            reason_code="ownership_conflict")
        result = source_authority.resolve_source_review(
            c, review_id, entity_type="household", entity_id=target, request_id="test",
            persist_mapping_too=False)

    assert result["applied"] == 1, "only the unowned document may be assigned"
    assert result["already_owned"] == 1
    assert _owner(free) == (None, target, None)
    assert _owner(owned) == (None, stored, None), "an existing owner must survive the resolution"


def test_resolving_records_the_decision_once_and_closes_the_review():
    folder = f"Farnsworth {_fresh()}"
    target = _household(f"Target {_fresh()}")
    did = _doc(folder)
    with engine.begin() as c:
        identity = _identity(c, did)
        review_id = source_authority.open_source_review(
            c, identity, document_id=did, reason_code="taxdome_folder_unresolved")
        source_authority.resolve_source_review(c, review_id, entity_type="household",
                                               entity_id=target, note="decided once",
                                               request_id="test")
        row = c.execute(sa.text("SELECT status, resolution_entity_type, resolution_entity_id, "
                                "resolution_note, resolved_at "
                                "FROM document_pipeline_source_reviews WHERE id = :i"),
                        {"i": review_id}).mappings().first()
    assert row["status"] == "resolved"
    assert row["resolution_entity_type"] == "household"
    assert row["resolution_entity_id"] == target
    assert row["resolved_at"] is not None
    assert _owner(did) == (None, target, None)


def test_resolving_persists_the_mapping_so_future_documents_skip_review_entirely():
    folder = f"Farnsworth {_fresh()}"
    target = _household(f"Target {_fresh()}")
    first = _doc(folder)
    with engine.begin() as c:
        identity = _identity(c, first)
        review_id = source_authority.open_source_review(
            c, identity, document_id=first, reason_code="taxdome_folder_unresolved")
        source_authority.resolve_source_review(c, review_id, entity_type="household",
                                               entity_id=target, request_id="test")

    later = _doc(folder)
    with engine.begin() as c:
        verdict = ownership.resolve(c, later, proposal={}, request_id="test")
        still_open = _open_reviews(c, identity.key)
    assert verdict.get("from_persisted_mapping") is True
    assert _owner(later) == (None, target, None)
    assert still_open == [], "a resolved folder must not reopen for the next document"


def test_closing_then_a_new_conflict_opens_exactly_one_new_review():
    """Deterministic: a resolved review stays resolved, and a later conflict gets one fresh review."""
    folder = f"Farnsworth {_fresh()}"
    target = _household(f"Target {_fresh()}")
    did = _doc(folder)
    with engine.begin() as c:
        identity = _identity(c, did)
        first_id = source_authority.open_source_review(
            c, identity, document_id=did, reason_code="taxdome_folder_unresolved")
        source_authority.resolve_source_review(c, first_id, entity_type="household",
                                               entity_id=target, request_id="test",
                                               persist_mapping_too=False)
        second_id = source_authority.open_source_review(
            c, identity, document_id=_doc(folder), reason_code="ownership_conflict")
        open_now = _open_reviews(c, identity.key)
        total = c.execute(sa.text("SELECT count(*) FROM document_pipeline_source_reviews "
                                  "WHERE subject_key = :k"), {"k": identity.key}).scalar()
    assert second_id != first_id
    assert len(open_now) == 1, "exactly one open review at a time"
    assert total == 2, "the resolved review is retained as history, not overwritten"


def test_only_one_open_review_per_identity_is_possible_at_the_database_level():
    """The partial unique index is the guarantee; the service is only the polite path to it."""
    folder = f"Farnsworth {_fresh()}"
    did = _doc(folder)
    with engine.begin() as c:
        identity = _identity(c, did)
        source_authority.open_source_review(c, identity, document_id=did,
                                            reason_code="ownership_conflict")
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(sa.text("""
                INSERT INTO document_pipeline_source_reviews
                    (subject_system, subject_type, subject_key, lane, reason_code, status)
                VALUES (:s, :t, :k, 'taxdome', 'ownership_conflict', 'open')
            """), {"s": identity.system, "t": identity.subject_type, "k": identity.key})


# --- merges leave nothing dangling -------------------------------------------------------------------

def test_deleting_a_document_removes_its_membership_and_leaves_no_orphan():
    folder = f"Farnsworth {_fresh()}"
    keep, doomed = _doc(folder), _doc(folder)
    with engine.begin() as c:
        identity = _identity(c, keep)
        review_id = source_authority.open_source_review(c, identity, document_id=keep,
                                                        reason_code="ownership_conflict")
        source_authority.open_source_review(c, identity, document_id=doomed,
                                            reason_code="ownership_conflict")
        assert _count(c, review_id) == 2
        c.execute(documents.delete().where(documents.c.id == doomed))
    _DOCS.remove(doomed)
    with engine.connect() as c:
        assert _count(c, review_id) == 1
        orphans = c.execute(sa.text("""
            SELECT count(*) FROM document_pipeline_source_review_documents m
             WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = m.document_id)
        """)).scalar()
        review_orphans = c.execute(sa.text("""
            SELECT count(*) FROM document_pipeline_source_review_documents m
             WHERE NOT EXISTS (SELECT 1 FROM document_pipeline_source_reviews r
                                WHERE r.id = m.review_id)
        """)).scalar()
    assert orphans == 0, "a membership row references a document that no longer exists"
    assert review_orphans == 0, "a membership row references a review that no longer exists"


def test_the_membership_table_is_registered_in_the_document_merge_registry():
    from app.services import document_merge as dm
    from app.services import document_merge_execute as dme

    assert dm.strategy_for("document_pipeline_source_review_documents",
                           "document_id") == "dedup_keyed"
    assert "document_pipeline_source_review_documents" in dme._DEDUP_KEYS
    assert dme._DEDUP_KEYS["document_pipeline_source_review_documents"][0] == ("review_id",)
    with engine.connect() as c:
        unregistered = [f"{d['table']}.{d['column']}" for d in dm._dependencies(c)
                        if d["strategy"] is None]
    assert unregistered == [], f"references with no declared strategy: {unregistered}"
