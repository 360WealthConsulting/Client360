"""Campaign, Referral Source & Attribution tests (Phase D.14).

Covers CRUD, authorization (incl. sensitive budget/ROI gating), book scope, campaign lifecycle
+ events, referral computed metrics, opportunity attribution + immutable-after-close, campaign
ROI/CAC, referral leaderboard, deterministic BD intelligence, executive summary, delete detaches
attribution (opportunity survives), timeline boundaries (campaign lifecycle not client-anchored;
client-linked referral events are), route auth, and dependency direction.
"""
import re
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, func, insert, select
from starlette.requests import Request

from app.db import (
    campaigns,
    engine,
    opportunities,
    people,
    record_assignments,
    referral_sources,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.bizdev import intelligence as bizintel
from app.services.campaign import reporting as crep
from app.services.campaign import service as csvc
from app.services.opportunity import service as osvc
from app.services.referral import reporting as rrep
from app.services.referral import service as rsvc

CAMPAIGN_CAPS = frozenset({"campaign.view", "campaign.edit", "campaign.delete", "campaign.report",
                           "campaign.archive", "campaign.manage_budget", "campaign.manage_roi"})
REFERRAL_CAPS = frozenset({"referral.view", "referral.edit", "referral.delete", "referral.report"})
OPP_CAPS = frozenset({"opportunity.view", "opportunity.edit", "opportunity.close"})
ALL = CAMPAIGN_CAPS | REFERRAL_CAPS | OPP_CAPS


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"bd-{tag}@e.test", normalized_email=f"bd-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Prospect {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "pid": pid, "tag": tag}


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(opportunities).where(opportunities.c.created_by == ids["uid"]))
        c.execute(delete(opportunities).where(opportunities.c.person_id == ids["pid"]))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(referral_sources).where(referral_sources.c.created_by == ids["uid"]))
        c.execute(delete(campaigns).where(campaigns.c.created_by == ids["uid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))


def _p(ids, caps=ALL):
    return Principal(ids["uid"], "a@e.com", f"Adv {ids['uid']}", frozenset(caps))


# --- campaign CRUD + lifecycle ----------------------------------------------

def test_campaign_create_code_and_lifecycle():
    ids = _setup()
    try:
        p = _p(ids)
        c = csvc.create_campaign(p, name="Q3 Webinar", actor_user_id=ids["uid"], marketing_channel="email")
        assert c["status"] == "draft" and c["code"].startswith("q3-webinar")
        launched = csvc.set_status(p, c["id"], new_status="active", actor_user_id=ids["uid"])
        assert launched["status"] == "active"
        got = csvc.get_campaign(p, c["id"])
        assert any(e["event_type"] == "launched" for e in got["events"])
        assert any(e["event_type"] == "created" for e in got["events"])
    finally:
        _teardown(ids)


def test_campaign_budget_and_roi_gating():
    ids = _setup()
    try:
        no_budget = _p(ids, {"campaign.view", "campaign.edit"})
        with pytest.raises(csvc.CampaignPermissionError):
            csvc.create_campaign(no_budget, name="X", actor_user_id=ids["uid"], budget=1000)
        c = csvc.create_campaign(no_budget, name="Y", actor_user_id=ids["uid"])
        with pytest.raises(csvc.CampaignPermissionError):
            csvc.update_campaign(no_budget, c["id"], actor_user_id=ids["uid"], fields={"budget": "5000"})
        # With the capability it succeeds.
        csvc.update_campaign(_p(ids), c["id"], actor_user_id=ids["uid"], fields={"budget": "5000"})
    finally:
        _teardown(ids)


def test_campaign_firm_visibility_and_archive_gating():
    ids = _setup()
    try:
        c = csvc.create_campaign(_p(ids), name="Z", actor_user_id=ids["uid"])
        # Any campaign.view holder sees any campaign (firm asset — no per-record scope).
        other = Principal(99991001, "o@e", "O", {"campaign.view"})
        assert csvc.get_campaign(other, c["id"]) is not None
        # Archive requires campaign.archive.
        no_arch = _p(ids, {"campaign.view", "campaign.edit"})
        csvc.set_status(no_arch, c["id"], new_status="active", actor_user_id=ids["uid"])
        with pytest.raises(csvc.CampaignPermissionError):
            csvc.set_status(no_arch, c["id"], new_status="archived", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- referral CRUD + scope + metrics ----------------------------------------

def test_referral_create_validates_and_client_timeline():
    ids = _setup()
    try:
        p = _p(ids)
        with pytest.raises(rsvc.ReferralError):
            rsvc.create_referral_source(p, name="Bad", source_type="cpa", actor_user_id=ids["uid"],
                                        person_id=99999999)
        s = rsvc.create_referral_source(p, name="Smith CPA", source_type="cpa",
                                        actor_user_id=ids["uid"], person_id=ids["pid"])
        # Client-linked referral emits an approved client-timeline event.
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.person_id == ids["pid"], timeline_events.c.source == "referral"))
        assert n == 1 and s["status"] == "active"
    finally:
        _teardown(ids)


def test_referral_scope_blocks_stranger():
    ids = _setup()
    try:
        s = rsvc.create_referral_source(_p(ids), name="Firm CPA", source_type="cpa",
                                        actor_user_id=ids["uid"])
        stranger = Principal(99991002, "s@e", "S", REFERRAL_CAPS)
        assert rsvc.get_referral_source(stranger, s["id"]) is None
        assert all(r["id"] != s["id"] for r in rsvc.list_referral_sources(stranger)["rows"])
    finally:
        _teardown(ids)


def test_referral_metrics_computed_from_opportunities():
    ids = _setup()
    try:
        p = _p(ids)
        s = rsvc.create_referral_source(p, name="Ref", source_type="cpa", actor_user_id=ids["uid"])
        o = osvc.create_opportunity(p, title="Lead", actor_user_id=ids["uid"], person_id=ids["pid"],
                                    expected_revenue=100000)
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], referral_source_id=s["id"])
        osvc.close_opportunity(p, o["id"], outcome="won", actor_user_id=ids["uid"])
        m = rsvc.referral_metrics(p, s["id"])
        assert m["won_referrals"] == 1 and m["lifetime_value"] == 100000
        assert m["conversion_rate"] == 1.0 and m["average_close_time_days"] is not None
    finally:
        _teardown(ids)


