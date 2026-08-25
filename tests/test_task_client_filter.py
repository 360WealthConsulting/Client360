"""/operations/items honours the person_id / household_id the Client360 quick actions already emit.

Before this, "Create Task" from a workspace landed on the firm-wide list and staff re-searched the
client the workspace already knew. The client filter narrows WITHIN record scope: an id the caller
cannot see returns nothing rather than falling back to everything.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import engine, household_relationships, households, operational_tasks, people, projects
from app.security.models import Principal
from app.services.operations import tasks as opstasks

# task.read gates the create_task quick action in both the registry and household.py (it was
# work.read until the quick actions were repointed at the canonical /tasks dashboard); work.read
# is retained because other quick actions and surfaces still use it.
_CAP = {"operations.view", "operations.manage", "client.read", "documents.view",
        "work.read", "task.read", "scheduling.view", "tax.read", "opportunity.view",
        "insurance.read", "communications.read"}
FIRM = Principal(1, "firm@t", "Firm", frozenset(_CAP | {"record.read_all"}))
#: no record.read_all -> scope_clause restricts to assigned records only
LIMITED = Principal(2, "limited@t", "Limited", frozenset(_CAP))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    tasks_t = operational_tasks
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            c.execute(tasks_t.delete().where(tasks_t.c.title.like(like)))
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            if ppl:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "TCF" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _fixture(tag):
    """Two people in two households plus a firm-level task, all tagged."""
    tasks_t = operational_tasks
    with engine.begin() as c:
        hid_a = c.execute(insert(households).values(name=f"Steinman Household {tag}")
                          .returning(households.c.id)).scalar_one()
        hid_b = c.execute(insert(households).values(name=f"Pullen Household {tag}")
                          .returning(households.c.id)).scalar_one()
        pid_a = c.execute(insert(people).values(first_name="Adam", last_name=f"Steinman{tag}",
                                                household_id=hid_a, active=True)
                          .returning(people.c.id)).scalar_one()
        pid_b = c.execute(insert(people).values(first_name="Norman", last_name=f"Pullen{tag}",
                                                household_id=hid_b, active=True)
                          .returning(people.c.id)).scalar_one()
        made = {}
        for key, vals in (
            ("person_a_open", {"person_id": pid_a, "status": "active"}),
            ("person_a_done", {"person_id": pid_a, "status": "completed"}),
            ("person_b", {"person_id": pid_b, "status": "active"}),
            ("household_a", {"household_id": hid_a, "status": "active"}),
            ("household_b", {"household_id": hid_b, "status": "active"}),
            ("firm", {"status": "active"}),
        ):
            made[key] = c.execute(insert(tasks_t).values(
                title=f"{key} {tag}", **vals).returning(tasks_t.c.id)).scalar_one()
    return {"pid_a": pid_a, "pid_b": pid_b, "hid_a": hid_a, "hid_b": hid_b, **made}


def _ids(result, tag):
    return {r["id"] for r in result["rows"] if tag in (r["title"] or "")}


# --------------------------------------------------------------------- filtering
def test_person_filter_returns_only_that_persons_tasks():
    tag = _tag()
    f = _fixture(tag)
    got = _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], page_size=200), tag)
    assert got == {f["person_a_open"], f["person_a_done"]}


def test_household_filter_returns_only_that_households_tasks():
    tag = _tag()
    f = _fixture(tag)
    got = _ids(opstasks.list_tasks(FIRM, household_id=f["hid_a"], page_size=200), tag)
    assert got == {f["household_a"]}


def test_unfiltered_listing_is_unchanged():
    tag = _tag()
    f = _fixture(tag)
    got = _ids(opstasks.list_tasks(FIRM, page_size=200), tag)
    assert got == {f["person_a_open"], f["person_a_done"], f["person_b"],
                   f["household_a"], f["household_b"], f["firm"]}


# --------------------------------------------------------------------- composition
def test_status_filter_composes_with_person_id():
    tag = _tag()
    f = _fixture(tag)
    got = _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], status="active", page_size=200), tag)
    assert got == {f["person_a_open"]}                       # narrows, never replaces


def test_open_only_composes_with_person_id():
    tag = _tag()
    f = _fixture(tag)
    got = _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], open_only=True, page_size=200), tag)
    assert got == {f["person_a_open"]}


def test_project_id_filter_composes_with_person_id():
    tag = _tag()
    f = _fixture(tag)
    tasks_t, projects_t = operational_tasks, projects
    with engine.begin() as c:
        proj = c.execute(insert(projects_t).values(name=f"Proj {tag}")
                         .returning(projects_t.c.id)).scalar_one()
        c.execute(tasks_t.update().where(tasks_t.c.id == f["person_a_open"])
                  .values(project_id=proj))
    got = _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], project_id=proj, page_size=200), tag)
    assert got == {f["person_a_open"]}
    with engine.begin() as c:
        c.execute(projects_t.delete().where(projects_t.c.id == proj))


def test_search_composes_with_person_id():
    tag = _tag()
    f = _fixture(tag)
    got = _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], search="person_a_open",
                                   page_size=200), tag)
    assert got == {f["person_a_open"]}


def test_pagination_still_applies_under_a_client_filter():
    tag = _tag()
    f = _fixture(tag)
    page1 = opstasks.list_tasks(FIRM, person_id=f["pid_a"], page=1, page_size=1)
    page2 = opstasks.list_tasks(FIRM, person_id=f["pid_a"], page=2, page_size=1)
    assert page1["total"] == 2 and page1["pages"] == 2
    assert len(page1["rows"]) == 1 and len(page2["rows"]) == 1
    assert page1["rows"][0]["id"] != page2["rows"][0]["id"]


def test_both_ids_require_both_to_match():
    """Conservative narrowing: AND, never a silent OR and never discarding one."""
    tag = _tag()
    f = _fixture(tag)
    # person A's tasks carry no household_id, so combining them matches nothing
    assert _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], household_id=f["hid_a"],
                                    page_size=200), tag) == set()
    # and a mismatched pair likewise
    assert _ids(opstasks.list_tasks(FIRM, person_id=f["pid_a"], household_id=f["hid_b"],
                                    page_size=200), tag) == set()


# --------------------------------------------------------------------- scope / non-disclosure
def test_out_of_scope_person_filter_does_not_fall_back_to_firm_wide():
    tag = _tag()
    f = _fixture(tag)
    result = opstasks.list_tasks(LIMITED, person_id=f["pid_a"], page_size=200)
    assert _ids(result, tag) == set()                        # nothing, not everything
    assert f["firm"] not in {r["id"] for r in result["rows"]}


def test_out_of_scope_household_filter_does_not_fall_back_to_firm_wide():
    tag = _tag()
    f = _fixture(tag)
    result = opstasks.list_tasks(LIMITED, household_id=f["hid_a"], page_size=200)
    assert _ids(result, tag) == set()


def test_a_client_filter_can_only_narrow_within_scope():
    """The filter ANDs with scope_clause, so it can never widen what a principal may see."""
    tag = _tag()
    f = _fixture(tag)
    unfiltered = {r["id"] for r in opstasks.list_tasks(LIMITED, page_size=200)["rows"]}
    filtered = {r["id"] for r in opstasks.list_tasks(LIMITED, person_id=f["pid_a"],
                                                     page_size=200)["rows"]}
    assert filtered <= unfiltered


# --------------------------------------------------------------------- route
def test_route_accepts_and_echoes_the_client_filter():
    import json

    from app.routes.operations import list_items
    from tests._portal_util import fake_request
    tag = _tag()
    f = _fixture(tag)
    resp = list_items(fake_request("/operations/items"), person_id=f["pid_a"], principal=FIRM)
    payload = json.loads(resp.body)
    assert payload["filters"] == {"person_id": f["pid_a"], "household_id": None}
    assert {t["id"] for t in payload["tasks"] if tag in t["title"]} == {
        f["person_a_open"], f["person_a_done"]}


def test_route_without_a_client_filter_is_unchanged():
    import json

    from app.routes.operations import list_items
    from tests._portal_util import fake_request
    tag = _tag()
    f = _fixture(tag)
    payload = json.loads(list_items(fake_request("/operations/items"), principal=FIRM).body)
    assert payload["filters"] == {"person_id": None, "household_id": None}
    assert f["firm"] in {t["id"] for t in payload["tasks"]}


def test_route_signature_exposes_both_client_parameters():
    import inspect

    from app.routes.operations import list_items
    params = inspect.signature(list_items).parameters
    assert params["person_id"].default is None
    assert params["household_id"].default is None


# --------------------------------------------------------------------- read-only
def test_listing_makes_no_data_changes():
    tag = _tag()
    f = _fixture(tag)
    tasks_t = operational_tasks

    def snapshot():
        with engine.connect() as c:
            rows = sorted(c.execute(select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status,
                                           tasks_t.c.person_id, tasks_t.c.household_id)
                                    .where(tasks_t.c.title.like(f"%{tag}%"))).all())
            return rows, c.scalar(select(func.count()).select_from(tasks_t))

    before = snapshot()
    for kwargs in ({}, {"person_id": f["pid_a"]}, {"household_id": f["hid_a"]},
                   {"person_id": f["pid_a"], "household_id": f["hid_a"]}):
        opstasks.list_tasks(FIRM, page_size=200, **kwargs)
    assert snapshot() == before


# ===================================================================== the staff HTML task page
# /operations/items stays the JSON API; /operations/task-list is the page the quick actions land on.
# NOT /operations/tasks: the middleware rule "/tasks(?:/|$)" is unanchored and would silently add
# task.read on top of this route's operations.view.

def _render_page(principal, **kwargs):
    from app.routes.operations import task_list_page
    from tests._portal_util import fake_request, render
    return render(task_list_page(fake_request("/operations/task-list", state_principal=principal),
                                 principal=principal, **kwargs))


def test_items_remains_json_and_still_honours_person_id():
    import json

    from app.routes.operations import list_items
    from tests._portal_util import fake_request
    tag = _tag()
    f = _fixture(tag)
    resp = list_items(fake_request("/operations/items"), person_id=f["pid_a"], principal=FIRM)
    assert resp.media_type == "application/json"
    payload = json.loads(resp.body)
    assert {t["id"] for t in payload["tasks"] if tag in t["title"]} == {
        f["person_a_open"], f["person_a_done"]}


def test_items_remains_json_and_still_honours_household_id():
    import json

    from app.routes.operations import list_items
    from tests._portal_util import fake_request
    tag = _tag()
    f = _fixture(tag)
    resp = list_items(fake_request("/operations/items"), household_id=f["hid_a"], principal=FIRM)
    assert resp.media_type == "application/json"
    assert {t["id"] for t in json.loads(resp.body)["tasks"] if tag in t["title"]} == {
        f["household_a"]}


def test_task_list_page_renders_html():
    _fixture(_tag())
    html = _render_page(FIRM)
    assert "<html" in html.lower() and "Operational tasks" in html


def test_person_scoped_page_shows_the_canonical_person_name():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(FIRM, person_id=f["pid_a"])
    assert f"Tasks for Adam Steinman{tag}" in html


def test_household_scoped_page_shows_the_household_name():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(FIRM, household_id=f["hid_a"])
    assert f"Tasks for Steinman Household {tag}" in html


def test_only_matching_tasks_appear_and_firm_wide_ones_do_not():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(FIRM, person_id=f["pid_a"])
    assert f"person_a_open {tag}" in html and f"person_a_done {tag}" in html
    assert f"firm {tag}" not in html                       # firm-level task absent
    assert f"person_b {tag}" not in html                   # other client absent
    assert f"household_a {tag}" not in html


def test_unfiltered_page_retains_normal_task_list_behaviour():
    tag = _tag()
    _fixture(tag)
    html = _render_page(FIRM)
    assert "Tasks" in html
    for key in ("person_a_open", "person_b", "household_a", "firm"):
        assert f"{key} {tag}" in html
    assert "Clear client filter" not in html               # no filter to clear


def test_out_of_scope_person_exposes_neither_name_nor_tasks():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(LIMITED, person_id=f["pid_a"])
    assert f"Steinman{tag}" not in html                    # the name is never revealed
    assert f"person_a_open {tag}" not in html
    assert f"firm {tag}" not in html                       # and no firm-wide fallback
    assert "Tasks for the selected client" in html         # neutral label


def test_out_of_scope_household_exposes_neither_name_nor_tasks():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(LIMITED, household_id=f["hid_a"])
    assert f"Steinman Household {tag}" not in html
    assert f"household_a {tag}" not in html
    assert "Tasks for the selected client" in html


def test_page_keeps_and_semantics_for_both_ids():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(FIRM, person_id=f["pid_a"], household_id=f["hid_a"])
    for key in ("person_a_open", "household_a", "firm"):
        assert f"{key} {tag}" not in html                  # AND matches nothing here


def test_clear_client_filter_points_at_the_unscoped_page():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(FIRM, person_id=f["pid_a"])
    assert 'href="/operations/task-list">Clear client filter</a>' in html


def test_creation_form_reuses_the_existing_route_and_carries_the_client():
    tag = _tag()
    f = _fixture(tag)
    html = _render_page(FIRM, person_id=f["pid_a"])
    assert 'action="/operations/items"' in html            # the EXISTING creation route
    assert f'name="person_id" value="{f["pid_a"]}"' in html
    assert 'name="household_id"' not in html


def test_page_route_is_not_caught_by_the_tasks_middleware_rule():
    """/operations/tasks would silently add task.read on top of operations.view."""
    from app.security.middleware import RULES
    assert not any(p.search("/operations/task-list") for p, _ in RULES)
    assert any(p.search("/operations/tasks") for p, _ in RULES)      # why the name differs


def test_page_route_is_registered_and_capability_gated():
    from app.main import app
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    route = next(r for r in app.routes if getattr(r, "path", None) == "/operations/task-list")
    assert "GET" in route.methods
    caps = [getattr(d.call, CAPABILITY_DEP_ATTR, None) for d in route.dependant.dependencies]
    assert ("operations.view",) in caps


# --------------------------------------------------------------------- quick actions repointed
# SUPERSEDED, kept as a regression: these two originally asserted /operations/task-list, because
# that is where this work first sent "Create Task". Production then proved the store was wrong --
# /operations/task-list reads `operational_tasks` (firm work, ADR-025) while a client's real tasks
# live in `tasks`, so the page correctly showed "No tasks" for a client who plainly had one. The
# quick actions now target the canonical /tasks dashboard. The Operations page and its filtering
# are deliberately NOT removed (see the tests above) -- they remain valid firm-work functionality.
def test_person_quick_action_targets_the_canonical_task_dashboard():
    from app.services.client360.registry import visible_quick_actions
    action = next(a for a in visible_quick_actions(FIRM, 7783, None) if a["key"] == "create_task")
    assert action["href"] == "/tasks?person_id=7783"
    assert action["label"] == "Create Task"


def test_household_quick_action_targets_the_canonical_task_dashboard():
    from app.services.client360.household import _quick_actions
    action = next(a for a in _quick_actions(FIRM, {"household_id": 215, "primary": None})
                  if a["key"] == "create_task")
    assert action["href"] == "/tasks?household_id=215"


def test_all_other_quick_actions_are_unchanged():
    from app.services.client360.registry import visible_quick_actions
    expected = {
        "schedule_meeting": "/scheduling?person_id=7783",
        "upload_document": "/client/7783?tab=documents",
        "add_note": "/people/7783/notes",
        "start_tax_return": "/tax/intake?person_id=7783",
        "create_opportunity": "/opportunities?person_id=7783",
        "start_insurance_case": "/insurance?person_id=7783",
        "send_secure_message": "/communications?person_id=7783",
        "generate_meeting_prep": "/workspace/meetings/7783",
    }
    actual = {a["key"]: a["href"] for a in visible_quick_actions(FIRM, 7783, None)}
    for key, href in expected.items():
        assert actual[key] == href, key


def test_rendering_the_page_makes_no_data_changes():
    tag = _tag()
    f = _fixture(tag)
    tasks_t = operational_tasks

    def snapshot():
        with engine.connect() as c:
            rows = sorted(c.execute(select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status)
                                    .where(tasks_t.c.title.like(f"%{tag}%"))).all())
            return rows, c.scalar(select(func.count()).select_from(tasks_t))

    before = snapshot()
    _render_page(FIRM)
    _render_page(FIRM, person_id=f["pid_a"])
    _render_page(LIMITED, household_id=f["hid_a"])
    assert snapshot() == before
