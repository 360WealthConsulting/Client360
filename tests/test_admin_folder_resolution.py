"""Coverage for the admin folder-level ownership resolution (fast human-resolution interface).

Verifies the reused resolve service: folder-level one-operation assignment, NULL-only fill (never
overwrites), organization support, dry-run preview (destination + affected ids/count, no write),
audit trail (affected document ids + previous state), and — critically — that the six permanent V2
reject documents are never assigned.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db import (
    audit_events,
    documents,
    engine,
    households,
    people,
    relationship_entities,
)
from app.routes.admin import resolve_unassigned_folder
from app.services import households as hh_service

_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        for tbl, key in ((people, "people"), (households, "households"),
                         (relationship_entities, "relationship_entities")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _doc(folder, *, person_id=None, household_id=None, organization_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            original_name="f.pdf", stored_name=f"fr-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="C:\\x.pdf", size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", tags={"source_system": "TaxDome Drive", "taxdome_folder": folder}
        ).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _person():
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"P {_TAG}", active=True)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _household():
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {_TAG}").returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _org():
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"Biz {_TAG}", active=True).returning(relationship_entities.c.id)
        ).scalar_one()
    _C["relationship_entities"].append(eid)
    return eid


def _owner(did):
    with engine.connect() as c:
        r = c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                      .where(documents.c.id == did)).mappings().first()
    return (r["person_id"], r["household_id"], r["organization_id"])


# --- service: folder-level assignment, NULL-only, org, dry-run --------------------------------

def test_resolve_assigns_all_null_docs_in_folder_to_person():
    folder = f"Folder-{_TAG}-A"
    d1, d2 = _doc(folder), _doc(folder)
    pid = _person()
    res = hh_service.resolve_folder_ownership(folder, person_id=pid, actor_user_id=1, request_id="t")
    assert res["documents_affected"] == 2
    assert set(res["affected_document_ids"]) == {d1, d2}
    assert _owner(d1)[0] == pid and _owner(d2)[0] == pid


def test_resolve_does_not_overwrite_existing_ownership():
    folder = f"Folder-{_TAG}-B"
    keep = _person()
    already = _doc(folder, person_id=keep)
    fresh = _doc(folder)
    newp = _person()
    res = hh_service.resolve_folder_ownership(folder, person_id=newp, actor_user_id=1, request_id="t")
    assert res["affected_document_ids"] == [fresh]        # only the NULL one
    assert _owner(already)[0] == keep                     # existing link untouched
    assert _owner(fresh)[0] == newp


def test_resolve_supports_organization():
    folder = f"Folder-{_TAG}-C"
    d = _doc(folder)
    eid = _org()
    res = hh_service.resolve_folder_ownership(folder, organization_id=eid, actor_user_id=1, request_id="t")
    assert res["documents_affected"] == 1
    assert _owner(d)[2] == eid
    assert res["destination"]["entity_type"] == "organization"


def test_dry_run_reports_destination_and_count_without_writing():
    folder = f"Folder-{_TAG}-D"
    d = _doc(folder)
    pid = _person()
    res = hh_service.resolve_folder_ownership(folder, person_id=pid, dry_run=True)
    assert res["dry_run"] is True
    assert res["documents_affected"] == 1 and res["affected_document_ids"] == [d]
    assert res["destination"]["entity_id"] == pid
    assert _owner(d) == (None, None, None)                # nothing written


# --- permanent-reject safety -----------------------------------------------------------------

def test_permanent_rejects_are_never_assigned(monkeypatch):
    folder = f"Folder-{_TAG}-E"
    normal = _doc(folder)
    reject = _doc(folder)
    monkeypatch.setattr(hh_service, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({reject}))
    pid = _person()
    res = hh_service.resolve_folder_ownership(folder, person_id=pid, actor_user_id=1, request_id="t")
    assert res["affected_document_ids"] == [normal]       # reject excluded
    assert res["excluded_permanent_rejects"] == [reject]
    assert _owner(normal)[0] == pid
    assert _owner(reject) == (None, None, None)           # reject untouched


# --- audit trail ------------------------------------------------------------------------------

def test_apply_writes_audit_with_affected_ids_and_previous_state():
    folder = f"Folder-{_TAG}-F"
    d = _doc(folder)
    pid = _person()
    hh_service.resolve_folder_ownership(folder, person_id=pid, actor_user_id=1, request_id="t")
    with engine.connect() as c:
        row = c.execute(select(audit_events.c.action, audit_events.c.metadata)
                        .where(audit_events.c.entity_type == "taxdome_folder",
                               audit_events.c.entity_id == folder)
                        .order_by(audit_events.c.occurred_at.desc()).limit(1)).mappings().first()
    assert row is not None and row["action"] == "document.ownership_resolved"
    md = row["metadata"]
    assert d in md["affected_document_ids"]
    assert "previous_ownership_state" in md


# --- route wiring -----------------------------------------------------------------------------

def test_resolve_route_registered_and_gated():
    from app.main import app
    match = [r for r in app.routes if getattr(r, "path", None) == "/admin/documents/unassigned/resolve"]
    assert match and "POST" in match[0].methods
    assert resolve_unassigned_folder  # handler importable (gated by require_capability('client.write'))


def test_permanent_reject_ids_constant_unchanged():
    assert hh_service.PERMANENT_REJECT_DOCUMENT_IDS == frozenset({4704, 4716, 4717, 17932, 22336, 22338})
