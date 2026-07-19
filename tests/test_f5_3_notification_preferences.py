"""F5.3 / Epic 5 — Notification preferences, consent & suppression decision tests (ADR-017).

Decision-layer only: structured deterministic decisions + reason codes, normative
precedence, preference-vs-consent separation, disabled channels via the F5.2 provider
boundary, no provider invocation, no ledger/workflow/domain mutation, no audit/evidence,
and reference-only content-free results.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.db import engine, households, people
from app.services.notification_preferences import (
    ALLOWED,
    DECISIONS,
    DISABLED,
    GRANTED,
    NOT_APPLICABLE,
    OPTED_IN,
    OPTED_OUT,
    REASON_CHANNEL_ALLOWED,
    REASON_CONSENT_EXPIRED,
    REASON_CONSENT_MISSING,
    REASON_GLOBAL_SUPPRESSION,
    REASON_PROVIDER_CHANNEL_DISABLED,
    REASON_RECIPIENT_OPTED_OUT,
    SUPPRESSED,
    WITHDRAWN,
    DeliveryDecision,
    evaluate_delivery,
    record_consent,
    record_preference,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "workflow.sla.escalated"


def _rr():
    return "user", f"user:{uuid.uuid4().hex[:10]}"


# --- decision structure / reason codes ---------------------------------------

def test_decision_structure_and_reason_codes():
    rt, rr = _rr()
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE)
    assert isinstance(d, DeliveryDecision)
    assert set(d.to_dict()) == {"decision", "channel", "recipient_ref", "purpose", "reason_code", "reason", "source_ref", "effective_ref"}
    assert d.decision in DECISIONS
    assert DECISIONS == {ALLOWED, SUPPRESSED, DISABLED, NOT_APPLICABLE}


# --- channel state (F5.2 boundary) -------------------------------------------

def test_in_app_default_allowed():
    rt, rr = _rr()
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE)
    assert d.decision == ALLOWED and d.reason_code == REASON_CHANNEL_ALLOWED


def test_email_sms_push_disabled_via_provider_boundary():
    rt, rr = _rr()
    for ch in ("email", "sms", "push"):
        d = evaluate_delivery(rt, rr, ch, PURPOSE)
        assert d.decision == DISABLED and d.reason_code == REASON_PROVIDER_CHANNEL_DISABLED


def test_positive_preference_does_not_enable_disabled_provider():
    rt, rr = _rr()
    record_preference(recipient_type=rt, recipient_ref=rr, channel="email", purpose=PURPOSE, preference_state=OPTED_IN)
    d = evaluate_delivery(rt, rr, "email", PURPOSE)
    assert d.decision == DISABLED  # rule 5: preference never enables a disabled provider


def test_unknown_channel_not_applicable():
    rt, rr = _rr()
    d = evaluate_delivery(rt, rr, "telepathy", PURPOSE)
    assert d.decision == NOT_APPLICABLE


# --- opt-out / global suppression --------------------------------------------

def test_recipient_opt_out_suppresses():
    rt, rr = _rr()
    record_preference(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, preference_state=OPTED_OUT)
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE)
    assert d.decision == SUPPRESSED and d.reason_code == REASON_RECIPIENT_OPTED_OUT and d.source_ref


def test_global_suppression_overrides_positive_preference():
    rt, rr = _rr()
    record_preference(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, preference_state=OPTED_IN)
    record_consent(recipient_type=rt, recipient_ref=rr, channel="*", purpose="*", consent_state=WITHDRAWN,
                   revoked_at=datetime.now(UTC), authority_ref="compliance:do-not-contact")
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE)
    assert d.decision == SUPPRESSED and d.reason_code == REASON_GLOBAL_SUPPRESSION


# --- consent (on an enabled channel via consent_required override) -----------

def test_consent_missing_suppresses():
    rt, rr = _rr()
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.decision == SUPPRESSED and d.reason_code == REASON_CONSENT_MISSING


def test_absence_of_preference_is_not_consent():
    rt, rr = _rr()
    # opting in is a preference, not consent: consent still required and missing -> suppressed
    record_preference(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, preference_state=OPTED_IN)
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.decision == SUPPRESSED and d.reason_code == REASON_CONSENT_MISSING


def test_valid_consent_allows():
    rt, rr = _rr()
    record_consent(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, consent_state=GRANTED,
                   effective_at=datetime.now(UTC) - timedelta(days=1))
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.decision == ALLOWED and d.reason_code == REASON_CHANNEL_ALLOWED


def test_expired_consent_suppresses():
    rt, rr = _rr()
    record_consent(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, consent_state=GRANTED,
                   effective_at=datetime.now(UTC) - timedelta(days=2), expires_at=datetime.now(UTC) - timedelta(days=1))
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.decision == SUPPRESSED and d.reason_code == REASON_CONSENT_EXPIRED


def test_withdrawn_consent_suppresses():
    rt, rr = _rr()
    record_consent(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, consent_state=WITHDRAWN,
                   revoked_at=datetime.now(UTC))
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.decision == SUPPRESSED and d.reason_code == REASON_CONSENT_MISSING


def test_future_effective_consent_not_yet_effective():
    rt, rr = _rr()
    record_consent(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, consent_state=GRANTED,
                   effective_at=datetime.now(UTC) + timedelta(days=1))
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.decision == SUPPRESSED and d.reason_code == REASON_CONSENT_MISSING


# --- precedence ordering -----------------------------------------------------

def test_precedence_disabled_beats_everything():
    rt, rr = _rr()
    record_consent(recipient_type=rt, recipient_ref=rr, channel="*", purpose="*", consent_state=WITHDRAWN, revoked_at=datetime.now(UTC))
    record_preference(recipient_type=rt, recipient_ref=rr, channel="email", purpose=PURPOSE, preference_state=OPTED_OUT)
    # even with global suppression + opt-out, a disabled provider is reported as disabled (rule 2)
    assert evaluate_delivery(rt, rr, "email", PURPOSE).decision == DISABLED


def test_precedence_global_beats_optout_and_consent():
    rt, rr = _rr()
    record_consent(recipient_type=rt, recipient_ref=rr, channel="*", purpose="*", consent_state=WITHDRAWN, revoked_at=datetime.now(UTC))
    record_preference(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, preference_state=OPTED_OUT)
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE, consent_required={"in_app"})
    assert d.reason_code == REASON_GLOBAL_SUPPRESSION  # global suppression checked before opt-out/consent


# --- uniqueness / current-state ----------------------------------------------

def test_preference_scope_is_current_state_upsert():
    rt, rr = _rr()
    id1 = record_preference(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, preference_state=OPTED_IN)
    id2 = record_preference(recipient_type=rt, recipient_ref=rr, channel="in_app", purpose=PURPOSE, preference_state=OPTED_OUT)
    assert id1 == id2  # same scope -> current-state update, not a duplicate row
    assert evaluate_delivery(rt, rr, "in_app", PURPOSE).reason_code == REASON_RECIPIENT_OPTED_OUT


# --- no side effects (decision layer only) -----------------------------------

def test_decision_causes_no_ledger_or_audit_or_workflow_mutation():
    from app.db import audit_events
    from app.services.notifications import _notifications_table
    from app.services.workflow_automation import launch_workflow, workflow_detail
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F53 {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F53 {s}", active=True).returning(people.c.id)).scalar_one()
    iid = launch_workflow("client_onboarding", actor_user_id=None, person_id=pid, household_id=hid, idempotency_key=f"f53-{s}")
    wf_before = (workflow_detail(iid)["workflow"]["status"], tuple(x["status"] for x in workflow_detail(iid)["steps"]))
    with engine.connect() as c:
        n_before = c.execute(select(func.count()).select_from(_notifications_table())).scalar_one()
        a_before = c.execute(select(func.count()).select_from(audit_events)).scalar_one()
    evaluate_delivery("workflow_instance", f"workflow_instance:{iid}", "in_app", PURPOSE)  # a decision
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(_notifications_table())).scalar_one() == n_before  # no ledger write
        assert c.execute(select(func.count()).select_from(audit_events)).scalar_one() == a_before          # no audit
    wf_after = (workflow_detail(iid)["workflow"]["status"], tuple(x["status"] for x in workflow_detail(iid)["steps"]))
    assert wf_before == wf_after  # no workflow/domain mutation


def test_no_content_and_scope_contract():
    rt, rr = _rr()
    d = evaluate_delivery(rt, rr, "in_app", PURPOSE)
    blob = str(d.to_dict())
    assert "title" not in blob and PURPOSE in blob  # purpose is a reference; no title/body content
    source = (REPO_ROOT / "app" / "services" / "notification_preferences.py").read_text()
    for forbidden in ("deliver_result", "dispatch_pending", "subscribe(", "APIRouter",
                      "write_audit_event", "record_evidence", "record_notification("):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "NOTIFICATION_PREFERENCES.md").is_file()
