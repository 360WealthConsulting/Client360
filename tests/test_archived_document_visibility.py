"""Archived documents must not render on a client's file — on every client-facing read.

The acceptance run found the leak: ``services.documents.person_documents_clause`` has always
excluded archived rows, but ``document_platform.relationships`` and ``business_workspace`` filtered
only ``lifecycle.active_documents_clause``, which by design covers soft-delete alone. So the same
archived document was hidden from the count and shown in the list beside it, and shown on the
Documents tab of a person, a household and a business.

The fix is one shared predicate — ``lifecycle.active_unarchived_clause`` — used by every read that
answers "what documents does this client have". This module pins the behaviour on each of them.

Two markers, not one. Archiving is written two independent ways and neither writer touches the
other's column:

  * ``document_platform.service.archive``  -> ``status = 'archived'`` (+ ``archived_at``)
  * ``services.documents.archive_document`` -> ``archived = true``

Both are asserted, because a predicate that checks only one leaks exactly the way the two
soft-delete markers already leaked once. Only the boolean is populated in the deployment today,
which is precisely why the status half needs a test rather than a reader's trust.

What must NOT change is also pinned: soft-deleted rows stay hidden, active rows stay visible, and a
single-document read still delivers an archived document — archiving takes a document off the
client's list, it does not withdraw it.
"""
import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    relationship_entities,
)
from app.security.models import Principal
from app.services.business_workspace import get_business_workspace
from app.services.document_platform.relationships import (
    client_document,
    client_documents,
    documents_for_entity,
)
from app.services.documents import count_person_documents, get_document, get_person_documents

_TAG = "ARCHVIS"
_CAPS = frozenset({"client.read", "record.read_all", "documents.view"})


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            c.execute(documents.delete().where(documents.c.stored_name.like(f"%{_TAG}%")))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(relationship_entities)
                      .where(relationship_entities.c.name.like(f"%{_TAG}%")))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _principal():
    return Principal(0, "staff@e.test", "Staff", _CAPS)


def _household():
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{_TAG} Household")
                         .returning(households.c.id)).scalar_one()


def _person(household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Nadia", last_name=f"Okoro{_TAG}", full_name=f"Nadia Okoro {_TAG}",
            active=True, household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _business():
    with engine.begin() as c:
        return c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"Okoro Trading {_TAG}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()


def _doc(label, *, person_id=None, household_id=None, organization_id=None,
         archived=False, status="active", deleted_at=None, archived_at=None):
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=f"{label}.pdf", stored_name=f"{label}-{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{uuid.uuid4().hex}", storage_provider="Client360 Local",
            size_bytes=64, sha256=uuid.uuid4().hex * 2,
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            archived=archived, status=status, deleted_at=deleted_at, archived_at=archived_at,
            current_version=1, tags={},
        ).returning(documents.c.id)).scalar_one()


def _archived_pair(**anchor):
    """One document archived by EACH writer, so a predicate that checks a single marker fails.

    ``flag`` is what ``services.documents.archive_document`` writes; ``status`` is what
    ``document_platform.service.archive`` writes. Neither writer sets the other's column.
    """
    from datetime import UTC, datetime
    return {
        "flag": _doc("archived-by-flag", archived=True, **anchor),
        "status": _doc("archived-by-status", status="archived",
                       archived_at=datetime.now(UTC), **anchor),
    }


# --- archived rows are suppressed on every client-facing read ------------------------------------

def test_an_archived_person_document_does_not_render():
    pid = _person()
    active = _doc("active", person_id=pid)
    archived = _archived_pair(person_id=pid)
    for read in (get_person_documents(pid), client_documents(_principal(), "person", pid)):
        ids = {d["id"] for d in read}
        assert active in ids
        assert archived["flag"] not in ids, "archived=true still rendering"
        assert archived["status"] not in ids, "status='archived' still rendering"


def test_an_archived_household_document_does_not_render():
    hid = _household()
    pid = _person(household_id=hid)
    active = _doc("active", household_id=hid)
    archived = _archived_pair(household_id=hid)
    reads = (get_person_documents(pid),                                  # the member's page
             client_documents(_principal(), "person", pid),
             client_documents(_principal(), "household", hid))           # the household's page
    for read in reads:
        ids = {d["id"] for d in read}
        assert active in ids
        assert not ({archived["flag"], archived["status"]} & ids)


def test_an_archived_business_document_does_not_render():
    biz = _business()
    active = _doc("active", organization_id=biz)
    archived = _archived_pair(organization_id=biz)
    reads = (documents_for_entity(_principal(), "organization", biz),
             client_documents(_principal(), "organization", biz),
             get_business_workspace(biz)["documents"])
    for read in reads:
        ids = {d["id"] for d in read}
        assert active in ids
        assert not ({archived["flag"], archived["status"]} & ids)


def test_the_preview_drawer_cannot_open_an_archived_document():
    """``client_document`` is the membership test the drawer authorises on. If it disagreed with the
    list, a row the list hides would still open by id."""
    pid = _person()
    archived = _archived_pair(person_id=pid)
    active = _doc("active", person_id=pid)
    assert client_document(_principal(), "person", pid, active)["id"] == active
    assert client_document(_principal(), "person", pid, archived["flag"]) is None
    assert client_document(_principal(), "person", pid, archived["status"]) is None


# --- counts and lists agree ----------------------------------------------------------------------

