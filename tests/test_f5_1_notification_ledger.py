"""F5.1 / Epic 5 — Canonical notification ledger & model acceptance tests (ADR-017).

Covers the additive ledger schema, deterministic idempotent creation, lifecycle/status
validation, recipient/source-reference behavior, the content/reference boundary
(content stays in the ledger; no events/audit/logs emitted), timestamps, and the
non-authoritative ledger guarantee (notifications never mutate workflow/domain state).
No providers, dispatch, consumers, preferences, or routes are introduced.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine, households, people, workflow_instances
from app.platform.outbox import outbox_events
from app.services.notifications import (
    DEAD,
    DELIVERED,
    DISABLED,
    FAILED,
    LIFECYCLE,
    NOTIFICATION_STATUSES,
    PENDING,
    SUPPRESSED,
    TERMINAL_STATUSES,
    NotificationRecord,
    get_notification,
    notification_dedupe_key,
    record_notification,
    validate_status,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _table():
    from app.db import metadata
    return metadata.tables["notifications"]


def _kw(**over):
    base = dict(notification_type="workflow.sla.escalated", recipient_type="user",
               recipient_ref=f"user:{uuid.uuid4().hex[:8]}", channel="in_app",
               title="Action required", body="A workflow step is overdue.")
    base.update(over)
    return base


# --- schema (migration) ------------------------------------------------------

def test_ledger_table_schema_and_constraints():
    t = _table()
    required = {"id", "notification_uid", "recipient_type", "recipient_ref", "channel",
                "notification_type", "status", "dedupe_key", "source_event_id", "source_ref",
                "provider_ref", "attempts", "last_error", "title", "body", "notification_metadata",
                "created_at", "updated_at", "delivered_at", "failed_at", "disabled_at",
                "suppressed_at", "dead_at", "read_at"}
    assert required <= set(t.columns.keys())
    # unique dedupe + uid and the three lookup indexes exist (reflected)
    constraint_names = {c.name for c in t.constraints if c.name}
    assert {"uq_notifications_uid", "uq_notifications_dedupe_key"} <= constraint_names
    assert {"ix_notifications_recipient", "ix_notifications_status", "ix_notifications_source_event"} <= {i.name for i in t.indexes}
    assert "read" not in NOTIFICATION_STATUSES  # read is a timestamp, not a status


def test_status_check_constraint_enforced_at_db_and_model():
    # Model-level validation rejects an unknown status before insert.
    with pytest.raises(ValueError, match="Invalid notification status"):
        record_notification(**_kw(status="bogus"))
    # DB-level CHECK rejects a raw insert that bypasses the model.
    t = _table()
    with pytest.raises(Exception):  # noqa: B017 - ck_notifications_status
        with engine.begin() as c:
            c.execute(t.insert().values(notification_uid=str(uuid.uuid4()), recipient_type="user",
                      recipient_ref="user:1", channel="in_app", notification_type="t", status="bogus",
                      dedupe_key=f"bad-{uuid.uuid4()}", title="x"))


# --- lifecycle / status spec -------------------------------------------------

def test_status_and_lifecycle_spec():
    assert NOTIFICATION_STATUSES == {PENDING, SUPPRESSED, DELIVERED, DISABLED, FAILED, DEAD}
    assert TERMINAL_STATUSES == {SUPPRESSED, DELIVERED, DISABLED, FAILED, DEAD}
    assert LIFECYCLE[PENDING] == TERMINAL_STATUSES and all(LIFECYCLE[t] == frozenset() for t in TERMINAL_STATUSES)
    assert validate_status("delivered") == "delivered"
    with pytest.raises(ValueError, match="Invalid notification status"):
        validate_status("read")  # read is not a status


# --- model creation ----------------------------------------------------------

def test_record_notification_creates_pending_row_with_content():
    rec = record_notification(**_kw())
    assert isinstance(rec, NotificationRecord)
    assert rec.id and rec.notification_uid and rec.status == PENDING
    assert rec.title == "Action required" and rec.body == "A workflow step is overdue."  # content in ledger
    assert rec.attempts == 0 and rec.created_at is not None
    # outcome timestamps unset initially; read is not set and is not a status
    assert rec.delivered_at is None and rec.failed_at is None and rec.read_at is None
    assert get_notification(notification_uid=rec.notification_uid).id == rec.id


# --- deterministic idempotent dedup ------------------------------------------

def test_deterministic_dedup_is_enforced():
    key_args = dict(notification_type="t.x", recipient_ref="user:42", channel="in_app",
                    source_event_id="evt-1", source_ref="workflow_instance:9")
    k1 = notification_dedupe_key(**key_args); k2 = notification_dedupe_key(**key_args)
    assert k1 == k2  # deterministic
    kw = _kw(notification_type="t.x", recipient_ref="user:42", channel="in_app",
             source_event_id="evt-1", source_ref="workflow_instance:9")
    r1 = record_notification(**kw)
    r2 = record_notification(**kw)  # duplicate logical notification
    assert r1.notification_uid == r2.notification_uid  # same row, not duplicated
    with engine.connect() as c:
        n = c.execute(select(func.count()).select_from(_table()).where(_table().c.dedupe_key == k1)).scalar_one()
    assert n == 1


# --- recipient / source references -------------------------------------------

def test_recipient_and_source_reference_behavior():
    rec = record_notification(**_kw(recipient_type="portal_account", recipient_ref="portal_account:7",
                                    source_event_id="evt-77", source_ref="workflow_instance:3",
                                    provider_ref="in_app"))
    assert rec.recipient_type == "portal_account" and rec.recipient_ref == "portal_account:7"
    assert rec.source_event_id == "evt-77" and rec.source_ref == "workflow_instance:3"
    assert rec.provider_ref == "in_app"


# --- content / reference boundary (no leak) ----------------------------------

def test_content_reference_boundary_emits_no_events_or_audit():
    from app.db import audit_events
    with engine.connect() as c:
        ob = c.execute(select(func.count()).select_from(outbox_events)).scalar_one()
        au = c.execute(select(func.count()).select_from(audit_events)).scalar_one()
    rec = record_notification(**_kw(body="SENSITIVE client detail"))
    with engine.connect() as c:
        ob2 = c.execute(select(func.count()).select_from(outbox_events)).scalar_one()
        au2 = c.execute(select(func.count()).select_from(audit_events)).scalar_one()
    # F5.1 records intent only: no outbox events, no audit records (so no content can leak there)
    assert ob2 == ob and au2 == au
    # metadata is references-only; content lives in title/body inside the ledger
    assert rec.notification_metadata == {}
    assert rec.body == "SENSITIVE client detail"


# --- non-authoritative ledger ------------------------------------------------

def test_ledger_is_non_authoritative_over_workflow_state():
    from app.services.workflow_automation import launch_workflow, workflow_detail
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F51 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F51 {s}", active=True).returning(people.c.id)).scalar_one()
    iid = launch_workflow("client_onboarding", actor_user_id=None, person_id=pid, household_id=hid, idempotency_key=f"f51-{s}")
    before = (workflow_detail(iid)["workflow"]["status"], tuple(st["status"] for st in workflow_detail(iid)["steps"]))
    rec = record_notification(**_kw(source_ref=f"workflow_instance:{iid}"))
    # reading, then deleting the notification, must not change workflow state
    get_notification(notification_uid=rec.notification_uid)
    with engine.begin() as c:
        c.execute(_table().delete().where(_table().c.id == rec.id))
    after = (workflow_detail(iid)["workflow"]["status"], tuple(st["status"] for st in workflow_detail(iid)["steps"]))
    assert before == after  # workflow instance/steps untouched by notification read+delete
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(workflow_instances).where(workflow_instances.c.id == iid)).scalar_one() == 1


# --- scope contract: portal_notifications untouched; no dispatch/providers ----

def test_scope_contract_ledger_only():
    from app.db import metadata
    assert "portal_notifications" in metadata.tables  # legacy ledger retained, separate table
    assert "notifications" in metadata.tables and _table() is not metadata.tables["portal_notifications"]
    source = (REPO_ROOT / "app" / "services" / "notifications.py").read_text()
    # No route, provider integration, event subscription, dispatch, or delivery code (F5.2–F5.7).
    for forbidden in ("APIRouter", "from app.portal.providers", "NOTIFICATION_PROVIDERS",
                      "subscribe(", "dispatch_pending", ".deliver("):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "NOTIFICATIONS.md").is_file()
