"""Delivered (downloaded) document filenames use display_name when present.

Label-only change. Every test here also holds the invariants: the same bytes are served, the file is
still located by storage_path/storage_uri, and original_name / stored_name / sha256 never move.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert, select

from app.db import documents, engine, households, people, relationship_entities
from app.services.document_naming import document_delivery_filename as delivered

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(like))))
            for col, vals in ((documents.c.person_id, ppl), (documents.c.household_id, hhs),
                              (documents.c.organization_id, ents)):
                if vals:
                    c.execute(documents.delete().where(col.in_(vals)))
            if ents:
                c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(ents)))
            if ppl:
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "DEL" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _doc(c, tmp_path, name, *, display_name=None, body=b"PDF-BYTES", **owner):
    u = uuid.uuid4().hex
    f = tmp_path / f"{u}.bin"
    f.write_bytes(body)
    import hashlib
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"stored-{u}", storage_path=str(f), storage_uri=str(f),
        size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), status="active",
        archived=False, display_name=display_name, content_type="application/pdf", **owner,
    ).returning(documents.c.id)).scalar_one()


def _row(doc_id):
    with engine.connect() as c:
        return dict(c.execute(select(documents).where(documents.c.id == doc_id)).mappings().one())


# --------------------------------------------------------------------- helper: the naming contract
@pytest.mark.parametrize("original,display,expected", [
    # the two production examples from the brief
    ("2025 W2 (2).pdf", "2025 - W-2 - ADAM DAVIS - (2)", "2025 - W-2 - ADAM DAVIS - (2).pdf"),
    ("Fidelity 1099-R 2025.pdf", "2025 - 1099-R - Adam Steinman - Fidelity",
     "2025 - 1099-R - Adam Steinman - Fidelity.pdf"),
    # no display_name -> original, byte for byte
    ("Fidelity 1099-R 2025.pdf", None, "Fidelity 1099-R 2025.pdf"),
    ("scan001.pdf", "", "scan001.pdf"),
    ("scan001.pdf", "   ", "scan001.pdf"),
    # extension never duplicated, case tolerated
    ("x.pdf", "already named.pdf", "already named.pdf"),
    ("x.pdf", "already named.PDF", "already named.PDF"),
    # extension preservation across the common types
    ("photo.jpg", "2024 - Driver's License - Ann", "2024 - Driver's License - Ann.jpg"),
    ("photo.JPG", "2024 - Driver's License - Ann", "2024 - Driver's License - Ann.JPG"),
    ("img.jpeg", "2024 - Passport - Ann", "2024 - Passport - Ann.jpeg"),
    ("book.xlsx", "2024 - Payroll Summary - Acme", "2024 - Payroll Summary - Acme.xlsx"),
    ("letter.docx", "2024 - Engagement Letter - Ann", "2024 - Engagement Letter - Ann.docx"),
    # original has no extension -> nothing invented
    ("noextension", "2024 - Form 1040 - Ann", "2024 - Form 1040 - Ann"),
])
def test_delivered_filename_contract(original, display, expected):
    assert delivered({"original_name": original, "display_name": display}) == expected


def test_missing_row_or_names_never_raises():
    assert delivered(None) == ""
    assert delivered({"original_name": None, "display_name": None}) == ""


# --------------------------------------------------------------------- security
@pytest.mark.parametrize("display", [
    "../../etc/passwd", "..\\..\\windows\\system32\\config",
    "/absolute/path/secret.pdf", "....//....//escape",
])
def test_path_like_display_name_cannot_traverse_or_leak_a_path(display):
    out = delivered({"original_name": "a.pdf", "display_name": display})
    assert "/" not in out and "\\" not in out
    assert ".." not in out
    assert out.endswith(".pdf")


@pytest.mark.parametrize("display", [
    "bad\r\nSet-Cookie: evil=1", "nul\x00byte", "tab\there", "\r\n\r\n",
    'quote" ; filename="evil.exe', "pipe|and<angle>brackets",
])
def test_control_characters_and_quotes_cannot_inject_a_header(display):
    out = delivered({"original_name": "a.pdf", "display_name": display})
    assert not any(ch in out for ch in "\r\n\x00\"<>|/\\")
    assert out and out.endswith(".pdf")


def test_a_display_name_of_only_dots_falls_back_to_the_original():
    assert delivered({"original_name": "a.pdf", "display_name": "..."}) == "a.pdf"
    assert delivered({"original_name": "a.pdf", "display_name": "///"}) == "a.pdf"


# --------------------------------------------------------------------- route behaviour
def _download(document_id, **kw):
    from app.routes.documents import download_document
    from tests._portal_util import fake_request
    return download_document(document_id, fake_request(f"/documents/{document_id}/download"), **kw)


def test_staff_download_uses_display_name_and_serves_the_same_bytes(tmp_path):
    tag = _tag()
    with engine.begin() as c:
        pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Davis{tag}",
                                              active=True).returning(people.c.id)).scalar_one()
        did = _doc(c, tmp_path, "2025 W2 (2).pdf", display_name="2025 - W-2 - ADAM DAVIS - (2)",
                   body=b"THE-REAL-BYTES", person_id=pid)
    before = _row(did)
    resp = _download(did)
    assert resp.filename == "2025 - W-2 - ADAM DAVIS - (2).pdf"
    assert str(resp.path) == before["storage_path"]              # same physical file
    assert resp.path.read_bytes() == b"THE-REAL-BYTES"           # same bytes
    assert _row(did) == before                                   # nothing mutated by downloading


def test_staff_download_without_a_display_name_is_unchanged(tmp_path):
    tag = _tag()
    with engine.begin() as c:
        pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Davis{tag}",
                                              active=True).returning(people.c.id)).scalar_one()
        did = _doc(c, tmp_path, "Fidelity 1099-R 2025.pdf", person_id=pid)
    assert _download(did).filename == "Fidelity 1099-R 2025.pdf"


def test_person_household_and_business_downloads_all_work(tmp_path):
    tag = _tag()
    with engine.begin() as c:
        pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Davis{tag}",
                                              active=True).returning(people.c.id)).scalar_one()
        hid = c.execute(insert(households).values(name=f"Davis Household {tag}")
                        .returning(households.c.id)).scalar_one()
        bid = c.execute(insert(relationship_entities).values(
            entity_type="business", name=f"Davis Holdings {tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        ids = [
            _doc(c, tmp_path, "w2.pdf", display_name="2025 - W-2 - Adam", person_id=pid),
            _doc(c, tmp_path, "1040.pdf", display_name="2024 - Form 1040 - Davis", household_id=hid),
            _doc(c, tmp_path, "1120s.pdf", display_name="2024 - Form 1120S - Holdings",
                 organization_id=bid),
        ]
    expected = ["2025 - W-2 - Adam.pdf", "2024 - Form 1040 - Davis.pdf",
                "2024 - Form 1120S - Holdings.pdf"]
    for did, want in zip(ids, expected, strict=True):
        assert _download(did).filename == want


def test_inline_disposition_still_derives_from_the_original_file_type(tmp_path):
    """The inline/attachment decision sniffs the REAL file type from original_name, not the label."""
    tag = _tag()
    with engine.begin() as c:
        pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Davis{tag}",
                                              active=True).returning(people.c.id)).scalar_one()
        did = _doc(c, tmp_path, "statement.pdf", display_name="2025 - Brokerage Statement - Adam",
                   person_id=pid)
    resp = _download(did, inline=True)
    assert "inline" in resp.headers["content-disposition"]
    assert resp.filename == "2025 - Brokerage Statement - Adam.pdf"


def test_download_route_authorization_dependencies_are_unchanged():
    """This route is gated by AuthenticationMiddleware on the /documents path, not by a route
    dependency — the change must not have introduced or removed one."""
    from app.main import app
    route = next(r for r in app.routes
                 if getattr(r, "path", None) == "/documents/{document_id}/download")
    param_names = {p.name for p in route.dependant.query_params + route.dependant.path_params}
    assert param_names == {"document_id", "inline"}
    assert route.dependant.dependencies == []


def test_portal_download_uses_the_same_helper():
    """The portal serves canonical documents rows to clients; it must deliver the same name."""
    import inspect

    from app.routes import portal
    src = inspect.getsource(portal.api_portal_document_download)
    assert "document_delivery_filename(row)" in src
    assert "documents.c.display_name" in src
    assert "require_scope(" in src                               # scope check still present
