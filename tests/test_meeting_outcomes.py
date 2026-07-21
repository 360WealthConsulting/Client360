"""Meeting Outcomes workflow tests (Phase D.4).

Covers the first WRITE surface in the Advisor Workspace: authorization
(client.read GET / client.write POST + explicit person record-scope), and that
factual outcomes transition agreed work into EXISTING authoritative services
(Timeline, Notes, Work Management tasks, Workflow engine) with idempotency (no
duplicate tasks) — creating no new engine/model.
"""
import asyncio
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, insert, select
from starlette.requests import Request

import app.db as d
from app.db import (
    engine,
    people,
    record_assignments,
    tasks,
    timeline_events,
    users,
    workflow_instances,
)
from app.security.models import Principal
from app.services.advisor_workspace import record_meeting_outcome

person_notes = d.metadata.tables["person_notes"]
ADVISOR_CAPS = frozenset({"client.read", "client.write", "work.read", "task.read", "task.write"})


def _req(path="/workspace/meetings/1/outcome", body=None):
    scope = {"type": "http", "method": "POST" if body is not None else "GET",
             "path": path, "headers": [], "query_string": b""}
    if body is None:
        return Request(scope)

    async def _receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, _receive)


def _setup():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"adv-{tag}@e.test", normalized_email=f"adv-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        a = conn.execute(people.insert().values(full_name=f"A {tag}", primary_email=f"a{tag}@e.test",
            normalized_email=f"a{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        b = conn.execute(people.insert().values(full_name=f"B {tag}", primary_email=f"b{tag}@e.test",
            normalized_email=f"b{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        conn.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=a, assignment_type="owner",
            effective_date="2026-01-01"))
    return {"uid": uid, "a": a, "b": b,
            "principal": Principal(uid, "a@e.com", "Adv", ADVISOR_CAPS)}


def _teardown(ids):
    # workflow_events is trigger-protected append-only and workflow_instances.person_id
    # FK-references people, so a person that had a review workflow launched (plus its
    # workflow ledger) is intentionally left as a leftover — the shared, un-isolated
    # test DB tolerates leftovers (same convention as leftover users).
    with engine.begin() as conn:
        for pid in (ids["a"], ids["b"]):
            conn.execute(delete(person_notes).where(person_notes.c.person_id == pid))
            conn.execute(delete(tasks).where(tasks.c.person_id == pid))
            conn.execute(delete(timeline_events).where(timeline_events.c.person_id == pid))
        conn.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        for pid in (ids["a"], ids["b"]):
            if not conn.scalar(select(workflow_instances.c.id).where(workflow_instances.c.person_id == pid).limit(1)):
                conn.execute(delete(people).where(people.c.id == pid))


def _count(table, person_id, **eq):
    with engine.connect() as conn:
        q = select(table).where(table.c.person_id == person_id)
        for k, v in eq.items():
            q = q.where(getattr(table.c, k) == v)
        return len(conn.execute(q).all())


# --- outcome writes reuse authoritative services -----------------------------

def test_record_outcome_writes_timeline_notes_tasks_and_workflow():
    ids = _setup()
    a, uid = ids["a"], ids["uid"]
    try:
        res = record_meeting_outcome(
            a, actor_user_id=uid, completed=True, meeting_notes="Discussed plan",
            decisions="Rebalance to target", comments="Client happy",
            follow_ups=["Send statement", "Book annual review"], next_review_code="annual_review")
        assert res == {"timeline": True, "notes": 3, "tasks": 2, "workflow": res["workflow"]}
        assert res["workflow"] is not None
        # Persisted through the EXISTING services.
        assert _count(timeline_events, a, event_type="meeting_completed") == 1
        assert _count(person_notes, a, note_type="meeting") == 3
        assert _count(tasks, a) == 2
        assert _count(workflow_instances, a) == 1
    finally:
        _teardown(ids)


