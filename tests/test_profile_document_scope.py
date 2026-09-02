"""The client profile must agree with itself about whether a client has documents.

Michael White's profile showed "No client documents" and `Documents: None` while the very same
page listed five of his documents. The cause was two different definitions of "this client's
documents": the LIST used the person+household union with both safety filters, while the COUNT in
``client_summary`` used person-anchored rows only and omitted the soft-delete check. A client whose
paperwork hangs off their household — which is the normal shape for a joint return or a family
organiser — therefore reported zero.

These tests pin the shared definition (``documents.person_documents_clause`` /
``count_person_documents``) so a count, a list and an alert can never disagree again, and so the
deleted/archived safety semantics stay enforced on the counting path too. Temp/test rows only.
"""
import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, people
from app.services.client_alerts import build_client_alerts
from app.services.client_summary import get_client_summary
from app.services.documents import count_person_documents, get_person_documents

_TAG = "PROFDOCSCOPE"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            c.execute(documents.delete().where(documents.c.stored_name.like(f"%{_TAG}%")))
            if pids:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _household():
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{_TAG} Household")
                         .returning(households.c.id)).scalar_one()


def _person(household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Michael", last_name="White", full_name=f"Michael White {_TAG}",
            active=True, household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _doc(name, *, person_id=None, household_id=None, status="active",
         deleted_at=None, archived=False):
    import uuid
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/seed/{_TAG}/{name}", storage_provider="Client360 Local",
            size_bytes=1024, sha256=uuid.uuid4().hex * 2,
            person_id=person_id, household_id=household_id,
            status=status, deleted_at=deleted_at, archived=archived,
            review_status="none", current_version=1,
        ).returning(documents.c.id)).scalar_one()


# --- the reported defect -------------------------------------------------------------------

def test_household_owned_documents_count_towards_the_person():
    """The acceptance case: zero person-anchored documents, real household-anchored ones."""
    hid = _household()
    pid = _person(household_id=hid)
    _doc("2021 Joint Return.pdf", household_id=hid)
    _doc("Organizer.pdf", household_id=hid)

    assert count_person_documents(pid) == 2
    assert get_client_summary(pid)["document_count"] == 2
    # The list and the count must describe the same set.
    assert len(get_person_documents(pid)) == 2


def test_no_client_documents_alert_does_not_fire_on_household_documents():
    """The visible symptom: the alert contradicted the list on the same screen."""
    hid = _household()
    pid = _person(household_id=hid)
    _doc("2021 Joint Return.pdf", household_id=hid)

    titles = {a["title"] for a in build_client_alerts(get_client_summary(pid))}
    assert "No client documents" not in titles


def test_alert_still_fires_for_a_client_with_no_documents():
    """The fix must not silence a TRUE negative — an empty client still says so."""
    pid = _person(household_id=_household())

    assert count_person_documents(pid) == 0
    titles = {a["title"] for a in build_client_alerts(get_client_summary(pid))}
    assert "No client documents" in titles


# --- safety semantics on the counting path -------------------------------------------------

def test_soft_deleted_household_document_is_not_counted():
    """The old count filtered `archived` alone, so a deleted document would have counted."""
    from datetime import UTC, datetime
    hid = _household()
    pid = _person(household_id=hid)
    _doc("Deleted Return.pdf", household_id=hid, status="deleted", deleted_at=datetime.now(UTC))
    # deleted_at written without the status flip — the production inconsistency.
    _doc("Half Deleted.pdf", household_id=hid, status="active", deleted_at=datetime.now(UTC))

    assert count_person_documents(pid) == 0
    assert get_client_summary(pid)["document_count"] == 0


def test_archived_household_document_is_not_counted():
    hid = _household()
    pid = _person(household_id=hid)
    _doc("Archived Note.pdf", household_id=hid, archived=True)

    assert count_person_documents(pid) == 0


def test_person_anchored_documents_still_count():
    """The union must not have replaced the person anchor, only widened past it."""
    pid = _person()
    _doc("Personal ID.pdf", person_id=pid)

    assert count_person_documents(pid) == 1
    assert get_client_summary(pid)["document_count"] == 1


def test_count_and_list_agree_on_a_mixed_set():
    """One client, documents on both anchors plus rows that must never be seen."""
    from datetime import UTC, datetime
    hid = _household()
    pid = _person(household_id=hid)
    _doc("Household Return.pdf", household_id=hid)
    _doc("Personal ID.pdf", person_id=pid)
    _doc("Gone.pdf", household_id=hid, status="deleted", deleted_at=datetime.now(UTC))
    _doc("Filed Away.pdf", household_id=hid, archived=True)

    names = {d["original_name"] for d in get_person_documents(pid)}
    assert names == {"Household Return.pdf", "Personal ID.pdf"}
    assert count_person_documents(pid) == len(names) == 2
    assert get_client_summary(pid)["document_count"] == 2


def test_another_households_documents_are_not_counted():
    """Widening to the household must not widen to the firm."""
    mine = _household()
    theirs = _household()
    pid = _person(household_id=mine)
    _doc("Someone Elses.pdf", household_id=theirs)

    assert count_person_documents(pid) == 0
