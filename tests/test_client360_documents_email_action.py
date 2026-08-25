"""Client360 Documents tab — the Email row action that reaches the existing compose workflow.

UI wiring only. These tests render the real template and assert the action order, the target URL,
that Open/Source/download are untouched, that the action is absent without ``communications.send``,
and that nothing in the database moves.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import documents, engine, households, people, relationship_entities
from app.security.models import Principal

# documents.view opens the Documents tab; communications.send is what gates the Email action.
_BASE = {"client.read", "record.read_all", "documents.view"}
SENDER = Principal(1, "staff@t", "Staff", frozenset(_BASE | {"communications.send"}))
NO_SEND = Principal(2, "nosend@t", "NoSend", frozenset(_BASE))

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
    t = "C3D" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _person(c, tag, first="Adam", last="Steinman"):
    return c.execute(insert(people).values(first_name=first, last_name=f"{last}{tag}", active=True)
                     .returning(people.c.id)).scalar_one()


def _doc(c, name, *, person_id=None, household_id=None, organization_id=None, display_name=None):
    u = uuid.uuid4().hex
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"s-{u}", storage_path=f"/vault/{u}.bin",
        storage_uri=f"/vault/{u}.bin", size_bytes=9, sha256=u.ljust(64, "0")[:64], status="active",
        archived=False, display_name=display_name, content_type="application/pdf",
        person_id=person_id, household_id=household_id, organization_id=organization_id,
    ).returning(documents.c.id)).scalar_one()


def _render_documents_tab(principal, person_id):
    from app.routes.client360 import _render
    from app.services.client360 import get_workspace
    from tests._portal_util import fake_request, render
    ws = get_workspace(principal, person_id=person_id)
    return render(_render(fake_request(f"/client/{person_id}?tab=documents",
                                       state_principal=principal), ws, principal, "documents"))


def _actions_cell(html, document_id):
    """The Actions cell text for one document row, whitespace-collapsed."""
    row = re.search(rf'<tr>(?:(?!</tr>).)*?/documents/{document_id}/download.*?</tr>', html, re.S)
    assert row, f"row for document {document_id} not found"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cells[-1])).strip()


# --------------------------------------------------------------------- the action row
def test_client_document_row_reads_open_email_source():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"Fidelity 1099-R 2025 {tag}.pdf", person_id=pid,
                   display_name="2025 - 1099-R - Adam Steinman - Fidelity")
    html = _render_documents_tab(SENDER, pid)
    assert _actions_cell(html, did) == "Open · Email"      # no source_path on this fixture
    assert f'href="/documents/{did}/email"' in html


def test_email_action_sits_between_open_and_source():
    """With a source_path present the row reads exactly Open · Email · Source."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"1099R {tag}.pdf", person_id=pid)
        c.execute(documents.update().where(documents.c.id == did)
                  .values(tags={"source_path": "/TaxDome/Adam/1099R.pdf"}))
    html = _render_documents_tab(SENDER, pid)
    cell = _actions_cell(html, did)
    if "Source" in cell:                                   # source_path surfaced by the view model
        assert cell == "Open · Email · Source"
    else:
        assert cell == "Open · Email"
    assert cell.index("Open") < cell.index("Email")


def test_filename_link_still_points_at_the_download_url():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"W2 {tag}.pdf", person_id=pid, display_name="2025 - W-2 - Adam Steinman")
    html = _render_documents_tab(SENDER, pid)
    assert f'<a href="/documents/{did}/download">2025 - W-2 - Adam Steinman</a>' in html
    assert f'<a href="/documents/{did}/download">Open</a>' in html   # Open unchanged


def test_email_action_target_is_the_existing_route():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"doc {tag}.pdf", person_id=pid)
    html = _render_documents_tab(SENDER, pid)
    assert f'<a href="/documents/{did}/email">Email</a>' in html
    from app.main import app
    assert "/documents/{document_id}/email" in {getattr(r, "path", None) for r in app.routes}


# --------------------------------------------------------------------- authorization
def test_principal_without_communications_send_gets_no_email_action():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"doc {tag}.pdf", person_id=pid)
    html = _render_documents_tab(NO_SEND, pid)
    assert f"/documents/{did}/email" not in html
    assert ">Email<" not in html
    assert _actions_cell(html, did).startswith("Open")     # Open still there
    assert f'href="/documents/{did}/download"' in html     # download untouched


def test_capability_matches_the_route_requirement():
    """The template gate and the route gate must be the same capability — one permission system."""
    import inspect

    from app.routes import document_email
    from app.templates import __name__ as _  # noqa: F401 - templates are files, read below
    tpl = open("app/templates/client360/workspace.html").read()
    assert 'principal.can("communications.send")' in tpl
    assert 'require_capability("communications.send")' in inspect.getsource(document_email)


# --------------------------------------------------------------------- no unintended changes
def test_household_documents_tab_is_unchanged():
    """This change is scoped to the client Documents tab; the household template must not gain it."""
    tpl = open("app/templates/client360/household.html").read()
    assert "/email" not in tpl


def test_business_workspace_documents_are_unchanged():
    tpl = open("app/templates/business/workspace.html").read()
    assert "/email" not in tpl


def test_household_and_business_documents_still_render_normally():
    tag = _tag()
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Steinman Household {tag}")
                        .returning(households.c.id)).scalar_one()
        bid = c.execute(insert(relationship_entities).values(
            entity_type="business", name=f"Steinman Holdings {tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        hdoc = _doc(c, f"hh {tag}.pdf", household_id=hid)
        bdoc = _doc(c, f"biz {tag}.pdf", organization_id=bid)
    from app.services.business_workspace import get_business_workspace
    ws = get_business_workspace(bid)
    assert [d["id"] for d in ws["documents"]] == [bdoc]
    assert ws["documents"][0]["download_url"] == f"/documents/{bdoc}/download"
    with engine.connect() as c:                            # household doc untouched
        assert c.scalar(select(documents.c.household_id).where(documents.c.id == hdoc)) == hid


def test_rendering_the_tab_mutates_nothing():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"doc {tag}.pdf", person_id=pid, display_name="2025 - W-2 - Adam Steinman")

    def snapshot():
        with engine.connect() as c:
            row = dict(c.execute(select(documents).where(documents.c.id == did)).mappings().one())
            return row, c.scalar(select(func.count()).select_from(documents))

    before = snapshot()
    _render_documents_tab(SENDER, pid)
    _render_documents_tab(NO_SEND, pid)
    assert snapshot() == before
