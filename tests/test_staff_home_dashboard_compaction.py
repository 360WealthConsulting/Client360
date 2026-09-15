from pathlib import Path

TEMPLATE = Path("app/templates/home/index.html")

def _html():
    return TEMPLATE.read_text(encoding="utf-8")

def test_priority_work_remains_primary_surface():
    html = _html()
    assert 'ui.card_head("Priority work"' in html

def test_secondary_sections_are_collapsible():
    html = _html()
    assert "<span>Waiting &amp; blocked / Reviews</span>" in html
    assert "<span>Client portal activity</span>" in html
    assert "<span>Recent client activity</span>" in html
    assert html.count(
        '<details class="workspace-category home-dashboard-section">'
    ) >= 3

def test_duplicate_quick_actions_card_removed():
    html = _html()
    assert 'href="/work?view=my_work">My Work</a>' in html
    assert 'ui.card_head("Quick actions")' not in html
    assert 'aria-label="Quick actions"' not in html

def test_document_requests_are_portal_status_not_second_work_surface():
    html = _html()
    assert "Documents requested from clients" in html
    assert 'ui.card_head("Open document requests"' not in html
    assert 'href="/requests"' not in html
    assert 'href="/actions"' not in html
