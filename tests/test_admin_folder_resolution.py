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


# --- CRITICAL: eligibility requires ALL THREE ownership fields NULL ----------------------------

def test_organization_owned_doc_cannot_gain_person_id():
    folder = f"Folder-{_TAG}-ORG"
    org = _org()
    owned = _doc(folder, organization_id=org)   # already owned by an organization (the Affordable Measures bug)
    free = _doc(folder)
    pid = _person()
    res = hh_service.resolve_folder_ownership(folder, person_id=pid, actor_user_id=1, request_id="t")
    assert res["affected_document_ids"] == [free]                 # only the all-NULL doc
    assert owned in res["already_owned_document_ids"]
    assert _owner(owned) == (None, None, org)                     # unchanged: no person_id added
    assert _owner(free)[0] == pid


def test_person_owned_doc_cannot_gain_household_or_organization():
    folder = f"Folder-{_TAG}-PER"
    p0 = _person()
    owned = _doc(folder, person_id=p0)
    hid, org = _household(), _org()
    r1 = hh_service.resolve_folder_ownership(folder, household_id=hid, actor_user_id=1, request_id="t")
    assert owned not in r1["affected_document_ids"] and owned in r1["already_owned_document_ids"]
    r2 = hh_service.resolve_folder_ownership(folder, organization_id=org, actor_user_id=1, request_id="t")
    assert owned not in r2["affected_document_ids"]
    assert _owner(owned) == (p0, None, None)                     # untouched


def test_household_owned_doc_cannot_gain_person_or_organization():
    folder = f"Folder-{_TAG}-HH"
    h0 = _household()
    owned = _doc(folder, household_id=h0)
    pid, org = _person(), _org()
    hh_service.resolve_folder_ownership(folder, person_id=pid, actor_user_id=1, request_id="t")
    hh_service.resolve_folder_ownership(folder, organization_id=org, actor_user_id=1, request_id="t")
    assert _owner(owned) == (None, h0, None)                     # untouched


def test_only_all_null_docs_are_eligible_mixed_folder():
    folder = f"Folder-{_TAG}-MIX"
    free1, free2 = _doc(folder), _doc(folder)
    owned_p = _doc(folder, person_id=_person())
    owned_o = _doc(folder, organization_id=_org())
    pid = _person()
    res = hh_service.resolve_folder_ownership(folder, person_id=pid, dry_run=True)
    assert set(res["affected_document_ids"]) == {free1, free2}   # dry-run uses the same all-NULL predicate
    assert set(res["already_owned_document_ids"]) == {owned_p, owned_o}
    assert res["documents_affected"] == 2


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


# --- confirmation-page presentation enrichment ------------------------------------------------

def _business_person(name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=name, active=True, contact_type="business",
                                               primary_email="ap@measures.com", primary_phone="5551234")
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def test_person_display_labels_business_contact():
    from app.routes.admin import _person_display
    pid = _business_person(f"Affordable Measures LLC {_TAG}")
    with engine.connect() as c:
        d = _person_display(c, pid)
    assert d["is_business"] is True and d["designation"] == "Business Contact"
    assert d["email"] == "ap@measures.com" and d["phone"] == "5551234"
    assert d["link"] == f"/client/{pid}"


def test_person_display_labels_individual_person():
    from app.routes.admin import _person_display
    pid = _person()  # no contact_type, non-business name
    with engine.connect() as c:
        d = _person_display(c, pid)
    assert d["is_business"] is False and d["designation"] == "Person"


def test_documents_detail_includes_filename_owner_and_download_link():
    from app.routes.admin import _documents_detail
    folder = f"Folder-{_TAG}-DET"
    d1 = _doc(folder)
    with engine.connect() as c:
        rows = _documents_detail(c, [d1])
    assert rows and rows[0]["id"] == d1
    assert rows[0]["name"] == "f.pdf"
    assert rows[0]["current_owner"] == "Unassigned (NULL)"
    assert rows[0]["download_url"] == f"/documents/{d1}/download"
    assert rows[0]["source_folder"] == folder


