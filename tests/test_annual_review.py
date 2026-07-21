"""Annual Review Workspace tests (Phase D.11).

Covers workspace composition, authorization + record scope, per-section capability
gating (no bypass), session lifecycle, checklist + note persistence, navigation
(routes render + Client360 link), service reuse (recommendations come from Advisor
Intelligence, not regenerated), and dependency direction (source domains never import
Annual Review). The workspace never mutates a source-domain record.
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select, text
from starlette.requests import Request

from app.db import (
    advisor_work_events,
    advisor_work_items,
    annual_review_sessions,
    compliance_reviews,
    engine,
    households,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services import annual_review as svc
from app.services.advisor_intelligence import get_client_signals

READ, CREATE, UPDATE = "annual_review.read", "annual_review.create", "annual_review.update"
FULL_CAPS = frozenset({READ, CREATE, UPDATE, "advisor_work.read", "timeline.read",
                       "compliance.review.read"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _dt(days_ago):
    return datetime.now(UTC) - timedelta(days=days_ago)


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"ar-{tag}@e.test", normalized_email=f"ar-{tag}@e.test",
            display_name=f"Advisor {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"C {tag}", primary_email=f"{tag}@e.test", normalized_email=f"{tag}@e.test",
            household_id=hh, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=date.today()))
        c.execute(insert(timeline_events).values(
            person_id=pid, household_id=hh, source="staff", event_type="note_added",
            title="Client note", summary="Discussed plan.", event_time=_dt(3),
            event_metadata={"actor_user_id": uid}))
        wid = c.execute(insert(advisor_work_items).values(
            recommendation_id=f"beneficiary_review_recommendation:account:{pid}",
            recommendation_type="beneficiary_review",
            governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT", rule_version="1.0.0",
            policy_gate="compliance_required", priority="medium",
            recommendation_snapshot={"title": "Beneficiary review"}, person_id=pid, household_id=hh,
            owner_principal_id=uid, created_by=uid, status="assigned", due_date=date.today(),
            created_at=_dt(2), updated_at=_dt(1)).returning(advisor_work_items.c.id)).scalar_one()
        c.execute(insert(advisor_work_events).values(
            advisor_work_item_id=wid, event_type="created", new_status="new",
            actor_principal_id=uid, occurred_at=_dt(2)))
        rvid = c.execute(insert(compliance_reviews).values(
            recommendation_id=f"beneficiary_review_recommendation:account:{pid}",
            recommendation_type="beneficiary_review", source_entity_type="account",
            source_entity_id=pid, person_id=pid, household_id=hh,
            governing_rule="RULE-BENEFICIARY-DESIGNATION-PRESENT", rule_version="1.0.0",
            policy_gate="compliance_required", recommendation_snapshot={"title": "x"},
            evidence_snapshot=["e"], status="pending_review", submitted_at=_dt(2),
            submitted_by=uid, assigned_reviewer_name="Reviewer R",
            assigned_reviewer_role="compliance").returning(compliance_reviews.c.id)).scalar_one()
    return {"uid": uid, "pid": pid, "hh": hh, "wid": wid, "rvid": rvid}


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(annual_review_sessions).where(annual_review_sessions.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        # advisor_work_events is append-only -> leave items with events as leftovers.
        if not c.scalar(select(text("count(*)")).select_from(advisor_work_events).where(
                advisor_work_events.c.advisor_work_item_id == ids["wid"])):
            c.execute(delete(advisor_work_items).where(advisor_work_items.c.id == ids["wid"]))
        c.execute(delete(compliance_reviews).where(compliance_reviews.c.id == ids["rvid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _principal(ids, caps=FULL_CAPS):
    return Principal(ids["uid"], "a@e.com", f"Advisor {ids['uid']}", frozenset(caps))


# --- composition -------------------------------------------------------------

def test_workspace_composition_has_all_sections():
    ids = _setup()
    try:
        ws = svc.compose_workspace(_principal(ids), ids["pid"])
        for key in ("person", "household_name", "snapshot", "client", "meeting",
                    "recommendations", "work", "activity", "compliance", "checklist"):
            assert key in ws
        assert ws["person"]["id"] == ids["pid"]
        assert ws["household_name"].startswith("HH ")
        assert len(ws["checklist"]) == len(svc.CHECKLIST_ITEMS)
        # Reused sections are populated (not None) for a fully-capable principal.
        assert ws["work"] and ws["work"][0]["id"] == ids["wid"]
        assert ws["work"][0]["owner_name"].startswith("Advisor ")  # resolved from users
        assert ws["activity"]["total"] >= 1
        assert ws["compliance"]["pending"] == 1
        assert ws["compliance"]["assignments"][0]["reviewer_name"] == "Reviewer R"
    finally:
        _teardown(ids)


def test_recommendations_are_reused_not_regenerated():
    ids = _setup()
    try:
        p = _principal(ids)
        ws = svc.compose_workspace(p, ids["pid"])
        expected = [s for s in get_client_signals(p, ids["pid"]) if s.category == "recommendation"]
        assert ws["recommendations"] == expected  # delegated, not re-derived
    finally:
        _teardown(ids)


# --- authorization + record scope + per-section gating ----------------------

def test_scope_first_returns_none_for_stranger():
    ids = _setup()
    try:
        stranger = Principal(999999, "s@e.com", "S", FULL_CAPS)
        assert svc.compose_workspace(stranger, ids["pid"]) is None
    finally:
        _teardown(ids)


def test_sections_gated_without_owning_capabilities():
    ids = _setup()
    try:
        base = _principal(ids, {READ})  # annual_review.read only, still owner-scoped
        ws = svc.compose_workspace(base, ids["pid"])
        assert ws is not None
        assert ws["work"] is None          # no advisor_work.read
        assert ws["activity"] is None      # no timeline.read
        assert ws["compliance"] is None    # no compliance.review.read
        # Core sections still present (recommendations are advisor-facing).
        assert "recommendations" in ws and ws["snapshot"] is not None
    finally:
        _teardown(ids)


# --- session lifecycle -------------------------------------------------------

def test_session_lifecycle_and_idempotent_start():
    ids = _setup()
    try:
        p = _principal(ids)
        s1 = svc.start_session(p, ids["pid"], advisor_id=ids["uid"])
        assert s1["status"] == "in_progress" and s1["started_at"] is not None
        # Idempotent: a second start returns the SAME open session.
        s2 = svc.start_session(p, ids["pid"], advisor_id=ids["uid"])
        assert s2["id"] == s1["id"]
        assert svc.open_session_for(p, ids["pid"])["id"] == s1["id"]
        done = svc.set_status(p, s1["id"], new_status="completed")
        assert done["status"] == "completed" and done["completed_at"] is not None
        # Completed is no longer editable.
        with pytest.raises(svc.InvalidSessionTransitionError):
            svc.save_session(p, s1["id"], notes="late note")
        arch = svc.set_status(p, s1["id"], new_status="archived")
        assert arch["status"] == "archived"
        assert svc.open_session_for(p, ids["pid"]) is None
        assert any(s["id"] == s1["id"] for s in svc.list_completed_sessions(p, ids["pid"]))
    finally:
        _teardown(ids)


def test_invalid_status_transition_rejected():
    ids = _setup()
    try:
        p = _principal(ids)
        s = svc.start_session(p, ids["pid"], advisor_id=ids["uid"])
        with pytest.raises(svc.InvalidSessionTransitionError):
            svc.set_status(p, s["id"], new_status="frozen")
    finally:
        _teardown(ids)


# --- checklist + note persistence -------------------------------------------

def test_checklist_and_note_persistence_filters_unknown_keys():
    ids = _setup()
    try:
        p = _principal(ids)
        s = svc.start_session(p, ids["pid"], advisor_id=ids["uid"])
        saved = svc.save_session(p, s["id"], notes="Reviewed beneficiaries.",
                                 checklist_state={"beneficiaries": True, "insurance": True,
                                                  "not_a_real_item": True})
        assert saved["notes"] == "Reviewed beneficiaries."
        assert saved["checklist_state"] == {"beneficiaries": True, "insurance": True}
        # build_checklist reflects stored state.
        checklist = svc.build_checklist(saved)
        checked = {c["key"] for c in checklist if c["checked"]}
        assert checked == {"beneficiaries", "insurance"}
    finally:
        _teardown(ids)


def test_session_scope_first():
    ids = _setup()
    try:
        p = _principal(ids)
        s = svc.start_session(p, ids["pid"], advisor_id=ids["uid"])
        stranger = Principal(999998, "s@e.com", "S", FULL_CAPS)
        assert svc.get_session(stranger, s["id"]) is None
        with pytest.raises(svc.SessionNotFoundError):
            svc.save_session(stranger, s["id"], notes="x")
    finally:
        _teardown(ids)


# --- navigation / routes -----------------------------------------------------

def _get(path):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def _post(path, body: bytes):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": path, "headers": [],
                    "query_string": b""}, receive)


def test_workspace_route_renders():
    from app.routes.annual_review import workspace as route
    ids = _setup()
    try:
        resp = route(_get(f"/annual-review/{ids['pid']}"), ids["pid"], principal=_principal(ids))
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "Annual Review" in body
        assert "Start annual review" in body  # no open session yet
        assert "Review checklist" in body
    finally:
        _teardown(ids)


def test_start_creates_session_and_session_route_renders():
    from app.routes.annual_review import session_view, start
    ids = _setup()
    try:
        p = _principal(ids)
        resp = asyncio.run(start(_post(f"/annual-review/{ids['pid']}/start", b""),
                                 ids["pid"], principal=p))
        assert resp.status_code == 303
        loc = resp.headers["location"]
        session_id = int(loc.rsplit("/", 1)[1])
        view = session_view(_get(loc), session_id, principal=p)
        assert view.status_code == 200
        assert "Mark review complete" in view.body.decode()
    finally:
        _teardown(ids)


def test_update_session_saves_via_route():
    from app.routes.annual_review import update_session
    ids = _setup()
    try:
        p = _principal(ids)
        s = svc.start_session(p, ids["pid"], advisor_id=ids["uid"])
        resp = asyncio.run(update_session(
            _post(f"/annual-review/session/{s['id']}",
                  b"notes=Discussed+goals&checklist=beneficiaries&checklist=tax_planning"),
            s["id"], principal=p))
        assert resp.status_code == 303
        reloaded = svc.get_session(p, s["id"])
        assert reloaded["notes"] == "Discussed goals"
        assert reloaded["checklist_state"] == {"beneficiaries": True, "tax_planning": True}
    finally:
        _teardown(ids)


def test_client360_link_present_when_capable():
    from pathlib import Path
    tmpl = (Path(__file__).resolve().parent.parent / "app" / "templates" / "people" / "workspace.html").read_text()
    assert "annual_review.read" in tmpl
    assert "/annual-review/{{ person.id }}" in tmpl


# --- dependency direction ----------------------------------------------------

def test_source_domains_do_not_import_annual_review():
    # Match IMPORT statements only — advisor_workspace has an unrelated review-template
    # code literally named "annual_review", which is not a dependency.
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    pattern = re.compile(r"import\s+annual_review\b|from\s+\S*annual_review\s+import|"
                         r"services\s+import\s+.*\bannual_review\b")
    for module in ("advisor_intelligence.py", "advisor_work.py", "compliance/reviews.py",
                   "activity_timeline/service.py", "advisor_workspace.py", "portfolio.py"):
        src = (root / module).read_text()
        assert not pattern.search(src), f"{module} must not import annual_review"
