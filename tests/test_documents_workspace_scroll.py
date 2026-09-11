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

# The three panes. Each scrolls independently; between them they are the whole workspace.
PANES = [".docws-rail", ".docws-list", ".docws-panel"]

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
        stack.append((name, found.group(1).split()[0] if found and found.group(1).split() else ""))
    return [f"{tag}.{cls}" if cls else tag for tag, cls in stack]


def _chain(surface: str) -> list[str]:
    """Every element enclosing the Documents screen, from ``<html>`` down to the frame itself.

    base.html supplies the shell above ``{% block content %}``, the surface template supplies
    whatever it wraps the screen in, and the screen partial supplies ``.docws``.
    """
    return (_open_elements_at("base.html", "{% block content %}")
            + _open_elements_at(surface, SCREEN_INCLUDE)
            + ["div.docws"])


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


def test_each_pane_scrolls_on_its_own():
    """The list and the preview/details pane each carry their own scrollbar.

    The rail is held to the same rule because the broken chain stretched all three, and a category
    list long enough to overflow was equally unreachable.
    """
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
