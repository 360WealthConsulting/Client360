"""Client360 Documents tab — the Email row action that reaches the existing compose workflow.

UI wiring only. These tests render the real template and assert the action order, the target URL,
that Open/Source/download are untouched, that the action is absent without ``communications.send``,
and that nothing in the database moves.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import insert, select

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
    """The Actions cell text for one document row, whitespace-collapsed.

    Phase 1 moved the row actions into an overflow (three-dot) menu, so the cell now reads as the
    menu's items rather than a middot-separated strip. The guarantees these tests exist for - which
    actions appear, in what order, and under which capability - are unchanged."""
    row = re.search(rf'<tr[^>]*>(?:(?!</tr>).)*?/documents/{document_id}/download.*?</tr>',
                    html, re.S)
    assert row, f"row for document {document_id} not found"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
    text = re.sub(r"<[^>]+>", " ", cells[-1])
    return re.sub(r"\s+", " ", text).replace("\u2026", "").strip()


# --------------------------------------------------------------------- the action row
def test_client_document_row_reads_open_email_source():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"Fidelity 1099-R 2025 {tag}.pdf", person_id=pid,
                   display_name="2025 - 1099-R - Adam Steinman - Fidelity")
    html = _render_documents_tab(SENDER, pid)
    cell = _actions_cell(html, did)
    # Open is a direct control; everything else sits behind the three-dot menu.
    assert cell.startswith("Open"), "Open stays immediately accessible"
    assert "⋯" in cell, "the remaining actions live behind an overflow menu"
    assert "Email" in cell
    assert "Source location" not in cell                   # no source_path on this fixture
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
    assert "Open" in cell and "Email" in cell
    assert cell.index("Open") < cell.index("Email")
    if "Source location" in cell:                          # source_path surfaced by the view model
        assert cell.index("Email") < cell.index("Source location")


def test_filename_opens_the_preview_panel_and_download_stays_reachable():
    """The filename anchor now SELECTS the document — it opens the preview drawer instead of
    downloading and leaving the page, which is the whole point of the Documents screen. Download
    is still one click away in the row menu, and the display name is still the label."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"W2 {tag}.pdf", person_id=pid, display_name="2025 - W-2 - Adam Steinman")
    html = _render_documents_tab(SENDER, pid)
    assert re.search(
        rf'<a class="docrow-name" href="/client/{pid}/documents/{did}/panel\?panel=preview"'
        rf'[^>]*>\s*2025 - W-2 - Adam Steinman\s*</a>', html), "name opens the preview panel"
    assert f'<a href="/documents/{did}/download">Download</a>' in html   # menu download preserved
    # The drawer is an ENHANCEMENT: that href is a real page, so the panel is reachable with
    # JavaScript disabled.
    assert "data-doc-open" in html


def test_email_action_target_is_the_existing_route():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"doc {tag}.pdf", person_id=pid)
    html = _render_documents_tab(SENDER, pid)
    assert f'<a href="/documents/{did}/email">Email' in html
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
    assert ">Email" not in html
    assert "Open" in _actions_cell(html, did)              # Open still there
    assert f'href="/documents/{did}/download"' in html     # download untouched


def test_capability_matches_the_route_requirement():
    """The template gate and the route gate must be the same capability — one permission system."""
    import inspect

    from app.routes import document_email
    from app.templates import __name__ as _  # noqa: F401 - templates are files, read below
    # The Documents screen lives in a shared partial. encoding= is explicit: these templates
    # contain em-dashes the Windows default codec cannot decode.
    tpl = open("app/templates/client360/_documents_screen.html", encoding="utf-8").read()
    assert 'principal.can("communications.send")' in tpl
    assert 'require_capability("communications.send")' in inspect.getsource(document_email)


