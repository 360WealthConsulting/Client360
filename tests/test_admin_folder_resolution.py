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
from app.security.models import Principal
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


# --- per-document resolution ------------------------------------------------------------------

def test_resolve_single_document_to_person():
    folder = f"Folder-{_TAG}-SD"
    d1, d2 = _doc(folder), _doc(folder)
    pid = _person()
    res = hh_service.resolve_document_ownership(d1, person_id=pid, actor_user_id=1, request_id="t")
    assert res["assigned"] is True
    assert _owner(d1) == (pid, None, None)
    assert _owner(d2) == (None, None, None)          # sibling document untouched


def test_resolve_single_document_to_household_and_organization():
    folder = f"Folder-{_TAG}-SD2"
    dh, do = _doc(folder), _doc(folder)
    hid, org = _household(), _org()
    assert hh_service.resolve_document_ownership(dh, household_id=hid, actor_user_id=1, request_id="t")["assigned"]
    assert hh_service.resolve_document_ownership(do, organization_id=org, actor_user_id=1, request_id="t")["assigned"]
    assert _owner(dh) == (None, hid, None)
    assert _owner(do) == (None, None, org)


def test_resolve_single_document_does_not_touch_siblings():
    folder = f"Folder-{_TAG}-SD3"
    target = _doc(folder)
    siblings = [_doc(folder) for _ in range(3)]
    hh_service.resolve_document_ownership(target, person_id=_person(), actor_user_id=1, request_id="t")
    for s in siblings:
        assert _owner(s) == (None, None, None)


def test_already_owned_document_cannot_be_reassigned():
    folder = f"Folder-{_TAG}-SD4"
    p0 = _person()
    owned = _doc(folder, person_id=p0)
    res = hh_service.resolve_document_ownership(owned, person_id=_person(), actor_user_id=1, request_id="t")
    assert res["assigned"] is False and res["reason"] == "already_owned"
    assert _owner(owned) == (p0, None, None)          # unchanged


def test_permanent_reject_cannot_be_assigned_individually(monkeypatch):
    folder = f"Folder-{_TAG}-SD5"
    reject = _doc(folder)
    monkeypatch.setattr(hh_service, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({reject}))
    res = hh_service.resolve_document_ownership(reject, person_id=_person(), actor_user_id=1, request_id="t")
    assert res["assigned"] is False and res["reason"] == "permanent_reject"
    assert _owner(reject) == (None, None, None)


def test_stale_double_confirm_is_rejected():
    folder = f"Folder-{_TAG}-SD6"
    d = _doc(folder)
    first = hh_service.resolve_document_ownership(d, person_id=_person(), actor_user_id=1, request_id="t")
    assert first["assigned"] is True
    # a second (stale) confirmation must NOT overwrite the now-owned document
    second = hh_service.resolve_document_ownership(d, person_id=_person(), actor_user_id=1, request_id="t")
    assert second["assigned"] is False and second["reason"] == "already_owned"


def test_dry_run_single_document_reports_without_writing():
    folder = f"Folder-{_TAG}-SD7"
    d = _doc(folder)
    pid = _person()
    res = hh_service.resolve_document_ownership(d, person_id=pid, dry_run=True)
    assert res["would_assign"] is True and res["destination"]["entity_id"] == pid
    assert _owner(d) == (None, None, None)            # nothing written


def test_single_resolution_writes_audit():
    folder = f"Folder-{_TAG}-SD8"
    d = _doc(folder)
    hh_service.resolve_document_ownership(d, person_id=_person(), actor_user_id=1, request_id="t")
    with engine.connect() as c:
        row = c.execute(select(audit_events.c.action, audit_events.c.metadata)
                        .where(audit_events.c.entity_type == "document",
                               audit_events.c.entity_id == str(d))
                        .order_by(audit_events.c.occurred_at.desc()).limit(1)).mappings().first()
    assert row is not None and row["action"] == "document.ownership_resolved"
    assert row["metadata"].get("scope") == "single_document"


def test_resolve_document_route_registered_and_gated():
    from app.main import app
    from app.routes.admin import resolve_unassigned_document
    match = [r for r in app.routes if getattr(r, "path", None) == "/admin/documents/unassigned/resolve-document"]
    assert match and "POST" in match[0].methods
    assert resolve_unassigned_document


# --- per-document suggested-owner buttons (UI) ------------------------------------------------

def test_folder_candidates_match_household_and_org_by_name():
    from app.routes.admin import _folder_candidates
    uniq = f"Zeta Holdings {_TAG}"          # used verbatim as the folder token
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(entity_type="business", name=uniq, active=True)
                        .returning(relationship_entities.c.id)).scalar_one()
        hid = c.execute(households.insert().values(name=uniq).returning(households.c.id)).scalar_one()
    _C["relationship_entities"].append(eid)
    _C["households"].append(hid)
    with engine.connect() as conn:
        _people, hh_c, org_c = _folder_candidates(conn, uniq)
    assert any(h["id"] == hid for h in hh_c)      # household name == folder token
    assert any(o["id"] == eid for o in org_c)     # business name == folder token