# --- attribution -------------------------------------------------------------

def test_attribution_set_validate_and_immutable_after_close():
    ids = _setup()
    try:
        p = _p(ids)
        c = csvc.create_campaign(p, name="Camp", actor_user_id=ids["uid"])
        o = osvc.create_opportunity(p, title="Lead", actor_user_id=ids["uid"], person_id=ids["pid"])
        with pytest.raises(osvc.OpportunityError):
            osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=99999999)
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=c["id"],
                             fields={"origin": "campaign"})
        assert len(osvc.attribution_for(o["id"])) == 1
        won = osvc.close_opportunity(p, o["id"], outcome="won", actor_user_id=ids["uid"])
        assert won["attribution_locked"] is True
        with pytest.raises(osvc.OpportunityError):
            osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=None)
        # Override bypasses the lock.
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=None, override=True)
    finally:
        _teardown(ids)


# --- reporting ---------------------------------------------------------------

def test_campaign_performance_roi_and_cac():
    ids = _setup()
    try:
        p = _p(ids)
        c = csvc.create_campaign(p, name="ROI", actual_cost=5000, actor_user_id=ids["uid"])
        o = osvc.create_opportunity(p, title="Deal", actor_user_id=ids["uid"], person_id=ids["pid"],
                                    expected_revenue=100000)
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=c["id"])
        osvc.close_opportunity(p, o["id"], outcome="won", actor_user_id=ids["uid"])
        perf = crep.campaign_performance(p, csvc.get_campaign(p, c["id"]))
        assert perf["revenue"] == 100000 and perf["roi"] == 19.0 and perf["acquisition_cost"] == 5000
    finally:
        _teardown(ids)


def test_referral_report_leaderboard():
    ids = _setup()
    try:
        p = _p(ids)
        s = rsvc.create_referral_source(p, name="Top", source_type="cpa", actor_user_id=ids["uid"])
        o = osvc.create_opportunity(p, title="D", actor_user_id=ids["uid"], person_id=ids["pid"],
                                    expected_revenue=80000)
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], referral_source_id=s["id"])
        osvc.close_opportunity(p, o["id"], outcome="won", actor_user_id=ids["uid"])
        rep = rrep.referral_report(p)
        assert rep["leaderboard"][0]["referral_source_id"] == s["id"]
        assert rep["total_referral_revenue"] == 80000
    finally:
        _teardown(ids)


