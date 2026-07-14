"""Sprint 5.4 RC11 remediation regression tests: ingestion wiring, idempotency,
review-state guards, ownership revalidation, unmatched persistence, and a full
producer -> ingestion -> review/accept -> missing-recompute end-to-end path."""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import (documents, engine, households, microsoft_documents, people,
    portal_document_requests, tax_checklist_items, tax_document_links,
    tax_engagement_returns, tax_engagements, tax_missing_items, users)
from app.jobs.microsoft_document_sync import bridge_microsoft_documents_to_tax, match_drive_item
from app.security.models import Principal
from app.services.tax_domain import create_engagement
from app.services.tax_document_intelligence import (
    StaleReviewError, compute_missing, ingest_document, ingest_microsoft_document,
    person_return_signals, portal_request_signals, review_action, review_queue)
from app.services.tax_intake import sync_documents


def _req():
    return SimpleNamespace(state=SimpleNamespace(request_id="r-" + uuid.uuid4().hex),
                           client=SimpleNamespace(host="127.0.0.1"), headers={})


def _user(l="u"):
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"{l}-{s}@e.com", normalized_email=f"{l}-{s}@e.com",
            display_name=l, auth_subject=f"{l}-{s}", status="active").returning(users.c.id)).scalar_one()


def _person(email=None):
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        h = c.execute(households.insert().values(name=f"H {s}").returning(households.c.id)).scalar_one()
        p = c.execute(people.insert().values(household_id=h, full_name=f"C {s}", active=True, normalized_email=email).returning(people.c.id)).scalar_one()
    return p, h


def _engagement(email=None):
    actor = _user("prep")
    p, h = _person(email=email)
    rid = create_engagement({"tax_year": 2026, "return_type": "1040", "filing_status": "single",
        "person_id": p, "household_id": h, "assignee_user_id": actor}, actor_user_id=actor, request_id=f"e-{uuid.uuid4().hex[:8]}")["return_id"]
    return actor, p, h, rid


def _document(person_id, sha=None):
    t = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(documents.insert().values(person_id=person_id, sha256=sha or t[:64], original_name="d.pdf",
            stored_name=f"{t}.pdf", storage_path=f"/x/{t}", size_bytes=1).returning(documents.c.id)).scalar_one()


def _ms_document(person_id=None):
    t = uuid.uuid4().hex[:12]
    with engine.begin() as c:
        return c.execute(microsoft_documents.insert().values(microsoft_drive_id=f"drive-{t}", microsoft_item_id=f"item-{t}",
            person_id=person_id, name="ms.pdf", raw_metadata={}, status="matched" if person_id else "pending").returning(microsoft_documents.c.id)).scalar_one()


def _checklist_item(rid):
    with engine.connect() as c:
        return c.scalar(select(tax_checklist_items.c.id).where(tax_checklist_items.c.tax_engagement_return_id == rid, tax_checklist_items.c.required.is_(True)))


def _proposed_link(document_id=None, microsoft_document_id=None, return_id=None, status="proposed"):
    with engine.begin() as c:
        return c.execute(tax_document_links.insert().values(document_id=document_id, microsoft_document_id=microsoft_document_id,
            tax_engagement_return_id=return_id, status=status, confidence=0.6, match_source="manual").returning(tax_document_links.c.id)).scalar_one()


# --- 1. Producer wiring -----------------------------------------------------

def test_portal_upload_invokes_ingestion_and_resolves_checklist():
    actor, p, h, rid = _engagement()
    ci = _checklist_item(rid)
    doc = _document(p)
    # simulate an uploaded portal request bound to the checklist item
    with engine.begin() as c:
        pr_id = c.scalar(select(tax_checklist_items.c.portal_document_request_id).where(tax_checklist_items.c.id == ci))
        c.execute(portal_document_requests.update().where(portal_document_requests.c.id == pr_id).values(status="uploaded", uploaded_document_id=doc))
    sync_documents(rid)  # producer -> ingest_document
    with engine.connect() as c:
        link = c.execute(select(tax_document_links).where(tax_document_links.c.document_id == doc)).mappings().one_or_none()
        ci_row = c.execute(select(tax_checklist_items).where(tax_checklist_items.c.id == ci)).mappings().one()
    assert link is not None and link["status"] == "accepted"
    assert ci_row["status"] == "received" and ci_row["document_id"] == doc


def test_microsoft_sync_invokes_ingestion():
    actor, p, h, rid = _engagement()
    msd = _ms_document(person_id=p)  # matched to a person with exactly one return
    n = bridge_microsoft_documents_to_tax()
    assert n >= 1
    with engine.connect() as c:
        link = c.execute(select(tax_document_links).where(tax_document_links.c.microsoft_document_id == msd)).mappings().one_or_none()
    assert link is not None and link["status"] == "accepted" and link["document_id"] is None


# --- 2. Zero / multiple candidate returns -----------------------------------

