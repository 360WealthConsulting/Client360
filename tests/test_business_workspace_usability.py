"""Business workspace parity: a Payroll link, and document rows that behave like the client ones.

Template-only. These tests render the real template and assert the capability gates, the link
targets, and that the person/household document rows are untouched.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import documents, engine, relationship_entities
from app.security.models import Principal

_BASE = {"client.read", "documents.view"}
FULL = Principal(1, "staff@t", "Staff",
                 frozenset(_BASE | {"payroll.read", "communications.send", "documents.delete",
                                    "record.read_all", "record.write_all"}))
NO_PAYROLL = Principal(2, "nopay@t", "NoPayroll",
                       frozenset(_BASE | {"communications.send", "documents.delete",
                                          "record.read_all"}))
PLAIN = Principal(3, "plain@t", "Plain", frozenset(_BASE | {"record.read_all"}))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        with engine.begin() as c:
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(f"%{tag}%"))))
            if ents:
                c.execute(documents.delete().where(documents.c.organization_id.in_(ents)))
                c.execute(relationship_entities.delete()
                          .where(relationship_entities.c.id.in_(ents)))
    _TAGS.clear()


def _tag():
    t = "BWU" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _business_with_doc(tag, name="1120S 2024.pdf", display_name="2024 - Form 1120S - Holdings"):
    with engine.begin() as c:
        bid = c.execute(insert(relationship_entities).values(
            entity_type="business", name=f"Steinman Holdings {tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        u = uuid.uuid4().hex
        did = c.execute(insert(documents).values(
            original_name=name, stored_name=f"s-{u}", storage_path=f"/vault/{u}.bin",
            storage_uri=f"/vault/{u}.bin", size_bytes=9, sha256=u.ljust(64, "0")[:64],
            status="active", archived=False, display_name=display_name,
            content_type="application/pdf", organization_id=bid).returning(documents.c.id)
        ).scalar_one()
    return bid, did


def _render(principal, business_id):
    from fastapi.templating import Jinja2Templates

    from app.services.business_workspace import get_business_workspace
    from app.templating import install_filters
    from tests._portal_util import fake_request, render
    tpl = Jinja2Templates(directory="app/templates")
    install_filters(tpl)
    return render(tpl.TemplateResponse(
        request=fake_request(f"/business/{business_id}", state_principal=principal),
        name="business/workspace.html",
        context={"principal": principal, "ws": get_business_workspace(business_id)}))


def _actions_cell(html, document_id):
    row = re.search(rf'<tr>(?:(?!</tr>).)*?/documents/{document_id}/download.*?</tr>', html, re.S)
    assert row, f"row for document {document_id} not found"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cells[-1])).strip()


# --------------------------------------------------------------------- payroll link
def test_payroll_link_present_with_payroll_read():
    tag = _tag()
    bid, _ = _business_with_doc(tag)
    html = _render(FULL, bid)
    assert f'href="/business/{bid}/payroll"' in html
    assert ">Payroll</a>" in html


def test_payroll_link_absent_without_payroll_read():
    tag = _tag()
    bid, _ = _business_with_doc(tag)
    html = _render(NO_PAYROLL, bid)
    assert "/payroll" not in html
    assert ">Payroll</a>" not in html


def test_payroll_link_target_matches_the_existing_route():
    tag = _tag()
    bid, _ = _business_with_doc(tag)
    assert f'href="/business/{bid}/payroll"' in _render(FULL, bid)
    from app.main import app
    assert "/business/{organization_id}/payroll" in {getattr(r, "path", None) for r in app.routes}


def test_template_gate_matches_the_payroll_route_capability():
    import inspect

    from app.routes import payroll
    tpl = open("app/templates/business/workspace.html", encoding="utf-8").read()
    assert 'principal.can("payroll.read")' in tpl
    assert 'require_capability("payroll.read")' in inspect.getsource(payroll.payroll_dashboard)


# --------------------------------------------------------------------- document row parity
def test_document_filename_is_an_anchor_to_the_download_url():
    tag = _tag()
    bid, did = _business_with_doc(tag)
    html = _render(FULL, bid)
    assert f'<td><a href="/documents/{did}/download">2024 - Form 1120S - Holdings</a></td>' in html


def test_action_label_is_open_not_download():
    tag = _tag()
    bid, did = _business_with_doc(tag)
    html = _render(FULL, bid)
    assert f'<a href="/documents/{did}/download">Open</a>' in html
    assert ">Download</a>" not in html


def test_business_document_row_reads_open_email_delete():
    tag = _tag()
    bid, did = _business_with_doc(tag)
    assert _actions_cell(_render(FULL, bid), did) == "Open · Email · Delete"


def test_email_and_delete_keep_their_existing_capability_gates():
    tag = _tag()
    bid, did = _business_with_doc(tag)
    plain = _render(PLAIN, bid)
    assert _actions_cell(plain, did) == "Open"                  # neither capability held
    assert f"/documents/{did}/email" not in plain
    assert f"/documents/{did}/delete" not in plain
    full = _render(FULL, bid)
    assert f'href="/documents/{did}/email"' in full
    assert f'action="/business/{bid}/documents/{did}/delete"' in full
    assert 'name="confirm" value="yes"' in full


def test_download_target_ordering_and_count_are_unchanged():
    tag = _tag()
    with engine.begin() as c:
        bid = c.execute(insert(relationship_entities).values(
            entity_type="business", name=f"Steinman Holdings {tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        ids = []
        for i in range(3):
            u = uuid.uuid4().hex
            ids.append(c.execute(insert(documents).values(
                original_name=f"doc{i}.pdf", stored_name=f"s-{u}", storage_path=f"/v/{u}",
                storage_uri=f"/v/{u}", size_bytes=1, sha256=u.ljust(64, "0")[:64], status="active",
                archived=False, content_type="application/pdf", organization_id=bid)
                .returning(documents.c.id)).scalar_one())
    from app.services.business_workspace import get_business_workspace
    ws = get_business_workspace(bid)
    assert ws["document_count"] == 3
    assert sorted(d["id"] for d in ws["documents"]) == sorted(ids)
    html = _render(FULL, bid)
    for did in ids:
        assert f'href="/documents/{did}/download"' in html
        assert f'href="/documents/{did}/email"' in html


# --------------------------------------------------------------------- nothing else moved
def test_person_and_household_document_templates_are_unchanged():
    """This change is scoped to the business template; the client surfaces keep their own controls.

    The client person and household Documents surfaces now render one SHARED partial, which is a
    stronger form of the same guarantee — there is no longer a per-surface copy that could drift
    toward the business template. So the assertions moved with the markup, and the two page
    templates are checked for what they must NOT have grown.

    encoding= is explicit throughout: these templates contain em-dashes, and open() with the
    Windows default codec cannot read them at all.
    """
    partial = "app/templates/client360/_documents_screen.html"
    tpl = open(partial, encoding="utf-8").read()
    assert 'class="act-open"' in tpl, partial                       # Open is still a direct control
    assert "/payroll" not in tpl, partial                           # no business-template concepts
    assert 'is_canonical and principal and principal.can("communications.send")' in tpl, partial

    for path in ("app/templates/client360/workspace.html",
                 "app/templates/client360/household.html"):
        page = open(path, encoding="utf-8").read()
        assert "/payroll" not in page, path
        # The document row markup lives in the partial; a second copy here would be the drift
        # this test exists to catch.
        assert "d.download_url" not in page, path
        assert 'include "client360/_documents_screen.html"' in page, path


def test_rendering_is_read_only():
    tag = _tag()
    bid, did = _business_with_doc(tag)

    def snapshot():
        with engine.connect() as c:
            row = dict(c.execute(select(documents).where(documents.c.id == did)).mappings().one())
            return row, c.scalar(select(func.count()).select_from(documents))

    before = snapshot()
    for principal in (FULL, NO_PAYROLL, PLAIN):
        _render(principal, bid)
    assert snapshot() == before
