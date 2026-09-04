"""Phase 2 — guarded bulk confirm of validated HIGH proposals: preview, re-check, atomic write, audit."""
import hashlib
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import audit_events, documents, engine, people, person_source_links, source_contacts
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import document_high_confirm as hc
from app.services import document_high_validation as hv

_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "abcdefghij")).capitalize()
# Alphabetic + capitalised so names built as f"First {_TAG}" are extractable by the content
# name matcher. A hex tag ("Jennifer a1b2c3d4") is not a name the extractor can see, so these
# fixtures used to reach HIGH on the email alone — the exact rule the safety patch removed.
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_DOCS: list = []
_PEOPLE: list = []
_SC: list = []
_LINKS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        # audit_events is an append-only hash chain — never deleted here (test rows are harmless).
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


def _doc(path, name="f.txt", person_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"hc-{_TAG}-{uuid.uuid4().hex}", storage_path=str(path), storage_uri=str(path),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive"}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id).where(documents.c.id == did)).first())


def _clean_high_doc(tmp_path, tag):
    email = f"{tag}-{_TAG}@mail.com"
    full_name = f"{tag}person {_A}"
    pid = _person(full_name, email=email)
    f = tmp_path / f"{tag}.txt"
    # The document must NAME the owner as well as carry their unique email. An identifier on its own
    # is a lead, not an owner — see tests/test_document_owner_proposal_safety.py.
    f.write_text(f"Statement for {full_name}\nremit to {email}\n")
    return _doc(f, name=f"{tag}.txt"), pid


# --- preview -------------------------------------------------------------------------------------

def test_clean_high_appears_in_preview_eligible(tmp_path):
    did, pid = _clean_high_doc(tmp_path, "prev")
    data = hc.preview_high_confirm()
    ids = {r["document_id"]: r for r in data["eligible"]}
    assert did in ids and ids[did]["proposed_entity_id"] == pid
    assert did not in {r["document_id"] for r in data["review"]}


def test_excluded_high_is_not_selectable_in_preview(tmp_path):
    # two different people's emails in one doc -> foreign_strong_identifier -> review, not eligible
    e1, e2 = f"x-{_TAG}@mail.com", f"y-{_TAG}@mail.com"
    _person(f"Xperson {_A}", email=e1)
    _person(f"Yperson {_A}", email=e2)
    # A legitimate HIGH for Xperson (name + their unique email), plus Yperson's email as the
    # foreign strong identifier the contradiction guard must catch.
    f = tmp_path / "d.txt"; f.write_text(f"Statement for Xperson {_A}  {e1} and {e2}\n")
    did = _doc(f, name="d.txt")
    data = hc.preview_high_confirm()
    assert did not in {r["document_id"] for r in data["eligible"]}
    assert did in {r["document_id"] for r in data["review"]}


# --- confirm: assigns, audits, no mutation of others ---------------------------------------------

def test_confirm_assigns_only_selected_and_audits(tmp_path):
    d1, p1 = _clean_high_doc(tmp_path, "a")
    d2, _p2 = _clean_high_doc(tmp_path, "b")          # eligible but NOT selected
    res = hc.confirm_documents([d1], actor_user_id=1, request_id="t-req")
    assert res["assigned_count"] == 1 and res["assigned"][0]["document_id"] == d1
    assert _owner(d1) == (p1, None, None)             # assigned
    assert _owner(d2) == (None, None, None)           # untouched (not selected)
    # ownership-resolution audit written by the reused per-document write path
    with engine.connect() as c:
        n = c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.entity_type == "document", audit_events.c.entity_id == str(d1),
            audit_events.c.action == "document.ownership_resolved")).scalar()
    assert n >= 1


def test_confirm_skips_already_owned(tmp_path):
    owned_by = _person(f"Owner {_A}")
    did = _doc(tmp_path / "o.txt", name="o.txt", person_id=owned_by)  # already owned
    (tmp_path / "o.txt").write_text("anything\n")
    res = hc.confirm_documents([did], actor_user_id=1, request_id="t")
    assert res["assigned_count"] == 0 and res["skipped_count"] == 1
    assert _owner(did) == (owned_by, None, None)      # ownership NOT overwritten


def test_confirm_skips_permanent_reject(tmp_path, monkeypatch):
    did, _p = _clean_high_doc(tmp_path, "rej")
    monkeypatch.setattr("app.services.document_owner_proposal.PERMANENT_REJECT_DOCUMENT_IDS",
                        frozenset({did}))
    res = hc.confirm_documents([did], actor_user_id=1, request_id="t")
    assert res["assigned_count"] == 0 and _owner(did) == (None, None, None)