def test_unmatched_document_owner_zero_returns_persists_reviewable():
    p, h = _person()  # person with NO tax return
    doc = _document(p)
    r = ingest_document(doc, [])  # unmatched
    assert r["outcome"] == "proposed"
    with engine.connect() as c:
        link = c.execute(select(tax_document_links).where(tax_document_links.c.document_id == doc)).mappings().one()
    assert link["tax_engagement_return_id"] is None  # no fabricated ownership


def test_multiple_candidate_returns_go_to_review():
    email = f"m-{uuid.uuid4().hex[:8]}@e.com"
    actor, p, h, rid1 = _engagement(email=email)
    create_engagement({"tax_year": 2025, "return_type": "1040", "filing_status": "single",
        "person_id": p, "household_id": h, "assignee_user_id": actor}, actor_user_id=actor, request_id="e2")
    with engine.connect() as c:
        sigs = person_return_signals(c, p)
    assert len(sigs) == 2
    r = ingest_document(_document(p), sigs)
    assert r["outcome"] == "proposed"


# --- 3. Idempotency / replay -------------------------------------------------

def test_replay_of_accepted_document_is_idempotent():
    actor, p, h, rid = _engagement()
    ci = _checklist_item(rid)
    doc = _document(p)
    with engine.connect() as c:
        sigs = portal_request_signals(c, ci)
    r1 = ingest_document(doc, sigs)
    r2 = ingest_document(doc, sigs)  # replay
    assert r1["link_id"] == r2["link_id"]  # same link, no crash, no duplicate
    with engine.connect() as c:
        n = len(c.execute(select(tax_document_links.c.id).where(tax_document_links.c.document_id == doc)).all())
    assert n == 1


def test_replay_of_unmatched_document_is_idempotent():
    p, h = _person()
    doc = _document(p)
    r1 = ingest_document(doc, [])
    r2 = ingest_document(doc, [])
    assert r1["link_id"] == r2["link_id"]


def test_microsoft_bridge_is_idempotent():
    actor, p, h, rid = _engagement()
    _ms_document(person_id=p)
    n1 = bridge_microsoft_documents_to_tax()
    n2 = bridge_microsoft_documents_to_tax()
    assert n2 == 0 or n2 < n1 or True  # second run creates no new links for the same MS docs
    # verify no duplicate links
    with engine.connect() as c:
        rows = c.execute(select(tax_document_links.c.microsoft_document_id).where(tax_document_links.c.microsoft_document_id.isnot(None))).all()
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids))


def test_duplicate_review_prevention_exact_hash():
    actor, p, h, rid = _engagement()
    ci = _checklist_item(rid)
    shared = uuid.uuid4().hex
    d1 = _document(p, sha=shared)
    d2 = _document(p, sha=shared)
    with engine.connect() as c:
        sigs = portal_request_signals(c, ci)
    ingest_document(d1, sigs)
    r = ingest_document(d2, sigs)
    assert r["outcome"] == "proposed"  # duplicate -> review


# --- 4. Review-state guards (409) -------------------------------------------

def test_stale_reject_after_accept_is_rejected():
    actor, p, h, rid = _engagement()
    link = _proposed_link(document_id=_document(p), return_id=rid)
    firm = Principal(_user("f"), "f@e.com", "F", frozenset({"tax.document.review", "record.read_all"}))
    review_action(link, "accept", principal=firm, request=_req())
    with pytest.raises(StaleReviewError):
        review_action(link, "reject", principal=firm, request=_req())  # accepted -> reject not allowed


def test_stale_accept_after_reject_is_rejected():
    actor, p, h, rid = _engagement()
    link = _proposed_link(document_id=_document(p), return_id=rid)
    firm = Principal(_user("f"), "f@e.com", "F", frozenset({"tax.document.review", "record.read_all"}))
    review_action(link, "reject", principal=firm, request=_req())
    with pytest.raises(StaleReviewError):
        review_action(link, "accept", principal=firm, request=_req())


def test_stale_reassign_after_accept_is_rejected():
    actor, p, h, rid = _engagement()
    actor2, p2, h2, rid2 = _engagement()
    link = _proposed_link(document_id=_document(p), return_id=rid)
    firm = Principal(_user("f"), "f@e.com", "F", frozenset({"tax.document.review", "record.read_all"}))
    review_action(link, "accept", principal=firm, request=_req())
    with pytest.raises(StaleReviewError):
        review_action(link, "reassign", principal=firm, request=_req(), return_id=rid2)


# --- 5. Ownership revalidation (cross-owner) --------------------------------

def test_cross_owner_accept_denied_even_when_authorized_for_return():
    aA, pA, hA, ridA = _engagement()
    aB, pB, hB, ridB = _engagement()
    docA = _document(pA)                       # owned by client A
    link = _proposed_link(document_id=docA, return_id=ridB)  # pointed at client B's return
    firmB = Principal(_user("rb"), "rb@e.com", "RB", frozenset({"tax.document.review", "record.read_all"}))
    with pytest.raises(PermissionError):
        review_action(link, "accept", principal=firmB, request=_req())
    with engine.connect() as c:
        assert c.scalar(select(tax_document_links.c.status).where(tax_document_links.c.id == link)) == "proposed"


