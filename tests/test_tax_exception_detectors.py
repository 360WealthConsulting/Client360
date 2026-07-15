"""Release 0.9.10 / Sprint 5.5 — Tax detectors & lifecycle hooks (Phase 3) tests.

Assertions target a specific fresh return via its stable dedupe keys, so results are
unaffected by other data in the shared test database.
"""
from datetime import date, datetime, timedelta, timezone

import uuid

import pytest
from sqlalchemy import select

from app.db import (documents, engine, exceptions, people, record_assignments,
    tax_engagement_returns, tax_engagement_letters, tax_engagements, tax_filing_events,
    tax_missing_items, tax_organizers, tax_return_reviews)
from app.security.models import Principal
from app.services import exception_engine as ee
from app.services import tax_exception_detectors as det
from app.services import tax_return_lifecycle as lc


def _now():
    return datetime.now(timezone.utc)


def _case():
    from tests.test_tax_intake import _case as intake_case
    user_id, person_id, household_id, portal, result = intake_case()
    return user_id, person_id, household_id, result["return_id"]


def _admin(u):
    return Principal(u, "a@e.com", "A", frozenset({"exception.read", "record.read_all"}))


def _by_dedupe(key):
    with engine.connect() as c:
        return c.execute(select(exceptions).where(exceptions.c.dedupe_key == key)).mappings().first()


def _count_dedupe(key):
    with engine.connect() as c:
        return len(c.execute(select(exceptions.c.id).where(exceptions.c.dedupe_key == key)).all())


def _set_status(rid, status, days_ago=0):
    with engine.begin() as c:
        c.execute(tax_engagement_returns.update().where(tax_engagement_returns.c.id == rid)
                  .values(status=status, status_entered_at=_now() - timedelta(days=days_ago)))


# --- each detector type -------------------------------------------------------

def test_missing_organizer_and_questionnaire_and_signatures():
    u, p, h, r = _case()  # create_engagement launches intake (organizer/questionnaire not completed, letter pending)
    det.detect_missing_organizer(actor_user_id=u)
    det.detect_missing_questionnaire(actor_user_id=u)
    det.detect_missing_signatures(actor_user_id=u)
    assert _by_dedupe(f"tax:organizer:{r}")["category"] == "client"
    assert _by_dedupe(f"tax:questionnaire:{r}") is not None
    assert _by_dedupe(f"tax:signatures:{r}")["category"] == "client"


def test_missing_documents_overdue():
    u, p, h, r = _case()
    with engine.begin() as c:
        item_id = c.execute(select(tax_missing_items.c.id)
                            .where(tax_missing_items.c.tax_engagement_return_id == r)).scalars().first()
        c.execute(tax_missing_items.update().where(tax_missing_items.c.id == item_id)
                  .values(status="open", due_date=date.today() - timedelta(days=3)))
    det.detect_missing_documents(actor_user_id=u)
    ex = _by_dedupe(f"tax:doc_missing:{item_id}")
    assert ex and ex["category"] == "document" and ex["severity"] == "medium"


def test_client_non_response_and_overdue_work():
    u, p, h, r = _case()
    _set_status(r, "awaiting_information", days_ago=6)
    det.detect_client_non_response(actor_user_id=u)
    assert _by_dedupe(f"tax:nonresponse:{r}") is not None
    _set_status(r, "in_preparation", days_ago=9)
    det.detect_overdue_work(actor_user_id=u)
    assert _by_dedupe(f"tax:overdue:{r}")["category"] == "filing"


def test_missing_preparer_and_reviewer():
    u, p, h, r = _case()
    with engine.begin() as c:
        c.execute(record_assignments.update().where(record_assignments.c.entity_type == "tax_return",
                  record_assignments.c.entity_id == r).values(inactive_date=date.today() - timedelta(days=1)))
    _set_status(r, "in_preparation")
    det.detect_missing_preparer(actor_user_id=u)
    assert _by_dedupe(f"tax:no_preparer:{r}") is not None
    with engine.begin() as c:
        review_id = c.execute(tax_return_reviews.insert().values(
            tax_engagement_return_id=r, review_type="manager", status="pending").returning(tax_return_reviews.c.id)).scalar_one()
    det.detect_missing_reviewer(actor_user_id=u)
    assert _by_dedupe(f"tax:no_reviewer:{review_id}") is not None