# --------------------------------------------------------------------- no unintended changes
def test_household_and_business_use_the_identical_gate():
    """Superseded scope guard. These two surfaces originally had no Email action (this change was
    scoped to the person tab); they gained it in a later bounded task and must use the SAME
    canonical-plus-capability gate, so the rule cannot diverge per surface."""
    # The person and household surfaces now share ONE partial, which is a stronger version of
    # this guarantee than asserting the same string twice: they cannot diverge at all.
    # `is_canonical` is that partial's alias for the same source_kind test.
    for path, gate in (
        ("app/templates/client360/_documents_screen.html",
         'is_canonical and principal and principal.can("communications.send")'),
        ("app/templates/business/workspace.html",
         'd.source_kind == "canonical" and principal and principal.can("communications.send")'),
    ):
        tpl = open(path, encoding="utf-8").read()
        assert gate in tpl, path
        assert '/documents/{{ d.id }}/email' in tpl, path

    for path in ("app/templates/client360/workspace.html",
                 "app/templates/client360/household.html"):
        assert '/email' not in open(path, encoding="utf-8").read(), path


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
            # Only the fixture row: a bare COUNT(*) over `documents` makes this assertion depend
            # on every other writer touching the database, which says nothing about whether
            # RENDERING wrote anything.
            return dict(c.execute(select(documents).where(documents.c.id == did)).mappings().one())

    before = snapshot()
    _render_documents_tab(SENDER, pid)
    _render_documents_tab(NO_SEND, pid)
    assert snapshot() == before


# --------------------------------------------------------- row overflow menu presentation
# The documents table scrolls horizontally, and overflow-x:auto forces overflow-y to a
# non-visible value - so an absolutely positioned menu is clipped by that scroll container and
# spills into the rows below instead of floating over them. documents.js promotes an open menu to
# position:fixed, which escapes ancestor overflow. These pin the contract that fix depends on.

def _assets():
    # `.rowmenu` moved to app.css in Phase 3 (the workspace identity header reuses the
    # same disclosure on surfaces that never load client360.css). Both sheets are read
    # so these assertions pin the RULE, not the file it happens to live in.
    import pathlib
    return ("\n".join(pathlib.Path(f).read_text(encoding="utf-8") for f in
                      ("app/static/css/app.css", "app/static/css/client360.css")),
            pathlib.Path("app/static/js/documents.js").read_text(encoding="utf-8"))


def test_the_row_menu_keeps_details_summary_markup():
    """Keyboard operation and the no-JS fallback both depend on this staying <details>."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag)
        did = _doc(c, f"menu {tag}.pdf", person_id=pid)
    html = _render_documents_tab(SENDER, pid)
    assert '<details class="rowmenu">' in html
    assert "<summary" in html and "</details>" in html
    # Open stays a direct control outside the menu; Email stays inside it.
    assert f'<a class="act-open" href="/client/{pid}/documents/{did}/panel?panel=preview"' in html
    assert f'/documents/{did}/email' in html


def test_the_open_menu_escapes_the_scroll_container():
    css, js = _assets()
    # Fallback is absolute (correct with no JavaScript); the promoted state is fixed.
    assert ".rowmenu .menu { position:absolute;" in css
    assert ".rowmenu .menu.is-floating { position:fixed;" in css
    assert "is-floating" in js, "the script must promote an opened menu"
    # position:fixed is what is NOT clipped by an ancestor's overflow.
    assert "position:fixed" in css


def test_the_menu_floats_on_an_opaque_surface_above_the_rows():
    css, _ = _assets()
    rule = css[css.index(".rowmenu .menu {"):css.index(".rowmenu .menu.is-floating")]
    assert "background:var(--surface)" in rule, "opaque, so rows cannot show through"
    assert "border:1px solid var(--border-strong)" in rule
    assert "box-shadow:var(--shadow)" in rule
    floating = css[css.index(".rowmenu .menu.is-floating"):]
    floating = floating[:floating.index("}") + 1]
    assert "z-index:1000" in floating, "must paint above the table"


def test_the_menu_is_out_of_flow_so_it_cannot_grow_a_row():
    css, _ = _assets()
    rule = css[css.index(".rowmenu .menu {"):css.index(".rowmenu .menu.is-floating")]
    assert "position:absolute" in rule
    floating = css[css.index(".rowmenu .menu.is-floating"):]
    assert "position:fixed" in floating[:floating.index("}") + 1]


def test_the_script_handles_the_bottom_of_the_viewport_and_closes_on_scroll():
    _, js = _assets()
    assert "clientHeight" in js and "getBoundingClientRect" in js, "it measures before placing"
    assert "btn.top - GAP - box.height" in js, "flips above the button when there is no room below"
    assert 'addEventListener("scroll"' in js, "a fixed menu must not float away from its row"
    assert 'addEventListener("resize"' in js


def test_the_horizontal_scroller_is_still_present():
    """The fix must not have been achieved by removing the scroll container."""
    css, _ = _assets()
    assert ".card.table-wrap { overflow:visible; }" in css, "one scroller, the inner one"
    assert "table.data.docs-table { width:100%; min-width:880px; table-layout:fixed; }" in css
