"""An unresolved TaxDome folder is ONE question, however many documents sit in it.

Conflicts already aggregated; unresolved folders did not. On the production corpus that gap is
3,259 documents spread across 51 folders — 3,259 review rows for 51 decisions, about 64 restatements
of each. A queue like that is not read, it is dismissed, and the 51 real questions go unanswered.

So the unresolved-folder path now opens one review per source identity and hangs the documents off
it, exactly as the conflict path does. These tests pin that, and pin the behaviour around it that
must NOT change: a folder that resolves still links, an owned document is still left alone, Drake
and SharePoint still raise their own per-document reviews, and nothing creates a client.

Fixture names are invented and match no real client.
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


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name)
                        .returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _person(name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=name, active=True, contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(*, folder=None, source="TaxDome Drive", household_id=None, person_id=None, tags=None):
    payload = dict(tags or {})
    if folder is not None:
        payload["taxdome_folder"] = folder
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, original_name="d.pdf",
            stored_name=f"tdu-{uuid.uuid4().hex}", storage_path="x", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags=payload).returning(documents.c.id)).scalar_one()
        c.execute(sa.text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                          "VALUES (:d, :s, '')"), {"d": did, "s": source})
    _DOCS.append(did)
    return did


def _unresolved_folder():
    """A folder name that resolves to no client, registered for cleanup by its normalised key."""
    name = f"Unmatched Folder {_fresh()}"
    _KEYS.append(source_authority.normalise_key(name))
    return name


def _open_reviews(conn, key):
    return conn.execute(sa.text(
        "SELECT id, reason_code, status FROM document_pipeline_source_reviews "
        "WHERE subject_key = :k AND status = 'open'"), {"k": key}).mappings().all()


def _members(conn, review_id):
    return source_authority.source_review_documents(conn, review_id)


def _per_document_reviews(conn, doc_ids):
    return conn.execute(sa.text(
        "SELECT count(*) FROM document_pipeline_ownership_reviews WHERE document_id = ANY(:ids)"),
        {"ids": list(doc_ids)}).scalar()


# --- the invariant this change exists for ---------------------------------------------------------

@pytest.mark.parametrize("documents_in_folder", [2, 12])
def test_one_unresolved_folder_opens_one_review_however_many_documents(documents_in_folder):
    folder = _unresolved_folder()
    key = source_authority.normalise_key(folder)
    ids = [_doc(folder=folder) for _ in range(documents_in_folder)]

    with engine.begin() as c:
        verdicts = [ownership.resolve(c, did) for did in ids]
        reviews = _open_reviews(c, key)
        members = _members(c, reviews[0]["id"])
        per_doc = _per_document_reviews(c, ids)

    assert all(v["outcome"] == OUTCOME_REVIEW for v in verdicts)
    assert all(v["reason_code"] == "taxdome_folder_unresolved" for v in verdicts)
    assert all(v.get("aggregated") for v in verdicts)
    assert len({v["source_review_id"] for v in verdicts}) == 1
    assert len(reviews) == 1, f"{documents_in_folder} documents opened {len(reviews)} reviews"
    assert sorted(members) == sorted(ids)
    assert per_doc == 0, "the aggregated path must not also write per-document review rows"


def test_many_unresolved_folders_produce_one_review_each_not_one_per_document():
    """The shape of the production finding, in miniature: N folders, M documents each, N reviews."""
    folders = [_unresolved_folder() for _ in range(5)]
    ids_by_folder = {f: [_doc(folder=f) for _ in range(7)] for f in folders}

    with engine.begin() as c:
        for ids in ids_by_folder.values():
            for did in ids:
                ownership.resolve(c, did)
        review_ids, total_members = set(), 0
        for folder in folders:
            rows = _open_reviews(c, source_authority.normalise_key(folder))
            assert len(rows) == 1
            review_ids.add(rows[0]["id"])
            total_members += len(_members(c, rows[0]["id"]))
        per_doc = _per_document_reviews(c, [d for ids in ids_by_folder.values() for d in ids])

    assert len(review_ids) == 5, "one review per folder"
    assert total_members == 35, "every document is attached"
    assert per_doc == 0


def test_a_later_document_joins_the_existing_open_folder_review():
    folder = _unresolved_folder()
    key = source_authority.normalise_key(folder)
    first = _doc(folder=folder)
    with engine.begin() as c:
        opened = ownership.resolve(c, first)["source_review_id"]

    later = _doc(folder=folder)
    with engine.begin() as c:
        joined = ownership.resolve(c, later)
        rows = _open_reviews(c, key)
        members = _members(c, opened)

    assert joined["source_review_id"] == opened, "the second document opened a second review"
    assert len(rows) == 1
    assert sorted(members) == sorted([first, later])


def test_resolving_the_folder_lets_later_documents_inherit_ownership_without_review():
    """The point of aggregating: one decision, and the folder stops asking."""
    folder = _unresolved_folder()
    key = source_authority.normalise_key(folder)
    household = _household(f"Quillon Household {_fresh()}")
    early = _doc(folder=folder)

    with engine.begin() as c:
        review_id = ownership.resolve(c, early)["source_review_id"]
        outcome = source_authority.resolve_source_review(
            c, review_id, entity_type="household", entity_id=household,
            note="one decision for the folder")

    later = _doc(folder=folder)
    with engine.begin() as c:
        verdict = ownership.resolve(c, later)
        still_open = _open_reviews(c, key)
        owner = c.execute(sa.select(documents.c.household_id)
                          .where(documents.c.id == later)).scalar()

    assert outcome["applied"] >= 1
    assert verdict["outcome"] == OUTCOME_LINKED
    assert verdict.get("from_persisted_mapping") is True
    assert owner == household
    assert still_open == [], "the decision was made; the folder must stop asking"


# --- what must not change -------------------------------------------------------------------------

def test_an_owned_document_in_an_unresolved_folder_keeps_its_owner():
    folder = _unresolved_folder()
    _KEYS.append(source_authority.normalise_key(folder))
    stored = _household(f"Vexley Household {_fresh()}")
    owned = _doc(folder=folder, household_id=stored)

    with engine.begin() as c:
        verdict = ownership.resolve(c, owned)
        after = c.execute(sa.select(documents.c.household_id)
                          .where(documents.c.id == owned)).scalar()

    assert verdict["outcome"] == OUTCOME_ALREADY_OWNED
    assert after == stored, "an existing owner was overwritten"


def test_a_resolving_folder_still_links_directly_and_opens_no_review():
    """A folder that maps to one client is unchanged: it links, and asks nobody anything."""
    tag = _fresh()
    person = _person(f"Marisol Ondrasik {tag}")
    folder = f"Marisol Ondrasik {tag}"
    _KEYS.append(source_authority.normalise_key(folder))
    did = _doc(folder=folder)

    with engine.begin() as c:
        verdict = ownership.resolve(c, did)
        rows = _open_reviews(c, source_authority.normalise_key(folder))

    assert verdict["outcome"] == OUTCOME_LINKED
    assert verdict["lane"] == ownership.LANE_TAXDOME
    assert rows == []
    assert verdict.get("entity_id") == person


def test_sharepoint_ambiguity_stays_one_review_per_document():
    """Evidence is about a document's content, so it is never collapsed onto a folder."""
    ids = [_doc(source="SharePoint") for _ in range(3)]
    proposal = {"confidence": "AMBIGUOUS", "evidence": ["two plausible clients"],
                "best_candidates": []}

    with engine.begin() as c:
        verdicts = [ownership.resolve(c, did, proposal=proposal) for did in ids]
        per_doc = _per_document_reviews(c, ids)
        aggregated = c.execute(sa.text(
            "SELECT count(*) FROM document_pipeline_source_review_documents "
            "WHERE document_id = ANY(:ids)"), {"ids": ids}).scalar()

    assert all(v["outcome"] == OUTCOME_REVIEW for v in verdicts)
    assert all(v["lane"] == ownership.LANE_SHAREPOINT for v in verdicts)
    assert all(not v.get("aggregated") for v in verdicts)
    assert per_doc == 3, "SharePoint must keep one review per document"
    assert aggregated == 0


def test_drake_ambiguity_stays_one_review_per_document():
    ids = [_doc(source="Drake", folder=None) for _ in range(2)]
    proposal = {"confidence": "AMBIGUOUS", "evidence": ["two candidate returns"],
                "best_candidates": []}

    with engine.begin() as c:
        verdicts = [ownership.resolve(c, did, proposal=proposal) for did in ids]
        per_doc = _per_document_reviews(c, ids)

    assert all(v["outcome"] == OUTCOME_REVIEW for v in verdicts)
    assert all(v["lane"] == ownership.LANE_DRAKE for v in verdicts)
    assert all(not v.get("aggregated") for v in verdicts)
    assert per_doc == 2


def test_aggregating_a_folder_creates_no_person_household_or_organization():
    folder = _unresolved_folder()
    ids = [_doc(folder=folder) for _ in range(4)]

    with engine.begin() as c:
        before = {t: c.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar()
                  for t in ("people", "households", "relationship_entities")}
    with engine.begin() as c:
        for did in ids:
            ownership.resolve(c, did)
    with engine.begin() as c:
        after = {t: c.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar()
                 for t in ("people", "households", "relationship_entities")}

    assert before == after, f"the pipeline created an entity: {before} -> {after}"
