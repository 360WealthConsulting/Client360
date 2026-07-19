"""F5.4 / Epic 5 — Event-driven notification intent creation tests (ADR-017).

Covers the canonical mapping registry, approved mappings, unknown/disabled mappings,
deterministic recipient derivation, the four F5.3 decision outcomes, intent-creation
policy, stable source/correlation/causation references, durable idempotency (including
across new DB sessions and via the outbox dispatcher), and the strict scope contract:
no dispatch, no provider invocation, no retry scheduling, no delivery attempts, no
workflow/domain/business-event mutation, no audit/evidence emission, and no notification
content in results/ledger/logs.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine, households, people
from app.platform.events import new_event
from app.platform.outbox import clear_subscribers, dispatch_pending, outbox_events, publish_event
from app.platform.workflow_events import approval_event_type
from app.services import notification_intents as intents
from app.services.notification_intents import (
    ALREADY_EXISTS,
    CREATED,
    DISABLED,
    FAILED,
    NOT_APPLICABLE,
    OUTCOMES,
    SUPPRESSED,
    IntentResult,
    NotificationMapping,
    create_intent_for_event,
    intent_dedupe_key,
    register_notification_consumers,
)
from app.services.notification_preferences import (
    ALLOWED,
    OPTED_OUT,
    WITHDRAWN,
    DeliveryDecision,
    record_consent,
    record_preference,
)
from app.services.notification_preferences import (
    DISABLED as D_DISABLED,
)
from app.services.notification_preferences import (
    NOT_APPLICABLE as D_NA,
)
from app.services.notification_preferences import (
    SUPPRESSED as D_SUPP,
)
from app.services.notifications import DISABLED as L_DISABLED
from app.services.notifications import PENDING as L_PENDING
from app.services.notifications import SUPPRESSED as L_SUPPRESSED
from app.services.notifications import get_notification

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUESTED = approval_event_type("requested")
REASSIGNED = approval_event_type("reassigned")


@pytest.fixture(autouse=True)
def _isolate():
    clear_subscribers()
    intents.clear_mappings()
    intents.install_default_mappings()
    yield
    clear_subscribers()
    intents.clear_mappings()


def _approver():
    return uuid.uuid4().int % 1_000_000_000 + 1


def _requested_event(approver_id, *, eid=None, corr=None, caus=None, instance=42, approval=7):
    return new_event(
        REQUESTED,
        {"workflow_instance_id": instance, "approval_id": approval, "kind": "requested",
         "approver_user_id": approver_id, "approver_team_id": None},
        event_id=eid or str(uuid.uuid4()),
        subject_ref=f"workflow_instance:{instance}",
        correlation_id=corr or f"workflow_instance:{instance}",
        causation_id=caus,
        producer="workflow.approvals",
    )


def _decider(decision, reason="r"):
    def _fn(rt, rr, ch, purpose, **kw):
        return DeliveryDecision(decision=decision, channel=ch, recipient_ref=rr,
                                purpose=purpose, reason_code=reason, reason="test")
    return _fn


def _drain_outbox(eid):
    """Dispatch until the target event's outbox row is processed (backlog-independent)."""
    for _ in range(50):
        s = dispatch_pending(batch_size=500)
        with engine.connect() as c:
            status = c.execute(select(outbox_events.c.status).where(outbox_events.c.event_id == eid)).scalar()
        if status == "dispatched" or (s["dispatched"] == 0 and s["failed"] == 0):
            return


# --- mapping registry --------------------------------------------------------

def test_default_mappings_are_the_two_approved_approval_events():
    m = intents.mappings()
    assert set(m) == {REQUESTED, REASSIGNED}
    assert m[REQUESTED].mapping_id == "workflow.approval.requested.v1"
    assert m[REQUESTED].channel == "in_app" and m[REQUESTED].recipient_type == "user"
    assert m[REQUESTED].consent_required is False and m[REQUESTED].enabled


def test_register_rejects_duplicate_event_type():
    with pytest.raises(ValueError):
        intents.register_mapping(NotificationMapping(
            mapping_id="dup", source_event_type=REQUESTED,
            notification_purpose=REQUESTED, recipient_resolver=lambda e: "user:1"))


# --- unknown / disabled ------------------------------------------------------

def test_unknown_event_type_is_not_applicable_noop():
    r = create_intent_for_event(new_event("some.unmapped.event", {"x": 1}))
    assert r.outcome == NOT_APPLICABLE and r.mapping_id is None and r.notification_uid is None


def test_disabled_mapping_creates_no_intent():
    intents.clear_mappings()
    intents.register_mapping(NotificationMapping(
        mapping_id="off", source_event_type=REQUESTED, notification_purpose=REQUESTED,
        recipient_resolver=intents._resolve_requested_approver, enabled=False))
    r = create_intent_for_event(_requested_event(_approver()))
    assert r.outcome == NOT_APPLICABLE and r.notification_uid is None


