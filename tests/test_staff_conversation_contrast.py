"""The staff conversation workspace must render in the shell's light system, not the flipped one.

WHAT BROKE, AND WHY A TEST CAN CATCH IT

The staff shell composes two palettes. ``app.css`` owns ``--surface``/``--text``/``--muted``/
``--border``/``--accent*`` and the semantic ``--info-soft``/``--warn-soft`` pair, and it is
theme-aware: a ``@media (prefers-color-scheme: dark)`` block flips every one of them to near-black.
``shell360.css`` owns ``--s-card``/``--s-page``/``--s-ink``/``--s-line``/..., loads afterwards, is
NOT theme-aware, and paints ``body.c360`` and ``body.c360 .card`` from those fixed light values.
``base.html`` sets no ``data-theme``, so nothing pins the choice — on an operator whose OS is dark
the first palette flips and the second does not.

``conversations.css`` drew entirely from the first palette, so the conversation workspace inherited
the dark half of that split while the surfaces beneath it stayed white. Measured in a browser at
the time: ``.thread-header`` text at 1.13:1, a staff reply bubble at 1.20:1, an internal note at
1.23:1 — all invisible.

These tests resolve the same ``var()`` chains the browser does, against the DARK definitions, and
assert the staff scope lands on readable light values. They fail if someone removes the scope
block, if a new rule reaches for a theme-flipped token, or if a token is re-pointed at a dark value.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_DIR = Path(__file__).parents[1] / "app/static/css"
CONVERSATIONS = (CSS_DIR / "conversations.css").read_text(encoding="utf-8")
SHELL360 = (CSS_DIR / "shell360.css").read_text(encoding="utf-8")
APP_CSS = (CSS_DIR / "app.css").read_text(encoding="utf-8")

# The staff scope: `body.c360 .conversation-page-head, body.c360 .conversation-shell { ... }`.
_SCOPE = re.search(
    r"body\.c360 \.conversation-page-head,\s*body\.c360 \.conversation-shell\s*\{(.*?)\}",
    CONVERSATIONS, re.S)


def _declarations(block: str) -> dict[str, str]:
    out = {}
    for line in block.split(";"):
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        if name.startswith("--") or name == "color":
            out[name] = value.strip()
    return out


def _tokens(css: str) -> dict[str, str]:
    """Every `--name:#hex` declaration in a stylesheet."""
    return {m.group(1): m.group(2) for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})", css)}


SHELL_TOKENS = _tokens(SHELL360)


def _dark_tokens() -> dict[str, str]:
    """app.css values as they resolve under `prefers-color-scheme: dark` — the failing case."""
    block = re.search(r"@media \(prefers-color-scheme: dark\)\s*\{(.*?)\n\}", APP_CSS, re.S)
    assert block, "app.css no longer has a dark block; this test's premise needs revisiting"
    return _tokens(block.group(1))


DARK_TOKENS = _dark_tokens()


def _resolve(value: str, depth: int = 0) -> str:
    """Resolve `var(--x,fallback)` the way the cascade would inside the staff scope."""
    assert depth < 10, f"var() chain did not terminate: {value}"
    value = value.strip()
    m = re.fullmatch(r"var\(\s*(--[a-z0-9-]+)\s*(?:,\s*(.*?))?\s*\)", value)
    if not m:
        return value
    name, fallback = m.group(1), m.group(2)
    if name in SHELL_TOKENS:                      # the shell palette wins inside the staff scope
        return SHELL_TOKENS[name]
    return _resolve(fallback, depth + 1) if fallback else ""


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(rgb) -> float:
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    lf, lb = _luminance(_rgb(fg)), _luminance(_rgb(bg))
    hi, lo = max(lf, lb), min(lf, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


@pytest.fixture(scope="module")
def scoped() -> dict[str, str]:
    assert _SCOPE, (
        "the staff conversation scope block is gone from conversations.css — without it the "
        "workspace inherits app.css's theme-flipped palette on a dark-mode machine")
    return {k: _resolve(v) for k, v in _declarations(_SCOPE.group(1)).items()}


# --- the defect itself -------------------------------------------------------------------------

def test_the_dark_theme_really_does_flip_the_tokens_this_scope_exists_to_neutralize():
    """Guard the premise. If app.css stops flipping these, the scope block can be reconsidered."""
    for token in ("--surface", "--text", "--info-soft", "--warn-soft"):
        assert token in DARK_TOKENS, f"{token} is no longer redefined for dark mode"
    # The values it flips to are exactly what made the panes unreadable.
    assert _luminance(_rgb(DARK_TOKENS["--surface"])) < 0.05, "dark --surface is no longer dark"
    assert _luminance(_rgb(DARK_TOKENS["--text"])) > 0.7, "dark --text is no longer light"


def test_the_unscoped_pairing_would_still_be_unreadable():
    """The regression this prevents: shell ink on a flipped surface, and flipped text on a shell card."""
    assert contrast(SHELL_TOKENS["--s-ink"], DARK_TOKENS["--surface"]) < 3.0
    assert contrast(DARK_TOKENS["--text"], SHELL_TOKENS["--s-card"]) < 3.0


# --- what the scope guarantees ------------------------------------------------------------------

def test_staff_surfaces_are_light(scoped):
    for name in ("--surface", "--surface-2"):
        assert _luminance(_rgb(scoped[name])) > 0.8, f"{name} resolved to {scoped[name]}, not a light surface"


def test_primary_and_secondary_text_meet_aa_on_every_staff_fill(scoped):
    fills = {"--surface": scoped["--surface"], "--surface-2": scoped["--surface-2"],
             "--accent-soft": scoped["--accent-soft"], "--info-soft": scoped["--info-soft"],
             "--warn-soft": scoped["--warn-soft"], "--good-soft": scoped["--good-soft"],
             "--crit-soft": scoped["--crit-soft"]}
    for fill_name, fill in fills.items():
        for ink_name in ("--text", "--text-2"):
            ratio = contrast(scoped[ink_name], fill)
            assert ratio >= 4.5, f"{ink_name} on {fill_name} is {ratio}:1, below WCAG AA (4.5:1)"


def test_the_message_bubbles_that_were_invisible_are_readable(scoped):
    """`.thread-msg.staff` fills from --info-soft, `.thread-msg.internal` from --warn-soft.
    In dark mode these measured 1.20:1 and 1.23:1 in the browser."""
    for fill in ("--info-soft", "--warn-soft"):
        assert contrast(scoped["--text"], scoped[fill]) >= 4.5


def test_borders_stay_visible_against_the_surface(scoped):
    ratio = contrast(scoped["--border"], scoped["--surface"])
    assert 1.05 <= ratio <= 4.5, f"border/surface contrast {ratio}:1 is invisible or harsh"


def test_the_scope_sets_an_explicit_ink_rather_than_inheriting_a_flipped_one(scoped):
    assert "color" in scoped, "the scope must set its own color; inheriting picks up the dark --text"
    assert _luminance(_rgb(scoped["color"])) < 0.2


# --- selection, controls and focus ---------------------------------------------------------------

def test_selected_and_unselected_rows_stay_distinguishable():
    """Hover and selection used one shared fill, so a selected row read as merely hovered."""
    assert "body.c360 .conversation-row:hover" in CONVERSATIONS
    active = re.search(r"body\.c360 \.conversation-row\.active\s*\{(.*?)\}", CONVERSATIONS, re.S)
    assert active, "the staff selected-row rule is missing"
    body = active.group(1)
    assert "box-shadow" in body and "inset" in body, "selection needs a marker beyond its fill"
    hover = re.search(r"body\.c360 \.conversation-row:hover\s*\{(.*?)\}", CONVERSATIONS, re.S).group(1)
    hover_fill = _resolve(re.search(r"background:\s*([^;]+)", hover).group(1))
    active_fill = _resolve(re.search(r"background:\s*([^;]+)", body).group(1))
    assert hover_fill != active_fill, "hover and selection share a fill, so selection is invisible"
    # Both are washes, not blocks of saturated colour, and both keep the ink readable.
    for fill in (hover_fill, active_fill):
        assert _luminance(_rgb(fill)) > 0.8, f"{fill} is too heavy a fill for a row"
        assert contrast(SHELL_TOKENS["--s-ink"], fill) >= 4.5


def test_form_controls_are_light_with_dark_text_and_a_visible_border():
    rule = re.search(r"body\.c360 \.conversation-shell :is\(input,textarea,select\)\s*\{(.*?)\}",
                     CONVERSATIONS, re.S)
    assert rule, "conversation inputs are unscoped and inherit the flipped surface"
    body = rule.group(1)
    bg = _resolve(re.search(r"background:\s*([^;]+)", body).group(1))
    fg = _resolve(re.search(r"color:\s*([^;]+)", body).group(1))
    assert _luminance(_rgb(bg)) > 0.8 and contrast(fg, bg) >= 4.5
    assert "border:" in body


def test_keyboard_focus_is_visible():
    rule = re.search(r"body\.c360 \.conversation-shell[^{]*:focus-visible\s*\{(.*?)\}",
                     CONVERSATIONS, re.S)
    assert rule, "no scoped focus-visible rule; the flipped accent was not visible on a light field"
    assert "outline" in rule.group(1)


def test_the_count_chip_is_not_invisible():
    """It inherited its colour from the rail and rendered foreground identical to background."""
    rule = re.search(r"\.conversation-count\s*\{[^}]*\}\s*$|body\.c360[^{]*\.conversation-count[^{]*\{(.*?)\}",
                     CONVERSATIONS, re.S)
    assert rule and rule.group(1), "the staff count chip has no explicit colour"
    body = rule.group(1)
    bg = _resolve(re.search(r"background:\s*([^;]+)", body).group(1))
    fg = _resolve(re.search(r"color:\s*([^;]+)", body).group(1))
    assert contrast(fg, bg) >= 4.5


# --- layout ---------------------------------------------------------------------------------------

def test_the_staff_shell_fits_a_1024px_viewport():
    """The staff shell loses 236px to the sidebar, so the shared <=1050px tracks (11+16+22=49rem)
    exceeded the 711px actually available and the shell scrolled sideways."""
    block = re.search(r"@media \(max-width:1100px\)\s*\{(.*?)\n\}", CONVERSATIONS, re.S)
    assert block, "no staff narrow-width rule; the shell overflows at 1024px"
    cols = re.search(r"body\.c360 \.conversation-shell\s*\{\s*grid-template-columns:([^;]+)", block.group(1))
    assert cols, "the narrow-width rule does not resize the grid"
    rems = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)rem", cols.group(1))]
    minimum = rems[0] + rems[1] + rems[3]          # rail + list min + thread min
    assert minimum * 16 <= 700, f"minimum track width {minimum}rem still overflows 711px"
    assert cols.group(1).count("minmax") == 2, "the three-pane structure must be preserved"


def test_the_client_portal_theme_is_untouched():
    """Every rule added here is scoped to body.c360. The portal renders under body.portal."""
    added = CONVERSATIONS.split("staff shell theme", 1)[1]
    for rule in re.findall(r"(?m)^([^@\s][^{]*)\{", added):
        rule = rule.strip()
        if not rule or rule.startswith("/*"):
            continue
        assert "body.c360" in rule, f"rule escapes the staff scope and could reach the portal: {rule[:70]}"