def test_confirm_skips_stale_no_longer_high(tmp_path):
    # a document with no identity -> NO_MATCH -> not eligible -> skipped, never assigned
    f = tmp_path / "n.txt"; f.write_text("adobe creative cloud total 10\n")
    did = _doc(f, name="n.txt")
    res = hc.confirm_documents([did], actor_user_id=1, request_id="t")
    assert res["assigned_count"] == 0 and res["skipped_count"] == 1
    assert _owner(did) == (None, None, None)


def test_confirm_no_client_created(tmp_path):
    did, _p = _clean_high_doc(tmp_path, "nc")
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(people)).scalar()
    hc.confirm_documents([did], actor_user_id=1, request_id="t")
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(people)).scalar()
    assert after == before                             # a bulk confirm NEVER creates a client


def test_confirm_partial_failure_is_isolated(tmp_path, monkeypatch):
    d1, p1 = _clean_high_doc(tmp_path, "ok")
    d2, _p2 = _clean_high_doc(tmp_path, "boom")
    real = hv.evaluate_high

    def _maybe_boom(conn, did, idx, **kw):
        if did == d2:
            raise RuntimeError("evaluation exploded")
        return real(conn, did, idx, **kw)

    monkeypatch.setattr(hc, "evaluate_high", _maybe_boom)
    res = hc.confirm_documents([d1, d2], actor_user_id=1, request_id="t")
    assert res["assigned_count"] == 1 and res["failed_count"] == 1
    assert _owner(d1) == (p1, None, None)             # good doc still assigned despite the other failing


# --- authorization -------------------------------------------------------------------------------

def test_bulk_confirm_requires_client_write():
    dep = require_capability("client.write")
    admin = Principal(1, "a@e.com", "Admin", frozenset({"client.write"}))
    assert dep(principal=admin) is admin
    ordinary = Principal(2, "b@e.com", "Staff", frozenset({"client.read"}))
    with pytest.raises(HTTPException) as exc:
        dep(principal=ordinary)
    assert exc.value.status_code == 403


def test_route_registered_and_gated():
    from app.main import app
    paths = {(getattr(r, "path", None)) for r in app.routes}
    assert "/admin/documents/high-confirm" in paths


# --- View link exposes the EXISTING authorized document route for every row ----------------------

def test_route_attaches_existing_authorized_view_url():
    # The preview must reuse the same authorized View URL as /admin/documents/unassigned/review,
    # NOT invent a new document-serving route.
    from app.routes.admin import _view_url
    from app.routes.admin_high_confirm import _with_view
    rows = [{"document_id": 459, "filename": "Form1095a.pdf"}]
    _with_view(rows)
    assert rows[0]["view_url"] == _view_url(459, "Form1095a.pdf")


def test_high_confirm_page_exposes_view_url_for_each_document():
    from app.routes.admin import templates
    ve, vr = "/documents/459/download?inline=1", "/documents/460/preview"
    elig = [{"document_id": 459, "filename": "Form1095a.pdf", "source_path": "Adrianna Hardy",
             "extraction_class": "ocr", "extraction_method": "ocr_cache", "proposed_entity_type": "person",
             "proposed_entity_id": 7430, "proposed_entity_name": "MARY HARDY", "confidence": "HIGH",
             "evidence_classes": ["name", "address"], "identity_provenance": "content",
             "view_url": ve, "contradictions": []}]
    rev = [{"document_id": 460, "filename": "Expenses.xlsx", "proposed_entity_type": "person",
            "proposed_entity_id": 5284, "proposed_entity_name": "ADRIANNA",
            "contradictions": ["foreign_strong_identifier"], "view_url": vr}]
    html = templates.get_template("admin/high_confirm.html").render(
        request=None, eligible=elig, review=rev, eligible_count=1, review_count=1)
    # ELIGIBLE row: a View control AND a clickable filename, both to the authorized URL
    assert "View ↗" in html
    assert html.count(ve) >= 2                          # View button + filename link
    assert f'>{"Form1095a.pdf"}</a>' in html            # filename is a hyperlink
    # REVIEW REQUIRED row: same — View link present, filename clickable
    assert html.count(vr) >= 2
    assert f'>{"Expenses.xlsx"}</a>' in html
    # no new document-serving path is invented (only /documents/... appears)
    assert "http" not in ve and ve.startswith("/documents/")
