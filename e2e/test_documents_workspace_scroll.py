"""The Documents workspace scrolls in its panes, at a real desktop viewport, at 100% zoom.

The companion guard in ``tests/test_documents_workspace_scroll.py`` proves the CSS height chain is
unbroken by reading the templates and stylesheets. This file proves the thing a user complained
about, which only a browser can measure: at a normal desktop window the document list extended below
the fold, the wheel moved nothing, and the missing rows appeared only at 50% zoom.

So every assertion here is geometry or behaviour, never a class name:

* the application shell fits the viewport and the PAGE does not scroll;
* the centre list overflows and scrolls on its own, and its last row can be brought into view;
* the preview/details pane scrolls independently of the list;
* zoom stays at 1 throughout, so nothing here is reachable only by zooming out;
* filtering, row selection and the preview drawer still behave.

Needs Playwright and a disposable test database, like the rest of ``e2e/`` — the directory skips
cleanly when the browser toolchain is absent.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert

from app.db import documents, engine, households, people

# Comfortably more rows than any desktop viewport can show, so "reachable" means "scrolled to",
# never "happened to fit". PAGE_SIZE is asked for explicitly (the screen's own default is 25, and
# its clamp is 200) so the list is certain to overflow whatever the row height is on the day.
ROW_COUNT = 120
PAGE_SIZE = 100

# A normal desktop window. The defect reproduced here and disappeared only when zoomed out.
DESKTOP = {"width": 1440, "height": 900}

# One row carries a distinctive token so filtering has something unambiguous to narrow to.
NEEDLE = "Zzyzx"

# A row offers TWO controls that open the document in the preview pane: the filename itself and the
# "Open" link in the Actions cell. Both carry `data-doc-open`, so that attribute alone is ambiguous
# and Playwright refuses it under strict mode. These tests drive the filename, which is the control
# a staff member actually clicks when working down a list.
OPEN_BY_NAME = "[data-doc-row] a.docrow-name[data-doc-open]"


@pytest.fixture(scope="module")
def client_with_many_documents():
    """A person whose document list cannot fit on one screen. Removed afterwards."""
    tag = "SCROLL" + uuid.uuid4().hex[:8]
    with engine.begin() as connection:
        household_id = connection.execute(
            households.insert().values(name=f"Scroll Household {tag}")
            .returning(households.c.id)).scalar_one()
        person_id = connection.execute(
            people.insert().values(household_id=household_id, first_name="Ada",
                                   last_name=tag, full_name=f"Ada {tag}", active=True)
            .returning(people.c.id)).scalar_one()
        for index in range(ROW_COUNT):
            unique = uuid.uuid4().hex
            label = NEEDLE if index == ROW_COUNT - 1 else f"Statement {index:03d}"
            connection.execute(insert(documents).values(
                original_name=f"{label} {tag}.pdf", stored_name=f"s-{unique}",
                storage_path=f"/vault/{unique}.bin", storage_uri=f"/vault/{unique}.bin",
                size_bytes=9, sha256=unique.ljust(64, "0")[:64], status="active", archived=False,
                content_type="application/pdf", person_id=person_id))
    yield {"person_id": person_id, "household_id": household_id, "tag": tag}
    with engine.begin() as connection:
        connection.execute(documents.delete().where(documents.c.person_id == person_id))
        connection.execute(people.delete().where(people.c.id == person_id))
        connection.execute(households.delete().where(households.c.id == household_id))


@pytest.fixture
def documents_tab(app_page, live_server, client_with_many_documents):
    """The Documents tab open at a desktop viewport, at 100% zoom, with rows on screen."""
    app_page.set_viewport_size(DESKTOP)
    app_page.goto(f"{live_server}/client/{client_with_many_documents['person_id']}"
                  f"?tab=documents&per={PAGE_SIZE}")
    app_page.wait_for_selector("[data-doc-row]")
    return app_page


def _metrics(page, selector):
    return page.eval_on_selector(selector, """el => ({
        client: el.clientHeight,
        scroll: el.scrollHeight,
        overflowY: getComputedStyle(el).overflowY,
    })""")


def _zoom(page):
    """The page's own zoom factor. 1 means the user has not zoomed out to cope."""
    return page.evaluate("() => (window.visualViewport ? window.visualViewport.scale : 1)")


def _in_viewport(page, locator) -> bool:
    return page.evaluate(
        """el => { const r = el.getBoundingClientRect();
                   return r.top >= 0 && r.bottom <= window.innerHeight; }""",
        locator.element_handle())


