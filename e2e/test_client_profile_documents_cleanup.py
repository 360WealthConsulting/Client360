"""The cleaned client-profile Documents tab, in a real browser.

The unit tests in ``tests/test_client_profile_documents_cleanup.py`` prove the markup. This proves
the rendered page: that the cleanup rail is not merely absent from the HTML but absent from the
layout, that the list now occupies the width the rail was taking, and that every control a staff
member uses is on screen and reachable.

Scrolling is deliberately NOT asserted here. It works, this change does not alter the frame that
makes it work, and ``e2e/test_documents_workspace_scroll.py`` already owns that behaviour. The one
scrolling-adjacent thing this file does check is that the preview pane still exists beside the list,
because removing a grid column could have collapsed it.

Needs Playwright and a disposable test database, like the rest of ``e2e/``.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert

from app.db import documents, engine, households, people

ROWS = 30
DESKTOP = {"width": 1440, "height": 900}


@pytest.fixture(scope="module")
def profile_client():
    tag = "CLEANUI" + uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"House {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, first_name="Ada", last_name=tag,
                                              full_name=f"Ada {tag}", active=True)
                        .returning(people.c.id)).scalar_one()
        for i in range(ROWS):
            u = uuid.uuid4().hex
            c.execute(insert(documents).values(
                original_name=f"Statement {i:02d} {tag}.pdf", stored_name=f"s-{u}",
                storage_path=f"/vault/{u}.bin", storage_uri=f"/vault/{u}.bin", size_bytes=9,
                sha256=u.ljust(64, "0")[:64], status="active", archived=False,
                content_type="application/pdf", person_id=pid))
    yield {"person_id": pid, "household_id": hid, "tag": tag}
    with engine.begin() as c:
        c.execute(documents.delete().where(documents.c.person_id == pid))
        c.execute(people.delete().where(people.c.id == pid))
        c.execute(households.delete().where(households.c.id == hid))


@pytest.fixture
def person_documents(app_page, live_server, profile_client):
    app_page.set_viewport_size(DESKTOP)
    app_page.goto(f"{live_server}/client/{profile_client['person_id']}?tab=documents")
    app_page.wait_for_selector("[data-doc-row]")
    return app_page


@pytest.fixture
def household_documents(app_page, live_server, profile_client):
    app_page.set_viewport_size(DESKTOP)
    app_page.goto(f"{live_server}/client/household/{profile_client['household_id']}?tab=documents")
    app_page.wait_for_selector("[data-doc-row]")
    return app_page


def test_no_cleanup_rail_is_rendered_on_a_person_profile(person_documents):
    """Not just missing from the DOM — occupying no space and offering no controls."""
    page = person_documents
    assert page.locator(".docws-rail").count() == 0
    assert page.locator(".docws-navitem").count() == 0
    columns = page.eval_on_selector(".docws", "el => getComputedStyle(el).gridTemplateColumns")
    assert len(columns.split()) == 2, f"the frame still has a third column: {columns}"


def test_no_cleanup_rail_is_rendered_on_a_household_profile(household_documents):
    page = household_documents
    assert page.locator(".docws-rail").count() == 0
    assert page.locator(".docws-navitem").count() == 0


def test_the_list_takes_the_width_the_rail_was_using(person_documents):
    """The filename column is what staff scan; it should have gained, not stayed the same."""
    page = person_documents
    frame = page.eval_on_selector(".docws", "el => el.getBoundingClientRect().width")
    listing = page.eval_on_selector(".docws-list", "el => el.getBoundingClientRect().width")
    assert listing / frame > 0.65, (
        f"the list is only {100 * listing / frame:.0f}% of the frame; the rail's width was not reclaimed")


def test_every_kept_control_is_on_screen_and_usable(person_documents):
    page = person_documents
    for selector in ("#doc-q", "#doc-year", "#doc-category", "#doc-type", "#doc-related"):
        control = page.locator(selector)
        assert control.count() == 1, f"{selector} is missing"
        assert control.is_visible(), f"{selector} is present but not visible"
        assert control.is_enabled(), f"{selector} is not usable"


def test_the_preview_pane_is_still_beside_the_list(person_documents):
    """Removing a grid column must not have collapsed the drawer."""
    page = person_documents
    assert page.locator("[data-docdrawer]").count() == 1
    box = page.eval_on_selector(".docws-panel", """el => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height};
    }""")
    assert box["w"] > 150 and box["h"] > 200, f"the preview pane collapsed: {box}"


def test_the_document_workspace_link_is_present_and_points_at_the_library(person_documents):
    page = person_documents
    link = page.locator('a[href="/document-library"]')
    assert link.count() >= 1, "no link to the Document Workspace"
    assert "Document Workspace" in link.first.inner_text()


def test_the_visibility_badge_still_marks_staff_only_documents(person_documents):
    page = person_documents
    badges = page.locator(".docrow-vis")
    assert badges.count() > 0, "no portal visibility badge on any row"
    assert "Internal only" in badges.first.inner_text()


def test_filtering_still_narrows_the_list_after_the_cleanup(person_documents, profile_client):
    """The toolbar is a real GET form and the category select must not double-submit dtab."""
    page = person_documents
    before = page.locator("[data-doc-row]").count()
    page.fill("#doc-q", "Statement 07")
    page.click(".docws-toolbar button[type=submit]")
    page.wait_for_selector("[data-doc-row]")
    after = page.locator("[data-doc-row]").count()
    assert 0 < after < before, f"search did not narrow the list ({before} -> {after})"
    assert page.url.count("dtab=") <= 1, f"dtab was submitted more than once: {page.url}"
