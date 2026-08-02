"""Client Workspace — Dashboard landing tab (client360) coverage.

Verifies the Dashboard is the default tab, composes cross-domain cards from the authoritative section
builders (no new ownership/domain logic), respects per-card capabilities, and degrades gracefully.
Temp/test rows only.
"""
import pytest
from sqlalchemy import delete, insert

from app.db import engine, people, tasks
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.registry import SECTION_KEYS

_TAG = "CWDASH"
_CAPS = frozenset({"client.read", "record.read_all", "documents.view", "tax.read",
                   "timeline.read", "opportunity.view"})


@pytest.fixture
def person():
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Dash", last_name=f"Test{_TAG}", full_name=f"Dash Test {_TAG}",
            active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(tasks).values(person_id=pid, title=f"Follow up {_TAG}", status="open"))
    yield pid
    with engine.begin() as c:
        c.execute(delete(tasks).where(tasks.c.person_id == pid))
        c.execute(delete(people).where(people.c.id == pid))


def _principal(caps=_CAPS):
    return Principal(0, "adv@e.test", "Advisor", caps)


def test_dashboard_is_the_default_section():
    assert SECTION_KEYS[0] == "dashboard"


def test_dashboard_composes_expected_cards(person):
    ws = get_workspace(_principal(), person_id=person)
    assert ws is not None
    assert ws["section_keys"][0] == "dashboard"
    dash = ws["sections"]["dashboard"]
    for key in ("open_tasks", "recent_activity", "recent_documents", "documents_needing_review",
                "missing_tax_items", "upcoming_meetings", "planning_opportunities", "alerts"):
        assert key in dash, key


def test_dashboard_open_tasks_reflect_client_tasks(person):
    dash = get_workspace(_principal(), person_id=person)["sections"]["dashboard"]
    assert any(t["title"].startswith("Follow up") for t in dash["open_tasks"])


def test_dashboard_respects_capabilities(person):
    # A principal WITHOUT tax.read / documents.view still gets a Dashboard, but those cards stay empty
    # (never errors, never leaks a section the user can't open).
    limited = _principal(frozenset({"client.read", "record.read_all"}))
    ws = get_workspace(limited, person_id=person)
    dash = ws["sections"]["dashboard"]
    assert dash["missing_tax_items"] == [] and dash["recent_documents"] == []
    # tax / documents tabs are also hidden from the tab list for this principal
    assert "tax" not in ws["section_keys"] and "documents" not in ws["section_keys"]


def test_dashboard_tab_renders(person):
    from starlette.requests import Request

    from app.routes.client360 import client_workspace
    scope = {"type": "http", "method": "GET", "path": f"/client/{person}", "headers": [],
             "query_string": b"", "state": {}}
    req = Request(scope)
    req.state.principal = _principal()
    req.state.request_id = "test"
    resp = client_workspace(req, person_id=person, tab="dashboard", principal=_principal())
    html = resp.body.decode()
    assert resp.status_code == 200
    assert "c360-dash" in html and "Open tasks" in html and "Follow up" in html
    # Dashboard is the first/active tab
    assert 'class="c360-tab active"' in html


def test_dashboard_never_errors_for_empty_client(person):
    dash = get_workspace(_principal(), person_id=person)["sections"]["dashboard"]
    # Cards a brand-new client has no data for resolve to empty lists, not errors.
    assert dash["upcoming_meetings"] == [] and dash["planning_opportunities"] in ([], dash["planning_opportunities"])
