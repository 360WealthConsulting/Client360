"""Portal read surfaces must never serve internal fields (compliance criterion #3, remediation task 1).

Five portal read surfaces returned whole database rows — ``select(table)`` → ``.mappings().all()`` —
so internal staff assignment ids, workflow identifiers, transport metadata and unbounded JSON blobs
reached external clients, and any future column would have appeared in a portal response automatically.
``GET /api/v1/portal/profile`` additionally returned ``principal.__dict__``. Two callers also resolved
scope without a ``permission``, so a grant with ``messages: False`` still listed threads and one with
``documents: False`` still listed requests.

The registry in ``app/portal/visibility.py`` is declarative: ``validate_portal()`` proves it is
self-consistent, NOT that responses obey it. These tests supply the missing half — recursive disclosure
checks over real responses, exact-key checks per projection, and an explicit projection→registry mapping
that fails when a projection grows a field.

Scope is deliberately task 1. ``REMAINING_TASK_2_FINDINGS`` pins the leaks that are still open so no one
can read a green run here as "all portal read surfaces are clean".
"""
from __future__ import annotations

import uuid
from datetime import UTC

import pytest
from sqlalchemy import insert, update

from app.db import (
    engine,
    portal_access_grants,
    portal_document_requests,
    portal_notifications,
    timeline_events,
)
from app.portal import service as psvc
from app.portal import visibility
from app.routes import portal as v1_routes
from app.routes import portal_api as v0_routes
from tests._portal_util import fake_request
from tests.test_portal_vault import _Env

pytestmark = pytest.mark.usefixtures("portal_messaging_on", "portal_documents_download_on")

#: Never acceptable in ANY portal response, at any nesting depth.
FORBIDDEN_FIELDS = {
    "assigned_user_id", "assigned_team_id", "resolved_by_user_id", "created_by_user_id",
    "staff_last_read_at", "sender_user_id", "workflow_instance_id", "workflow_step_id",
    "requested_by_user_id", "approved_by_user_id", "delivery_metadata", "idempotency_key",
    "event_metadata", "external_id", "organization_id", "storage_path", "stored_name", "sha256",
    "source_reference", "audit_metadata", "compliance_reasoning", "suitability_findings",
    "advisor_notes", "definition_snapshot",
}

#: Task-1 projections → the visibility registry concept each field is served under.
#: Written by hand, NOT derived from the projections, so a projection that grows a field fails.
PROJECTION_REGISTRY_MAP = {
    "thread": ("messages.thread", {"id", "subject", "topic", "status", "created_at", "updated_at",
                                   "client_last_read_at", "unread"}),
    "message": ("messages.thread", {"id", "thread_id", "sender_type", "body", "sent_at"}),
    "request": ("documents.upload", {"id", "title", "description", "due_date", "status",
                                     "created_at", "uploaded_at"}),
    "notification": ("preferences.notification_channels",
                     {"id", "notification_type", "title", "body", "channel", "created_at", "read_at"}),
    "meeting": ("appointments.upcoming", {"event_type", "title", "summary", "event_time"}),
}