def test_the_document_count_and_the_document_list_agree_about_archived_rows():
    """The defect's most visible symptom: a client page whose count and list disagreed."""
    hid = _household()
    pid = _person(household_id=hid)
    _doc("active-person", person_id=pid)
    _doc("active-household", household_id=hid)
    _archived_pair(person_id=pid)
    _archived_pair(household_id=hid)
    assert count_person_documents(pid) == len(get_person_documents(pid)) == 2


def test_the_business_document_count_matches_its_list():
    biz = _business()
    _doc("active", organization_id=biz)
    _archived_pair(organization_id=biz)
    ws = get_business_workspace(biz)
    assert ws["document_count"] == len(ws["documents"]) == 1


# --- what must NOT change ------------------------------------------------------------------------

def test_soft_deleted_documents_are_still_suppressed():
    """Both delete markers, including the half-written row the census found 49 of."""
    from datetime import UTC, datetime
    hid = _household()
    pid = _person(household_id=hid)
    active = _doc("active", person_id=pid)
    deleted = _doc("deleted", person_id=pid, status="deleted", deleted_at=datetime.now(UTC))
    half = _doc("half-deleted", person_id=pid, status="active", deleted_at=datetime.now(UTC))
    for read in (get_person_documents(pid), client_documents(_principal(), "person", pid),
                 client_documents(_principal(), "household", hid)):
        ids = {d["id"] for d in read}
        assert active in ids
        assert not ({deleted, half} & ids)


def test_active_documents_still_render_on_every_read():
    hid = _household()
    pid = _person(household_id=hid)
    biz = _business()
    person_doc = _doc("active-person", person_id=pid)
    household_doc = _doc("active-household", household_id=hid)
    business_doc = _doc("active-business", organization_id=biz)
    assert {person_doc, household_doc} == {d["id"] for d in get_person_documents(pid)}
    assert {person_doc, household_doc} <= {
        d["id"] for d in client_documents(_principal(), "household", hid)}
    assert business_doc in {d["id"] for d in client_documents(_principal(), "organization", biz)}
    assert client_document(_principal(), "person", pid, person_doc)["id"] == person_doc


def test_an_archived_document_is_still_retrievable_by_id():
    """Archiving takes a document OFF the client's list; it does not withdraw the document. The
    single-document read — which every delivery entry point uses — is deliberately unchanged."""
    pid = _person()
    archived = _archived_pair(person_id=pid)
    assert get_document(archived["flag"])["id"] == archived["flag"]
    assert get_document(archived["status"])["id"] == archived["status"]


def test_the_row_is_untouched_by_being_filtered_out():
    """The fix is a read predicate. It must not archive, unarchive or otherwise write anything."""
    pid = _person()
    archived = _archived_pair(person_id=pid)
    get_person_documents(pid)
    client_documents(_principal(), "person", pid)
    with engine.connect() as c:
        rows = c.execute(select(documents.c.id, documents.c.archived, documents.c.status)
                         .where(documents.c.id.in_(list(archived.values())))).mappings().all()
    by_id = {r["id"]: r for r in rows}
    assert by_id[archived["flag"]]["archived"] is True
    assert by_id[archived["flag"]]["status"] == "active"
    assert by_id[archived["status"]]["archived"] is False
    assert by_id[archived["status"]]["status"] == "archived"


def test_the_delete_only_predicate_still_admits_archived_documents():
    """The document library is NOT a client list: ``service.list_documents`` keeps the delete-only
    predicate so staff can still ask for archived rows by status. The two clauses must therefore
    differ exactly by the archived rows, and by nothing else."""
    from app.services.document_platform.lifecycle import (
        active_documents_clause,
        active_unarchived_clause,
    )
    pid = _person()
    archived = _archived_pair(person_id=pid)
    active = _doc("active", person_id=pid)
    mine = documents.c.stored_name.like(f"%{_TAG}%")
    with engine.connect() as c:
        undeleted = set(c.scalars(select(documents.c.id).where(active_documents_clause(), mine)))
        listable = set(c.scalars(select(documents.c.id).where(active_unarchived_clause(), mine)))
    assert set(archived.values()) <= undeleted, "archived rows still exist and are not deleted"
    assert undeleted - listable == set(archived.values())
    assert listable == {active}


def test_counts_are_unaffected_for_a_client_with_nothing_archived():
    """A client with no archived documents must see exactly the behaviour they saw before."""
    hid = _household()
    pid = _person(household_id=hid)
    ids = {_doc("a", person_id=pid), _doc("b", household_id=hid), _doc("c", household_id=hid)}
    assert {d["id"] for d in get_person_documents(pid)} == ids
    assert count_person_documents(pid) == 3
    assert ids <= {d["id"] for d in client_documents(_principal(), "person", pid)}


def test_the_client_reads_use_the_shared_predicate_rather_than_respelling_it():
    """Pins the convention itself. Re-spelling the pair at each call site is exactly how the two
    definitions drifted apart, so each client-read module must import the shared clause — and the
    relationships module, whose every read is client-facing, must not still hold the delete-only
    one."""
    from app.services import business_workspace
    from app.services import documents as documents_service
    from app.services.document_platform import relationships

    for module in (relationships, business_workspace, documents_service):
        assert hasattr(module, "active_unarchived_clause"), \
            f"{module.__name__} does not use the shared predicate"
    assert not hasattr(relationships, "active_documents_clause"), \
        "a client read still filters soft-delete only"