def test_filing_rejection_hook_and_transmission_and_acceptance_pending():
    u, p, h, r = _case()
    # hook: entering rejected raises FILING_REJECTED (blocker)
    lc.transition_return(r, "rejected", actor_user_id=u, force=True)
    rej = _by_dedupe(f"tax:filing_rejected:{r}")
    assert rej and rej["severity"] == "blocker"
    # transmission failure from a filing event with a reason_code
    with engine.begin() as c:
        fe = c.execute(tax_filing_events.insert().values(
            tax_engagement_return_id=r, filing_status="rejected", reason_code="TRANSMIT_ERR",
            idempotency_key=f"fe-{uuid.uuid4().hex[:10]}").returning(tax_filing_events.c.id)).scalar_one()
    det.detect_filing_transmission_failure(actor_user_id=u)
    assert _by_dedupe(f"tax:transmission:{fe}")["severity"] == "blocker"
    # acceptance pending
    _set_status(r, "filed", days_ago=4)
    det.detect_acceptance_pending(actor_user_id=u)
    assert _by_dedupe(f"tax:acceptance_pending:{r}") is not None


def test_compliance_docs_missing_and_review_skipped():
    u, p, h, r = _case()
    _set_status(r, "delivered")  # delivered but intake missing items still open
    det.detect_compliance_docs_missing(actor_user_id=u)
    assert _by_dedupe(f"tax:retention:{r}")["category"] == "compliance"
    _set_status(r, "ready_to_file")  # post-review state, no approved manager review
    det.detect_required_review_skipped(actor_user_id=u)
    signoff = _by_dedupe(f"tax:signoff:{r}")
    assert signoff and signoff["severity"] == "blocker"


def test_document_ambiguity():
    u, p, h, r = _case()
    with engine.begin() as c:
        doc = c.execute(documents.insert().values(
            person_id=p, original_name="ambiguous.pdf", stored_name=f"amb-{uuid.uuid4().hex}",
            storage_path="x", size_bytes=1, sha256=uuid.uuid4().hex, review_status="ready_for_review",
        ).returning(documents.c.id)).scalar_one()
    det.detect_document_ambiguity(actor_user_id=u)
    assert _by_dedupe(f"tax:docreview:{doc}")["category"] == "document"


def test_blocked_workflow_step():
    from app.db import workflow_escalations, tax_workflow_links
    u, p, h, r = _case()
    with engine.connect() as c:
        wf = c.scalar(select(tax_workflow_links.c.workflow_instance_id)
                      .where(tax_workflow_links.c.tax_engagement_return_id == r))
    if wf is None:
        pytest.skip("no workflow linked to this return")
    from app.db import workflow_steps
    with engine.begin() as c:
        step = c.scalar(select(workflow_steps.c.id).where(workflow_steps.c.workflow_instance_id == wf))
        esc = c.execute(workflow_escalations.insert().values(
            workflow_instance_id=wf, workflow_step_id=step, escalation_type="sla_breach",
            status="open", due_at=_now()).returning(workflow_escalations.c.id)).scalar_one()
    det.detect_blocked_workflow_step(actor_user_id=u)
    assert _by_dedupe(f"tax:wf_blocked:{esc}")["category"] == "workflow"


# --- stable dedupe + idempotency ---------------------------------------------

def test_stable_dedupe_and_repeated_scans_no_duplicates():
    u, p, h, r = _case()
    det.detect_missing_organizer(actor_user_id=u)
    first = _by_dedupe(f"tax:organizer:{r}")
    det.detect_missing_organizer(actor_user_id=u)
    det.detect_missing_organizer(actor_user_id=u)
    assert _count_dedupe(f"tax:organizer:{r}") == 1  # never duplicated
    again = _by_dedupe(f"tax:organizer:{r}")
    assert first["id"] == again["id"]


# --- source clearing / reopening ---------------------------------------------