# --- recipient derivation ----------------------------------------------------

def test_missing_recipient_reference_creates_no_intent():
    ev = _requested_event(None)  # approver_user_id None -> team-only, no single recipient
    r = create_intent_for_event(ev)
    assert r.outcome == NOT_APPLICABLE and "recipient" in r.description


def test_reassigned_recipient_is_the_new_approver():
    new_id = _approver()
    ev = new_event(REASSIGNED, {"workflow_instance_id": 9, "approval_id": 3, "kind": "reassigned",
                                "from_approver": 111, "to_approver": new_id},
                   subject_ref="workflow_instance:9")
    r = create_intent_for_event(ev, decision_fn=_decider(ALLOWED, "channel_allowed"))
    assert r.outcome == CREATED and r.recipient_ref == f"user:{new_id}"


# --- the four F5.3 decision outcomes ----------------------------------------

def test_decision_allowed_creates_pending_intent():
    ap = _approver()
    r = create_intent_for_event(_requested_event(ap), decision_fn=_decider(ALLOWED, "channel_allowed"))
    assert r.outcome == CREATED
    rec = get_notification(notification_uid=r.notification_uid)
    assert rec.status == L_PENDING and rec.recipient_ref == f"user:{ap}"


def test_decision_suppressed_creates_suppressed_intent():
    r = create_intent_for_event(_requested_event(_approver()), decision_fn=_decider(D_SUPP, "recipient_opted_out"))
    assert r.outcome == SUPPRESSED
    assert get_notification(notification_uid=r.notification_uid).status == L_SUPPRESSED


def test_decision_disabled_creates_disabled_non_deliverable_intent():
    r = create_intent_for_event(_requested_event(_approver()), decision_fn=_decider(D_DISABLED, "provider_channel_disabled"))
    assert r.outcome == DISABLED
    rec = get_notification(notification_uid=r.notification_uid)
    assert rec.status == L_DISABLED and rec.attempts == 0  # non-deliverable; no attempt

def test_decision_not_applicable_creates_no_row():
    r = create_intent_for_event(_requested_event(_approver()), decision_fn=_decider(D_NA, "no_applicable_preference"))
    assert r.outcome == NOT_APPLICABLE and r.notification_uid is None


def test_unrecognized_decision_is_failed_and_safe():
    r = create_intent_for_event(_requested_event(_approver()), decision_fn=_decider("weird", "x"))
    assert r.outcome == FAILED and r.notification_uid is None
    assert r.outcome in OUTCOMES


# --- real F5.3 integration (no injected decision) ----------------------------

def test_real_decision_in_app_default_allowed():
    ap = _approver()
    r = create_intent_for_event(_requested_event(ap))  # in_app enabled, no opt-out -> allowed
    assert r.outcome == CREATED
    assert get_notification(notification_uid=r.notification_uid).status == L_PENDING


def test_real_decision_opt_out_suppresses():
    ap = _approver()
    record_preference(recipient_type="user", recipient_ref=f"user:{ap}", channel="in_app",
                      purpose=REQUESTED, preference_state=OPTED_OUT)
    r = create_intent_for_event(_requested_event(ap))
    assert r.outcome == SUPPRESSED and r.decision_reason_code == "recipient_opted_out"


def test_real_decision_global_suppression():
    from datetime import UTC, datetime
    ap = _approver()
    record_consent(recipient_type="user", recipient_ref=f"user:{ap}", channel="*", purpose="*",
                   consent_state=WITHDRAWN, revoked_at=datetime.now(UTC))
    r = create_intent_for_event(_requested_event(ap))
    assert r.outcome == SUPPRESSED and r.decision_reason_code == "global_suppression"


# --- references preserved ----------------------------------------------------

def test_source_correlation_causation_references_preserved():
    ap = _approver()
    eid, corr, caus = str(uuid.uuid4()), "workflow_instance:42", str(uuid.uuid4())
    r = create_intent_for_event(_requested_event(ap, eid=eid, corr=corr, caus=caus),
                                decision_fn=_decider(ALLOWED, "channel_allowed"))
    rec = get_notification(notification_uid=r.notification_uid)
    assert rec.source_event_id == eid
    assert rec.notification_metadata["correlation_id"] == corr
    assert rec.notification_metadata["causation_id"] == caus
    assert rec.notification_metadata["mapping_id"] == "workflow.approval.requested.v1"
    assert rec.notification_metadata["references"]["approval_id"] == 7


# --- idempotency -------------------------------------------------------------

def test_dedupe_key_is_deterministic():
    m = intents.mappings()[REQUESTED]
    k1 = intent_dedupe_key(m, "evt-1", "user:5")
    k2 = intent_dedupe_key(m, "evt-1", "user:5")
    assert k1 == k2 and k1.startswith("f5.4:")


