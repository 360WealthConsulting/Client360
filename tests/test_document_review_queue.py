"""Phase 3 — MEDIUM + AMBIGUOUS review queue: candidate buttons, existing atomic write, no auto-assign."""
import hashlib
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import (
    audit_events,
    documents,
    engine,
    people,
    person_source_links,
    source_contacts,
)
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import document_review_queue as rq

_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "abcdefghij")).capitalize()
# Alphabetic + capitalised so names built as f"First {_TAG}" are extractable by the content
# name matcher. A hex tag ("Jennifer a1b2c3d4") is not a name the extractor can see, so these
# fixtures used to reach HIGH on the email alone — the exact rule the safety patch removed.
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_DOCS: list = []
_PEOPLE: list = []
_SC: list = []
_LINKS: list = []

PRIN = Principal(1, "admin@t", "Admin", frozenset({"client.write", "record.write_all", "record.read_all"}))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _LINKS:
            c.execute(person_source_links.delete().where(person_source_links.c.id.in_(_LINKS)))
        if _SC:
            c.execute(source_contacts.delete().where(source_contacts.c.id.in_(_SC)))
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    for lst in (_DOCS, _PEOPLE, _SC, _LINKS):
        lst.clear()


def _person(full_name, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    if email:
        with engine.begin() as c:
            sid = c.execute(source_contacts.insert().values(
                source_system="TaxDome", source_file="t.zip", source_record_id=uuid.uuid4().hex,
                source_hash=uuid.uuid4().hex, email=email, raw_data={}
            ).returning(source_contacts.c.id)).scalar_one()
            lid = c.execute(person_source_links.insert().values(
                person_id=pid, source_contact_id=sid, match_method="email", confirmed=True
            ).returning(person_source_links.c.id)).scalar_one()
        _SC.append(sid); _LINKS.append(lid)
    return pid


def _doc(tmp_path, body, name="d.txt", person_id=None):
    f = tmp_path / f"{uuid.uuid4().hex}.txt"
    f.write_text(body)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"rq-{_TAG}-{uuid.uuid4().hex}", storage_path=str(f), storage_uri=str(f),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive"}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id).where(documents.c.id == did)).first())


def _find(bucket, did):
    return next((r for r in bucket if r["document_id"] == did), None)


def _audit(did, action):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.entity_type == "document", audit_events.c.entity_id == str(did),
            audit_events.c.action == action)).scalar()


# --- queue contents --------------------------------------------------------------------------------

def test_medium_shows_direct_proposed_candidate(tmp_path):
    pid = _person(f"Zephyrina {_A}")                       # name in index, NO email -> name-only MEDIUM
    did = _doc(tmp_path, f"2021 Year-End Statement for Zephyrina {_A}\n")
    q = rq.review_queue()
    row = _find(q["medium"], did)
    assert row and len(row["candidates"]) == 1 and row["candidates"][0]["id"] == pid
    assert row["confidence"] == "MEDIUM"                   # view_url is attached by the route, not here
    assert _owner(did) == (None, None, None)               # READ-ONLY


def test_ambiguous_shows_multiple_candidates_no_default(tmp_path):
    p1 = _person(f"John Smith{_A[:3]}")
    p2 = _person(f"John Smith{_A[:3]}")                     # duplicate name -> ambiguous
    did = _doc(tmp_path, f"Prepared for John Smith{_A[:3]}\n")
    row = _find(rq.review_queue()["ambiguous"], did)
    assert row and {c["id"] for c in row["candidates"]} == {p1, p2}   # all candidates, no single default


def test_view_url_uses_existing_authorized_route(tmp_path):
    from app.routes.admin import _view_url
    pid = _person(f"Viewy {_A}")  # noqa: F841 — seeds the MEDIUM match
    did = _doc(tmp_path, f"2020 Year-End Statement for Viewy {_A}\n")
    row = _find(rq.review_queue()["medium"], did)
    # the route attaches this exact authorized URL (same helper /admin/documents/unassigned/review uses)
    assert _view_url(did, row["filename"]).startswith("/documents/")


# --- approval via the existing atomic write path ---------------------------------------------------

def test_approve_assigns_and_audits(tmp_path):
    pid = _person(f"Approvia {_A}")
    did = _doc(tmp_path, f"2019 Form 1040 for Approvia {_A}\n")
    r = rq.approve_ownership(did, "person", pid, principal=PRIN, request_id="t")
    assert r["ok"] and _owner(did) == (pid, None, None)
    assert _audit(did, "document.ownership_resolved") >= 1   # existing ownership-resolution audit


def test_approve_already_owned_is_rejected_safely(tmp_path):
    keeper = _person(f"Keeper {_A}")
    other = _person(f"Other {_A}")
    did = _doc(tmp_path, "anything\n", person_id=keeper)     # already owned
    r = rq.approve_ownership(did, "person", other, principal=PRIN, request_id="t")
    assert r["ok"] is False
    assert _owner(did) == (keeper, None, None)               # ownership NOT overwritten


def test_queue_read_only_no_auto_assignment(tmp_path):
    _person(f"Noassign {_A}")
    did = _doc(tmp_path, f"2018 Form 1040 for Noassign {_A}\n")
    rq.review_queue()
    rq.review_queue()                                        # repeated read
    assert _owner(did) == (None, None, None)


# --- isolation from other sets ---------------------------------------------------------------------

def test_high_clean_not_in_review_queue(tmp_path):
    # a clean HIGH (email match) belongs to the bulk-confirm set, not the review queue
    email = f"high-{_TAG}@mail.com"
    full_name = f"Highperson {_A}"
    _person(full_name, email=email)
    # Names the owner AND carries their unique email — the evidence a clean HIGH now requires.
    did = _doc(tmp_path, f"Statement for {full_name}\nremit to {email}\n")
    q = rq.review_queue()
    assert _find(q["medium"], did) is None and _find(q["ambiguous"], did) is None
    assert _find(q["high_review"], did) is None
    assert _owner(did) == (None, None, None)                 # HIGH bulk-confirm set untouched


def test_no_match_new_entity_not_in_review_queue(tmp_path):
    # a NO_MATCH doc (new-entity candidate) is not a review-queue row
    did = _doc(tmp_path, f"Dear Brandnew {_A}, contact new-{_TAG}@x.com\n")
    q = rq.review_queue()
    assert all(_find(q[b], did) is None for b in ("medium", "ambiguous", "high_review"))


# --- authorization ---------------------------------------------------------------------------------

def test_review_routes_require_client_write():
    dep = require_capability("client.write")
    assert dep(principal=PRIN) is PRIN
    ordinary = Principal(2, "x@t", "Staff", frozenset({"client.read"}))
    with pytest.raises(HTTPException) as exc:
        dep(principal=ordinary)
    assert exc.value.status_code == 403


def test_routes_registered_under_admin():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/admin/documents/review-queue" in paths
    assert "/admin/documents/review-queue/approve" in paths