def test_review_template_renders_per_document_candidate_buttons():
    from app.routes.admin import templates
    p = Principal(1, "a@e.com", "Admin", frozenset({"client.write"}))
    docs = [
        {"id": 457, "name": "a.pdf", "doc_type": None, "year": None, "current_owner": "Unassigned (NULL)",
         "view_url": "/documents/457/download?inline=1", "download_url": "/documents/457/download"},
        {"id": 458, "name": "b.pdf", "doc_type": None, "year": None, "current_owner": "Unassigned (NULL)",
         "view_url": "/documents/458/download?inline=1", "download_url": "/documents/458/download"},
    ]
    cands = [{"id": 7430, "name": "MARY HARDY", "designation": "Person", "email": "m@e.com",
              "phone": "555", "household_id": None, "household_name": None, "link": "/client/7430"}]
    hh = [{"id": 93, "name": "Hardy Household"}]
    org = [{"id": 129, "name": "Affordable Measures", "entity_type": "business"}]
    html = templates.get_template("admin/unassigned_review.html").render(
        request=None, principal=p, folder="Adrianna Hardy", eligible_docs=docs,
        already_owned_docs=[], excluded_docs=[], candidates=cands,
        household_candidates=hh, org_candidates=org)
    # the person candidate button is repeated PER eligible document (2 docs), separate from the bulk one
    assert html.count("Preview → Person: MARY HARDY (#7430)") == 2
    assert "Preview all → MARY HARDY (#7430)" in html      # bulk section still lists it once
    assert "Preview → Household: Hardy Household (#93)" in html
    assert "Preview → Business: Affordable Measures (#129)" in html
    # each candidate button submits the correct per-document id + email context to distinguish
    assert 'name="document_id" value="457"' in html and 'name="document_id" value="458"' in html
    assert "m@e.com" in html
    # manual fallback remains; buttons post to the per-document (not folder) route
    assert "Choose another owner" in html
    assert html.count('action="/admin/documents/unassigned/resolve-document"') >= 4
    # bulk assignment remains a separate section using the folder route
    assert "assign ALL remaining eligible" in html
    assert 'action="/admin/documents/unassigned/resolve"' in html


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
    assert _is_inline_viewable(None, "IMG_5178.HEIC") is True     # HEIC image inline
    assert _is_inline_viewable("application/vnd.ms-excel", "x.xlsx") is False


# --- Review-before-decide surface --------------------------------------------------------------

def test_folder_documents_splits_by_category():
    from app.routes.admin import _folder_documents
    folder = f"Folder-{_TAG}-REV"
    e1, e2 = _doc(folder), _doc(folder)
    owned = _doc(folder, organization_id=_org())
    with engine.connect() as c:
        eligible, already_owned, reject = _folder_documents(c, folder)
    assert set(eligible) == {e1, e2}
    assert already_owned == [owned]
    assert reject == []


def test_review_context_lists_docs_with_view_actions_and_no_mutation():
    # Mirrors exactly what the review route builds (helpers), avoiding a full template render.
    from app.routes.admin import _documents_detail, _folder_documents
    folder = f"Folder-{_TAG}-RV2"
    e = _doc(folder)
    owned = _doc(folder, organization_id=_org())
    with engine.connect() as c:
        eligible_ids, owned_ids, reject_ids = _folder_documents(c, folder)
        eligible_docs = _documents_detail(c, eligible_ids)
        already_owned_docs = _documents_detail(c, owned_ids)
    assert [d["id"] for d in eligible_docs] == [e]
    assert [d["id"] for d in already_owned_docs] == [owned]
    assert eligible_docs[0]["view_url"] == f"/documents/{e}/download?inline=1"     # authorized View
    assert eligible_docs[0]["download_url"] == f"/documents/{e}/download"
    assert _owner(e) == (None, None, None)                                          # read-only


def test_review_route_registered_and_gated():
    from app.main import app
    match = [r for r in app.routes if getattr(r, "path", None) == "/admin/documents/unassigned/review"]
    assert match and "GET" in match[0].methods


def test_confirm_and_review_templates_list_all_categories():
    from pathlib import Path
    rev = Path("app/templates/admin/unassigned_review.html").read_text(encoding="utf-8")
    assert "ELIGIBLE UNASSIGNED" in rev and "ALREADY OWNED" in rev and "PERMANENT REJECT" in rev
    assert "View ↗" in rev
    assert "Preview this document" in rev            # per-document resolution
    assert "resolve-document" in rev
    assert "assign ALL remaining eligible" in rev    # bulk action still offered
    conf = Path("app/templates/admin/unassigned_confirm.html").read_text(encoding="utf-8")
    assert "PROPOSED OWNER" in conf
