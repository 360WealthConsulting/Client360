"""Opportunity & Pipeline domain tests (Phase D.13).

Covers ownership/reference validation (never creates People/Orgs, never infers ownership),
authorization + record/book scope + enumeration blocking, configurable stage transitions
(logic keys off category), win/loss close, field-edit-does-not-emit-timeline, approved timeline
events, activity logging + M365 reference validation, Advisor Work reference (opportunity-owned
link), pipeline reporting + win rate + aging, sensitive forecast redaction, deterministic
pipeline intelligence, additive consumer reads, CRUD + delete, Annual Review / Business Owner
integration gating, and dependency direction.
"""
import re
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select
from starlette.requests import Request

from app.db import (
    advisor_work_items,
    engine,
    households,
    opportunities,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.opportunity import intelligence, reporting
from app.services.opportunity import service as svc

CAPS = frozenset({"opportunity.view", "opportunity.edit", "opportunity.delete",
                  "opportunity.assign", "opportunity.close", "opportunity.report",
                  "opportunity.forecast"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"op-{tag}@e.test", normalized_email=f"op-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", household_id=hh, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "pid": pid, "hh": hh, "tag": tag}


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(opportunities).where(opportunities.c.created_by == ids["uid"]))
        c.execute(delete(opportunities).where(opportunities.c.person_id == ids["pid"]))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(advisor_work_items).where(advisor_work_items.c.created_by == ids["uid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _p(ids, caps=CAPS):
    return Principal(ids["uid"], "a@e.com", f"Adv {ids['uid']}", frozenset(caps))


def _stage(pipeline_id, code):
    return next(s for s in svc.list_stages(pipeline_id) if s["code"] == code)


# --- create / validation -----------------------------------------------------

def test_create_defaults_and_stage_category():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="Wealth prospect", actor_user_id=ids["uid"],
                                   person_id=ids["pid"], expected_revenue=250000, source="referral")
        assert o["status"] == "open"                      # from stage category
        assert o["stage_id"] is not None
        assert o["probability"] == 10                     # default lead probability
        assert o["primary_advisor_id"] == ids["uid"]
    finally:
        _teardown(ids)


def test_create_validates_targets_and_never_creates_people():
    ids = _setup()
    try:
        before = None
        with engine.connect() as c:
            before = c.scalar(select(func.count()).select_from(people))
        with pytest.raises(svc.OpportunityError):
            svc.create_opportunity(_p(ids), title="x", actor_user_id=ids["uid"], person_id=99999999)
        with engine.connect() as c:
            after = c.scalar(select(func.count()).select_from(people))
        assert before == after                            # no person fabricated
    finally:
        _teardown(ids)


def test_create_prospect_with_no_target_allowed():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="Cold lead", actor_user_id=ids["uid"])
        assert o["person_id"] is None and o["status"] == "open"
    finally:
        _teardown(ids)


# --- scope -------------------------------------------------------------------

def test_scope_first_and_enumeration_blocked():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="Mine", actor_user_id=ids["uid"], person_id=ids["pid"])
        stranger = Principal(99990001, "s@e.com", "S", CAPS)
        assert svc.get_opportunity(stranger, o["id"]) is None       # not visible
        assert all(r["id"] != o["id"] for r in svc.list_opportunities(stranger)["rows"])
        assert svc.get_opportunity(_p(ids), o["id"]) is not None     # visible to owner
    finally:
        _teardown(ids)


# --- transitions -------------------------------------------------------------

def test_stage_transition_keys_off_category_and_emits_timeline():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="T", actor_user_id=ids["uid"], person_id=ids["pid"])
        qual = _stage(o["pipeline_id"], "qualified")
        svc.change_stage(_p(ids), o["id"], new_stage_id=qual["id"], actor_user_id=ids["uid"])
        got = svc.get_opportunity(_p(ids), o["id"])
        assert got["stage_id"] == qual["id"] and got["status"] == "open"
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.person_id == ids["pid"], timeline_events.c.source == "opportunity"))
        assert n >= 2   # created + qualified
    finally:
        _teardown(ids)


def test_close_won_sets_closed_and_reason():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="T", actor_user_id=ids["uid"], person_id=ids["pid"])
        won = svc.close_opportunity(_p(ids), o["id"], outcome="won", actor_user_id=ids["uid"],
                                    reason="Great fit")
        assert won["status"] == "won" and won["closed_at"] is not None and won["win_reason"] == "Great fit"
    finally:
        _teardown(ids)