def test_source_condition_clears_and_reopens():
    u, p, h, r = _case()
    det.detect_missing_organizer(actor_user_id=u)
    ex = _by_dedupe(f"tax:organizer:{r}")
    assert ex["status"] == "open"
    # clear the source: organizer completed
    with engine.begin() as c:
        c.execute(tax_organizers.update().where(tax_organizers.c.tax_engagement_return_id == r).values(status="completed"))
    det.detect_missing_organizer(actor_user_id=u)  # reconcile → auto-close
    assert _by_dedupe(f"tax:organizer:{r}")["status"] in ("resolved", "cancelled")
    # source recurs
    with engine.begin() as c:
        c.execute(tax_organizers.update().where(tax_organizers.c.tax_engagement_return_id == r).values(status="in_progress"))
    det.detect_missing_organizer(actor_user_id=u)
    reopened = _by_dedupe(f"tax:organizer:{r}")
    assert reopened["status"] in ("open", "reopened")


# --- lifecycle gates ---------------------------------------------------------

def _to_delivered(r, u):
    for s in ("ready_to_prepare", "in_preparation", "manager_review", "partner_review",
              "client_review", "awaiting_efile_authorization", "ready_to_file", "filed", "accepted", "delivered"):
        lc.transition_return(r, s, actor_user_id=u, force=True)


def test_blocker_gate_blocks_and_force_overrides_with_audit():
    from app.db import audit_events
    u, p, h, r = _case()
    _to_delivered(r, u)
    ee.raise_exception(code="COMPLIANCE_SIGNOFF_MISSING", actor_user_id=u,
                       tax_engagement_return_id=r, person_id=p, household_id=h, dedupe_key=f"gate:{r}")
    with pytest.raises(ValueError) as exc:
        lc.transition_return(r, "completed", actor_user_id=u)  # not force
    assert "blocker" in str(exc.value).lower()
    assert lc.transition_return(r, "completed", actor_user_id=u, force=True) == "completed"
    with engine.connect() as c:
        overridden = c.scalar(select(audit_events.c.id).where(
            audit_events.c.action == "tax.exception.blocker_overridden",
            audit_events.c.entity_id == str(r)))
    assert overridden is not None


def test_non_blocker_exception_does_not_prevent_transition():
    u, p, h, r = _case()
    _to_delivered(r, u)
    ee.raise_exception(code="CLIENT_UNRESPONSIVE", actor_user_id=u,  # medium, non-blocker
                       tax_engagement_return_id=r, person_id=p, household_id=h, dedupe_key=f"nb:{r}")
    assert lc.transition_return(r, "completed", actor_user_id=u) == "completed"


def test_filing_blocker_closed_on_acceptance():
    u, p, h, r = _case()
    lc.transition_return(r, "rejected", actor_user_id=u, force=True)  # opens FILING_REJECTED
    assert _by_dedupe(f"tax:filing_rejected:{r}")["status"] == "open"
    lc.transition_return(r, "ready_to_file", actor_user_id=u, force=True)
    lc.transition_return(r, "filed", actor_user_id=u, force=True)
    lc.transition_return(r, "accepted", actor_user_id=u, force=True)  # hook closes filing blockers
    assert _by_dedupe(f"tax:filing_rejected:{r}")["status"] in ("resolved", "cancelled")


# --- record scope ------------------------------------------------------------

def test_detector_exceptions_are_record_scoped():
    u, p, h, r = _case()
    det.detect_missing_organizer(actor_user_id=u)
    owner = Principal(u, "o@e.com", "O", frozenset({"exception.read"}))          # assigned to the return
    outsider = Principal(9_500_001, "x@e.com", "X", frozenset({"exception.read"}))  # not assigned
    owner_ids = {e["dedupe_key"] for e in ee.list_exceptions(owner)}
    outsider_ids = {e["dedupe_key"] for e in ee.list_exceptions(outsider)}
    assert f"tax:organizer:{r}" in owner_ids
    assert f"tax:organizer:{r}" not in outsider_ids


# --- orchestrator idempotency ------------------------------------------------

def test_full_scan_is_idempotent():
    u, p, h, r = _case()
    det.scan_tax_exceptions(actor_user_id=u)
    with engine.connect() as c:
        before = c.scalar(select(exceptions.c.id).where(exceptions.c.dedupe_key == f"tax:organizer:{r}"))
        total_before = len(c.execute(select(exceptions.c.id)).all())
    det.scan_tax_exceptions(actor_user_id=u)
    with engine.connect() as c:
        after = c.scalar(select(exceptions.c.id).where(exceptions.c.dedupe_key == f"tax:organizer:{r}"))
        total_after = len(c.execute(select(exceptions.c.id)).all())
    assert before == after and total_before == total_after  # no new rows on re-scan
