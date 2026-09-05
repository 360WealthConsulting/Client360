"""Email row action on the HOUSEHOLD and BUSINESS document lists.

Same deployed route, same capability, same canonical-only rule as the person Documents tab. UI
wiring only: these tests render the real templates and assert the action, its target, the
capability gate, that vault rows never receive a canonical link, and that nothing is written.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    relationship_entities,
)
from app.security.models import Principal

# documents.view opens the Documents tab; communications.send gates the Email action.
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
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "HBE" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _household(c, tag):
    hid = c.execute(insert(households).values(name=f"Steinman Household {tag}")
                    .returning(households.c.id)).scalar_one()
    pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Steinman{tag}",
                                          household_id=hid, active=True)
                    .returning(people.c.id)).scalar_one()
    c.execute(insert(household_relationships).values(
        household_id=hid, person_id=pid, relationship_type="member",
        is_primary=True, is_primary_household=True))
    return hid, pid


def _business(c, tag):
    return c.execute(insert(relationship_entities).values(
        entity_type="business", name=f"Steinman Holdings {tag}", active=True)
        .returning(relationship_entities.c.id)).scalar_one()


def _doc(c, name, *, person_id=None, household_id=None, organization_id=None, display_name=None):
    u = uuid.uuid4().hex
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"s-{u}", storage_path=f"/vault/{u}.bin",
        storage_uri=f"/vault/{u}.bin", size_bytes=9, sha256=u.ljust(64, "0")[:64], status="active",
        archived=False, display_name=display_name, content_type="application/pdf",
        person_id=person_id, household_id=household_id, organization_id=organization_id,
    ).returning(documents.c.id)).scalar_one()


def _render_household(principal, household_id):
    from fastapi.templating import Jinja2Templates

    from app.services.client360 import get_workspace
    from app.templating import install_filters
    from tests._portal_util import fake_request, render
    ws = get_workspace(principal, household_id=household_id)
    tpl = Jinja2Templates(directory="app/templates")
    install_filters(tpl)
    return render(tpl.TemplateResponse(
        request=fake_request(f"/client/household/{household_id}?tab=documents",
                             state_principal=principal),
        name="client360/household.html",
        context={"principal": principal, "ws": ws, "active_tab": "documents"}))


def _render_business(principal, business_id):
    from fastapi.templating import Jinja2Templates

    from app.services.business_workspace import get_business_workspace
    from app.templating import install_filters
    from tests._portal_util import fake_request, render
    ws = get_business_workspace(business_id)
    tpl = Jinja2Templates(directory="app/templates")
    install_filters(tpl)
    return render(tpl.TemplateResponse(
        request=fake_request(f"/business/{business_id}", state_principal=principal),
        name="business/workspace.html", context={"principal": principal, "ws": ws}))


def _actions_cell(html, document_id):
    # `<tr[^>]*>` because the Documents screen puts data-* attributes on the row. The business
    # workspace still emits a bare <tr>, and both are matched by the same pattern.
    row = re.search(rf'<tr[^>]*>(?:(?!</tr>).)*?/documents/{document_id}/download.*?</tr>',
                    html, re.S)
    assert row, f"row for document {document_id} not found"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cells[-1])).replace("…", "").strip()


# --------------------------------------------------------------------- household
def test_household_canonical_document_shows_email_for_a_sender():
    tag = _tag()
    with engine.begin() as c:
        hid, _ = _household(c, tag)
        did = _doc(c, f"1040 {tag}.pdf", household_id=hid,
                   display_name="2024 - Form 1040 - Steinman Household")
    html = _render_household(SENDER, hid)
    cell = _actions_cell(html, did)
    # Open stays the direct control; everything else sits behind the overflow menu, Email included.
    assert cell.startswith("Open")
    assert "Email" in cell
    assert f'<a href="/documents/{did}/email">Email' in html


def test_household_email_link_points_at_the_existing_route():
    tag = _tag()
    with engine.begin() as c:
        hid, _ = _household(c, tag)
        did = _doc(c, f"doc {tag}.pdf", household_id=hid)
    assert f'href="/documents/{did}/email"' in _render_household(SENDER, hid)
    from app.main import app
    assert "/documents/{document_id}/email" in {getattr(r, "path", None) for r in app.routes}


def test_household_without_communications_send_has_no_email():
    tag = _tag()
    with engine.begin() as c:
        hid, _ = _household(c, tag)
        did = _doc(c, f"doc {tag}.pdf", household_id=hid)
    html = _render_household(NO_SEND, hid)
    assert f"/documents/{did}/email" not in html
    cell = _actions_cell(html, did)
    assert cell.startswith("Open")                                # Open preserved
    assert "Email" not in cell
    assert f'href="/documents/{did}/download"' in html            # download preserved


def test_household_filename_opens_the_document_and_download_stays_reachable():
    """The filename anchor now opens the preview drawer rather than downloading immediately — the
    Documents screen selects a document instead of leaving the page. Download is still one click
    away in the row menu, and the display name is still what is shown."""
    tag = _tag()
    with engine.begin() as c:
        hid, _ = _household(c, tag)
        did = _doc(c, f"w2 {tag}.pdf", household_id=hid, display_name="2025 - W-2 - Adam Steinman")
    html = _render_household(SENDER, hid)
    assert re.search(
        rf'<a class="docrow-name" href="/client/household/{hid}/documents/{did}/panel\?panel=preview"'
        rf'[^>]*>\s*2025 - W-2 - Adam Steinman\s*</a>', html), "name opens the preview panel"
    assert f'<a href="/documents/{did}/download">Download</a>' in html


# --------------------------------------------------------------------- business
def test_business_canonical_document_shows_email_for_a_sender():
    tag = _tag()
    with engine.begin() as c:
        bid = _business(c, tag)
        did = _doc(c, f"1120S {tag}.pdf", organization_id=bid,
                   display_name="2024 - Form 1120S - Steinman Holdings")
    html = _render_business(SENDER, bid)
    # The business action label was "Download" when this test was written; a later bounded task
    # renamed it to "Open" so all three workspaces read alike. The Email action is unchanged.
    assert _actions_cell(html, did) == "Open · Email"
    assert f'<a href="/documents/{did}/email">Email</a>' in html


def test_business_email_link_points_at_the_existing_route():
    tag = _tag()
    with engine.begin() as c:
        bid = _business(c, tag)
        did = _doc(c, f"doc {tag}.pdf", organization_id=bid)
    assert f'href="/documents/{did}/email"' in _render_business(SENDER, bid)


def test_business_without_communications_send_has_no_email():
    tag = _tag()
    with engine.begin() as c:
        bid = _business(c, tag)
        did = _doc(c, f"doc {tag}.pdf", organization_id=bid)
    html = _render_business(NO_SEND, bid)
    assert f"/documents/{did}/email" not in html
    assert _actions_cell(html, did) == "Open"                      # the download action remains
    assert f'<a href="/documents/{did}/download">Open</a>' in html


def test_business_document_ordering_and_count_are_unchanged():
    tag = _tag()
    with engine.begin() as c:
        bid = _business(c, tag)
        ids = [_doc(c, f"doc{i} {tag}.pdf", organization_id=bid) for i in range(3)]
    from app.services.business_workspace import get_business_workspace
    ws = get_business_workspace(bid)
    assert ws["document_count"] == 3
    assert sorted(d["id"] for d in ws["documents"]) == sorted(ids)
    assert all(d["source_kind"] == "canonical" for d in ws["documents"])


# --------------------------------------------------------------------- vault safety
def test_a_non_canonical_row_never_receives_a_canonical_email_link():
    """A vault row's id belongs to vault_documents; linking it to /documents/{id}/email would
    address an unrelated CANONICAL document. Injected into a REAL rendered workspace beside a real
    canonical row, so the two are proven to be treated differently on the same page."""
    from fastapi.templating import Jinja2Templates

    from app.services.client360 import get_workspace
    from app.templating import install_filters
    from tests._portal_util import fake_request, render
    tag = _tag()
    with engine.begin() as c:
        hid, _ = _household(c, tag)
        canonical = _doc(c, f"canonical {tag}.pdf", household_id=hid)
    ws = get_workspace(SENDER, household_id=hid)
    # The Documents screen renders `screen.rows`, so the vault row is injected there — beside the
    # real canonical row it has to be told apart from, on the same rendered page.
    rows = ws["sections"]["documents"]["screen"]["rows"]
    template_row = dict(rows[0])
    rows.append({**template_row, "id": 999_001, "name": "Vault document 999001",
                 "source_kind": "vault", "source": "Vault", "sources": [],
                 "download_url": "/api/vault/documents/999001/download"})
    tpl = Jinja2Templates(directory="app/templates")
    install_filters(tpl)
    html = render(tpl.TemplateResponse(
        request=fake_request(f"/client/household/{hid}?tab=documents", state_principal=SENDER),
        name="client360/household.html",
        context={"principal": SENDER, "ws": ws, "active_tab": "documents"}))
    assert "/documents/999001/email" not in html               # vault id NEVER emailed
    assert "/api/vault/documents/999001/download" in html      # its own download preserved
    assert f"/documents/{canonical}/email" in html             # canonical sibling still offered


def test_templates_gate_on_both_canonical_and_capability():
    # The person and household Documents surfaces share one partial, so they cannot gate this
    # differently; `is_canonical` is that partial's alias for the same source_kind test. The
    # business workspace still carries its own copy of the gate.
    #
    # encoding= is explicit: these templates contain em-dashes that the Windows default codec
    # cannot decode at all.
    for path, gate in (
        ("app/templates/client360/_documents_screen.html",
         'is_canonical and principal and principal.can("communications.send")'),
        ("app/templates/business/workspace.html",
         'd.source_kind == "canonical" and principal and principal.can("communications.send")'),
    ):
        tpl = open(path, encoding="utf-8").read()
        assert gate in tpl, path

    partial = open("app/templates/client360/_documents_screen.html", encoding="utf-8").read()
    assert '{% set is_canonical = d.source_kind == "canonical" %}' in partial


# --------------------------------------------------------------------- no writes
def test_rendering_mutates_nothing():
    tag = _tag()
    with engine.begin() as c:
        hid, _ = _household(c, tag)
        bid = _business(c, tag)
        hdoc = _doc(c, f"hh {tag}.pdf", household_id=hid)
        bdoc = _doc(c, f"biz {tag}.pdf", organization_id=bid)

    def snapshot():
        with engine.connect() as c:
            rows = sorted(c.execute(select(documents).where(
                documents.c.id.in_([hdoc, bdoc]))).mappings(), key=lambda r: r["id"])
            # Only the fixture rows: a bare COUNT(*) over `documents` makes this test fail
            # whenever anything else is writing to the same database, which says nothing about
            # whether RENDERING wrote anything.
            return [dict(r) for r in rows]

    before = snapshot()
    for principal in (SENDER, NO_SEND):
        _render_household(principal, hid)
        _render_business(principal, bid)
    assert snapshot() == before
