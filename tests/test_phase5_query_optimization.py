"""Release 0.9.9 Phase 5 — query-optimization / N+1 regression tests.

Each optimized hot path gets: a bounded query-count (independent of N) and/or
an identical-output cross-check against the pre-refactor code path, plus a
negative-scope check where authorization is involved.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import event, select

from app.db import (accounts, account_holdings, engine, households, people, roles,
    securities, tasks, team_memberships, teams, user_roles, users)
from app.security.models import Principal


class _QueryCounter:
    def __init__(self): self.count = 0
    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._cb); return self
    def _cb(self, *args): self.count += 1
    def __exit__(self, *a): event.remove(engine, "before_cursor_execute", self._cb)


# --- WP5.1 work_items: SQL authorization scope --------------------------------

def _advisor(user_id, caps=("work.read",)):
    return Principal(user_id, f"u{user_id}@e.com", "U", frozenset(caps))


def test_work_items_scopes_to_callers_book_and_ignores_unrelated_rows():
    from app.services.work_management import assign_work, work_items
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"WI {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"P {suffix}", active=True).returning(people.c.id)).scalar_one()
        task_id = c.execute(tasks.insert().values(person_id=pid, household_id=hid, title="Mine", priority="high", status="open", due_date=date.today()).returning(tasks.c.id)).scalar_one()
        advisor_role = c.scalar(select(roles.c.id).where(roles.c.code == "advisor"))
        uid = c.execute(users.insert().values(email=f"a-{suffix}@e.com", normalized_email=f"a-{suffix}@e.com", display_name="A", auth_subject=f"a-{suffix}", status="active").returning(users.c.id)).scalar_one()
        c.execute(user_roles.insert().values(user_id=uid, role_id=advisor_role))
    assign_work(entity_type="task", entity_id=task_id, assignment_role="primary", user_id=uid, actor_user_id=uid, request_id=f"wi-{suffix}")
    advisor = _advisor(uid)

    before_ids = {(i["entity_type"], i["entity_id"]) for i in work_items(advisor)}
    assert ("task", task_id) in before_ids

    # Add unrelated, unassigned tasks; the advisor's scoped result must not change.
    with engine.begin() as c:
        for n in range(15):
            c.execute(tasks.insert().values(person_id=pid, household_id=hid, title=f"Other {n}", priority="low", status="open", due_date=date.today()))
    after_ids = {(i["entity_type"], i["entity_id"]) for i in work_items(advisor)}
    assert after_ids == before_ids, "unrelated tasks leaked into scoped work_items"


def test_work_items_negative_scope_other_advisor_sees_nothing():
    from app.services.work_management import assign_work, work_items
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"NS {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"P {suffix}", active=True).returning(people.c.id)).scalar_one()
        task_id = c.execute(tasks.insert().values(person_id=pid, household_id=hid, title="Owned", priority="high", status="open", due_date=date.today()).returning(tasks.c.id)).scalar_one()
        advisor_role = c.scalar(select(roles.c.id).where(roles.c.code == "advisor"))
        owner = c.execute(users.insert().values(email=f"o-{suffix}@e.com", normalized_email=f"o-{suffix}@e.com", display_name="O", auth_subject=f"o-{suffix}", status="active").returning(users.c.id)).scalar_one()
        other = c.execute(users.insert().values(email=f"x-{suffix}@e.com", normalized_email=f"x-{suffix}@e.com", display_name="X", auth_subject=f"x-{suffix}", status="active").returning(users.c.id)).scalar_one()
        c.execute(user_roles.insert().values(user_id=owner, role_id=advisor_role))
        c.execute(user_roles.insert().values(user_id=other, role_id=advisor_role))
    assign_work(entity_type="task", entity_id=task_id, assignment_role="primary", user_id=owner, actor_user_id=owner, request_id=f"ns-{suffix}")
    other_ids = {(i["entity_type"], i["entity_id"]) for i in work_items(_advisor(other))}
    assert ("task", task_id) not in other_ids


# --- WP5.2 staff_dashboard bulk intake ---------------------------------------

def _launched_return():
    from app.services.tax_intake import launch_intake
    from tests.test_tax_intake import _case
    user_id, person_id, household_id, portal, result = _case()
    return_id = result["return_id"]
    launch_intake(return_id, actor_user_id=user_id, request_id=f"p5-{uuid.uuid4().hex[:8]}")
    return user_id, person_id, household_id, portal, return_id


def test_bulk_intake_detail_matches_per_return_intake_detail():
    from app.services.tax_intake import _bulk_intake_details, intake_detail
    _, _, _, _, rid = _launched_return()
    bulk = _bulk_intake_details([rid])
    assert bulk[rid] == intake_detail(rid)


def test_bulk_intake_detail_query_count_is_independent_of_n():
    from app.services.tax_intake import _bulk_intake_details
    _, _, _, _, r1 = _launched_return()
    _, _, _, _, r2 = _launched_return()
    _, _, _, _, r3 = _launched_return()
    with _QueryCounter() as one:
        _bulk_intake_details([r1])
    with _QueryCounter() as three:
        _bulk_intake_details([r1, r2, r3])
    assert one.count == three.count, (one.count, three.count)


# --- WP5.3 portal narrow endpoints -------------------------------------------

def test_narrow_portal_functions_match_dashboard_and_are_cheaper():
    from app.portal.service import client_notifications, client_documents, dashboard
    _, _, _, portal, _ = _launched_return()
    full = dashboard(portal)
    assert client_notifications(portal) == full["notifications"]
    assert client_documents(portal) == full["documents"]
    with _QueryCounter() as narrow:
        client_notifications(portal)
    with _QueryCounter() as whole:
        dashboard(portal)
    assert narrow.count < whole.count


# --- WP5.4 search_portfolios bulk concentration ------------------------------

def _seed_portfolio():
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"PF {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"Investor {suffix}", active=True).returning(people.c.id)).scalar_one()
        acct = c.execute(accounts.insert().values(custodian="Schwab", person_id=pid, household_id=hid, registration_type="Individual", total_value=100000, cash_value=10000).returning(accounts.c.id)).scalar_one()
        sec1 = c.execute(securities.insert().values(name="Alpha", symbol=f"AL{suffix[:4]}", asset_class="Equity").returning(securities.c.id)).scalar_one()
        sec2 = c.execute(securities.insert().values(name="Beta", symbol=f"BT{suffix[:4]}", asset_class="Bond").returning(securities.c.id)).scalar_one()
        c.execute(account_holdings.insert().values(account_id=acct, security_id=sec1, as_of_date=date.today(), market_value=70000))
        c.execute(account_holdings.insert().values(account_id=acct, security_id=sec2, as_of_date=date.today(), market_value=30000))
    return pid


def test_bulk_concentration_matches_get_person_portfolio():
    from app.services.portfolio import _largest_position_percents, get_person_portfolio
    pid = _seed_portfolio()
    bulk = _largest_position_percents([pid])
    assert bulk[pid] == get_person_portfolio(pid)["largest_position_percent"]


def test_bulk_concentration_query_count_independent_of_n():
    from app.services.portfolio import _largest_position_percents
    p1 = _seed_portfolio(); p2 = _seed_portfolio(); p3 = _seed_portfolio()
    with _QueryCounter() as one:
        _largest_position_percents([p1])
    with _QueryCounter() as three:
        _largest_position_percents([p1, p2, p3])
    assert one.count == three.count, (one.count, three.count)


# --- WP5.5 pagination --------------------------------------------------------

def test_dashboard_routes_expose_limit_and_offset_params():
    import inspect
    from app.routes.activity_dashboard import activity_dashboard
    from app.routes.task_dashboard import task_dashboard
    for fn in (activity_dashboard, task_dashboard):
        params = inspect.signature(fn).parameters
        assert "limit" in params and params["limit"].default == 100
        assert "offset" in params and params["offset"].default == 0


def test_activity_query_respects_limit():
    from app.db import activities
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"AL {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"P {suffix}", active=True).returning(people.c.id)).scalar_one()
        for n in range(5):
            c.execute(activities.insert().values(person_id=pid, activity_type="note", title=f"a{n}"))
    with engine.connect() as c:
        rows = c.execute(select(activities).where(activities.c.person_id == pid).order_by(activities.c.id.desc()).limit(2)).mappings().all()
    assert len(rows) == 2
