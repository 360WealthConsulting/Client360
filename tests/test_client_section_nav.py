"""The client/household tab bar offers a chip only for a section the template can actually draw.

THE DEFECT

``_section_nav.html`` grouped the composed sections into tabs and swept every ungrouped key into
"More", on the stated principle that nothing should be hidden. But the services compose more
sections than either workspace template renders, so More advertised chips that opened onto
"No renderer for this section." — a tab whose only content was an apology for having none. On an
ordinary client record that was ``knowledge`` and ``recommendations``.

THE RULE NOW

Each template declares ``rendered_sections``: the keys its own ``elif active_tab == …`` chain
handles. The nav intersects the principal's visible sections with that set.

These tests derive the renderable set from the template SOURCE and compare it to the declaration, so
the two cannot drift: adding a renderer without its chip, or deleting one and leaving the chip, both
fail here rather than in front of a user.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).parents[1] / "app/templates/client360"
NAV = TEMPLATES / "_section_nav.html"
WORKSPACE = TEMPLATES / "workspace.html"
HOUSEHOLD = TEMPLATES / "household.html"


def _rendered_from_chain(path: Path) -> set[str]:
    """The sections the template's own branch chain handles."""
    return set(re.findall(r'active_tab == "([a-z_]+)"', path.read_text(encoding="utf-8")))


def _declared(path: Path) -> set[str]:
    """The sections the template says it draws."""
    src = path.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*set rendered_sections = \[(.*?)\]\s*%\}", src, re.S)
    assert match, f"{path.name} does not declare rendered_sections"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


@pytest.mark.parametrize("template", [WORKSPACE, HOUSEHOLD], ids=["workspace", "household"])
def test_the_declared_sections_match_the_renderers_that_exist(template):
    """The guard against drift, in both directions."""
    declared, actual = _declared(template), _rendered_from_chain(template)
    missing = actual - declared
    phantom = declared - actual
    assert not missing, f"{template.name} renders {sorted(missing)} but offers no chip for them"
    assert not phantom, f"{template.name} offers chips for {sorted(phantom)} but cannot draw them"


@pytest.mark.parametrize("template", [WORKSPACE, HOUSEHOLD], ids=["workspace", "household"])
def test_each_workspace_declares_its_own_set(template):
    """The two render different sections — a shared constant would re-create the bug for one of
    them. ``members`` is household-only; ``dashboard`` and ``vault`` are person-only."""
    declared = _declared(template)
    if template is HOUSEHOLD:
        assert "members" in declared and "dashboard" not in declared
    else:
        assert "dashboard" in declared and "members" not in declared


def test_the_nav_intersects_visible_sections_with_drawable_ones():
    """Capability gating is unchanged: a section the principal cannot see is still absent. The new
    filter narrows further, it never widens."""
    nav = NAV.read_text(encoding="utf-8")
    assert "k in ws.section_keys and k in drawable" in nav, \
        "the grouped branch must require BOTH visible and drawable"
    assert "k not in gk.all and k in drawable" in nav, \
        "the More branch must require drawable too — that is where the dead chips came from"


def test_a_template_that_declares_nothing_keeps_its_whole_nav():
    """Defensive: a future template that forgets the declaration should degrade to the old
    behaviour, not lose every tab."""
    nav = NAV.read_text(encoding="utf-8")
    assert "rendered_sections if rendered_sections is defined else ws.section_keys" in nav


def test_the_apology_fallback_is_gone_from_both_workspaces():
    """"No renderer for this section." was the user-visible symptom. It must not be renderable."""
    for template in (WORKSPACE, HOUSEHOLD):
        src = template.read_text(encoding="utf-8")
        body = re.sub(r"\{#.*?#\}", "", src, flags=re.S)     # comments may discuss it; markup may not
        assert "No renderer for this section" not in body, \
            f"{template.name} can still render the apology text"


def test_the_sections_with_no_renderer_are_still_composed_by_the_services():
    """This change hides CHIPS, not data. The services still compose these sections and any caller
    reading them directly is unaffected — which is why this is a template-only fix."""
    nav = NAV.read_text(encoding="utf-8")
    assert "knowledge" in nav and "recommendations" in nav, \
        "the More group definition should still list them; they simply no longer get a chip"