def test_no_duplicate_tasks_on_double_submit():
    ids = _setup()
    a, uid = ids["a"], ids["uid"]
    try:
        first = record_meeting_outcome(a, actor_user_id=uid, follow_ups=["Send statement"])
        second = record_meeting_outcome(a, actor_user_id=uid, follow_ups=["Send statement"])
        assert first["tasks"] == 1
        assert second["tasks"] == 0  # idempotent — no duplicate
        assert _count(tasks, a) == 1
    finally:
        _teardown(ids)


def test_next_review_only_whitelisted_template_launches():
    ids = _setup()
    a, uid = ids["a"], ids["uid"]
    try:
        bad = record_meeting_outcome(a, actor_user_id=uid, next_review_code="__evil__")
        assert bad["workflow"] is None
        assert _count(workflow_instances, a) == 0
        good = record_meeting_outcome(a, actor_user_id=uid, next_review_code="annual_review")
        assert good["workflow"] is not None
    finally:
        _teardown(ids)


def test_empty_outcome_writes_nothing():
    ids = _setup()
    a, uid = ids["a"], ids["uid"]
    try:
        res = record_meeting_outcome(a, actor_user_id=uid, follow_ups=["", "  "])
        assert res == {"timeline": False, "notes": 0, "tasks": 0, "workflow": None}
        assert _count(tasks, a) == 0
        assert _count(timeline_events, a, event_type="meeting_completed") == 0
    finally:
        _teardown(ids)


# --- authorization -----------------------------------------------------------

def test_get_form_authorized_and_inaccessible_person_404():
    from app.routes.workspace import meeting_outcome_form
    ids = _setup()
    try:
        resp = meeting_outcome_form(_req(), ids["a"], None, principal=ids["principal"])
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "Record meeting outcome" in body
        try:
            meeting_outcome_form(_req(), ids["b"], None, principal=ids["principal"])
            raise AssertionError("expected 404 for inaccessible person")
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        _teardown(ids)


def test_post_denies_inaccessible_person():
    from app.routes.workspace import meeting_outcome_submit
    ids = _setup()
    try:
        try:
            asyncio.run(meeting_outcome_submit(_req(body=b"completed=on"), ids["b"], principal=ids["principal"]))
            raise AssertionError("expected 404 for inaccessible person")
        except HTTPException as exc:
            assert exc.status_code == 404
        # Nothing was written for B.
        assert _count(timeline_events, ids["b"], event_type="meeting_completed") == 0
    finally:
        _teardown(ids)


def test_post_records_outcome_end_to_end():
    from app.routes.workspace import meeting_outcome_submit
    ids = _setup()
    a = ids["a"]
    try:
        body = b"completed=on&meeting_notes=Talked&follow_up=Send+doc&follow_up=&next_review="
        resp = asyncio.run(meeting_outcome_submit(_req(body=body), a, principal=ids["principal"]))
        assert resp.status_code == 303
        assert f"/workspace/meetings/{a}/outcome?saved=1" in resp.headers["location"]
        assert _count(timeline_events, a, event_type="meeting_completed") == 1
        assert _count(person_notes, a, note_type="meeting") == 1
        assert _count(tasks, a) == 1  # only the non-blank follow-up
    finally:
        _teardown(ids)


def test_outcome_form_has_no_ai_or_recommendation_content():
    from app.routes.workspace import meeting_outcome_form
    ids = _setup()
    try:
        body = meeting_outcome_form(_req(), ids["a"], None, principal=ids["principal"]).body.decode().lower()
        # Note: "Next review recommendation" is a factual advisor input field, not an
        # AI/generated recommendation — so the ban targets policy-gated intelligence terms.
        for banned in ("roth", "cross-sell", "suitab", "coverage gap",
                       "retirement readiness", "estate planning", "we recommend", "ai-generated"):
            assert banned not in body, f"banned content: {banned}"
    finally:
        _teardown(ids)
