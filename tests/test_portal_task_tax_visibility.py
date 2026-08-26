"""Task and tax portal surfaces must never serve internal workflow or staff review data (criterion #3,
remediation task 2A).

``client_tasks`` returned whole ``workflow_steps`` rows including ``definition_snapshot`` — the internal
workflow definition. ``portal_intakes`` returned the staff intake structure with ``engagement_id``,
``person_id``, ``household_id`` and ``workflow_id``. ``portal_returns`` returned the staff
``return_detail`` verbatim, including ``tax_return_reviews`` (reviewer identity and notes), raw lifecycle
events and filing events. Four callers also resolved scope without a permission, so a grant with
``tasks: False`` still reached all of it.

The staff services are deliberately unchanged; only the portal boundary narrowed. ``test_staff_*`` below
proves that by asserting the staff detail still carries the review a client must never see.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert, update

from app.db import (
    engine,
    portal_access_grants,
    tax_client_approvals,
    tax_return_reviews,
    workflow_instances,
    workflow_steps,
)
from app.portal import service as psvc
from app.routes import portal as v1_routes
from app.services.tax_return_lifecycle import portal_returns, return_detail
from tests.test_portal_vault import _Env

pytestmark = pytest.mark.usefixtures("portal_messaging_on")

SECRET_WORKFLOW_MARKER = "MUST_NOT_REACH_PORTAL"
INTERNAL_REVIEW_NOTE = "INTERNAL REVIEW NOTE MUST NEVER REACH PORTAL"

#: Task-2A forbidden names, layered on top of the task-1 set.
FORBIDDEN_FIELDS = {
    "definition_snapshot", "secret_internal_workflow_marker", "workflow_id", "workflow_instance_id",
    "workflow_step_id", "template_step_id", "engagement_id", "household_id", "assigned_user_id",
    "assigned_team_id", "reviewed_by_user_id", "reviewer_user_id", "reviewer_id", "reviewer_notes",
    "review_notes", "decision_notes", "compliance_reasoning", "suitability_findings", "internal_notes",
    "staff_notes", "event_metadata", "external_id", "source", "organization_id", "audit_metadata",
    "raw_payload", "request_payload", "response_payload", "answers", "checklist", "gates",
    "preparer_ready", "reviews", "lifecycle_events", "filing_events", "events",
    # task-1 set, retained
    "resolved_by_user_id", "created_by_user_id", "staff_last_read_at", "sender_user_id",
    "requested_by_user_id", "approved_by_user_id", "delivery_metadata", "idempotency_key",
    "storage_path", "stored_name", "sha256", "source_reference", "advisor_notes",
}

TASK_KEYS = {"id", "name", "workflow_name", "status", "due_date", "completed_at"}
INTAKE_KEYS = {"context", "letter", "organizer", "questionnaire", "missing", "client_readiness"}
RETURN_KEYS = {"return_id", "return", "client_approvals"}
APPROVAL_KEYS = {"approval_type", "status", "requested_at", "decided_at"}


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def walk(value, path="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")


def assert_clean(payload, label):
    hits = [(p, k) for p, k, _v in walk(payload) if k in FORBIDDEN_FIELDS]
    assert hits == [], f"{label} disclosed internal field(s): {hits}"
    markers = [p for p, _k, v in walk(payload)
               if isinstance(v, str) and (SECRET_WORKFLOW_MARKER in v or INTERNAL_REVIEW_NOTE in v)]
    assert markers == [], f"{label} leaked an internal marker at {markers}"


def _grant(account_id, permissions):
    with engine.begin() as c:
        c.execute(update(portal_access_grants)
                  .where(portal_access_grants.c.portal_account_id == account_id)
                  .values(permissions=permissions))


def _client_task(person_id, household_id):
    """A client-facing workflow step whose definition_snapshot carries a secret marker."""
    with engine.begin() as c:
        wid = c.execute(insert(workflow_instances).values(
            name="Tax Organizer", person_id=person_id, household_id=household_id,
            status="active").returning(workflow_instances.c.id)).scalar_one()
        sid = c.execute(insert(workflow_steps).values(
            workflow_instance_id=wid, name="Upload your W-2", sequence=1, status="active",
            waiting_on="client",
            definition_snapshot={"secret_internal_workflow_marker": SECRET_WORKFLOW_MARKER,
                                 "assignment_config": {"audience": "client"}},
        ).returning(workflow_steps.c.id)).scalar_one()
    return sid


def _return_with_internal_review(env, person_id, household_id):
    """A tax return carrying a staff review the client must never see.

    Built through the canonical ``create_engagement`` service so the tax year, return type and
    engagement are created the way the application creates them."""
    from app.services.tax_domain import create_engagement
    result = create_engagement(
        {"tax_year": 2026, "return_type": "1040", "filing_status": "single",
         "person_id": person_id, "household_id": household_id, "assignee_user_id": env.user_id},
        actor_user_id=env.user_id, request_id=f"vis-{uuid.uuid4().hex[:8]}")
    rid = result["return_id"]
    with engine.begin() as c:
        c.execute(insert(tax_return_reviews).values(
            tax_engagement_return_id=rid, review_type="preparer", status="completed",
            reviewer_user_id=env.user_id, notes=INTERNAL_REVIEW_NOTE))
        c.execute(insert(tax_client_approvals).values(
            tax_engagement_return_id=rid, approval_type="e_file_authorization",
            status="pending", decision_notes="client-private note"))
    return rid


# --- exact projection key sets -------------------------------------------------

def test_client_task_projection_keys_are_exact(env):
    _, principal, pid, hid = env.account(permissions={"tasks": True})
    _client_task(pid, hid)
    tasks = psvc.client_tasks(principal)
    assert tasks, "fixture produced no client-facing task"
    for t in tasks:
        assert set(t) == TASK_KEYS, f"task projection drifted: {set(t) ^ TASK_KEYS}"


def test_portal_return_projection_keys_are_exact(env):
    _, principal, pid, hid = env.account(permissions={"tasks": True})
    _return_with_internal_review(env, pid, hid)
    returns = portal_returns(principal)
    assert returns, "fixture produced no return"
    for r in returns:
        assert set(r) == RETURN_KEYS, f"return projection drifted: {set(r) ^ RETURN_KEYS}"
        assert set(r["return"]) == {"return_type", "status", "filing_status"}
        for a in r["client_approvals"]:
            assert set(a) == APPROVAL_KEYS, f"approval projection drifted: {set(a) ^ APPROVAL_KEYS}"


# --- STEP 7: workflow definition leak ------------------------------------------

def test_definition_snapshot_never_reaches_the_portal(env):
    _, principal, pid, hid = env.account(permissions={"tasks": True, "documents": True,
                                                      "messages": True})
    _client_task(pid, hid)
    assert_clean(psvc.client_tasks(principal), "client_tasks")
    assert_clean(v1_routes.api_tasks(principal=principal), "v1 tasks route")
    assert_clean(psvc.dashboard(principal)["tasks"], "dashboard.tasks")


# --- STEP 6: tax review leak, and staff data preserved --------------------------

def test_portal_return_hides_the_internal_review(env):
    _, principal, pid, hid = env.account(permissions={"tasks": True})
    _return_with_internal_review(env, pid, hid)
    assert_clean(portal_returns(principal), "portal_returns")


def test_staff_return_detail_still_contains_the_internal_review(env):
    """Proves the PORTAL boundary changed, not the staff data model."""
    _, _principal, pid, hid = env.account(permissions={"tasks": True})
    rid = _return_with_internal_review(env, pid, hid)
    staff = return_detail(rid)
    assert staff["reviews"], "staff return_detail lost its reviews"
    assert any(r["notes"] == INTERNAL_REVIEW_NOTE for r in staff["reviews"])
    assert any(r["reviewer_user_id"] == env.user_id for r in staff["reviews"])
    assert "events" in staff and "filing_events" in staff


# --- permission regressions (tasks=False) ---------------------------------------

def test_tasks_permission_gates_every_task_and_tax_surface(env):
    from app.services.tax_intake import portal_intakes
    _, principal, pid, hid = env.account(permissions={"tasks": True})
    _client_task(pid, hid)
    _return_with_internal_review(env, pid, hid)
    assert psvc.client_tasks(principal), "tasks should be visible with tasks=True"
    assert portal_returns(principal), "returns should be visible with tasks=True"

    _grant(principal.account_id, {"tasks": False, "documents": True})
    assert psvc.client_tasks(principal) == [], "tasks leaked with tasks=False"
    assert psvc.client_action_needed(principal) == [], "action-needed leaked with tasks=False"
    assert portal_intakes(principal) == [], "intakes leaked with tasks=False"
    assert portal_returns(principal) == [], "returns leaked with tasks=False"
    assert v1_routes.api_tasks(principal=principal)["tasks"] == []

    _grant(principal.account_id, {"tasks": True, "documents": True})
    assert psvc.client_tasks(principal), "tasks must return once the grant is restored"


def test_action_detail_fails_closed_without_the_tasks_grant(env):
    _, principal, _pid, _hid = env.account(permissions={"tasks": False, "documents": True})
    from app.services.exception_engine import ExceptionNotFoundError
    with pytest.raises((ExceptionNotFoundError, PermissionError, ValueError)):
        psvc.client_action_detail(principal, 999_777)


# --- STEP 11: whole-dashboard aggregate ------------------------------------------

def test_complete_dashboard_aggregate_discloses_nothing_internal(env):
    _, principal, pid, hid = env.account(permissions={"tasks": True, "documents": True,
                                                      "messages": True})
    _client_task(pid, hid)
    _return_with_internal_review(env, pid, hid)
    psvc.create_thread(principal, household_id=hid, person_id=pid, subject="Q", body="hello")
    env.staff_doc(pid, client_visible=True)
    assert_clean(psvc.dashboard(principal), "complete dashboard aggregate")
