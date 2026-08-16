"""New-entity detection & proposal: propose (never auto-create); approve/reject via canonical services."""
import hashlib
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import (
    audit_events,
    document_facts,
    documents,
    engine,
    households,
    people,
    person_source_links,
    relationship_entities,
    source_contacts,
)
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import document_entity_proposal as ep

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_DOCS: list = []
_PEOPLE: list = []
_SC: list = []
_LINKS: list = []
_ORGS: list = []
_HH: list = []

PRIN = Principal(1, "admin@t", "Admin",
                 frozenset({"client.write", "organization.write", "organization.read",
                            "record.write_all", "record.read_all"}))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(document_facts.delete().where(document_facts.c.document_id.in_(_DOCS)))
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _LINKS:
            c.execute(person_source_links.delete().where(person_source_links.c.id.in_(_LINKS)))
        if _SC:
            c.execute(source_contacts.delete().where(source_contacts.c.id.in_(_SC)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
        if _ORGS:
            c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(_ORGS)))
    for lst in (_DOCS, _PEOPLE, _SC, _LINKS, _ORGS, _HH):
        lst.clear()


def _person(full_name, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True)
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


def _doc(tmp_path, body, name="d.txt"):
    f = tmp_path / f"{uuid.uuid4().hex}.txt"
    f.write_text(body)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None, original_name=name,
            stored_name=f"ep-{_TAG}-{uuid.uuid4().hex}", storage_path=str(f), storage_uri=str(f),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive", "taxdome_folder": ""}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _prop_for(did):
    return next((p for p in ep.detect_new_entity_candidates() if p["document_id"] == did), None)


def _count(table):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(table)).scalar()


def _audit_count(did, action):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.entity_type == "document", audit_events.c.entity_id == str(did),
            audit_events.c.action == action)).scalar()


# --- detection -----------------------------------------------------------------------------------

def test_existing_entity_found_no_new_proposal(tmp_path):
    email = f"exist-{_TAG}@mail.com"
    _person(f"Existing {_A}", email=email)                 # matchable existing person
    did = _doc(tmp_path, f"Dear Existing {_A}, remit to {email}\n")
    assert _prop_for(did) is None                          # matched existing -> NOT a new-entity proposal


def test_strong_unknown_person_yields_proposal(tmp_path):
    email = f"newp-{_TAG}@mail.com"
    did = _doc(tmp_path, f"Dear Jackson {_A}, your statement. Contact {email}\n")
    p = _prop_for(did)
    assert p and p["entity_type"] == "person" and p["primary_name"] == f"Jackson {_A.capitalize()}"
    assert "email" in p["evidence"]["evidence_classes"]


def test_strong_unknown_business_yields_proposal(tmp_path):
    did = _doc(tmp_path, f"Invoice from Zorptech {_A.capitalize()} LLC for services rendered.\n")
    p = _prop_for(did)
    assert p and p["entity_type"] == "organization"
    assert p["primary_name"].lower().startswith("zorptech")


def test_household_evidence_yields_proposal(tmp_path):
    sur = f"Rivera{_A[:3]}"
    did = _doc(tmp_path, f"Taxpayer: John {sur}\nSpouse: Jane {sur}\nJoint return.\n")
    p = _prop_for(did)
    assert p and p["entity_type"] == "household"
    assert p["members"] and len(p["members"]) == 2


def test_weak_bare_name_no_proposal(tmp_path):
    did = _doc(tmp_path, f"Dear Solo{_A} regarding matters.\n")   # single token, no corroboration
    assert _prop_for(did) is None


def test_ambiguous_two_surnames_no_proposal(tmp_path):
    did = _doc(tmp_path, f"Taxpayer: John Alpha{_A[:3]}\nSpouse: Jane Beta{_A[:3]}\n")
    assert _prop_for(did) is None                          # different surnames -> ambiguous, no proposal


def test_duplicate_candidate_surfaced(tmp_path):
    sur = f"Duplo{_A[:3]}"
    _person(f"Existing {sur}")                             # same surname, different first name (no match)
    email = f"dup-{_TAG}@mail.com"
    did = _doc(tmp_path, f"Dear Marcus {sur}, remit to {email}\n")
    p = _prop_for(did)
    assert p and p["entity_type"] == "person"
    assert any(c["type"] == "person" for c in p["candidates"])   # existing same-surname surfaced


# --- approval / rejection (canonical creation, audit, no auto-create) ----------------------------

def test_approval_creates_exactly_one_person(tmp_path):
    email = f"appr-{_TAG}@mail.com"
    did = _doc(tmp_path, f"Dear Approvedguy {_A}, contact {email}\n")
    before = _count(people)
    r = ep.approve_proposal(did, "person", principal=PRIN, request_id="t")
    assert r["ok"] and r["created_entity_type"] == "person"
    _PEOPLE.append(r["created_entity_id"])                 # register for cleanup
    assert _count(people) == before + 1                    # EXACTLY one entity created
    assert _audit_count(did, "document.new_entity_approved") >= 1


def test_approval_creates_exactly_one_organization(tmp_path):
    did = _doc(tmp_path, f"Invoice from Quibix {_A.capitalize()} LLC.\n")
    before = _count(relationship_entities)
    r = ep.approve_proposal(did, "organization", principal=PRIN, request_id="t")
    assert r["ok"] and r["created_entity_type"] == "organization"
    _ORGS.append(r["created_entity_id"])
    assert _count(relationship_entities) == before + 1


def test_rejection_creates_no_entity_and_audits(tmp_path):
    email = f"rej-{_TAG}@mail.com"
    did = _doc(tmp_path, f"Dear Rejectguy {_A}, contact {email}\n")
    before = _count(people)
    r = ep.reject_proposal(did, principal=PRIN, request_id="t", reason="not a client")
    assert r["ok"] and _count(people) == before            # nothing created
    assert _audit_count(did, "document.new_entity_rejected") >= 1
    assert _prop_for(did) is None                          # rejected -> no longer proposed


def test_repeated_processing_no_duplicate_entity(tmp_path):
    email = f"once-{_TAG}@mail.com"
    did = _doc(tmp_path, f"Dear Onceonly {_A}, contact {email}\n")
    r1 = ep.approve_proposal(did, "person", principal=PRIN, request_id="t")
    _PEOPLE.append(r1["created_entity_id"])
    before = _count(people)
    r2 = ep.approve_proposal(did, "person", principal=PRIN, request_id="t")   # second attempt
    assert r2["ok"] is False and r2["reason"] == "already_decided"
    assert _count(people) == before                        # no second entity
    assert _prop_for(did) is None                          # decided -> not re-proposed


def test_rejected_document_not_reproposed_after_detection(tmp_path):
    email = f"norep-{_TAG}@mail.com"
    did = _doc(tmp_path, f"Dear Norepeat {_A}, contact {email}\n")
    assert _prop_for(did) is not None
    ep.reject_proposal(did, principal=PRIN, request_id="t")
    assert _prop_for(did) is None                          # retained rejection suppresses re-proposal


# --- authorization -------------------------------------------------------------------------------

def test_approval_routes_require_client_write():
    dep = require_capability("client.write")
    assert dep(principal=PRIN) is PRIN
    ordinary = Principal(2, "x@t", "Staff", frozenset({"client.read"}))
    with pytest.raises(HTTPException) as exc:
        dep(principal=ordinary)
    assert exc.value.status_code == 403


def test_routes_registered_under_admin():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/admin/documents/entity-proposals" in paths
    assert "/admin/documents/entity-proposals/approve" in paths
    assert "/admin/documents/entity-proposals/reject" in paths