def test_the_shell_fits_the_viewport_and_the_page_does_not_scroll(documents_tab):
    """The whole application is inside the window, so there is no page-level scrollbar to hunt.

    Both halves are asserted because they failed for different reasons. The document's scroll area
    grew past the window on a single absolutely positioned `.sr-only` label that escaped every clip
    above it, and the consequence was not theoretical: the window really could be scrolled, taking
    the entire shell off screen and leaving blank canvas behind it.
    """
    page = documents_tab
    assert _zoom(page) == 1
    measured = page.evaluate("""() => ({
        viewport: window.innerHeight,
        page: document.scrollingElement.scrollHeight,
    })""")
    assert measured["page"] <= measured["viewport"] + 1, measured

    moved = page.evaluate("""() => {
        window.scrollTo(0, 5000);
        const y = window.scrollY;
        window.scrollTo(0, 0);
        return y;
    }""")
    assert moved == 0, f"the window scrolled {moved}px, carrying the shell off screen"


def test_the_document_list_overflows_and_scrolls_on_its_own(documents_tab):
    """The centre pane is the scroll container — the symptom was that it was not."""
    listing = _metrics(documents_tab, ".docws-list")
    assert listing["overflowY"] == "auto"
    assert listing["scroll"] > listing["client"], (
        f"the list was stretched to its own content instead of scrolling: {listing}")


def test_the_last_row_is_reachable_without_zooming(documents_tab):
    """The defect, stated as the user met it: rows past the fold could not be reached.

    The list is scrolled, nothing else. If the page had to move, or the browser had to zoom out,
    this fails.
    """
    page = documents_tab
    rows = page.locator("[data-doc-row]")
    assert rows.count() > 0
    last = rows.last

    assert not _in_viewport(page, last), (
        "seed more rows: the list did not actually overflow this viewport")

    page.eval_on_selector(".docws-list", "el => { el.scrollTop = el.scrollHeight; }")

    assert _in_viewport(page, last), "the final row is still off screen after scrolling the list"
    assert page.evaluate("() => window.scrollY") == 0, "the page scrolled instead of the list"
    assert _zoom(page) == 1


def test_the_preview_pane_scrolls_independently_of_the_list(documents_tab):
    """Opening a document must not make the list jump, and a long panel scrolls by itself."""
    page = documents_tab
    page.eval_on_selector(".docws-list", "el => { el.scrollTop = 240; }")
    # Clicked in the page rather than through Playwright, which would scroll the row into view
    # first and destroy the very measurement this test is making.
    page.eval_on_selector(OPEN_BY_NAME, "el => el.click()")
    page.wait_for_selector("[data-docdrawer-body]")

    assert page.eval_on_selector(".docws-list", "el => el.scrollTop") == 240, (
        "opening a document moved the list")
    panel = _metrics(page, ".docws-panel")
    assert panel["overflowY"] == "auto"

    # Whether this client's panel is long enough to overflow depends on the document; if it is,
    # it must scroll here rather than pushing the frame past the viewport.
    if panel["scroll"] > panel["client"]:
        page.eval_on_selector(".docws-panel", "el => { el.scrollTop = el.scrollHeight; }")
        assert page.eval_on_selector(".docws-panel", "el => el.scrollTop") > 0
    assert page.evaluate("() => window.scrollY") == 0


def test_selecting_a_row_still_marks_it_and_fills_the_drawer(documents_tab):
    """Row selection is unchanged: the row is marked and the panel loads that document."""
    page = documents_tab
    row = page.locator("[data-doc-row][data-panel-url]").first
    opener = row.locator("a.docrow-name[data-doc-open]")
    assert opener.count() == 1, "the filename link is no longer the unambiguous way to open a row"
    name = opener.inner_text()
    opener.click()
    page.wait_for_selector("[data-doc-row].is-selected")

    assert "is-selected" in (row.get_attribute("class") or "")
    assert name.strip()[:20] in page.locator(".docws-panel").inner_text()


def test_filtering_still_narrows_the_list_and_the_result_still_scrolls(documents_tab,
                                                                      client_with_many_documents):
    """Search is a real GET form; narrowing must not reintroduce the trap on the way back."""
    page = documents_tab
    before = page.locator("[data-doc-row]").count()

    page.fill("#doc-q", NEEDLE)
    page.click(".docws-toolbar button[type=submit]")
    page.wait_for_selector("[data-doc-row]")

    after = page.locator("[data-doc-row]").count()
    assert 0 < after < before, f"search did not narrow the list ({before} -> {after})"
    assert NEEDLE in page.locator(".docws-list").inner_text()

    # A short list must not scroll, and the page must still not scroll either.
    assert page.evaluate("() => document.scrollingElement.scrollHeight") <= \
        page.evaluate("() => window.innerHeight") + 1
    assert _metrics(page, ".docws-list")["overflowY"] == "auto"