def test_duplicate_event_does_not_duplicate_intent():
    ap = _approver()
    ev = _requested_event(ap, eid=str(uuid.uuid4()))
    r1 = create_intent_for_event(ev, decision_fn=_decider(ALLOWED, "channel_allowed"))
    r2 = create_intent_for_event(ev, decision_fn=_decider(ALLOWED, "channel_allowed"))
    assert r1.outcome == CREATED and r2.outcome == ALREADY_EXISTS
    assert r1.notification_uid == r2.notification_uid


def test_idempotency_survives_new_db_session_via_dedupe_key():
    ap = _approver()
    ev = _requested_event(ap, eid=str(uuid.uuid4()))
    assert create_intent_for_event(ev, decision_fn=_decider(ALLOWED, "channel_allowed")).outcome == CREATED
    # A fresh engine connection (independent session) must still see the intent.
    from app.services.notifications import _notifications_table
    with engine.connect() as c:
        key = intent_dedupe_key(intents.mappings()[REQUESTED], ev.event_id, f"user:{ap}")
        n = c.execute(select(func.count()).select_from(_notifications_table())
                      .where(_notifications_table().c.dedupe_key == key)).scalar_one()
    assert n == 1
    r2 = create_intent_for_event(ev, decision_fn=_decider(ALLOWED, "channel_allowed"))
    assert r2.outcome == ALREADY_EXISTS


# --- outbox / transaction-boundary integration -------------------------------

def test_post_commit_dispatch_creates_intent_and_is_idempotent():
    ap = _approver()
    register_notification_consumers()
    eid = str(uuid.uuid4())
    with engine.begin() as conn:
        publish_event(conn, _requested_event(ap, eid=eid))
    key = intent_dedupe_key(intents.mappings()[REQUESTED], eid, f"user:{ap}")
    assert get_notification(dedupe_key=key) is None  # not created inside the txn / pre-dispatch
    _drain_outbox(eid)
    rec = get_notification(dedupe_key=key)
    assert rec is not None and rec.status == L_PENDING
    _drain_outbox(eid)  # re-dispatch: no duplicate
    from app.services.notifications import _notifications_table
    with engine.connect() as c:
        n = c.execute(select(func.count()).select_from(_notifications_table())
                      .where(_notifications_table().c.dedupe_key == key)).scalar_one()
    assert n == 1


def test_intent_not_created_for_rolled_back_event():
    ap = _approver()
    register_notification_consumers()
    eid = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            publish_event(conn, _requested_event(ap, eid=eid))
            raise RuntimeError("roll back the authoritative txn")
    except RuntimeError:
        pass
    dispatch_pending()
    key = intent_dedupe_key(intents.mappings()[REQUESTED], eid, f"user:{ap}")
    assert get_notification(dedupe_key=key) is None  # event never committed -> no intent


# --- no side effects (scope contract) ----------------------------------------

def test_intent_creation_causes_no_workflow_domain_audit_or_provider_mutation():
    from app.db import audit_events
    from app.services.workflow_automation import launch_workflow, workflow_detail
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F54 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F54 {s}", active=True).returning(people.c.id)).scalar_one()
    iid = launch_workflow("client_onboarding", actor_user_id=None, person_id=pid, household_id=hid, idempotency_key=f"f54-{s}")
    wf_before = (workflow_detail(iid)["workflow"]["status"], tuple(x["status"] for x in workflow_detail(iid)["steps"]))
    with engine.connect() as c:
        oc_before = c.execute(select(func.count()).select_from(outbox_events)).scalar_one()
        a_before = c.execute(select(func.count()).select_from(audit_events)).scalar_one()
    create_intent_for_event(_requested_event(_approver()), decision_fn=_decider(ALLOWED, "channel_allowed"))
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(outbox_events)).scalar_one() == oc_before  # no event emitted
        assert c.execute(select(func.count()).select_from(audit_events)).scalar_one() == a_before    # no audit/evidence
    wf_after = (workflow_detail(iid)["workflow"]["status"], tuple(x["status"] for x in workflow_detail(iid)["steps"]))
    assert wf_before == wf_after  # no workflow/domain mutation


def test_result_is_content_free_and_scope_contract():
    ap = _approver()
    r = create_intent_for_event(_requested_event(ap), decision_fn=_decider(ALLOWED, "channel_allowed"))
    assert isinstance(r, IntentResult)
    blob = str(r.to_dict())
    assert "title" not in blob and "body" not in blob  # no content in the result
    # the ledger row's title is a stable *template reference*, never rendered content
    rec = get_notification(notification_uid=r.notification_uid)
    assert rec.title == f"template:{REQUESTED}" and rec.body is None
    source = (REPO_ROOT / "app" / "services" / "notification_intents.py").read_text()
    for forbidden in ("deliver_result", ".deliver(", "dispatch_pending", "write_audit_event",
                      "record_evidence", "APIRouter", "record.read_all", "schedule_retry"):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "NOTIFICATION_INTENTS.md").is_file()
