"""Client / household activity timeline tests (Phase D.10).

Covers the projection contract, deterministic IDs + ordering, per-domain adapter mapping
(domain events / advisor work / compliance), redaction + unauthorized exclusion, person &
household scoping, filters/search/date-range/pagination, source links, missing-actor
behavior, no mutation controls / no raw JSON exposure, and dependency direction. It never
persists or fabricates events.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, insert, select, text
from starlette.requests import Request

from app.db import (
    advisor_work_events,
    advisor_work_items,
    compliance_decisions,
    compliance_reviews,
    engine,
    households,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.activity_timeline import service as tsvc

TL = "timeline.read"


def _sfx():
    return uuid.uuid4().hex[:8]


def _dt(days_ago):
    return datetime.now(UTC) - timedelta(days=days_ago)


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"tl-{tag}@e.test", normalized_email=f"tl-{tag}@e.test",
            display_name=f"Actor {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"C {tag}", primary_email=f"{tag}@e.test", normalized_email=f"{tag}@e.test",
            household_id=hh, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner", effective_date=date.today()))
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="household", entity_id=hh, assignment_type="owner", effective_date=date.today()))
        # A domain event (existing timeline_events).
        c.execute(insert(timeline_events).values(
            person_id=pid, household_id=hh, source="staff", event_type="note_added",
            title="Client note added", summary="Discussed plan.", event_time=_dt(3),
            event_metadata={"actor_user_id": uid}))
        # An advisor work item + events (D.9).
        wid = c.execute(insert(advisor_work_items).values(
            recommendation_id=f"beneficiary_review_recommendation:account:{pid}",
            recommendation_type="beneficiary_review", governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT",
            rule_version="1.0.0", policy_gate="compliance_required", priority="medium",
            recommendation_snapshot={"title": "Beneficiary review"}, person_id=pid, household_id=hh,
            created_by=uid, status="completed", completed_at=_dt(1), completed_by=uid,
            completion_notes="secret note", created_at=_dt(2), updated_at=_dt(1)).returning(advisor_work_items.c.id)).scalar_one()
        c.execute(insert(advisor_work_events).values(
            advisor_work_item_id=wid, event_type="created", new_status="new",
            actor_principal_id=uid, occurred_at=_dt(2)))
        c.execute(insert(advisor_work_events).values(
            advisor_work_item_id=wid, event_type="completed", prior_status="new", new_status="completed",
            actor_principal_id=uid, occurred_at=_dt(1), note="secret completion note"))
        # A compliance review + decision (D.7).
        rvid = c.execute(insert(compliance_reviews).values(
            recommendation_id=f"beneficiary_review_recommendation:account:{pid}",
            recommendation_type="beneficiary_review", source_entity_type="account", source_entity_id=pid,
            person_id=pid, household_id=hh, governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT",
            rule_version="1.0.0", policy_gate="compliance_required", recommendation_snapshot={"title": "x"},
            evidence_snapshot=["e"], status="declined", submitted_at=_dt(2), submitted_by=uid).returning(compliance_reviews.c.id)).scalar_one()
        c.execute(insert(compliance_decisions).values(
            compliance_review_id=rvid, decision="declined", reviewer_principal_id=uid, decided_at=_dt(1),
            comments="confidential reviewer comment", governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT",
            rule_version="1.0.0", evidence_snapshot=["e"]))
    return {"uid": uid, "pid": pid, "hh": hh, "wid": wid, "rvid": rvid}


def _teardown(ids):
    with engine.begin() as c:
        # advisor_work_events + compliance_decisions are append-only -> items/reviews with
        # events are leftovers; delete the childless rows only.
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        if not c.scalar(select(text("count(*)")).select_from(advisor_work_events).where(
                advisor_work_events.c.advisor_work_item_id == ids["wid"])):
            c.execute(delete(advisor_work_items).where(advisor_work_items.c.id == ids["wid"]))
        if not c.scalar(select(text("count(*)")).select_from(compliance_decisions).where(
                compliance_decisions.c.compliance_review_id == ids["rvid"])):
            c.execute(delete(compliance_reviews).where(compliance_reviews.c.id == ids["rvid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _full_principal(ids):  # timeline + advisor_work + compliance details
    return Principal(ids["uid"], "a@e.com", "P", frozenset({TL, "advisor_work.read", "compliance.review.read"}))


def _base_principal(ids):  # timeline only -> redacted work/compliance details
    return Principal(ids["uid"], "a@e.com", "P", frozenset({TL}))


# --- contract + ordering + mapping ------------------------------------------

def test_projection_contract_ids_and_ordering():
    ids = _setup()
    try:
        res = tsvc.client_timeline(_full_principal(ids), ids["pid"], page_size=50)
        rows = res["rows"]
        ids_seen = [r.event_id for r in rows]
        assert len(ids_seen) == len(set(ids_seen))  # no duplicates
        # Deterministic stable ids per source.
        assert any(r.event_id.startswith("domain:timeline_event:") for r in rows)
        assert any(r.event_id.startswith("advisor_work:event:") for r in rows)
        assert any(r.event_id == "compliance:decision:" + str(_decision_id(ids)) for r in rows)
        # Reverse-chronological, deterministic secondary sort.
        keyed = [(r.occurred_at, r.sort_key) for r in rows]
        assert keyed == sorted(keyed, reverse=True)
        # JSON-safe presentation model (no raw snapshots exposed).
        import json
        for r in rows:
            json.loads(json.dumps(r.to_dict()))
            assert "recommendation_snapshot" not in r.to_dict()
            assert "evidence_snapshot" not in r.to_dict()
    finally:
        _teardown(ids)


def _decision_id(ids):
    with engine.connect() as c:
        return c.scalar(select(compliance_decisions.c.id).where(
            compliance_decisions.c.compliance_review_id == ids["rvid"]))


def test_advisor_work_and_compliance_mapping():
    ids = _setup()
    try:
        rows = tsvc.client_timeline(_full_principal(ids), ids["pid"], page_size=50)["rows"]
        titles = {r.title for r in rows}
        assert "Advisor work created" in titles
        assert "Advisor work completed" in titles
        assert "Compliance review submitted" in titles
        assert any(r.title.startswith("Compliance decision recorded") for r in rows)
    finally:
        _teardown(ids)


# --- redaction + unauthorized exclusion of details --------------------------

def test_redaction_without_source_capabilities():
    ids = _setup()
    try:
        rows = tsvc.client_timeline(_base_principal(ids), ids["pid"], page_size=50)["rows"]
        work_completed = next(r for r in rows if r.title == "Advisor work completed")
        assert work_completed.redacted is True
        assert "secret" not in work_completed.summary
        assert work_completed.summary == "Additional details are restricted."
        assert work_completed.source_url is None  # link withheld without advisor_work.read
        decision = next(r for r in rows if r.title.startswith("Compliance decision"))
        assert decision.redacted is True
        assert "confidential" not in decision.summary
        assert decision.source_url is None
    finally:
        _teardown(ids)


def test_details_visible_with_source_capabilities():
    ids = _setup()
    try:
        rows = tsvc.client_timeline(_full_principal(ids), ids["pid"], page_size=50)["rows"]
        work = next(r for r in rows if r.title == "Advisor work completed")
        assert work.redacted is False
        assert "secret completion note" in work.summary
        assert work.source_url == f"/advisor-work/{ids['wid']}"
        decision = next(r for r in rows if r.title.startswith("Compliance decision"))
        assert "confidential reviewer comment" in decision.summary
        assert decision.source_url == f"/compliance/reviews/{ids['rvid']}"
    finally:
        _teardown(ids)


# --- scoping -----------------------------------------------------------------

def test_person_scope_first():
    ids = _setup()
    try:
        stranger = Principal(999999, "s@e.com", "S", frozenset({TL}))
        assert tsvc.client_timeline(stranger, ids["pid"]) is None
    finally:
        _teardown(ids)


def test_household_scope_includes_members():
    ids = _setup()
    try:
        res = tsvc.household_timeline(_full_principal(ids), ids["hh"], page_size=50)
        assert res is not None and res["total"] >= 3
        assert all(r.person_id == ids["pid"] or r.household_id == ids["hh"] for r in res["rows"])
    finally:
        _teardown(ids)


# --- filters / search / date range / pagination -----------------------------

def test_filters_search_daterange_pagination():
    ids = _setup()
    try:
        p = _full_principal(ids)
        assert tsvc.client_timeline(p, ids["pid"], source_domain="advisor_work")["rows"]
        assert all(r.source_domain == "advisor_work"
                   for r in tsvc.client_timeline(p, ids["pid"], source_domain="advisor_work")["rows"])
        assert tsvc.client_timeline(p, ids["pid"], search="Compliance review")["total"] >= 1
        assert tsvc.client_timeline(p, ids["pid"], search="zzz-none")["total"] == 0
        # Date range excludes older events.
        recent = tsvc.client_timeline(p, ids["pid"], date_from=(date.today()).isoformat())
        assert recent["total"] == 0  # all events are >= 1 day old
        paged = tsvc.client_timeline(p, ids["pid"], page=1, page_size=1)
        assert paged["page_size"] == 1 and paged["pages"] >= 1
    finally:
        _teardown(ids)


def test_page_size_is_bounded():
    ids = _setup()
    try:
        res = tsvc.client_timeline(_full_principal(ids), ids["pid"], page_size=99999)
        assert res["page_size"] <= tsvc.MAX_PAGE_SIZE
    finally:
        _teardown(ids)


def test_missing_actor_is_tolerated():
    ids = _setup()
    try:
        with engine.begin() as c:  # a domain event with no actor metadata
            c.execute(insert(timeline_events).values(
                person_id=ids["pid"], source="system", event_type="import",
                title="Imported", summary="", event_time=_dt(4), event_metadata={}))
        rows = tsvc.client_timeline(_full_principal(ids), ids["pid"], page_size=50)["rows"]
        imported = next(r for r in rows if r.title == "Imported")
        assert imported.actor_principal_id is None
        assert imported.actor_display_name is None
    finally:
        _teardown(ids)


# --- routes ------------------------------------------------------------------

def _req(path):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def test_route_requires_timeline_read_and_renders():
    from app.routes.activity_timeline import client_timeline as route
    ids = _setup()
    try:
        resp = route(_req(f"/people/{ids['pid']}/timeline"), ids["pid"], principal=_full_principal(ids))
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "Activity Timeline" in body
        assert "not the administrative audit log" in body
        # No mutation / bulk controls.
        for control in ("method=\"post\"", "Delete", "Bulk", "Approve"):
            assert control not in body
    finally:
        _teardown(ids)


def test_household_route_renders():
    from app.routes.activity_timeline import household_timeline as route
    ids = _setup()
    try:
        resp = route(_req(f"/households/{ids['hh']}/timeline"), ids["hh"], principal=_full_principal(ids))
        assert resp.status_code == 200
        assert "Activity Timeline" in resp.body.decode()
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_source_domains_do_not_import_timeline():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    for module in ("advisor_intelligence.py", "advisor_work.py", "compliance/reviews.py"):
        src = (root / module).read_text()
        assert "activity_timeline" not in src
