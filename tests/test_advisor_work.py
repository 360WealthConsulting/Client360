"""Advisor Work Management tests (Phase D.9).

Covers work creation from recommendations, idempotent duplicate prevention, ownership,
the explicit lifecycle (assign/start/wait/complete/cancel/archive) with stale
protection, the append-only event history, recommendation integration (Create work /
Work exists, and that completion never changes the recommendation), authorization,
scope isolation, and queue search/filter/sort/pagination.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, insert, select, text
from starlette.requests import Request

from app.db import (
    accounts,
    advisor_work_events,
    advisor_work_items,
    engine,
    households,
    people,
    record_assignments,
    users,
)
from app.security.models import Principal
from app.services import advisor_work as svc
from app.services.advisor_intelligence import get_client_signals
from app.services.advisor_workspace import FIRM_TZ

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=FIRM_TZ)
CAPS = frozenset({"client.read", "insurance.read", "advisor_work.read",
                  "advisor_work.create", "advisor_work.assign", "advisor_work.update"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup(*, assigned=True):
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"aw-{tag}@e.test", normalized_email=f"aw-{tag}@e.test",
            display_name="AW", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"C {tag}", primary_email=f"{tag}@e.test", normalized_email=f"{tag}@e.test",
            household_id=hh, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"IRA-{tag}", account_name="IRA",
            status="open", registration_type="Traditional IRA", last_review_date=NOW.date()))
        if assigned:
            c.execute(insert(record_assignments).values(
                user_id=uid, entity_type="person", entity_id=pid,
                assignment_type="owner", effective_date=NOW.date()))
    return {"uid": uid, "pid": pid, "hh": hh, "principal": Principal(uid, "a@e.com", "AW", CAPS)}


def _teardown(ids):
    with engine.begin() as c:
        item_ids = list(c.scalars(select(advisor_work_items.c.id).where(
            advisor_work_items.c.person_id == ids["pid"])))
        for iid in item_ids:  # events append-only -> items with events are leftovers
            if not c.scalar(select(text("count(*)")).select_from(advisor_work_events).where(
                    advisor_work_events.c.advisor_work_item_id == iid)):
                c.execute(delete(advisor_work_items).where(advisor_work_items.c.id == iid))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(accounts).where(accounts.c.person_id == ids["pid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _rec_id(ids):
    return next(s.id for s in get_client_signals(ids["principal"], ids["pid"], now=NOW)
                if s.category == "recommendation"
                and s.recommendation.recommendation_type == "beneficiary_review")


# --- creation + duplicate prevention -----------------------------------------

def test_create_from_recommendation_snapshots():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"],
                                              recommendation_id=rid, actor_user_id=ids["uid"])
        assert item["status"] == "new"
        assert item["recommendation_id"] == rid
        assert item["governing_rule"] == "RULE-BENEFICIARY-DESIGNATION-PRESENT"
        assert item["rule_version"] == "1.0.0"
        assert item["policy_gate"] == "compliance_required"
        assert item["recommendation_snapshot"]["id"] == rid
        assert item["recommendation_snapshot"]["evidence"]
        assert svc.get_work(ids["principal"], item["id"])["events"][0]["event_type"] == "created"
    finally:
        _teardown(ids)


def test_only_recommendations_are_eligible():
    ids = _setup()
    try:
        op = next((s for s in get_client_signals(ids["principal"], ids["pid"], now=NOW)
                   if s.category != "recommendation"), None)
        if op:
            with pytest.raises(svc.IneligibleRecommendationError):
                svc.create_from_recommendation(ids["principal"], person_id=ids["pid"],
                                               recommendation_id=op.id, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_duplicate_open_work_prevented_then_allowed_after_completion():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        a = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        b = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        assert a["id"] == b["id"]  # idempotent
        svc.complete(ids["principal"], a["id"], completion_notes="done", expected_status="new", actor_user_id=ids["uid"])
        # After completion a NEW open item may be created.
        c = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        assert c["id"] != a["id"]
    finally:
        _teardown(ids)


def test_inaccessible_person_cannot_create_work():
    ids = _setup(assigned=False)
    try:
        admin = Principal(ids["uid"], "a@e.com", "AW", CAPS | {"record.read_all"})
        rid = next(s.id for s in get_client_signals(admin, ids["pid"], now=NOW)
                   if s.category == "recommendation")
        with pytest.raises(svc.IneligibleRecommendationError):
            svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- lifecycle + ownership ---------------------------------------------------

def test_assign_and_lifecycle_transitions():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        assert svc.assign(ids["principal"], item["id"], owner_principal_id=ids["uid"],
                          expected_status="new", actor_user_id=ids["uid"])["status"] == "assigned"
        assert svc.update_status(ids["principal"], item["id"], new_status="in_progress",
                                 expected_status="assigned", actor_user_id=ids["uid"])["status"] == "in_progress"
        assert svc.update_status(ids["principal"], item["id"], new_status="waiting",
                                 expected_status="in_progress", actor_user_id=ids["uid"])["status"] == "waiting"
        svc.update_status(ids["principal"], item["id"], new_status="in_progress", expected_status="waiting", actor_user_id=ids["uid"])
        out = svc.complete(ids["principal"], item["id"], completion_notes="finished",
                           expected_status="in_progress", actor_user_id=ids["uid"])
        assert out["status"] == "completed"
        got = svc.get_work(ids["principal"], item["id"])
        assert got["completed_by"] == ids["uid"]
        assert [e["event_type"] for e in got["events"]] == \
            ["created", "assigned", "in_progress", "waiting", "in_progress", "completed"]
    finally:
        _teardown(ids)


def test_invalid_transition_and_stale_protection():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        # archive is not allowed from 'new'
        with pytest.raises(svc.InvalidTransitionError):
            svc.update_status(ids["principal"], item["id"], new_status="archived", expected_status="new", actor_user_id=ids["uid"])
        # stale expected_status
        with pytest.raises(svc.StaleWorkError):
            svc.complete(ids["principal"], item["id"], completion_notes="x", expected_status="assigned", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_cancel_then_archive():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.update_status(ids["principal"], item["id"], new_status="cancelled", expected_status="new", actor_user_id=ids["uid"], note="not needed")
        svc.update_status(ids["principal"], item["id"], new_status="archived", expected_status="cancelled", actor_user_id=ids["uid"])
        assert svc.get_work(ids["principal"], item["id"])["status"] == "archived"
    finally:
        _teardown(ids)


# --- completion never changes the recommendation ----------------------------

def test_completion_does_not_alter_recommendation():
    ids = _setup()
    try:
        before = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        rid = _rec_id(ids)
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.complete(ids["principal"], item["id"], completion_notes="done", expected_status="new", actor_user_id=ids["uid"])
        after = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        # Recommendation ids, evidence, ordering, serialization unchanged; still present.
        assert [s.to_dict() for s in before] == [s.to_dict() for s in after]
        assert any(s.id == rid for s in after)
    finally:
        _teardown(ids)


# --- append-only events ------------------------------------------------------

def test_events_are_append_only():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        eid = svc.get_work(ids["principal"], item["id"])["events"][0]["id"]
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(advisor_work_events.update().where(advisor_work_events.c.id == eid).values(note="x"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(advisor_work_events).where(advisor_work_events.c.id == eid))
    finally:
        _teardown(ids)


# --- integration index -------------------------------------------------------

def test_open_work_index_reflects_open_items():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        assert svc.open_work_index(ids["principal"], ids["pid"]) == {}
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        idx = svc.open_work_index(ids["principal"], ids["pid"])
        assert idx[rid]["id"] == item["id"]
        svc.complete(ids["principal"], item["id"], completion_notes="d", expected_status="new", actor_user_id=ids["uid"])
        assert svc.open_work_index(ids["principal"], ids["pid"]) == {}  # completed -> not open
    finally:
        _teardown(ids)


# --- queue scope / search / filter / sort / pagination ----------------------

def test_queue_scope_isolation_and_filters():
    a = _setup()
    b = _setup()
    try:
        ia = svc.create_from_recommendation(a["principal"], person_id=a["pid"], recommendation_id=_rec_id(a), actor_user_id=a["uid"])
        svc.create_from_recommendation(b["principal"], person_id=b["pid"], recommendation_id=_rec_id(b), actor_user_id=b["uid"])
        rows = svc.list_work(a["principal"])["rows"]
        assert ia["id"] in {r["id"] for r in rows}
        assert all(r["person_id"] == a["pid"] for r in rows)  # scope-isolated
        assert svc.list_work(a["principal"], status="new")["total"] >= 1
        assert svc.list_work(a["principal"], recommendation_type="beneficiary_review")["total"] >= 1
        assert svc.list_work(a["principal"], policy_gate="compliance_required")["total"] >= 1
        paged = svc.list_work(a["principal"], page=1, page_size=1)
        assert paged["page_size"] == 1
    finally:
        _teardown(a)
        _teardown(b)


# --- route authorization + rendering + no-bulk ------------------------------

def _req(path="/advisor-work"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def test_routes_require_distinct_advisor_work_capabilities():
    import inspect

    from app.routes import advisor_work as route
    src = inspect.getsource(route)
    for cap in ("advisor_work.read", "advisor_work.create", "advisor_work.assign", "advisor_work.update"):
        assert f'require_capability("{cap}")' in src
    # Existing work.read is a different system and is not used here.
    assert 'require_capability("work.read")' not in src


def test_queue_and_detail_render_no_bulk_controls():
    from app.routes.advisor_work import work_detail, work_queue
    ids = _setup()
    try:
        item = svc.create_from_recommendation(ids["principal"], person_id=ids["pid"], recommendation_id=_rec_id(ids), actor_user_id=ids["uid"])
        qbody = work_queue(_req(), principal=ids["principal"]).body.decode()
        assert "Advisor Work" in qbody
        for control in ("Select all", "Bulk", "Delete"):
            assert control not in qbody
        dbody = work_detail(_req(f"/advisor-work/{item['id']}"), item["id"], principal=ids["principal"]).body.decode()
        assert "History" in dbody
        assert "never" in dbody  # the "never changes the recommendation" disclaimer
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_advisor_intelligence_does_not_import_advisor_work():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "services" / "advisor_intelligence.py").read_text()
    # Word-boundary match so the legitimate `advisor_workspace` import is not a match.
    assert re.search(r"\badvisor_work\b", src) is None
