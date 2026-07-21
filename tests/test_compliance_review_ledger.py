"""Compliance review + decision-evidence ledger tests (Phase D.7).

Covers eligibility, idempotent review creation, snapshots, the explicit status
lifecycle, reviewer assignment + the authorized-reviewer block, the append-only
decision ledger (approve blocked, returned/declined recorded, supersession), stale
protection, Rule-Catalog version validation, authorization, scope isolation, queue
search/sort/filter/pagination, rendering, and the one-way dependency direction.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, insert, select, text
from starlette.requests import Request

from app.db import (
    accounts,
    compliance_decisions,
    compliance_reviews,
    engine,
    households,
    people,
    record_assignments,
    reviewer_authorities,
    users,
)
from app.security.models import Principal
from app.services.advisor_intelligence import get_client_signals
from app.services.advisor_workspace import FIRM_TZ
from app.services.compliance import reviews as svc

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=FIRM_TZ)
TODAY = NOW.date()
READ = "compliance.review.read"
SUBMIT = "compliance.review.submit"
ASSIGN = "compliance.review.assign"
DECIDE = "compliance.review.decide"
CAPS = frozenset({"client.read", "insurance.read", READ, SUBMIT, ASSIGN, DECIDE})


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup(*, assigned=True):
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"cr-{tag}@e.test", normalized_email=f"cr-{tag}@e.test",
            display_name="CR", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", household_id=hh, active=True).returning(people.c.id)).scalar_one()
        # An IRA account with no active beneficiary -> beneficiary_review_recommendation (compliance_required gate).
        c.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"IRA-{tag}",
            account_name=f"IRA {tag}", status="open", registration_type="Traditional IRA",
            last_review_date=TODAY))
        if assigned:
            c.execute(insert(record_assignments).values(
                user_id=uid, entity_type="person", entity_id=pid,
                assignment_type="owner", effective_date=TODAY))
    return {"uid": uid, "pid": pid, "hh": hh,
            "principal": Principal(uid, "a@e.com", "CR", CAPS)}


def _teardown(ids):
    # compliance_decisions is append-only (a trigger blocks DELETE) and reviews are
    # FK-RESTRICT referenced by their decisions, so a review that has a decision is
    # intentionally left as a leftover (its person_id/household_id SET NULL when the
    # person/household are deleted) — the shared, un-isolated test DB tolerates this
    # (same convention as workflow ledgers). Reviews with no decision are deletable.
    with engine.begin() as c:
        for rid in list(c.scalars(select(compliance_reviews.c.id).where(
                compliance_reviews.c.person_id == ids["pid"]))):
            has_dec = c.scalar(select(text("count(*)")).select_from(compliance_decisions).where(
                compliance_decisions.c.compliance_review_id == rid))
            if not has_dec:
                c.execute(delete(compliance_reviews).where(compliance_reviews.c.id == rid))
        c.execute(delete(reviewer_authorities).where(reviewer_authorities.c.principal_id == ids["uid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(accounts).where(accounts.c.person_id == ids["pid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))  # SET NULL on leftover reviews
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _rec_id(ids):
    """The beneficiary_review_recommendation Signal id for the seeded client."""
    sigs = get_client_signals(ids["principal"], ids["pid"], now=NOW)
    rec = next(s for s in sigs
               if s.category == "recommendation"
               and s.recommendation.recommendation_type == "beneficiary_review")
    return rec.id


def _grant_authority(ids, *, rule="RULE-BENEFICIARY-DESIGNATION-PRESENT"):
    with engine.begin() as c:
        c.execute(insert(reviewer_authorities).values(
            principal_id=ids["uid"], reviewer_role="chief_compliance_officer",
            reviewer_name="Recorded Reviewer", authority_scope=[rule], status="active",
            source_reference="test-authority"))


# --- eligibility + creation --------------------------------------------------

def test_only_governed_recommendations_are_eligible():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        assert svc.eligible_recommendation(ids["principal"], ids["pid"], rid) is not None
        # An operational signal id is not eligible.
        sigs = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        op = next((s for s in sigs if s.category != "recommendation"), None)
        if op:
            assert svc.eligible_recommendation(ids["principal"], ids["pid"], op.id) is None
    finally:
        _teardown(ids)


def test_submit_snapshots_rule_version_and_evidence():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"],
                                   recommendation_id=rid, actor_user_id=ids["uid"])
        assert review["status"] == "pending_assignment"
        assert review["governing_rule"] == "RULE-BENEFICIARY-DESIGNATION-PRESENT"
        assert review["rule_version"] == "1.0.0"
        assert review["policy_gate"] == "compliance_required"
        assert review["recommendation_snapshot"]["id"] == rid
        assert review["evidence_snapshot"]  # snapshotted, not a live reference
        assert review["recommendation_id"] == rid
    finally:
        _teardown(ids)


def test_submit_is_idempotent_no_duplicate_open_review():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        a = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        b = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        assert a["id"] == b["id"]
        with engine.connect() as c:
            n = c.scalar(select(text("count(*)")).select_from(compliance_reviews).where(
                compliance_reviews.c.recommendation_id == rid))
        assert n == 1
    finally:
        _teardown(ids)


# --- lifecycle + assignment --------------------------------------------------

def test_assignment_without_authority_blocks():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        res = svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                                  reviewer_principal_id=ids["uid"], reviewer_role="compliance_reviewer",
                                  reviewer_name=None, actor_user_id=ids["uid"])
        assert res["authorized"] is False
        assert res["status"] == "blocked_pending_authorized_reviewer"
    finally:
        _teardown(ids)


def test_assignment_with_recorded_authority_becomes_pending_review():
    ids = _setup()
    try:
        _grant_authority(ids)
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        res = svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                                  reviewer_principal_id=ids["uid"], reviewer_role="chief_compliance_officer",
                                  reviewer_name="Recorded Reviewer", actor_user_id=ids["uid"])
        assert res["authorized"] is True
        assert res["status"] == "pending_review"
    finally:
        _teardown(ids)


# --- approval blocking (no authorized reviewer) ------------------------------

def test_final_approval_blocked_without_authority():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="compliance_reviewer",
                            reviewer_name=None, actor_user_id=ids["uid"])
        with pytest.raises(svc.ApprovalBlockedError):
            svc.record_decision(ids["principal"], review["id"], decision="approved",
                                expected_status="blocked_pending_authorized_reviewer",
                                actor_user_id=ids["uid"])
        got = svc.get_review(ids["principal"], review["id"])
        assert got["status"] == "blocked_pending_authorized_reviewer"
        assert got["decisions"] == []  # no approval decision recorded
    finally:
        _teardown(ids)


def test_authorized_approval_records_decision():
    ids = _setup()
    try:
        _grant_authority(ids)
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="chief_compliance_officer",
                            reviewer_name="Recorded Reviewer", actor_user_id=ids["uid"])
        out = svc.record_decision(ids["principal"], review["id"], decision="approved",
                                  expected_status="pending_review", actor_user_id=ids["uid"],
                                  scope_reviewed="beneficiary designation")
        assert out["status"] == "approved"
        got = svc.get_review(ids["principal"], review["id"])
        assert got["status"] == "approved"
        assert len(got["decisions"]) == 1
        assert got["decisions"][0]["reviewer_name"] == "Recorded Reviewer"
    finally:
        _teardown(ids)


# --- decision validation -----------------------------------------------------

def test_decision_required_comments():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="compliance_reviewer",
                            reviewer_name=None, actor_user_id=ids["uid"])
        with pytest.raises(svc.DecisionValidationError):
            svc.record_decision(ids["principal"], review["id"], decision="returned",
                                expected_status="blocked_pending_authorized_reviewer", actor_user_id=ids["uid"])
        with pytest.raises(svc.DecisionValidationError):
            svc.record_decision(ids["principal"], review["id"], decision="declined",
                                expected_status="blocked_pending_authorized_reviewer", actor_user_id=ids["uid"],
                                comments="")
    finally:
        _teardown(ids)


def test_returned_and_declined_recorded_by_operational_reviewer():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="operations_manager",
                            reviewer_name=None, actor_user_id=ids["uid"])
        out = svc.record_decision(ids["principal"], review["id"], decision="returned",
                                  expected_status="blocked_pending_authorized_reviewer",
                                  actor_user_id=ids["uid"], comments="needs more info")
        assert out["status"] == "returned"
    finally:
        _teardown(ids)


# --- append-only + supersession + stale --------------------------------------

def test_decision_ledger_is_append_only():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="compliance_reviewer",
                            reviewer_name=None, actor_user_id=ids["uid"])
        out = svc.record_decision(ids["principal"], review["id"], decision="declined",
                                  expected_status="blocked_pending_authorized_reviewer",
                                  actor_user_id=ids["uid"], comments="declined")
        did = out["decision_id"]
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(compliance_decisions.update().where(
                    compliance_decisions.c.id == did).values(comments="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(compliance_decisions).where(compliance_decisions.c.id == did))
    finally:
        _teardown(ids)


def test_stale_decision_is_rejected():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        # Submit a decision against an outdated expected status.
        with pytest.raises(svc.StaleReviewError):
            svc.record_decision(ids["principal"], review["id"], decision="declined",
                                expected_status="pending_review", actor_user_id=ids["uid"],
                                comments="x")
    finally:
        _teardown(ids)


def test_superseding_decision_references_prior():
    ids = _setup()
    try:
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="compliance_reviewer",
                            reviewer_name=None, actor_user_id=ids["uid"])
        first = svc.record_decision(ids["principal"], review["id"], decision="returned",
                                    expected_status="blocked_pending_authorized_reviewer",
                                    actor_user_id=ids["uid"], comments="first")
        # A reconsideration references the prior decision (status is now 'returned').
        second = svc.record_decision(ids["principal"], review["id"], decision="declined",
                                     expected_status="returned", actor_user_id=ids["uid"],
                                     comments="reconsidered", supersedes_decision_id=first["decision_id"])
        got = svc.get_review(ids["principal"], review["id"])
        assert len(got["decisions"]) == 2
        assert got["decisions"][1]["supersedes_decision_id"] == first["decision_id"]
        assert second["status"] == "declined"
    finally:
        _teardown(ids)


# --- catalog version validation ----------------------------------------------

def test_version_mismatch_blocks_approval():
    ids = _setup()
    try:
        _grant_authority(ids)
        rid = _rec_id(ids)
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
        # Tamper the snapshotted rule_version so it no longer matches the catalog.
        with engine.begin() as c:
            c.execute(compliance_reviews.update().where(
                compliance_reviews.c.id == review["id"]).values(rule_version="9.9.9"))
        svc.assign_reviewer(ids["principal"], review["id"], expected_status="pending_assignment",
                            reviewer_principal_id=ids["uid"], reviewer_role="chief_compliance_officer",
                            reviewer_name="Recorded Reviewer", actor_user_id=ids["uid"])
        with pytest.raises(svc.ApprovalBlockedError):
            svc.record_decision(ids["principal"], review["id"], decision="approved",
                                expected_status="pending_review", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- authorization + scope ---------------------------------------------------

def test_submit_denies_inaccessible_person():
    ids = _setup(assigned=False)  # principal not assigned to the person
    try:
        # Fetch the rec id via a read_all principal, then attempt submit as the scoped one.
        admin = Principal(ids["uid"], "a@e.com", "CR", CAPS | {"record.read_all"})
        rid = next(s.id for s in get_client_signals(admin, ids["pid"], now=NOW)
                   if s.category == "recommendation")
        with pytest.raises(svc.IneligibleRecommendationError):
            svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=rid, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_queue_is_scope_isolated():
    a = _setup()
    b = _setup()
    try:
        ra = svc.submit_review(a["principal"], person_id=a["pid"], recommendation_id=_rec_id(a), actor_user_id=a["uid"])
        svc.submit_review(b["principal"], person_id=b["pid"], recommendation_id=_rec_id(b), actor_user_id=b["uid"])
        # a's principal sees only a's review (book-scoped).
        rows = svc.list_reviews(a["principal"])["rows"]
        ids_seen = {r["id"] for r in rows}
        assert ra["id"] in ids_seen
        assert all(r["person_id"] == a["pid"] for r in rows)
    finally:
        _teardown(a)
        _teardown(b)


def test_queue_search_filter_sort_pagination():
    ids = _setup()
    try:
        svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=_rec_id(ids), actor_user_id=ids["uid"])
        res = svc.list_reviews(ids["principal"], status="pending_assignment")
        assert res["rows"] and all(r["status"] == "pending_assignment" for r in res["rows"])
        assert svc.list_reviews(ids["principal"], search="RULE-BENEFICIARY")["total"] >= 1
        assert svc.list_reviews(ids["principal"], policy_gate="compliance_required")["total"] >= 1
        paged = svc.list_reviews(ids["principal"], page=1, page_size=1)
        assert paged["page_size"] == 1 and paged["page"] == 1
    finally:
        _teardown(ids)


# --- route authorization + rendering + no bulk controls ----------------------

def _req(path="/compliance/reviews"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def test_routes_require_distinct_capabilities():
    # The four endpoints declare four distinct capabilities (read/submit/assign/decide),
    # so viewing governance metadata is separated from making a decision.
    import inspect

    from app.routes import compliance as croute
    sources = inspect.getsource(croute)
    for cap in (READ, SUBMIT, ASSIGN, DECIDE):
        assert f'require_capability("{cap}")' in sources
    # A read-only principal can render the queue.
    resp = croute.review_queue(_req(), principal=Principal(1, "a@e.com", "X", frozenset({READ})))
    assert resp.status_code == 200


def test_queue_renders_and_detail_shows_history_no_bulk_controls():
    from app.routes.compliance import review_detail, review_queue
    ids = _setup()
    try:
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=_rec_id(ids), actor_user_id=ids["uid"])
        qbody = review_queue(_req(), principal=ids["principal"]).body.decode()
        assert "Compliance Reviews" in qbody
        assert "RULE-BENEFICIARY-DESIGNATION-PRESENT" in qbody
        # No bulk-approval / inline-decision controls in the queue.
        for control in ("Approve all", "Bulk approve", "Record decision", "/decision"):
            assert control not in qbody
        dbody = review_detail(_req(f"/compliance/reviews/{review['id']}"), review["id"], principal=ids["principal"]).body.decode()
        assert "Decision history" in dbody
        assert "not an electronic signature" in dbody
    finally:
        _teardown(ids)


def test_decision_form_hidden_without_decide_capability():
    from app.routes.compliance import review_detail
    ids = _setup()
    try:
        review = svc.submit_review(ids["principal"], person_id=ids["pid"], recommendation_id=_rec_id(ids), actor_user_id=ids["uid"])
        reader = Principal(ids["uid"], "a@e.com", "R", frozenset({READ, "client.read", "record.read_all"}))
        body = review_detail(_req(f"/compliance/reviews/{review['id']}"), review["id"], principal=reader).body.decode()
        assert "Record decision" not in body  # decision form gated by compliance.review.decide
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_advisor_intelligence_does_not_import_compliance():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app" / "services" / "advisor_intelligence.py").read_text()
    assert "services.compliance" not in src
    assert "import compliance" not in src


def test_reviewer_authorities_seeded_empty():
    # No fabricated authority is seeded by migrations. Any row present is test-created
    # and carries a recorder (recorded_by); a system-seeded row would have none.
    with engine.connect() as c:
        assert c.scalar(select(text("count(*)")).select_from(reviewer_authorities).where(
            reviewer_authorities.c.recorded_by.is_(None))) == 0
