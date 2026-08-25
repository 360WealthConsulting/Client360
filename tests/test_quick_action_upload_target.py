"""The Upload Document quick action points at the workspace's own Documents tab.

It used to deep-link to /document-library, which took staff out of the client and made them
re-establish the owner the workspace already knew — the more prominent control was the slower path.
Registry-only change; these tests read the registry and render the real templates.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import documents, engine, household_relationships, households, people
from app.security.models import Principal
from app.services.client360.registry import QUICK_ACTIONS, visible_quick_actions

# task.read joins this set because create_task moved off work.read when it was repointed at the
# canonical /tasks dashboard; without it the action is correctly suppressed and absent below.
FULL = Principal(1, "staff@t", "Staff", frozenset({
    "client.read", "documents.view", "record.read_all", "work.read", "task.read", "tax.read",
    "scheduling.view", "opportunity.view", "insurance.read", "communications.read"}))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            if ppl:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(documents.delete().where(documents.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(documents.delete().where(documents.c.household_id.in_(hhs)))
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "QAU" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _upload_action(person_id=None, household_id=None):
    return next(a for a in visible_quick_actions(FULL, person_id, household_id)
                if a["key"] == "upload_document")


# --------------------------------------------------------------------- targets
def test_person_upload_action_targets_its_own_documents_tab():
    assert _upload_action(person_id=7783)["href"] == "/client/7783?tab=documents"


def test_household_upload_action_targets_its_own_documents_tab():
    assert _upload_action(household_id=215)["href"] == "/client/household/215?tab=documents"


def test_upload_action_no_longer_points_at_the_generic_library():
    for kwargs in ({"person_id": 7783}, {"household_id": 215}):
        assert "/document-library" not in _upload_action(**kwargs)["href"]


def test_label_is_unchanged():
    assert _upload_action(person_id=1)["label"] == "Upload Document"


def test_person_wins_when_both_ids_are_present():
    """A person workspace passes its own id; the household is only context."""
    assert _upload_action(person_id=7783, household_id=215)["href"] == "/client/7783?tab=documents"


def test_ownerless_context_still_falls_back_to_the_library():
    assert _upload_action()["href"] == "/document-library"


# --------------------------------------------------------------------- nothing else moved
def test_every_other_quick_action_is_unchanged():
    expected = {
        "schedule_meeting": "/scheduling?person_id=7783",
        "add_note": "/people/7783/notes",
        # SUPERSEDED TWICE: /operations/items -> /operations/task-list -> /tasks. Both Operations
        # targets read `operational_tasks` (firm work, ADR-025); a client's tasks live in `tasks`,
        # which is what the canonical /tasks dashboard reads.
        "create_task": "/tasks?person_id=7783",
        "start_tax_return": "/tax/intake?person_id=7783",
        "create_opportunity": "/opportunities?person_id=7783",
        "start_insurance_case": "/insurance?person_id=7783",
        "send_secure_message": "/communications?person_id=7783",
        "generate_meeting_prep": "/workspace/meetings/7783",
    }
    actual = {a["key"]: a["href"] for a in visible_quick_actions(FULL, 7783, None)}
    for key, href in expected.items():
        assert actual[key] == href, key


def test_the_registry_still_defines_exactly_one_upload_action():
    assert sum(1 for a in QUICK_ACTIONS if a.key == "upload_document") == 1
    action = next(a for a in QUICK_ACTIONS if a.key == "upload_document")
    assert action.capability == "documents.view"          # gate unchanged


def test_business_workspace_is_unchanged():
    """Quick actions are a client360 concept; the business template must not gain them."""
    tpl = open("app/templates/business/workspace.html").read()
    assert "quick_action" not in tpl
    assert "/document-library" not in tpl


# --------------------------------------------------------------------- rendered + read-only
def test_rendered_workspaces_link_to_their_own_documents_tab():
    from app.routes.client360 import _render
    from app.services.client360 import get_workspace
    from tests._portal_util import fake_request, render
    tag = _tag()
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Steinman Household {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(first_name="Adam", last_name=f"Steinman{tag}",
                                              household_id=hid, active=True)
                        .returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(
            household_id=hid, person_id=pid, relationship_type="member",
            is_primary=True, is_primary_household=True))

    person_html = render(_render(fake_request(f"/client/{pid}", state_principal=FULL),
                                 get_workspace(FULL, person_id=pid), FULL, "summary"))
    assert f'href="/client/{pid}?tab=documents"' in person_html
    assert "/document-library" not in person_html

    from fastapi.templating import Jinja2Templates

    from app.templating import install_filters
    tpl = Jinja2Templates(directory="app/templates")
    install_filters(tpl)
    hh_html = render(tpl.TemplateResponse(
        request=fake_request(f"/client/household/{hid}", state_principal=FULL),
        name="client360/household.html",
        context={"principal": FULL, "ws": get_workspace(FULL, household_id=hid),
                 "active_tab": "summary"}))
    assert f'href="/client/household/{hid}?tab=documents"' in hh_html
    assert "/document-library" not in hh_html


def test_reading_the_registry_and_rendering_mutate_nothing():
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(documents))
    for _ in range(3):
        visible_quick_actions(FULL, 7783, None)
        visible_quick_actions(FULL, None, 215)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)) == before