def test_field_edit_does_not_emit_timeline():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="T", actor_user_id=ids["uid"], person_id=ids["pid"])
        with engine.connect() as c:
            before = c.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.person_id == ids["pid"], timeline_events.c.source == "opportunity"))
        svc.update_opportunity(_p(ids), o["id"], actor_user_id=ids["uid"],
                               fields={"expected_revenue": "500000", "next_action": "Call"})
        with engine.connect() as c:
            after = c.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.person_id == ids["pid"], timeline_events.c.source == "opportunity"))
        assert before == after                            # field edits are not timeline events
    finally:
        _teardown(ids)


# --- activities + M365 reference + work link ---------------------------------

def test_log_activity_and_m365_reference_validation():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="T", actor_user_id=ids["uid"], person_id=ids["pid"])
        with engine.begin() as c:
            tid = c.execute(timeline_events.insert().values(
                person_id=ids["pid"], source="microsoft", event_type="calendar_event",
                title="Discovery call", event_time=datetime.now(UTC),
                external_id=f"outlook-calendar-x-person-{ids['pid']}").returning(
                    timeline_events.c.id)).scalar_one()
        act = svc.log_activity(_p(ids), o["id"], activity_type="meeting", actor_user_id=ids["uid"],
                               subject="Discovery", timeline_event_id=tid)
        assert act["timeline_event_id"] == tid
        with pytest.raises(svc.OpportunityError):
            svc.log_activity(_p(ids), o["id"], activity_type="note", actor_user_id=ids["uid"],
                             timeline_event_id=99999999)
    finally:
        _teardown(ids)


def test_advisor_work_reference_is_opportunity_owned():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="T", actor_user_id=ids["uid"], person_id=ids["pid"])
        with engine.begin() as c:
            wid = c.execute(advisor_work_items.insert().values(
                recommendation_id=f"x:{ids['tag']}", recommendation_type="beneficiary_review",
                governing_rule="R", rule_version="1.0.0", policy_gate="none", priority="medium",
                recommendation_snapshot={}, person_id=ids["pid"], created_by=ids["uid"],
                status="new").returning(advisor_work_items.c.id)).scalar_one()
        svc.link_work(_p(ids), o["id"], wid, actor_user_id=ids["uid"])
        got = svc.get_opportunity(_p(ids), o["id"])
        assert any(w["advisor_work_item_id"] == wid for w in got["linked_work"])
    finally:
        _teardown(ids)


# --- reporting + forecast redaction ------------------------------------------

def test_pipeline_report_win_rate_and_aging():
    ids = _setup()
    try:
        p = _p(ids)
        a = svc.create_opportunity(p, title="A", actor_user_id=ids["uid"], person_id=ids["pid"],
                                   expected_revenue=100000)
        svc.close_opportunity(p, a["id"], outcome="won", actor_user_id=ids["uid"])
        b = svc.create_opportunity(p, title="B", actor_user_id=ids["uid"], person_id=ids["pid"])
        svc.close_opportunity(p, b["id"], outcome="lost", actor_user_id=ids["uid"], reason="price")
        svc.create_opportunity(p, title="C", actor_user_id=ids["uid"], person_id=ids["pid"],
                               expected_revenue=50000)
        rep = reporting.pipeline_report(p, today=date.today())
        assert rep["counts"]["won"] == 1 and rep["counts"]["lost"] == 1 and rep["counts"]["open"] == 1
        assert rep["win_rate"] == 0.5
        assert rep["open_value"] == 50000
        assert "price" in rep["loss_reasons"]
        assert sum(rep["aging"].values()) == 1
    finally:
        _teardown(ids)


def test_forecast_weighted_and_route_redaction():
    ids = _setup()
    try:
        p = _p(ids)
        svc.create_opportunity(p, title="F", actor_user_id=ids["uid"], person_id=ids["pid"],
                               expected_revenue=200000, probability=50)
        fc = reporting.forecast_report(p)
        assert fc["expected_revenue_total"] == 200000 and fc["weighted_forecast_total"] == 100000
        # Route withholds forecast without the forecast capability.
        from app.routes.opportunity import reports as reports_route
        req = Request({"type": "http", "method": "GET", "path": "/opportunities/reports",
                       "headers": [], "query_string": b""})
        no_fc = Principal(ids["uid"], "a@e", "A", frozenset({"opportunity.report"}))
        resp = reports_route(req, principal=no_fc)
        assert resp.status_code == 200 and "Revenue forecast" not in resp.body.decode() \
            or "requires the forecast capability" in resp.body.decode()
    finally:
        _teardown(ids)