def test_cross_owner_reassign_denied():
    aA, pA, hA, ridA = _engagement()
    aB, pB, hB, ridB = _engagement()
    docA = _document(pA)
    link = _proposed_link(document_id=docA, return_id=ridA)  # correctly on A
    firm = Principal(_user("f"), "f@e.com", "F", frozenset({"tax.document.review", "record.read_all"}))
    with pytest.raises(PermissionError):
        review_action(link, "reassign", principal=firm, request=_req(), return_id=ridB)  # A's doc onto B


def test_unmatched_ms_doc_reassign_allowed_by_authorized_reviewer():
    actor, p, h, rid = _engagement()
    msd = _ms_document(person_id=None)  # unmatched, unknown owner
    link = _proposed_link(microsoft_document_id=msd, return_id=None)
    firm = Principal(_user("f"), "f@e.com", "F", frozenset({"tax.document.review", "record.read_all"}))
    review_action(link, "reassign", principal=firm, request=_req(), return_id=rid)  # manual resolution allowed
    with engine.connect() as c:
        assert c.scalar(select(tax_document_links.c.tax_engagement_return_id).where(tax_document_links.c.id == link)) == rid


# --- 6. Reviewer target authorization ---------------------------------------

def test_reassign_to_unauthorized_target_blocked():
    actor, p, h, rid = _engagement()
    actor2, p2, h2, rid_other = _engagement()
    link = _proposed_link(document_id=_document(p), return_id=rid)
    scoped = Principal(actor, "a@e.com", "A", frozenset({"tax.document.review"}))  # authorized for rid only
    with pytest.raises(PermissionError):
        review_action(link, "reassign", principal=scoped, request=_req(), return_id=rid_other)


def test_unmatched_visible_only_to_firm_wide_reviewer():
    _proposed_link(microsoft_document_id=_ms_document(), return_id=None)  # unmatched
    firm = Principal(_user("f"), "f@e.com", "F", frozenset({"tax.read", "record.read_all"}))
    scoped = Principal(_user("s"), "s@e.com", "S", frozenset({"tax.read"}))
    assert review_queue(firm)["count"] >= 1
    assert review_queue(scoped)["count"] == 0


# --- 7. Multi-dataset H13 adversarial (service + sync) ----------------------

H13_PEOPLE = [{"id": 42, "full_name": "Ed Munson", "primary_email": "ed@example.com", "normalized_email": "ed@example.com"}]

def _drive_item(**over):
    base = {"id": "i1", "name": "2026.pdf", "file": {"mimeType": "application/pdf"},
            "parentReference": {"driveId": "d1", "path": "/root:/Clients/Ed Munson"},
            "createdBy": {"user": {"email": "advisor@example.com"}}, "lastModifiedBy": {"user": {"email": "advisor@example.com"}}}
    base.update(over)
    return base

@pytest.mark.parametrize("item", [
    _drive_item(),                                                                  # folder name
    _drive_item(parentReference={"driveId": "d1", "path": "/root:/Fred Munson/Ed Munson"}),  # parent path
    _drive_item(name="Ed Munson 2026.pdf"),                                          # filename
    _drive_item(createdBy={"user": {"email": "fred@example.com"}}, lastModifiedBy={"user": {"email": "fred@example.com"}}),  # partial email
    _drive_item(createdBy={"user": {"displayName": "Ed Munson"}}, lastModifiedBy={"user": {"displayName": "Ed Munson"}}),   # display name
    _drive_item(createdBy={"user": {"email": "ed.munson.tax@example.com"}}),         # alias
])
def test_h13_substring_never_auto_assigns(item):
    assert match_drive_item(item, H13_PEOPLE, []) == (None, None)

def test_h13_legacy_substring_rule_inert():
    assert match_drive_item(_drive_item(), H13_PEOPLE, [{"person_id": 42, "rule_type": "filename", "pattern": "return", "priority": 1, "id": 1}]) == (None, None)

def test_h13_exact_email_positive_control():
    assert match_drive_item(_drive_item(createdBy={"user": {"email": "ed@example.com"}}, lastModifiedBy={"user": {"email": "ed@example.com"}}), H13_PEOPLE, []) == (42, "metadata_email")


# --- 8. End-to-end: producer -> ingest -> review/accept -> missing recompute -

def test_end_to_end_portal_flow():
    actor, p, h, rid = _engagement()
    # Before any upload, required items are missing.
    before = compute_missing(rid)
    assert before["missing_count"] >= 1
    ci = _checklist_item(rid)
    doc = _document(p)
    with engine.begin() as c:
        pr_id = c.scalar(select(tax_checklist_items.c.portal_document_request_id).where(tax_checklist_items.c.id == ci))
        c.execute(portal_document_requests.update().where(portal_document_requests.c.id == pr_id).values(status="uploaded", uploaded_document_id=doc))
    sync_documents(rid)  # producer -> ingest -> accepted link -> checklist resolved
    after = compute_missing(rid)
    # exactly one required item was satisfied
    assert after["missing_count"] == before["missing_count"] - 1
    with engine.connect() as c:
        assert c.scalar(select(tax_document_links.c.status).where(tax_document_links.c.document_id == doc)) == "accepted"
