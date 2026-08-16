"""Phase 4 review surface — CONTEXT_HIGH + CONTEXT_LIKELY only: candidates, live-recheck approve, safety."""
import hashlib
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import audit_events, documents, engine, households, people
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import document_nomatch_analysis as na

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_DOCS: list = []
_PEOPLE: list = []
_HH: list = []

PRIN = Principal(1, "admin@t", "Admin", frozenset({"client.write", "record.write_all", "record.read_all"}))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    for lst in (_DOCS, _PEOPLE, _HH):
        lst.clear()


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _person(full_name, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True, household_id=household_id)
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(tmp_path, body, *, folder, person_id=None, household_id=None, name="d.txt"):
    f = tmp_path / f"{uuid.uuid4().hex}.txt"
    f.write_text(body)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=None, original_name=name,
            stored_name=f"cr-{_TAG}-{uuid.uuid4().hex}", storage_path=str(f), storage_uri=str(f),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive", "taxdome_folder": folder}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id).where(documents.c.id == did)).first())


def _audit(did, action):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.entity_type == "document", audit_events.c.entity_id == str(did),
            audit_events.c.action == action)).scalar()


def _in(bucket_list, did):
    return next((r for r in bucket_list if r["document_id"] == did), None)


# --- candidate surface -----------------------------------------------------------------------------

def test_context_high_appears_with_one_approve_owner(tmp_path):
    folder = f"F-{_A}-A"
    owner = _person(f"Owner {_A}")
    _doc(tmp_path, "resolved\n", folder=folder, person_id=owner)
    did = _doc(tmp_path, "sibling, no identity\n", folder=folder)
    data = na.context_candidates()
    row = _in(data["context_high"], did)
    assert row and (row["proposed_owner_type"], row["proposed_owner_id"]) == ("person", owner)
    assert _in(data["context_likely"], did) is None
    assert _owner(did) == (None, None, None)                   # READ-ONLY


def test_context_likely_appears_lower_confidence(tmp_path):
    folder = f"F-{_A}-B"
    hid = _household(f"{_A} Household")
    a = _person(f"Aspouse {_A}", household_id=hid)
    b = _person(f"Bspouse {_A}", household_id=hid)
    _doc(tmp_path, "a\n", folder=folder, person_id=a)
    _doc(tmp_path, "b\n", folder=folder, person_id=b)
    did = _doc(tmp_path, "joint, no identity\n", folder=folder)
    data = na.context_candidates()
    row = _in(data["context_likely"], did)
    assert row and row["proposed_owner_type"] == "household"
    assert _in(data["context_high"], did) is None


def test_conflict_general_and_new_entity_excluded(tmp_path):
    # CONFLICT
    fc = f"F-{_A}-C"
    p1, p2 = _person(f"U1 {_A}"), _person(f"U2 {_A}")
    _doc(tmp_path, "1\n", folder=fc, person_id=p1)
    _doc(tmp_path, "2\n", folder=fc, person_id=p2)
    d_conf = _doc(tmp_path, "ambiguous\n", folder=fc)
    d_gen = _doc(tmp_path, "office supplies\n", folder=f"General-{_A}")
    d_new = _doc(tmp_path, f"Dear Brandnew {_A}, contact new-{_TAG}@x.com\n", folder=f"Intake-{_A}")
    data = na.context_candidates()
    ids = {r["document_id"] for r in data["context_high"] + data["context_likely"]}
    assert d_conf not in ids and d_gen not in ids and d_new not in ids


def test_view_url_uses_existing_authorized_route(tmp_path):
    from app.routes.admin import _view_url
    folder = f"F-{_A}-V"
    _doc(tmp_path, "resolved\n", folder=folder, person_id=_person(f"Vowner {_A}"))
    did = _doc(tmp_path, "sibling\n", folder=folder)
    row = _in(na.context_candidates()["context_high"], did)
    assert _view_url(did, row["filename"]).startswith("/documents/")


# --- approval via existing atomic path -------------------------------------------------------------

def test_approve_assigns_and_audits(tmp_path):
    folder = f"F-{_A}-AP"
    owner = _person(f"Approvia {_A}")
    _doc(tmp_path, "resolved\n", folder=folder, person_id=owner)
    did = _doc(tmp_path, "sibling\n", folder=folder)
    r = na.approve_context(did, "person", owner, principal=PRIN, request_id="t")
    assert r["ok"] and _owner(did) == (owner, None, None)
    assert _audit(did, "document.ownership_resolved") >= 1


def test_approve_stale_context_rejected(tmp_path):
    # a GENERAL doc is not an assignable A/B proposal -> stale_context
    did = _doc(tmp_path, "office supplies\n", folder=f"General-{_A}")
    r = na.approve_context(did, "person", 123456, principal=PRIN, request_id="t")
    assert r["ok"] is False and r["reason"] == "stale_context"
    assert _owner(did) == (None, None, None)


def test_approve_owner_changed_rejected(tmp_path):
    folder = f"F-{_A}-OC"
    owner = _person(f"Realowner {_A}")
    _doc(tmp_path, "resolved\n", folder=folder, person_id=owner)
    did = _doc(tmp_path, "sibling\n", folder=folder)
    r = na.approve_context(did, "person", owner + 999999, principal=PRIN, request_id="t")  # wrong owner
    assert r["ok"] is False and r["reason"] == "owner_changed"
    assert _owner(did) == (None, None, None)


def test_approve_already_owned_rejected_no_overwrite(tmp_path):
    folder = f"F-{_A}-OWN"
    keeper = _person(f"Keeper {_A}")
    owner = _person(f"Folderowner {_A}")
    _doc(tmp_path, "resolved\n", folder=folder, person_id=owner)
    did = _doc(tmp_path, "already owned\n", folder=folder, person_id=keeper)   # already owned
    r = na.approve_context(did, "person", owner, principal=PRIN, request_id="t")
    assert r["ok"] is False                                    # not an eligible NO_MATCH -> stale_context
    assert _owner(did) == (keeper, None, None)                 # ownership NOT overwritten


# --- authorization ---------------------------------------------------------------------------------

def test_review_routes_require_client_write():
    dep = require_capability("client.write")
    assert dep(principal=PRIN) is PRIN
    with pytest.raises(HTTPException) as exc:
        dep(principal=Principal(2, "x@t", "Staff", frozenset({"client.read"})))
    assert exc.value.status_code == 403


def test_routes_registered_under_admin():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/admin/documents/context-review" in paths
    assert "/admin/documents/context-review/approve" in paths


# --- reconciliation (Part 1) -----------------------------------------------------------------------

def test_reconcile_counts_unsupported_as_phase4_nomatch(tmp_path):
    # a doc with no extractable text: batch route == UNSUPPORTED, Phase-4 confidence == NO_MATCH
    from scripts.reconcile_nomatch_delta import reconcile
    did = _doc(tmp_path, "irrelevant\n", folder=f"F-{_A}-U", name="scan.heic")  # heic + no OCR cache
    res = reconcile()
    delta_ids = {r["document_id"] for r in res["delta_rows"]}
    assert did in delta_ids                                     # counted as the NO_MATCH-vs-UNSUPPORTED delta
    assert res["phase4_nomatch"] == res["batch_nomatch"] + res["delta_unsupported"]