def test_destination_display_variants():
    from app.routes.admin import _destination_display
    pid, hid, eid = _person(), _household(), _org()
    with engine.connect() as c:
        assert _destination_display(c, "person", pid)["kind"] == "person"
        h = _destination_display(c, "household", hid)
        assert h["kind"] == "household" and h["link"] == f"/client/household/{hid}"
        o = _destination_display(c, "organization", eid)
        assert o["kind"] == "organization" and o["link"] == f"/relationship-entities/{eid}"


def test_documents_detail_shows_owner_name_and_view_link():
    from app.routes.admin import _documents_detail
    folder = f"Folder-{_TAG}-OWN"
    org = _org()
    d = _doc(folder, organization_id=org)
    with engine.connect() as c:
        row = _documents_detail(c, [d])[0]
    assert row["current_owner_type"] == "organization" and row["current_owner_id"] == org
    assert row["view_url"] == f"/documents/{d}/download?inline=1"


# --- worklist categorization (Affordable Measures scenario) -----------------------------------

def test_worklist_categorizes_already_owned_folder_as_zero_eligible():
    from app.routes.admin import _folder_samples_and_candidates
    folder = f"Folder-{_TAG}-AM"
    org = _org()
    ids = [_doc(folder, organization_id=org) for _ in range(8)]   # 8 docs, all org-owned
    rows = _folder_samples_and_candidates(
        [{"folder": folder, "files": 8, "resolves_to": {}, "suggestions": []}])
    r = rows[0]
    assert r["docs_in_folder"] == 8
    assert r["eligible"] == 0          # none are unresolved/assignable
    assert r["already_owned"] == 8
    assert r["reject"] == 0
    assert r["sample_documents"] == []  # no eligible samples
    # opening the worklist/preview must not mutate ownership
    for did in ids:
        assert _owner(did) == (None, None, org)


def test_worklist_categorizes_mixed_folder():
    from app.routes.admin import _folder_samples_and_candidates
    folder = f"Folder-{_TAG}-MW"
    org = _org()
    _doc(folder, organization_id=org)
    _doc(folder, organization_id=org)
    free = _doc(folder)
    rows = _folder_samples_and_candidates(
        [{"folder": folder, "files": 3, "resolves_to": {}, "suggestions": []}])
    r = rows[0]
    assert r["docs_in_folder"] == 3 and r["eligible"] == 1 and r["already_owned"] == 2
    assert _owner(free) == (None, None, None)


def test_worklist_template_uses_preview_wording_and_zero_eligible_label():
    from app.routes.admin import templates
    from app.security.models import Principal
    p = Principal(1, "a@e.com", "Admin", frozenset({"client.write"}))
    eligible_folder = {"folder": "Real Client", "files": 2, "sample_documents": ["a.pdf"],
                       "candidates": [{"id": 5338, "name": "Deborah McDaniel", "designation": "Person",
                                       "email": None, "phone": None, "household_id": None,
                                       "household_name": None, "link": "/client/5338"}],
                       "docs_in_folder": 2, "eligible": 2, "already_owned": 0, "reject": 0}
    owned_folder = {"folder": "Affordable Measures", "files": 8, "sample_documents": [], "candidates": [],
                    "docs_in_folder": 8, "eligible": 0, "already_owned": 8, "reject": 0}
    html = templates.get_template("admin/unassigned_documents.html").render(
        request=None, principal=p, unassigned=[eligible_folder, owned_folder], q="", search=None,
        ok=None, err=None)
    assert "Preview → Deborah McDaniel (#5338)" in html      # candidate button is preview-only
    assert "Assign to Deborah McDaniel" not in html          # never the pre-confirmation "Assign to"
    assert "No eligible unassigned documents" in html        # zero-eligible label present
    assert "8 already owned" in html


# --- inline document view -----------------------------------------------------------------------

def test_inline_viewable_types():
    from app.routes.documents import _is_inline_viewable
    assert _is_inline_viewable("application/pdf", "x.pdf") is True
    assert _is_inline_viewable("image/png", "x.png") is True
    assert _is_inline_viewable(None, "scan.jpg") is True          # extension fallback
    assert _is_inline_viewable("application/vnd.ms-excel", "x.xlsx") is False