# --- intelligence ------------------------------------------------------------

def test_pipeline_intelligence_deterministic():
    ids = _setup()
    try:
        p = _p(ids)
        o = svc.create_opportunity(p, title="Old", actor_user_id=ids["uid"], person_id=ids["pid"])
        # Backdate creation to force aging + stalled + missing-next-action observations.
        with engine.begin() as c:
            c.execute(opportunities.update().where(opportunities.c.id == o["id"])
                      .values(created_at=datetime.now(UTC) - timedelta(days=120)))
        intel = intelligence.pipeline_intelligence(p, today=date.today())
        kinds = {obs["kind"] for obs in intel["observations"] if obs["opportunity_id"] == o["id"]}
        assert "aging" in kinds and "stalled" in kinds and "missing_next_action" in kinds
        assert intel["thresholds"]["aging_days"] == 90    # fixed, documented threshold
    finally:
        _teardown(ids)


# --- additive consumer reads + integrations ----------------------------------

def test_opportunities_for_person_scoped():
    ids = _setup()
    try:
        p = _p(ids)
        svc.create_opportunity(p, title="X", actor_user_id=ids["uid"], person_id=ids["pid"])
        assert len(svc.opportunities_for_person(p, ids["pid"])) == 1
        stranger = Principal(99990002, "s@e", "S", CAPS)
        assert svc.opportunities_for_person(stranger, ids["pid"]) == []
    finally:
        _teardown(ids)


def test_annual_review_and_business_owner_integration_gated():
    ids = _setup()
    try:
        svc.create_opportunity(_p(ids), title="Cross-sell", actor_user_id=ids["uid"], person_id=ids["pid"])
        from app.services import annual_review
        # With opportunity.view -> business_development populated.
        full = Principal(ids["uid"], "a@e", "A", frozenset({"annual_review.read", "opportunity.view"}))
        ws = annual_review.compose_workspace(full, ids["pid"])
        assert ws["business_development"] is not None and ws["business_development"]["total"] == 1
        # Without opportunity.view -> section omitted (restricted != missing).
        base = Principal(ids["uid"], "a@e", "A", frozenset({"annual_review.read"}))
        assert annual_review.compose_workspace(base, ids["pid"])["business_development"] is None
    finally:
        _teardown(ids)


# --- CRUD + delete -----------------------------------------------------------

def test_delete_opportunity():
    ids = _setup()
    try:
        o = svc.create_opportunity(_p(ids), title="Del", actor_user_id=ids["uid"], person_id=ids["pid"])
        svc.delete_opportunity(_p(ids), o["id"])
        assert svc.get_opportunity(_p(ids), o["id"]) is None
    finally:
        _teardown(ids)


# --- routes ------------------------------------------------------------------

def test_board_route_renders():
    from app.routes.opportunity import board
    ids = _setup()
    try:
        req = Request({"type": "http", "method": "GET", "path": "/opportunities",
                       "headers": [], "query_string": b""})
        resp = board(req, principal=_p(ids))
        assert resp.status_code == 200 and "Opportunity Pipeline" in resp.body.decode()
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_source_domains_do_not_import_opportunity():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    pattern = re.compile(r"import\s+opportunity\b|from\s+app\.services\.opportunity\s+import")
    # Producer/source domains must not import the Opportunity domain (consumers may, lazily).
    for module in ("advisor_intelligence.py", "advisor_work.py", "compliance/reviews.py",
                   "activity_timeline/service.py", "organization_service.py", "tax_domain.py",
                   "insurance.py"):
        src = (root / module).read_text()
        assert not pattern.search(src), f"{module} must not import opportunity"


def test_advisor_intelligence_does_not_import_opportunity_domain():
    # ADR-018: Pipeline Intelligence is NOT registered into the D.5 producer seam, so
    # advisor_intelligence never imports the Opportunity domain. (The pre-existing D.5C
    # "opportunity" signal *category* is unrelated to the new domain.)
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "advisor_intelligence.py").read_text()
    assert "app.services.opportunity" not in src
    assert not re.search(r"import\s+opportunity\b", src)
