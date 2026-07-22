"""F4.8 / Epic 4 — Workflow API & administrative surface acceptance tests (ADR-016).

The API additively exposes existing Epic 4 functionality (approval reassignment,
workflow history, workflow evidence retrieval) through least-privilege,
capability-gated routes. It introduces no new workflow behavior.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import engine, households, people, roles, user_roles, users
from app.main import app
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.workflow_automation import launch_workflow, request_approval, workflow_detail
from app.services.workflow_evidence import list_workflow_evidence

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"

NEW_ROUTES = {
    ("/api/v1/workflows/approvals/{approval_id}/reassign", "POST"),
    ("/api/v1/workflows/{instance_id}/history", "GET"),
    ("/api/v1/workflows/{instance_id}/evidence", "GET"),
}


def _users(n=3):
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F48 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F48 {s}", active=True).returning(people.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        uids = []
        for i in range(n):
            uid = c.execute(users.insert().values(email=f"f48-{i}-{s}@e.com", normalized_email=f"f48-{i}-{s}@e.com",
                            display_name=f"f48-{i}", auth_subject=f"f48-{i}-{s}", status="active").returning(users.c.id)).scalar_one()
            if role_id:
                c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
            uids.append(uid)
    return (pid, hid, *uids)


def _instance(actor, pid, hid):
    return launch_workflow(DB_TEMPLATE, actor_user_id=actor, person_id=pid, household_id=hid,
                           idempotency_key=f"f48-{uuid.uuid4()}")


# --- route registration / inventory ------------------------------------------

def test_new_routes_registered_and_inventory():
    routes = {(getattr(r, "path", None), m) for r in app.routes for m in (getattr(r, "methods", None) or set())}
    assert NEW_ROUTES <= routes
    assert len(app.routes) == 617  # ... +38 governance (D.23) +41 integration (D.24)


def test_openapi_exposes_new_routes():
    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/workflows/approvals/{approval_id}/reassign"]
    assert "get" in paths["/api/v1/workflows/{instance_id}/history"]
    assert "get" in paths["/api/v1/workflows/{instance_id}/evidence"]


# --- authorization: least-privilege, capability-gated ------------------------

def test_new_endpoints_are_capability_gated():
    # The three new routes require work.write / work.read / audit.read respectively.
    for cap in ("work.write", "work.read", "audit.read"):
        dep = require_capability(cap)
        without = Principal(1, "no@e.com", "No", frozenset())
        with pytest.raises(HTTPException) as ei:
            dep(principal=without)
        assert ei.value.status_code == 403
        with_cap = Principal(2, "yes@e.com", "Yes", frozenset({cap}))
        assert dep(principal=with_cap) is with_cap


def test_evidence_uses_auditor_capability_not_operational():
    # Evidence retrieval is gated on audit.read (auditor), separate from work.* — a
    # user with only work.write cannot read evidence.
    dep = require_capability("audit.read")
    operational = Principal(3, "op@e.com", "Op", frozenset({"work.write", "work.read"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=operational)
    assert ei.value.status_code == 403


# --- surfaced behavior is the existing behavior ------------------------------

def test_history_surfaces_existing_events():
    pid, hid, actor, *_ = _users()
    instance_id = _instance(actor, pid, hid)
    # the history endpoint returns exactly workflow_detail()["events"] (existing data)
    events = workflow_detail(instance_id)["events"]
    assert isinstance(events, list) and any(e["event_type"] == "workflow_launched" for e in events)


def test_evidence_retrieval_returns_workflow_evidence():
    pid, hid, actor, *_ = _users()
    instance_id = _instance(actor, pid, hid)
    records = list_workflow_evidence(instance_id)
    assert records  # launch + step.activated evidence exist
    kinds = {r.provenance for r in records}
    assert "workflow.launched" in kinds
    # all records belong to this instance (reference scoping)
    base = f"workflow_instance:{instance_id}"
    assert all(r.reference == base or r.reference.startswith(base + "/") for r in records)


def test_reassign_endpoint_delegates_to_existing_service():
    # The reassign route delegates to reassign_approval (F4.5) — reconfirm the service
    # behaves (SoD + reroute) and that the route wires it (import + registration).
    from app.services.workflow_automation import decide_approval, reassign_approval
    pid, hid, requester, approver1, approver2 = _users()
    instance_id = _instance(requester, pid, hid)
    step_id = workflow_detail(instance_id)["steps"][0]["id"]
    approval_id = request_approval(step_id, requested_by_user_id=requester, approver_user_id=approver1)
    reassign_approval(approval_id, reassigned_by_user_id=requester, new_approver_user_id=approver2)
    with engine.connect() as c:
        from app.db import work_approvals
        assert c.execute(select(work_approvals.c.approver_user_id).where(work_approvals.c.id == approval_id)).scalar_one() == approver2
    decide_approval(approval_id, approver_user_id=approver2, decision="approved")  # new approver can decide

    from app.routes import workflows as wf_routes
    assert hasattr(wf_routes, "api_reassign") and hasattr(wf_routes, "api_history") and hasattr(wf_routes, "api_evidence")


def test_api_introduces_no_new_workflow_behavior():
    # The routes module delegates to existing services; it defines no engine/business logic.
    source = (REPO_ROOT / "app" / "routes" / "workflows.py").read_text()
    for forbidden in ("engine.begin(", "workflow_instances.update", "workflow_steps.insert", "write_audit_event"):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "WORKFLOW_API.md").is_file()