# --- intelligence + exec summary --------------------------------------------

def test_bizdev_intelligence_deterministic():
    ids = _setup()
    try:
        p = _p(ids)
        # Overspending campaign.
        c = csvc.create_campaign(p, name="Over", budget=1000, actual_cost=5000, actor_user_id=ids["uid"])
        csvc.set_status(p, c["id"], new_status="active", actor_user_id=ids["uid"])
        # Opportunity with no attribution -> missing_attribution.
        osvc.create_opportunity(p, title="Unattributed", actor_user_id=ids["uid"], person_id=ids["pid"])
        intel = bizintel.business_development_intelligence(p, today=date.today())
        kinds = {o["kind"] for o in intel["observations"]}
        assert "campaign_overspend" in kinds
        assert "campaign_no_opportunities" in kinds
        assert "missing_attribution" in kinds
        assert intel["thresholds"]["inactive_referral_days"] == 180
    finally:
        _teardown(ids)


def test_executive_summary_composition():
    ids = _setup()
    try:
        p = _p(ids)
        c = csvc.create_campaign(p, name="E", actual_cost=1000, actor_user_id=ids["uid"])
        o = osvc.create_opportunity(p, title="D", actor_user_id=ids["uid"], person_id=ids["pid"],
                                    expected_revenue=50000)
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=c["id"])
        osvc.close_opportunity(p, o["id"], outcome="won", actor_user_id=ids["uid"])
        es = bizintel.executive_summary(p)
        assert es["campaign_revenue"] == 50000 and es["campaign_cost"] == 1000
    finally:
        _teardown(ids)


# --- delete detaches attribution --------------------------------------------

def test_campaign_delete_detaches_attribution_opportunity_survives():
    ids = _setup()
    try:
        p = _p(ids)
        c = csvc.create_campaign(p, name="Del", actor_user_id=ids["uid"])
        o = osvc.create_opportunity(p, title="D", actor_user_id=ids["uid"], person_id=ids["pid"])
        osvc.set_attribution(p, o["id"], actor_user_id=ids["uid"], campaign_id=c["id"])
        csvc.delete_campaign(p, c["id"])
        survived = osvc.get_opportunity(p, o["id"])
        assert survived is not None and survived["campaign_id"] is None  # SET NULL
    finally:
        _teardown(ids)


# --- timeline boundary -------------------------------------------------------

def test_campaign_lifecycle_not_in_client_timeline():
    ids = _setup()
    try:
        p = _p(ids)
        c = csvc.create_campaign(p, name="NoTL", actor_user_id=ids["uid"])
        csvc.set_status(p, c["id"], new_status="active", actor_user_id=ids["uid"])
        with engine.connect() as conn:
            n = conn.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.source == "campaign"))
        assert n == 0   # campaigns have no client anchor -> not on the person timeline
    finally:
        _teardown(ids)


# --- routes ------------------------------------------------------------------

def test_route_renders():
    from app.routes.business_development import dashboard
    from app.routes.campaign import board as cboard
    from app.routes.referral import board as rboard
    ids = _setup()
    try:
        p = _p(ids)

        def req(path):
            return Request({"type": "http", "method": "GET", "path": path,
                            "headers": [], "query_string": b""})

        assert "Campaigns" in cboard(req("/campaigns"), principal=p).body.decode()
        assert "Referral Sources" in rboard(req("/referral-sources"), principal=p).body.decode()
        assert "Business Development" in dashboard(req("/business-development"), principal=p).body.decode()
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_source_domains_and_advisor_intelligence_do_not_import_bizdev():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    pattern = re.compile(r"import\s+(campaign|referral|bizdev)\b|"
                         r"from\s+app\.services\.(campaign|referral|bizdev)\s+import")
    for module in ("advisor_intelligence.py", "advisor_work.py", "compliance/reviews.py",
                   "activity_timeline/service.py", "annual_review.py", "business_owner.py"):
        src = (root / module).read_text()
        assert not pattern.search(src), f"{module} must not import campaign/referral/bizdev"
    # advisor_intelligence stays untouched (no new domain imports).
    ai = (root / "advisor_intelligence.py").read_text()
    assert "app.services.campaign" not in ai and "app.services.referral" not in ai