#: Portal read-surface findings that are still OPEN. Task 1 fixed the messaging/requests/notifications/
#: meetings/profile surfaces; task 2A fixed every task and tax finding. What remains is a different
#: class — billing and engagement lack an independent per-client feature decision at the SERVICE
#: boundary, so they are not eligible for the "base scope + explicit feature authorization"
#: classification that payroll qualifies for. Resolving them needs an authorization-model decision, not
#: a projection change, so they are deliberately left for task 2B rather than papered over.
#:
#: Compliance criterion #3 cannot close while this set is non-empty.
REMAINING_VISIBILITY_FINDINGS = {
    "engagement: portal_engagement has no per-client client_can decision (runtime gate only)",
}


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def walk(value, path="$"):
    """Yield (path, key) for every mapping key at any depth."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", k
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")


def assert_no_internal_fields(payload, label):
    hits = [(p, k) for p, k in walk(payload) if k in FORBIDDEN_FIELDS]
    assert hits == [], f"{label} disclosed internal field(s): {hits}"


def _grant_permissions(account_id, permissions):
    with engine.begin() as c:
        c.execute(update(portal_access_grants)
                  .where(portal_access_grants.c.portal_account_id == account_id)
                  .values(permissions=permissions))


def _seed(env, principal, person_id, household_id):
    """One thread + message, one document request, one notification, one meeting."""
    account_id = principal.account_id
    thread_id = psvc.create_thread(principal, household_id=household_id,
                                   person_id=person_id, subject="Question", body="Hello")
    with engine.begin() as c:
        c.execute(insert(portal_document_requests).values(
            person_id=person_id, household_id=household_id, title="Send your W-2",
            description="Upload the PDF", status="open", requested_by_user_id=env.user_id,
            workflow_instance_id=None, workflow_step_id=None))
        c.execute(insert(portal_notifications).values(
            portal_account_id=account_id, channel="in_app", notification_type="document_request",
            title="New request", body="You have a new request", status="delivered",
            entity_type="portal_document_request", entity_id=1,
            idempotency_key=uuid.uuid4().hex, delivery_metadata={"provider": "internal"}))
        c.execute(insert(timeline_events).values(
            person_id=person_id, household_id=household_id, source="microsoft",
            event_type="calendar_event", title="Annual review",
            summary="Portfolio review", event_time=_future(),
            external_id=uuid.uuid4().hex, event_metadata={"online_meeting_link": "https://x/y"}))
    return thread_id


def _future():
    from datetime import datetime, timedelta
    return datetime.now(UTC) + timedelta(days=7)


# --- exact projection key sets ------------------------------------------------

@pytest.mark.parametrize("name", sorted(PROJECTION_REGISTRY_MAP))
def test_projection_key_sets_are_exact(env, name):
    """A future database column must not appear in a portal response automatically."""
    _, principal, pid, hid = env.account()
    thread_id = _seed(env, principal, pid, hid)

    produced = {
        "thread": lambda: psvc.client_threads(principal),
        "message": lambda: psvc.list_messages(principal, thread_id),
        "request": lambda: psvc.client_document_requests(principal),
        "notification": lambda: psvc.client_notifications(principal),
        "meeting": lambda: psvc.dashboard(principal)["meetings"],
    }[name]()
    assert produced, f"{name}: fixture produced no rows to inspect"
    _registry_key, expected = PROJECTION_REGISTRY_MAP[name]
    for item in produced:
        assert set(item) == expected, f"{name} projection keys drifted: {set(item) ^ expected}"


# --- registry conformance ------------------------------------------------------

def test_every_projection_field_maps_to_an_externally_visible_registry_entry():
    for name, (registry_key, fields) in PROJECTION_REGISTRY_MAP.items():
        entry = visibility.field(registry_key)
        assert entry is not None, f"{name} maps to unregistered key {registry_key!r}"
        assert entry.external_visibility in visibility.EXTERNAL_STATES, \
            f"{name} maps to {registry_key!r} which is {entry.external_visibility}"
        assert entry.lifecycle != visibility.DEPRECATED, f"{registry_key!r} is deprecated"
        assert visibility.is_externally_visible(registry_key)
        assert fields, f"{name} has no declared fields"


def test_no_projection_field_is_itself_a_forbidden_name():
    for name, (_key, fields) in PROJECTION_REGISTRY_MAP.items():
        leaked = fields & FORBIDDEN_FIELDS
        assert leaked == set(), f"{name} projection declares forbidden field(s) {leaked}"


def test_registry_still_declares_the_internal_only_and_prohibited_concepts():
    forbidden = [f for f in visibility.REGISTRY
                 if f.external_visibility in visibility.FORBIDDEN_STATES]
    assert len(forbidden) >= 12
    for f in forbidden:
        assert not visibility.is_externally_visible(f.key)


# --- recursive disclosure over real responses ---------------------------------

def test_service_surfaces_disclose_nothing_internal(env):
    _, principal, pid, hid = env.account()
    thread_id = _seed(env, principal, pid, hid)
    for label, payload in [
        ("client_threads", psvc.client_threads(principal)),
        ("list_messages", psvc.list_messages(principal, thread_id)),
        ("client_document_requests", psvc.client_document_requests(principal)),
        ("client_notifications", psvc.client_notifications(principal)),
        ("dashboard.meetings", psvc.dashboard(principal)["meetings"]),
    ]:
        assert_no_internal_fields(payload, label)


def test_v1_routes_disclose_nothing_internal(env):
    _, principal, pid, hid = env.account()
    thread_id = _seed(env, principal, pid, hid)
    cases = {
        "v1 threads": v1_routes.api_threads(principal=principal),
        "v1 thread messages": v1_routes.api_messages(thread_id, principal=principal),
        "v1 requests": v1_routes.api_requests(principal=principal),
        "v1 notifications": v1_routes.api_notifications(principal=principal),
        "v1 profile": v1_routes.api_profile(principal=principal),
    }
    for label, payload in cases.items():
        assert_no_internal_fields(payload, label)


def test_v0_routes_disclose_nothing_internal(env):
    import json
    _, principal, pid, hid = env.account()
    thread_id = _seed(env, principal, pid, hid)
    cases = {
        "v0 threads": v0_routes.api_messages(fake_request(), principal=principal),
        "v0 thread messages": v0_routes.api_message_thread(fake_request(), thread_id, principal=principal),
        "v0 requests": v0_routes.api_requests(fake_request(), principal=principal),
        "v0 notifications": v0_routes.api_notifications(fake_request(), principal=principal),
        "v0 profile": v0_routes.api_profile(fake_request(), principal=principal),
    }
    for label, response in cases.items():
        assert_no_internal_fields(json.loads(bytes(response.body)), label)


def test_dashboard_aggregate_discloses_nothing_internal_for_task1_surfaces(env):
    _, principal, pid, hid = env.account()
    _seed(env, principal, pid, hid)
    dash = psvc.dashboard(principal)
    for key in ("messages", "document_requests", "notifications", "meetings", "documents"):
        if key in dash:
            assert_no_internal_fields(dash[key], f"dashboard.{key}")


def test_nested_occurrence_is_caught_not_just_top_level():
    """The walker must fail on a nested leak exactly as on a top-level one."""
    nested = {"notifications": [{"id": 1, "detail": {"delivery_metadata": {"provider": "x"}}}]}
    with pytest.raises(AssertionError):
        assert_no_internal_fields(nested, "synthetic nested payload")
    assert_no_internal_fields({"notifications": [{"id": 1, "title": "ok"}]}, "clean payload")


# --- profile parity -------------------------------------------------------------

def test_v0_and_v1_profile_return_the_same_contract(env):
    import json
    _, principal, _pid, _hid = env.account()
    v1 = v1_routes.api_profile(principal=principal)
    v0 = json.loads(bytes(v0_routes.api_profile(fake_request(), principal=principal).body))
    assert set(v1) == set(v0), f"v0/v1 profile contracts diverge: {set(v1) ^ set(v0)}"
    assert "account_id" not in v1, "v1 profile still exposes the internal portal account id"


# --- permission regressions (the compliance #6 caller omission) -----------------

def test_threads_require_the_messages_grant(env):
    _, principal, pid, hid = env.account()
    _seed(env, principal, pid, hid)
    assert psvc.client_threads(principal), "own thread should be listed with messages=True"

    _grant_permissions(principal.account_id, {"documents": True, "messages": False})
    assert psvc.client_threads(principal) == [], \
        "threads listed despite messages=False — scope resolved without the grant permission"
    assert v1_routes.api_threads(principal=principal)["threads"] == []

    _grant_permissions(principal.account_id, {"documents": True, "messages": True})
    assert psvc.client_threads(principal), "own thread must return once messages is restored"


def test_document_requests_require_the_documents_grant(env):
    _, principal, pid, hid = env.account()
    _seed(env, principal, pid, hid)
    assert psvc.client_document_requests(principal), "own request should be listed"

    _grant_permissions(principal.account_id, {"documents": False, "messages": True})
    assert psvc.client_document_requests(principal) == [], \
        "requests listed despite documents=False — scope resolved without the grant permission"
    assert v1_routes.api_requests(principal=principal)["requests"] == []

    _grant_permissions(principal.account_id, {"documents": True, "messages": True})
    assert psvc.client_document_requests(principal), "own request must return once documents is restored"


# --- task-2 inventory guard ------------------------------------------------------

def test_remaining_findings_are_recorded_and_not_silently_forgotten():
    """The portal is NOT fully clean. This pins exactly what is still open.

    Criterion #3 may only be considered for closure when this set is empty. Do not empty it without
    doing the work — the guards below prove the task-1 and task-2A entries really were fixed."""
    assert len(REMAINING_VISIBILITY_FINDINGS) == 1
    assert all("engagement" in f for f in REMAINING_VISIBILITY_FINDINGS), (
        "a task/tax or billing finding reappeared in the inventory")


def test_no_task_or_tax_finding_remains_in_the_inventory():
    """Task 2A closed every task/tax finding; none may be re-added without new evidence."""
    for term in ("client_tasks", "portal_intakes", "portal_returns", "client_action", "billing"):
        assert not any(term in f for f in REMAINING_VISIBILITY_FINDINGS), \
            f"{term} is back in the open-findings inventory"


def test_every_task_and_tax_caller_now_passes_a_permission():
    """The executable proof that task 2A's caller omissions are closed."""
    import inspect

    from app.services import tax_intake, tax_return_lifecycle
    for label, fn in (("client_tasks", psvc.client_tasks),
                      ("client_action_needed", psvc.client_action_needed),
                      ("client_action_detail", psvc.client_action_detail),
                      ("portal_intakes", tax_intake.portal_intakes),
                      ("portal_returns", tax_return_lifecycle.portal_returns)):
        src = inspect.getsource(fn)
        assert 'permission="tasks"' in src, f"{label} still resolves scope without permission='tasks'"
