"""Client 360 exposes one visible document operating surface.

`documents` is the canonical person/household document workspace.
The old person `vault` renderer remains in the template for bookmarked legacy
URLs, but the grouped navigation must not advertise it as a second document tab.
"""

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
NAV = ROOT / "app/templates/client360/_section_nav.html"
PERSON = ROOT / "app/templates/client360/workspace.html"
HOUSEHOLD = ROOT / "app/templates/client360/household.html"


def _src(path):
    return path.read_text(encoding="utf-8")


def test_documents_group_has_one_visible_surface():
    nav = _src(NAV)

    assert '("Documents",  ["documents"])' in nav
    assert '("Documents",  ["documents","vault"])' not in nav
    assert '("Documents",  ["vault","documents"])' not in nav


def test_person_legacy_vault_renderer_is_preserved():
    """Old bookmarked ?tab=vault URLs must not suddenly become an error."""

    person = _src(PERSON)

    assert 'active_tab == "vault"' in person
    assert "<h2>Vault</h2>" in person


def test_documents_renderer_remains_canonical_for_person():
    person = _src(PERSON)

    assert 'active_tab == "documents"' in person
    assert 'client360/_documents_screen.html' in person
    assert '/client/{{ ws.entity_id }}/documents/upload' in person


def test_documents_renderer_remains_canonical_for_household():
    household = _src(HOUSEHOLD)

    assert 'active_tab == "documents"' in household
    assert 'client360/_documents_screen.html' in household
    assert '/client/household/{{ ws.household_id }}/documents/upload' in household


def test_household_never_advertises_or_renders_vault():
    household = _src(HOUSEHOLD)

    chain = set(
        re.findall(
            r'active_tab == "([a-z_]+)"',
            household,
        )
    )

    assert "documents" in chain
    assert "vault" not in chain


def test_tasks_and_work_remain_separate_visible_concepts():
    nav = _src(NAV)

    # Semantic analysis proved these are related but distinct workflows.
    assert '("Tax",        ["tax","work"])' in nav
    assert '("Work",       ["tasks","meetings"])' in nav


def test_messages_and_communications_remain_separate_views():
    nav = _src(NAV)

    # Secure portal messages and the broader communications feed are distinct.
    assert '("Messages",   ["communications","messages"])' in nav


def test_comment_documents_legacy_vault_decision():
    nav = _src(NAV)

    assert "legacy `vault` renderer" in nav
    assert "`documents` is the canonical visible document workspace" in nav
