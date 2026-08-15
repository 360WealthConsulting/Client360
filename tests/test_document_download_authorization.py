"""Coverage for document download authorization (object-security middleware helper).

Household- and organization-owned documents (person_id NULL) must be downloadable when the principal has
record scope for the owning household/organization — previously only person_id was checked, so doc 800
(household 157, person NULL) returned 403. record.read_all still bypasses; unowned/missing stay denied.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import delete

from app.db import documents, engine, households, metadata, people, relationship_entities, users
from app.security.middleware import _document_in_scope
from app.security.models import Principal

_record_assignments = metadata.tables["record_assignments"]
_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "households": [], "relationship_entities": [], "users": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["users"]:
            c.execute(delete(_record_assignments).where(_record_assignments.c.user_id.in_(_C["users"])))
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        for tbl, key in ((relationship_entities, "relationship_entities"), (people, "people"),
                         (households, "households"), (users, "users")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _user():
    em = f"u-{uuid.uuid4().hex[:10]}@e.com"
    with engine.begin() as c:
        uid = c.execute(users.insert().values(email=em, normalized_email=em, display_name="U")
                        .returning(users.c.id)).scalar_one()
    _C["users"].append(uid)
    return uid


def _assign(user_id, entity_type, entity_id):
    with engine.begin() as c:
        c.execute(_record_assignments.insert().values(
            user_id=user_id, entity_type=entity_type, entity_id=entity_id, assignment_type="reviewer"))


def _household(name=None):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name or f"HH {_TAG}").returning(households.c.id)
                        ).scalar_one()
    _C["households"].append(hid)
    return hid


def _person(hid=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"P {_TAG}", active=True, household_id=hid)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _entity():
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(entity_type="business", name=f"Biz {_TAG}",
                                                              active=True).returning(relationship_entities.c.id)
                        ).scalar_one()
    _C["relationship_entities"].append(eid)
    return eid


def _doc(*, person_id=None, household_id=None, organization_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            original_name="d.pdf", stored_name=f"da-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="D:\\Content\\x.pdf", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active").returning(documents.c.id)
        ).scalar_one()
    _C["documents"].append(did)
    return did


def _principal(user_id, caps=frozenset()):
    return Principal(user_id, f"u{user_id}@e.com", "U", caps)


def _in_scope(principal, doc_id, write=False):
    with engine.connect() as c:
        return _document_in_scope(c, principal, doc_id, write=write)


# --- household-owned (the doc-800 fix) ----------------------------------------

def test_household_owned_document_allowed_with_household_scope():
    hid = _household()
    doc = _doc(household_id=hid)          # doc-800 shape: person_id NULL, household_id set
    uid = _user()
    _assign(uid, "household", hid)
    assert _in_scope(_principal(uid), doc) is True


def test_household_owned_document_allowed_for_record_read_all():
    doc = _doc(household_id=_household())
    assert _in_scope(_principal(_user(), frozenset({"record.read_all"})), doc) is True


def test_household_owned_document_denied_without_scope():
    doc = _doc(household_id=_household())
    assert _in_scope(_principal(_user()), doc) is False          # no assignment, not read_all -> denied


# --- person + organization + edge cases --------------------------------------

def test_person_owned_document_still_authorized_by_person_scope():
    pid = _person()
    doc = _doc(person_id=pid)
    uid = _user()
    _assign(uid, "person", pid)
    assert _in_scope(_principal(uid), doc) is True


def test_organization_owned_document_allowed_with_org_scope():
    eid = _entity()
    doc = _doc(organization_id=eid)
    uid = _user()
    _assign(uid, "organization", eid)
    assert _in_scope(_principal(uid), doc) is True


def test_unowned_document_denied_even_for_read_all():
    doc = _doc()                          # firm/unfiled — no canonical owner
    assert _in_scope(_principal(_user(), frozenset({"record.read_all"})), doc) is False


def test_missing_document_denied():
    assert _in_scope(_principal(_user(), frozenset({"record.read_all"})), 999_000_777) is False


# --- admin manual-resolution review: narrow read-only exception for UNASSIGNED documents ------

def test_admin_can_view_genuinely_unassigned_document_for_review():
    doc = _doc()  # all three ownership fields NULL
    admin = _principal(_user(), frozenset({"client.write"}))   # gates /admin/documents/unassigned
    assert _in_scope(admin, doc) is True                        # can inspect to determine owner


def test_non_admin_cannot_view_unassigned_document():
    doc = _doc()
    assert _in_scope(_principal(_user()), doc) is False         # no client.write -> normal denial


def test_read_all_alone_does_not_grant_the_admin_review_exception():
    doc = _doc()
    assert _in_scope(_principal(_user(), frozenset({"record.read_all"})), doc) is False


def test_admin_review_exception_is_read_only_not_write():
    doc = _doc()
    admin = _principal(_user(), frozenset({"client.write"}))
    assert _in_scope(admin, doc, write=True) is False           # exception never grants write


def test_admin_review_exception_does_not_apply_to_owned_documents():
    # A client.write holder with NO record scope for the owner is still denied an ALREADY-OWNED doc.
    hid = _household()
    doc = _doc(household_id=hid)
    admin = _principal(_user(), frozenset({"client.write"}))
    assert _in_scope(admin, doc) is False                       # normal record-scope rules apply
