"""The canonical staff task dashboard (/tasks) is client-aware and reads the AUTHORITATIVE `tasks`
table.

Production proved the defect these tests pin: /client/3824?tab=tasks showed a real "Lease agreement"
task while /operations/task-list?person_id=3824 showed none. They were reading two different stores.
Under ADR-025 `tasks` is the authoritative client-task store and `operational_tasks` is firm work,
so the client quick actions belong here -- and the Operations page keeps its own filtering, which is
still valid firm-work functionality (tests/test_task_client_filter.py).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import engine, household_relationships, households, people, tasks
from app.routes.task_dashboard import task_dashboard
from app.security.models import Principal
from tests._portal_util import fake_request, render

_CAP = {"task.read", "client.read", "documents.view", "work.read", "scheduling.view",
        "tax.read", "opportunity.view", "insurance.read", "communications.read",
        "operations.view"}
#: record.read_all -> every record is in scope
FIRM = Principal(1, "firm@t", "Firm", frozenset(_CAP | {"record.read_all"}))
#: no record.read_all and no assignments -> every fixture record is OUT of scope
LIMITED = Principal(2, "limited@t", "Limited", frozenset(_CAP))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            c.execute(delete(tasks).where(tasks.c.title.like(like)))
            if ppl:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(delete(tasks).where(tasks.c.person_id.in_(ppl)))
                c.execute(delete(people).where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(delete(households).where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "TDF" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _fixture(tag):
    """Household A with TWO members, household B with one, plus an empty household.

    Every task is stored the way ``app.services.tasks.create_task`` stores one: ``person_id`` only,
    ``household_id`` left NULL. That is the shape that made the first attempt at this page wrong.
    ``person_a_lease`` is the production row that exposed the store mismatch: status "open" (a value
    the operational_tasks CHECK constraint forbids), priority "normal", a due date, no assignee.
    Spouse A2 carries first/last with ``full_name`` NULL -- the canonical display-name fallback.
    """
    with engine.begin() as c:
        hid_a = c.execute(insert(households).values(name=f"White Household {tag}")
                          .returning(households.c.id)).scalar_one()
        hid_b = c.execute(insert(households).values(name=f"Pullen Household {tag}")
                          .returning(households.c.id)).scalar_one()
        hid_empty = c.execute(insert(households).values(name=f"Empty Household {tag}")
                              .returning(households.c.id)).scalar_one()
        pid_a = c.execute(insert(people).values(first_name="Michael", last_name=f"White{tag}",
                                                household_id=hid_a, active=True)
                          .returning(people.c.id)).scalar_one()
        pid_a2 = c.execute(insert(people).values(first_name="Susan", last_name=f"White{tag}",
                                                 household_id=hid_a, active=True)
                           .returning(people.c.id)).scalar_one()
        pid_b = c.execute(insert(people).values(first_name="Norman", last_name=f"Pullen{tag}",
                                                household_id=hid_b, active=True)
                          .returning(people.c.id)).scalar_one()
        # household membership lives in household_relationships -- the join the household
        # workspace's roster (portfolio._household_members) actually reads.
        for pid, hid, primary in ((pid_a, hid_a, True), (pid_a2, hid_a, False),
                                  (pid_b, hid_b, True)):
            c.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="member", is_primary=primary))
        made = {}
        for key, vals in (
            ("person_a_lease", {"person_id": pid_a, "status": "open",
                                "priority": "normal", "due_date": "2026-08-26"}),
            ("person_a_done", {"person_id": pid_a, "status": "complete"}),
            # the SPOUSE's task -- only a member-set filter finds it
            ("person_a2_spouse", {"person_id": pid_a2, "status": "open"}),
            # a different household entirely
            ("person_b", {"person_id": pid_b, "status": "open"}),
        ):
            made[key] = c.execute(
                insert(tasks).values(title=f"{key} {tag}", **vals).returning(tasks.c.id)
            ).scalar_one()
    return {"hid_a": hid_a, "hid_b": hid_b, "hid_empty": hid_empty,
            "pid_a": pid_a, "pid_a2": pid_a2, "pid_b": pid_b, **made}


def _page(principal=FIRM, **kw):
    return render(task_dashboard(fake_request("/tasks", state_principal=principal),
                                 principal=principal, **kw))


# ------------------------------------------------------------------ canonical store + filtering
def test_person_filter_reads_the_canonical_tasks_table():
    tag = _tag(); f = _fixture(tag)
    html = _page(person_id=f["pid_a"])
    assert f"person_a_lease {tag}" in html
    assert f"person_a_done {tag}" in html
    # a person filter is the person's own tasks -- not their spouse's
    assert f"person_a2_spouse {tag}" not in html


def test_lease_agreement_shaped_row_appears_for_its_person():
    """The exact production shape: status "open", priority "normal", dated, unassigned."""
    tag = _tag(); f = _fixture(tag)
    html = _page(person_id=f["pid_a"])
    assert f"person_a_lease {tag}" in html
    assert "open" in html and "normal" in html and "2026-08-26" in html


def test_unrelated_client_tasks_do_not_appear_under_a_person_filter():
    tag = _tag(); f = _fixture(tag)
    html = _page(person_id=f["pid_a"])
    assert f"person_b {tag}" not in html


def test_household_filter_aggregates_member_tasks_stored_by_person_id_only():
    """The defect this replaces: every fixture task has household_id NULL, exactly as
    create_task() writes them, so a tasks.household_id filter would have found nothing."""
    tag = _tag(); f = _fixture(tag)
    with engine.connect() as c:
        assert all(r is None for r in c.scalars(
            select(tasks.c.household_id).where(tasks.c.person_id.in_(
                [f["pid_a"], f["pid_a2"], f["pid_b"]]))))
    html = _page(household_id=f["hid_a"])
    assert f"person_a_lease {tag}" in html
    assert f"person_a2_spouse {tag}" in html          # the spouse, via the member set


def test_unrelated_household_tasks_do_not_appear():
    tag = _tag(); f = _fixture(tag)
    html = _page(household_id=f["hid_a"])
    assert f"person_b {tag}" not in html
    other = _page(household_id=f["hid_b"])
    assert f"person_b {tag}" in other
    assert f"person_a_lease {tag}" not in other
    assert f"person_a2_spouse {tag}" not in other


def test_household_with_no_members_lists_nothing_and_never_falls_back():
    tag = _tag(); f = _fixture(tag)
    html = _page(household_id=f["hid_empty"])
    assert f"Tasks for Empty Household {tag}" in html
    assert "Global tasks" not in html
    for key in ("person_a_lease", "person_a2_spouse", "person_b"):
        assert f"{key} {tag}" not in html


def test_household_member_set_matches_the_household_workspace_resolver():
    """Pins the mirroring: the dashboard's member set IS the workspace roster's member set."""
    from app.routes.task_dashboard import _household_member_ids
    from app.security.authorization import accessible_person_ids
    from app.services.portfolio import _household_members
    tag = _tag(); f = _fixture(tag)
    with engine.connect() as c:
        roster = [m["id"] for m in _household_members(f["hid_a"])]
        accessible = accessible_person_ids(c, FIRM)
        expected = roster if accessible is None else [i for i in roster if i in accessible]
        assert sorted(_household_member_ids(c, FIRM, f["hid_a"])) == sorted(expected)
        assert sorted(expected) == sorted([f["pid_a"], f["pid_a2"]])


def test_dashboard_household_view_equals_the_household_workspace_tasks_tab():
    """The whole point of the correction: the two surfaces must not disagree for one household."""
    from app.services.client360.household import get_household_workspace
    tag = _tag(); f = _fixture(tag)
    ws = get_household_workspace(FIRM, f["hid_a"])
    tab_titles = {t["title"] for t in ws["sections"]["tasks"]["tasks"]}
    assert tab_titles == {f"person_a_lease {tag}", f"person_a_done {tag}",
                          f"person_a2_spouse {tag}"}
    html = _page(household_id=f["hid_a"])
    for title in tab_titles:
        assert title in html
    assert f"person_b {tag}" not in html


def test_both_ids_use_conservative_and_semantics():
    tag = _tag(); f = _fixture(tag)
    # person A AND household B: A is not a member of B, so nothing matches. Never an OR that
    # would show either side's tasks.
    html = _page(person_id=f["pid_a"], household_id=f["hid_b"])
    for key in ("person_a_lease", "person_a_done", "person_a2_spouse", "person_b"):
        assert f"{key} {tag}" not in html
    # person A AND household A: A IS a member, so only A's own tasks -- not the spouse's
    html = _page(person_id=f["pid_a"], household_id=f["hid_a"])
    assert f"person_a_lease {tag}" in html
    assert f"person_a2_spouse {tag}" not in html


def test_task_created_through_the_canonical_service_appears_under_its_household():
    """End to end through app.services.tasks.create_task -- the real write path, not a raw insert."""
    from app.services.tasks import create_task
    tag = _tag(); f = _fixture(tag)
    title = f"Lease agreement {tag}"
    tid = create_task(f["pid_a"], title=title, priority="normal", source="client360")
    assert tid is not None
    with engine.connect() as c:
        assert c.scalar(select(tasks.c.household_id).where(tasks.c.id == tid)) is None
    assert title in _page(household_id=f["hid_a"])
    assert title in _page(person_id=f["pid_a"])
    assert title not in _page(household_id=f["hid_b"])


# ------------------------------------------------------------------ unfiltered behaviour is intact
def test_unfiltered_dashboard_is_unchanged_and_firm_wide():
    tag = _tag(); _fixture(tag)
    html = _page()
    assert "Global tasks" in html
    assert "Clear client filter" not in html
    for key in ("person_a_lease", "person_b"):
        assert f"{key} {tag}" in html


def test_limit_and_offset_still_bound_the_read():
    tag = _tag(); _fixture(tag)
    assert _page(limit=1).count("<tr>") == 2          # header row + one task row
    first = _page(limit=1)
    second = _page(limit=1, offset=1)
    assert first != second
    # the RC9 clamp is preserved
    assert _page(limit=99999, offset=-5) is not None


# ------------------------------------------------------------------ scope / non-disclosure
def test_out_of_scope_person_shows_no_tasks_and_no_name():
    tag = _tag(); f = _fixture(tag)
    html = _page(principal=LIMITED, person_id=f["pid_a"])
    assert "Tasks for the selected client" in html
    assert f"White{tag}" not in html
    for key in ("person_a_lease", "person_a_done", "person_a2_spouse", "person_b"):
        assert f"{key} {tag}" not in html


def test_out_of_scope_household_shows_no_tasks_and_no_name():
    tag = _tag(); f = _fixture(tag)
    html = _page(principal=LIMITED, household_id=f["hid_a"])
    assert "Tasks for the selected client" in html
    assert f"White Household {tag}" not in html
    assert f"person_a_lease {tag}" not in html


def test_out_of_scope_filter_never_falls_back_to_the_firm_wide_list():
    tag = _tag(); f = _fixture(tag)
    html = _page(principal=LIMITED, person_id=f["pid_a"])
    assert "Global tasks" not in html
    assert f"person_b {tag}" not in html


def test_unknown_id_in_scope_still_discloses_nothing():
    tag = _tag(); _fixture(tag)
    html = _page(person_id=99_000_111)
    assert "Tasks for the selected client" in html
    assert f"person_a_lease {tag}" not in html


# ------------------------------------------------------------------ rendered client context
def test_in_scope_person_page_names_the_client_and_clears_the_filter():
    tag = _tag(); f = _fixture(tag)
    html = _page(person_id=f["pid_a"])
    assert f"Tasks for Michael White{tag}" in html
    assert 'href="/tasks">Clear client filter</a>' in html


def test_in_scope_household_page_names_the_household():
    tag = _tag(); f = _fixture(tag)
    html = _page(household_id=f["hid_a"])
    assert f"Tasks for White Household {tag}" in html
    assert 'href="/tasks">Clear client filter</a>' in html


def test_client_column_uses_the_canonical_display_name_fallback():
    """full_name is NULL on every fixture person, so the Client cell used to render "None"."""
    tag = _tag(); f = _fixture(tag)
    with engine.connect() as c:
        assert c.scalar(select(people.c.full_name).where(people.c.id == f["pid_a"])) is None
    html = _page(person_id=f["pid_a"])
    assert f'href="/people/{f["pid_a"]}">Michael White{tag}</a>' in html
    assert ">None</a>" not in html
    # a populated full_name still wins, unchanged
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == f["pid_a"])
                  .values(full_name=f"M. J. White{tag}"))
    assert f"M. J. White{tag}</a>" in _page(person_id=f["pid_a"])


def test_household_page_names_every_member_row():
    tag = _tag(); f = _fixture(tag)
    html = _page(household_id=f["hid_a"])
    assert f"Michael White{tag}" in html and f"Susan White{tag}" in html


# ------------------------------------------------------------------ read-only
def test_rendering_the_page_changes_no_data():
    tag = _tag(); f = _fixture(tag)
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(tasks))
        rows = [tuple(r) for r in c.execute(
            select(tasks.c.id, tasks.c.status, tasks.c.household_id)
            .where(tasks.c.person_id == f["pid_a"]).order_by(tasks.c.id))]
    _page(person_id=f["pid_a"]); _page(household_id=f["hid_a"]); _page()
    _page(principal=LIMITED, person_id=f["pid_a"])
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(tasks)) == before
        assert [tuple(r) for r in c.execute(
            select(tasks.c.id, tasks.c.status, tasks.c.household_id)
            .where(tasks.c.person_id == f["pid_a"]).order_by(tasks.c.id))] == rows


# ------------------------------------------------------------------ capability agreement
def test_route_and_middleware_require_the_same_capability():
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    from app.security.middleware import RULES
    rule = next(code for pattern, code in RULES if pattern.search("/tasks"))
    assert rule == "task.read"
    dep = next(d.dependency for d in task_dashboard.__defaults__
               if getattr(d, "dependency", None) is not None)
    assert getattr(dep, CAPABILITY_DEP_ATTR) == ("task.read",)


def test_create_task_quick_action_uses_task_read_on_both_surfaces():
    from app.services.client360.registry import QUICK_ACTIONS
    qa = next(q for q in QUICK_ACTIONS if q.key == "create_task")
    assert qa.capability == "task.read"
    # a principal WITHOUT task.read must not be offered the action
    from app.services.client360.registry import visible_quick_actions
    no_task = Principal(3, "n@t", "N", frozenset(_CAP - {"task.read"}))
    assert not any(a["key"] == "create_task" for a in visible_quick_actions(no_task, 7783, None))
    from app.services.client360.household import _quick_actions
    ctx = {"household_id": 215, "primary": None}
    assert not any(a["key"] == "create_task" for a in _quick_actions(no_task, ctx))
    assert any(a["key"] == "create_task" for a in _quick_actions(FIRM, ctx))


def test_quick_actions_target_the_canonical_dashboard():
    from app.services.client360.household import _quick_actions
    from app.services.client360.registry import visible_quick_actions
    person = next(a for a in visible_quick_actions(FIRM, 3824, None) if a["key"] == "create_task")
    assert person["href"] == "/tasks?person_id=3824"
    hh = next(a for a in _quick_actions(FIRM, {"household_id": 215, "primary": None})
              if a["key"] == "create_task")
    assert hh["href"] == "/tasks?household_id=215"


def test_no_other_quick_action_capability_changed():
    from app.services.client360.registry import QUICK_ACTIONS
    assert {q.key: q.capability for q in QUICK_ACTIONS} == {
        "schedule_meeting": "scheduling.view",
        "upload_document": "documents.view",
        "add_note": "client.read",
        "create_task": "task.read",
        "start_tax_return": "tax.read",
        "create_opportunity": "opportunity.view",
        "start_insurance_case": "insurance.read",
        "send_secure_message": "communications.read",
        "generate_meeting_prep": "client.read",
    }


# ------------------------------------------------------------------ the firm-wide collection gate
def test_client_filtered_tasks_is_not_a_firm_wide_collection():
    """Without this exemption the repointed quick action would 403 for every role that holds
    task.read -- none of them hold record.read_all -- so the button would be dead on arrival."""
    from app.security.middleware import _firm_wide_collection_denied
    def req(query):
        return fake_request("/tasks", query=query)
    assert _firm_wide_collection_denied(req({}), LIMITED, "record.read_all")
    assert not _firm_wide_collection_denied(req({"person_id": "3824"}), LIMITED, "record.read_all")
    assert not _firm_wide_collection_denied(req({"household_id": "215"}), LIMITED, "record.read_all")
    # an empty parameter is not a client filter and must not open the firm-wide list
    assert _firm_wide_collection_denied(req({"person_id": ""}), LIMITED, "record.read_all")
    # record.read_all is still never denied
    assert not _firm_wide_collection_denied(req({}), FIRM, "record.read_all")


def test_other_firm_wide_collections_are_untouched():
    from app.security.middleware import FIRM_WIDE_COLLECTION, _firm_wide_collection_denied
    for path in ("/people", "/households", "/portfolio", "/relationship-entities"):
        assert FIRM_WIDE_COLLECTION.match(path), path
        r = fake_request(path, query={"person_id": "3824"})
        assert _firm_wide_collection_denied(r, LIMITED, "record.read_all"), path
    # the regex itself still matches /tasks; only the applied gate is narrowed
    assert FIRM_WIDE_COLLECTION.match("/tasks")


# ------------------------------------------------------------------ Operations work from 2129dd6
def test_operations_task_list_remains_registered_and_unchanged():
    from app.main import app
    route = next(r for r in app.routes if getattr(r, "path", None) == "/operations/task-list")
    assert "GET" in route.methods


def test_operations_items_client_filtering_remains():
    import inspect

    from app.services.operations import tasks as opstasks
    sig = inspect.signature(opstasks.list_tasks).parameters
    assert "person_id" in sig and "household_id" in sig
    src = inspect.getsource(opstasks.list_tasks)
    assert "tasks_t.c.person_id == person_id" in src
    assert "tasks_t.c.household_id == household_id" in src
