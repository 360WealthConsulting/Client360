"""The Documents workspace must fit the viewport and scroll in its panes, not as a page.

THE DEFECT THIS GUARDS. ``/client/{id}?tab=documents`` clamps itself to the viewport — ``body.docsui``
is ``height:100vh; overflow:hidden`` — and then hands the leftover height down a chain of ancestors
to three panes that each scroll on their own. That only works if EVERY ancestor on the chain passes
its height through. One did not: the client workspace wraps every tab's body in ``.c360-panel``,
which client360.css gives nothing but ``margin-top:8px``. A content-sized block cannot pass a height
down, so the three-pane frame below it measured against a parent with no height of its own, grew to
its full content height, and was cut off by the clip above it. The rows past the fold had no
scrollbar anywhere and came back only when the browser was zoomed out — the list pane was not
"failing to scroll", it had been stretched to the full height of its own content, which is a subtly
different fault and the reason the panes' own (correct) ``overflow-y:auto`` never fired.

WHY THE TEST IS SHAPED THIS WAY. The ancestor chain is computed from the templates rather than
written down here, so the guard is about the defect and not about today's markup: wrap the screen in
one more div tomorrow and this fails until that div is told to forward height too. Geometry itself —
that a long list is reachable at a desktop viewport without zooming, and that filtering, row
selection and the preview pane still work — needs a real browser and lives in
``e2e/test_documents_workspace_scroll.py``.

Reads template and stylesheet source only. No database, no rendering, no route.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "app/templates"
CSS_DIR = ROOT / "app/static/css"

# The stylesheets base.html loads, in load order, so a later sheet settles a conflict the way the
# browser settles it.
SHEETS = ["main.css", "work.css", "tax.css", "app.css", "shell360.css", "utilities.css",
          "conversations.css", "client360.css", "documents_workspace.css"]

# The two staff surfaces that render the shared Documents screen. Both must be safe: one partial
# inside two different workspace templates is exactly how one of them could drift into a scroll
# trap the other does not have.
SURFACES = ["client360/workspace.html", "client360/household.html"]

SCREEN_INCLUDE = '{% include "client360/_documents_screen.html" %}'

# The document table, the thing the centre pane exists to scroll.
TABLE_MARKER = '<table class="data docs-table docws-grid">'

# The centre pane: the element that owns the vertical scrollbar for the rows.
SCROLLER = "section.docws-list"

# The panes. Each scrolls independently; between them they are the whole surface.
# `.docws-rail` was the third. It carried the operational cleanup queues, which are the Document
# Workspace's job rather than a client profile's, so it and its rules are gone. Its removal is the
# ONLY change here: the frame, the viewport clamp and both remaining panes are untouched, and every
# other assertion in this file still proves the scrolling behaviour it always did.
PANES = [".docws-list", ".docws-panel"]

# The scopes that are all true of a signed-in operator on the Documents tab, so a rule carrying any
# of them reaches this screen.
SCOPES = ("body.c360", "body.docsui")

NARROW = "max-width:1024px"

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_JINJA = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.S)
_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*?)(/?)>", re.S)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_MEDIA = re.compile(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)


# --- reading the templates ---------------------------------------------------


def _open_elements_at(template: str, marker: str) -> list[str]:
    """The elements still open at ``marker``, outermost first, as ``tag`` or ``tag.class``.

    The class kept is the LAST one written, which is this codebase's module-specific name — the
    documents card is ``class="card table-wrap docws-tablewrap"`` and it is the ``docws-tablewrap``
    rule that governs it here.

    Jinja is blanked rather than removed so an ``{% if %}`` body cannot be mistaken for markup.
    Every branch of the workspace templates' tab chain is balanced HTML, so the stack standing at
    the Documents branch is the real ancestor list.
    """
    source = (TEMPLATES / template).read_text(encoding="utf-8")
    assert marker in source, f"{template} no longer contains {marker!r}"
    head = _JINJA.sub(lambda m: " " * (m.end() - m.start()), source[:source.index(marker)])

    stack: list[tuple[str, str]] = []
    for match in _TAG.finditer(head):
        closing, name, attrs, selfclosing = match.groups()
        name = name.lower()
        if name in _VOID or selfclosing:
            continue
        if closing:
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == name:
                    del stack[depth:]
                    break
            continue
        found = re.search(r'class="([^"]*)"', attrs)
        classes = found.group(1).split() if found else []
        stack.append((name, classes[-1] if classes else ""))
    return [f"{tag}.{cls}" if cls else tag for tag, cls in stack]


def _chain(surface: str) -> list[str]:
    """Every element enclosing the Documents screen, from ``<html>`` down to the frame itself.

    base.html supplies the shell above ``{% block content %}``, the surface template supplies
    whatever it wraps the screen in, and the screen partial supplies ``.docws``.
    """
    return (_open_elements_at("base.html", "{% block content %}")
            + _open_elements_at(surface, SCREEN_INCLUDE)
            + ["div.docws"])


def _chain_to_the_table(surface: str) -> list[str]:
    """The same chain, carried all the way down to the document table itself.

    Stopping at ``.docws`` was not far enough. The frame was given its height correctly and the
    list pane still could not scroll, because two more elements sit between the pane and the rows
    and one of them was collapsing. A guard that stops at the pane cannot see that.
    """
    below = _open_elements_at("client360/_documents_screen.html", TABLE_MARKER)
    return _chain(surface)[:-1] + below + ["table.docws-grid"]


def _selector(element: str) -> str:
    """``.class`` for an element carrying one, else the bare tag name."""
    tag, _, cls = element.partition(".")
    return f".{cls}" if cls else tag


# --- reading the stylesheets -------------------------------------------------


def _reaches(head: str, selector: str) -> bool:
    """Does one comma-separated selector target this element on this screen?"""
    head = " ".join(head.split())
    if head == selector:
        return True
    return head.endswith(f" {selector}") and head.split()[0] in SCOPES


def _declarations(selector: str, *, inside_media: str | None = None) -> dict[str, str]:
    """The declarations that reach ``selector`` here, cascaded in stylesheet load order.

    Every rule involved is one class deep, so load order alone resolves them. ``inside_media``
    restricts the read to the ``@media`` blocks whose condition contains that text, which is how
    the narrow-viewport fallback is asserted.
    """
    out: dict[str, str] = {}
    for sheet in SHEETS:
        css = _COMMENT.sub(" ", (CSS_DIR / sheet).read_text(encoding="utf-8"))
        if inside_media is None:
            css = _MEDIA.sub(" ", css)
        else:
            css = " ".join(
                block.group(0).split("{", 1)[1].rsplit("}", 1)[0]
                for block in _MEDIA.finditer(css)
                if inside_media in block.group(0).split("{", 1)[0])
        for rule in _RULE.finditer(css):
            if not any(_reaches(head, selector) for head in rule.group(1).split(",")):
                continue
            for declaration in rule.group(2).split(";"):
                name, sep, value = declaration.partition(":")
                if sep:
                    out[name.strip()] = " ".join(value.split())
    return out


def _sized_by_its_parent(declarations: dict[str, str]) -> bool:
    """True when the element takes a height it is GIVEN rather than one its content dictates.

    Two ways to be given one on this chain: the viewport clamp itself, or a flex share of a
    parent's free space.
    """
    if declarations.get("height") == "100vh":
        return True
    flex = declarations.get("flex", "")
    if flex and not flex.startswith(("0 0", "none", "0 1")):
        return True
    return declarations.get("flex-grow") == "1"


# --- the guard ---------------------------------------------------------------


@pytest.mark.parametrize("surface", SURFACES)
def test_the_wrapper_that_trapped_the_scroll_is_still_on_the_chain(surface):
    """The chain really does run through ``.c360-panel`` — the premise of the tests below.

    If a refactor unwraps the Documents branch, this fails loudly rather than letting the rules
    written for that wrapper quietly stop applying to anything.
    """
    chain = _chain(surface)
    assert "section.c360-panel" in chain, chain
    assert (chain.index("main.app-content")
            < chain.index("section.c360-panel")
            < chain.index("div.docws")), chain


@pytest.mark.parametrize("surface", SURFACES)
def test_the_shell_is_clamped_to_the_viewport(surface):
    """The application shell fits the browser window, and the page does not scroll as a whole."""
    body = _declarations("body.docsui")
    assert body.get("height") == "100vh"
    assert body.get("overflow") == "hidden"
    assert _declarations(_selector(_chain(surface)[2])).get("height") == "100vh"


@pytest.mark.parametrize("surface", SURFACES)
def test_every_ancestor_forwards_height_to_the_three_pane_frame(surface):
    """No ancestor between the viewport clamp and the frame may be sized by its own content.

    This is the regression itself. ``.c360-panel`` failed every check below — no display, no flex
    and no ``min-height`` — so it stood as a content-sized block in the middle of a height chain
    and everything under it grew past the clip.
    """
    # Everything below <html>/<body>, which the viewport clamp above already covers.
    links = _chain(surface)[2:]
    for parent, child in zip(links, links[1:], strict=False):
        above = _declarations(_selector(parent))
        below = _declarations(_selector(child))

        assert above.get("display") in ("flex", "grid"), (
            f"{surface}: {parent} is a block, so {child} cannot be given a share of its height")
        assert above.get("min-height") == "0", (
            f"{surface}: {parent} keeps its min-content floor and cannot shrink to the viewport")
        assert _sized_by_its_parent(above), (
            f"{surface}: {parent} is sized by its content rather than by the space it was given")

        assert below.get("min-height") == "0", (
            f"{surface}: {child} keeps its min-content floor and will overflow {parent}")
        assert _sized_by_its_parent(below), (
            f"{surface}: {child} does not take the height {parent} has to give")


@pytest.mark.parametrize("surface", SURFACES)
def test_nothing_between_the_scrolling_pane_and_the_table_collapses(surface):
    """Below the pane the rule inverts: its children must NOT shrink, or there is nothing to scroll.

    Giving the pane its height is only half the job. The table's card is a flex item of the pane,
    `flex-shrink` defaults to 1, and the card's `overflow:hidden` removes the automatic minimum size
    that would otherwise hold a flex item at its content height. So the card shrank to the space
    left in the pane — 570px against 6241px of table — and clipped the rest. The pane's content then
    fitted exactly, the pane never overflowed, and its correct `overflow-y:auto` had nothing to
    scroll. Every row past the fold stayed unreachable even though every ancestor above was right.

    An element that must not shrink must say so, and must not clip what it is then tall enough to
    show.
    """
    links = _chain_to_the_table(surface)
    links = links[links.index(SCROLLER):]
    for parent, child in zip(links, links[1:], strict=False):
        above = _declarations(_selector(parent))
        if above.get("display") != "flex":
            continue  # not a flex item, so it cannot be shrunk by its parent
        below = _declarations(_selector(child))
        assert below.get("flex", "").startswith("0 0"), (
            f"{surface}: {child} can shrink inside the scrolling {parent}, which collapses it "
            f"instead of giving {parent} something to scroll")
        assert below.get("overflow", "visible") == "visible", (
            f"{surface}: {child} clips its own content inside the scrolling {parent}")


def test_the_scrolling_pane_contains_its_absolutely_positioned_descendants():
    """An abspos box inside the pane must be contained BY the pane, not by the initial box.

    `.sr-only` is `position:absolute`, and the pager carries one. With no positioned ancestor on
    this screen its containing block was the initial one — the single box that an `overflow`
    further up cannot clip. Its static position sits below a table thousands of pixels tall, so
    that one label extended the document's scrollable area to 6513px under a shell pinned at 900,
    and the window really could be scrolled, carrying the whole application off screen.
    """
    assert _declarations(".docws-list").get("position") == "relative"


def test_each_pane_scrolls_on_its_own():
    """The list and the preview/details pane each carry their own scrollbar."""
    for pane in PANES:
        declarations = _declarations(pane)
        assert declarations.get("min-height") == "0", f"{pane} keeps its min-content floor"
        assert declarations.get("overflow-y") == "auto", f"{pane} has no scrollbar of its own"


def test_the_list_keeps_its_own_wheel_gestures():
    """Reaching the end of the list must not hand the rest of the gesture to an ancestor."""
    assert _declarations(".docws-list").get("overscroll-behavior") == "contain"


def test_the_narrow_viewport_fallback_releases_every_clamp():
    """Below 1024px the workspace is one scrolling column, so nothing above it may still clip.

    The same defect is reachable by narrowing the window: the fallback returned the shell and the
    panes to auto height but left ``body`` at ``height:100vh; overflow:hidden``, cutting the single
    column off at the fold with nothing able to scroll it.
    """
    body = _declarations("body.docsui", inside_media=NARROW)
    assert body.get("height") == "auto"
    assert body.get("overflow") == "visible"
    for selector in (".app-shell", ".app-main", ".app-content", ".c360-panel"):
        declarations = _declarations(selector, inside_media=NARROW)
        assert declarations.get("overflow") == "visible", (
            f"{selector} still clips when the workspace is one scrolling column")
