"""Document platform tests (Phase D.16).

Covers CRUD + anchor validation (never creates People/Orgs), classification vocab, deterministic
lifecycle (approve/archive/soft-delete/restore) + events, immutable versioning + restore, folders,
retention (derived expiration), record scope (person-anchored + firm docs), relationships +
consumer visibility read, timeline integration (client-anchored only), Annual Review / Opportunity
read-only visibility, Analytics document_count consumption, Microsoft 365 references, and
dependency direction.
"""
import re
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, func, insert, select
from starlette.requests import Request

from app.db import (
    document_events,
    document_folders,
    documents,
    engine,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.document_platform import relationships as drel
from app.services.document_platform import service as dsvc
from app.services.document_platform import versions as dver

CAPS = frozenset({"documents.view", "documents.edit", "documents.delete", "documents.version",
                  "documents.approve", "documents.archive", "documents.restore",
                  "documents.export", "documents.manage_retention"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"dm-{tag}@e.test", normalized_email=f"dm-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "pid": pid, "tag": tag}


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(documents).where(documents.c.created_by_user_id == ids["uid"]))
        c.execute(delete(documents).where(documents.c.person_id == ids["pid"]))
        c.execute(delete(document_folders).where(document_folders.c.created_by == ids["uid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))


def _p(ids, caps=CAPS):
    return Principal(ids["uid"], "a@e.com", f"U {ids['uid']}", frozenset(caps))


def _doc(ids, **kw):
    return dsvc.create_document(_p(ids), original_name="Doc.pdf", actor_user_id=ids["uid"],
                                person_id=ids["pid"], classification="client", **kw)


# --- CRUD + validation -------------------------------------------------------

def test_create_defaults_and_version_seeded():
    ids = _setup()
    try:
        d = _doc(ids, storage_provider="sharepoint", storage_uri="https://sp/x")
        assert d["status"] == "active" and d["current_version"] == 1
        assert d["storage_provider"] == "sharepoint"
        assert len(dver.list_versions(d["id"])) == 1 and dver.current_version(d["id"])["version_number"] == 1
    finally:
        _teardown(ids)


def test_create_validates_anchor_and_classification():
    ids = _setup()
    try:
        with pytest.raises(dsvc.DocumentError):
            dsvc.create_document(_p(ids), original_name="x", actor_user_id=ids["uid"], person_id=99999999)
        with pytest.raises(dsvc.DocumentError):
            dsvc.create_document(_p(ids), original_name="x", actor_user_id=ids["uid"],
                                 classification="not_a_class")
    finally:
        _teardown(ids)


# --- lifecycle ---------------------------------------------------------------

def test_lifecycle_and_events():
    ids = _setup()
    try:
        p = _p(ids)
        d = _doc(ids)
        assert dsvc.approve(p, d["id"], actor_user_id=ids["uid"])["status"] == "approved"
        assert dsvc.archive(p, d["id"], actor_user_id=ids["uid"])["status"] == "archived"
        assert dsvc.restore(p, d["id"], actor_user_id=ids["uid"])["status"] == "active"
        deleted = dsvc.soft_delete(p, d["id"], actor_user_id=ids["uid"])
        assert deleted["status"] == "deleted" and deleted["deleted_at"] is not None
        # Deleted docs are excluded from listing.
        assert all(r["id"] != d["id"] for r in dsvc.list_documents(p)["rows"])
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(document_events)
                         .where(document_events.c.document_id == d["id"]))
        assert n >= 4   # uploaded, approved, archived, restored, deleted
    finally:
        _teardown(ids)


def test_invalid_transition_rejected():
    ids = _setup()
    try:
        p = _p(ids)
        d = dsvc.create_document(p, original_name="Draft.pdf", actor_user_id=ids["uid"],
                                 person_id=ids["pid"], classification="client", status="draft")
        with pytest.raises(dsvc.DocumentError):
            dsvc.approve(p, d["id"], actor_user_id=ids["uid"])   # cannot approve a draft directly
    finally:
        _teardown(ids)


# --- versioning --------------------------------------------------------------

def test_versioning_major_minor_and_restore():
    ids = _setup()
    try:
        p = _p(ids)
        d = _doc(ids)
        v2 = dver.create_version(p, d["id"], actor_user_id=ids["uid"], bump="major")
        assert v2["major"] == 2 and v2["minor"] == 0 and v2["is_current"] is True
        v3 = dver.create_version(p, d["id"], actor_user_id=ids["uid"], bump="minor")
        assert v3["major"] == 2 and v3["minor"] == 1
        assert dver.current_version(d["id"])["version_number"] == 3
        v1 = [v for v in dver.list_versions(d["id"]) if v["version_number"] == 1][0]
        dver.restore_version(p, d["id"], v1["id"], actor_user_id=ids["uid"])
        assert dver.current_version(d["id"])["version_number"] == 1   # restored, history intact
        assert len(dver.list_versions(d["id"])) == 3                  # history not rewritten
    finally:
        _teardown(ids)


# --- relationships + consumer visibility -------------------------------------

def test_relationships_and_documents_for_entity():
    ids = _setup()
    try:
        p = _p(ids)
        d = _doc(ids)
        drel.link_entity(p, d["id"], entity_type="opportunity", entity_id=555, actor_user_id=ids["uid"])
        with pytest.raises(drel.RelationshipError):
            drel.link_entity(p, d["id"], entity_type="not_a_type", entity_id=1, actor_user_id=ids["uid"])
        assert len(drel.list_relationships(d["id"])) == 1
        # Consumer visibility: by relationship and by anchor.
        assert len(drel.documents_for_entity(p, "opportunity", 555)) == 1
        assert len(drel.documents_for_entity(p, "person", ids["pid"])) == 1
        drel.unlink_entity(p, d["id"], entity_type="opportunity", entity_id=555)
        assert drel.documents_for_entity(p, "opportunity", 555) == []
    finally:
        _teardown(ids)


# --- scope -------------------------------------------------------------------

def test_scope_person_anchored_and_firm_docs():
    ids = _setup()
    try:
        p = _p(ids)
        client_doc = _doc(ids)
        firm_doc = dsvc.create_document(p, original_name="Firm Policy.pdf", actor_user_id=ids["uid"],
                                        classification="internal")   # no client anchor
        stranger = Principal(99993001, "s@e", "S", {"documents.view"})
        assert dsvc.get_document(stranger, client_doc["id"]) is None        # not in book
        assert dsvc.get_document(stranger, firm_doc["id"]) is not None       # firm doc visible
        assert dsvc.get_document(p, client_doc["id"]) is not None            # owner sees it
    finally:
        _teardown(ids)


# --- retention ---------------------------------------------------------------

def test_retention_derives_expiration():
    ids = _setup()
    try:
        p = _p(ids)
        d = dsvc.create_document(p, original_name="Tax.pdf", actor_user_id=ids["uid"],
                                 person_id=ids["pid"], classification="tax",
                                 effective_date=date(2020, 1, 1))
        pol = [x for x in dsvc.list_retention_policies() if x["code"] == "standard-7y"][0]
        updated = dsvc.apply_retention(p, d["id"], pol["id"], actor_user_id=ids["uid"])
        assert updated["expiration_date"] == date(2027, 1, 1)   # 2020 + 7 years
    finally:
        _teardown(ids)


# --- timeline integration ----------------------------------------------------

def test_timeline_events_client_anchored_only():
    ids = _setup()
    try:
        p = _p(ids)
        d = _doc(ids)
        dsvc.approve(p, d["id"], actor_user_id=ids["uid"])
        firm = dsvc.create_document(p, original_name="Firm.pdf", actor_user_id=ids["uid"],
                                    classification="internal")
        dsvc.approve(p, firm["id"], actor_user_id=ids["uid"])
        with engine.connect() as c:
            client_events = c.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.person_id == ids["pid"], timeline_events.c.source == "document"))
        assert client_events >= 2   # uploaded + approved for the client doc; firm doc emits none
    finally:
        _teardown(ids)


# --- consumer integrations + analytics ---------------------------------------

def test_annual_review_documents_section_gated():
    ids = _setup()
    try:
        _doc(ids)
        from app.services import annual_review
        with_docs = Principal(ids["uid"], "a@e", "A", frozenset({"annual_review.read", "documents.view"}))
        ws = annual_review.compose_workspace(with_docs, ids["pid"])
        assert ws["documents"] is not None and len(ws["documents"]) >= 1
        without = Principal(ids["uid"], "a@e", "A", frozenset({"annual_review.read"}))
        assert annual_review.compose_workspace(without, ids["pid"])["documents"] is None
    finally:
        _teardown(ids)


def test_analytics_document_count_metric():
    ids = _setup()
    try:
        _doc(ids)
        from app.services.analytics import metrics
        p = Principal(ids["uid"], "a@e", "A", frozenset({"analytics.view"}))
        m = metrics.compute_metric(p, "document_count")
        assert m["value"] >= 1 and m["category"] == "operations"
    finally:
        _teardown(ids)


# --- M365 reference ----------------------------------------------------------

def test_microsoft365_storage_reference():
    ids = _setup()
    try:
        d = _doc(ids, storage_provider="onedrive", storage_uri="https://onedrive/item/123")
        got = dsvc.get_document(_p(ids), d["id"])
        assert got["storage_provider"] == "onedrive" and got["storage_uri"].endswith("123")
    finally:
        _teardown(ids)


# --- routes ------------------------------------------------------------------

def test_library_route_renders():
    from app.routes.document_library import library
    ids = _setup()
    try:
        req = Request({"type": "http", "method": "GET", "path": "/document-library",
                       "headers": [], "query_string": b""})
        resp = library(req, principal=_p(ids))
        assert resp.status_code == 200 and "Document Library" in resp.body.decode()
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_document_platform_does_not_import_consumers_or_analytics():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services" / "document_platform"
    pattern = re.compile(r"import\s+(annual_review|business_owner|opportunity|campaign|referral|"
                         r"analytics)\b|from\s+app\.services\.(annual_review|business_owner|"
                         r"opportunity|campaign|referral|analytics)")
    for module in ("service.py", "versions.py", "relationships.py"):
        assert not pattern.search((root / module).read_text()), f"{module} must not import consumers"
    # Advisor intelligence untouched.
    ai = (root.parent / "advisor_intelligence.py").read_text()
    assert "document_platform" not in ai
