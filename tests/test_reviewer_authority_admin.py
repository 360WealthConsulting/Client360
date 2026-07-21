"""Reviewer authority administration tests (Phase D.8).

Covers the authority model, draft creation + activation evidence requirements, scope
semantics (rule-id and policy-gate), the explicit lifecycle and its blocks, the
append-only event history, superseding versions, stale/conflict protection,
self-administration prohibition, inactive-principal handling, authorization + list
search/filter/sort/pagination, rendering, and D.7 integration (approve only with a
matching active in-scope authority; blocked otherwise). No authority is ever seeded.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select, text
from starlette.requests import Request

from app.db import (
    accounts,
    engine,
    households,
    people,
    record_assignments,
    reviewer_authorities,
    reviewer_authority_events,
    users,
)
from app.security.models import Principal
from app.services.advisor_intelligence import get_client_signals
from app.services.advisor_workspace import FIRM_TZ
from app.services.compliance import authority_admin as aa
from app.services.compliance import reviews as rsvc
from app.services.compliance.reviewer_authority import reviewer_authority

TODAY = date(2026, 7, 16)
NOW = datetime(2026, 7, 16, 9, 0, tzinfo=FIRM_TZ)
RULE = "RULE-BENEFICIARY-DESIGNATION-PRESENT"
READ = "compliance.authority.read"
MANAGE = "compliance.authority.manage"


def _sfx():
    return uuid.uuid4().hex[:8]


def _user(c, *, status="active"):
    t = _sfx()
    return c.execute(users.insert().values(
        email=f"u-{t}@e.test", normalized_email=f"u-{t}@e.test",
        display_name=f"U {t}", status=status).returning(users.c.id)).scalar_one()


def _setup():
    with engine.begin() as c:
        admin = _user(c)      # the administrator recording authority
        reviewer = _user(c)   # the subject principal
    return {"admin": admin, "reviewer": reviewer}


def _teardown(ids):
    with engine.begin() as c:
        auth_ids = list(c.scalars(select(reviewer_authorities.c.id).where(
            reviewer_authorities.c.principal_id.in_(list(ids.values())))))
        # reviewer_authority_events is append-only (cannot delete); authorities are
        # FK-RESTRICT referenced by events, so a record with events is a leftover.
        for aid in auth_ids:
            has_ev = c.scalar(select(text("count(*)")).select_from(reviewer_authority_events).where(
                reviewer_authority_events.c.reviewer_authority_id == aid))
            if not has_ev:
                # also drop any that supersede this? handled by leaving as leftover
                try:
                    c.execute(delete(reviewer_authorities).where(reviewer_authorities.c.id == aid))
                except Exception:
                    pass
        # users left as leftovers (shared-DB convention).


def _draft(ids, *, scope=(RULE,), effective=TODAY, expiration=None, complete=True):
    return aa.create_draft(
        ids["admin"], principal_id=ids["reviewer"], reviewer_role="chief_compliance_officer",
        reviewer_name="Recorded Reviewer", authority_scope=list(scope),
        effective_date=effective if complete else None, expiration_date=expiration,
        source_reference="LIC-123" if complete else None,
        evidence_description="state license on file" if complete else None)


# --- model + creation --------------------------------------------------------

def test_create_draft_records_facts_and_event():
    ids = _setup()
    try:
        row = _draft(ids)
        assert row["status"] == "draft"
        assert row["recorded_by"] == ids["admin"]
        assert row["principal_id"] == ids["reviewer"]
        assert row["authority_scope"] == [RULE]
        got = aa.get_authority(row["id"])
        assert got["events"][0]["event_type"] == "created"
    finally:
        _teardown(ids)


def test_self_administration_blocked_on_create():
    ids = _setup()
    try:
        with pytest.raises(aa.SelfAdministrationError):
            aa.create_draft(ids["admin"], principal_id=ids["admin"], reviewer_role="x",
                            authority_scope=[RULE], effective_date=TODAY,
                            source_reference="s", evidence_description="e")
    finally:
        _teardown(ids)


def test_unknown_principal_rejected():
    ids = _setup()
    try:
        with pytest.raises(aa.UnknownPrincipalError):
            aa.create_draft(ids["admin"], principal_id=99999999, reviewer_role="x",
                            authority_scope=[RULE], effective_date=TODAY,
                            source_reference="s", evidence_description="e")
    finally:
        _teardown(ids)


# --- activation requirements + lifecycle -------------------------------------

def test_activation_requires_complete_evidence():
    ids = _setup()
    try:
        row = _draft(ids, complete=False)
        with pytest.raises(aa.IncompleteEvidenceError):
            aa.activate(ids["admin"], row["id"], expected_status="draft")
    finally:
        _teardown(ids)


def test_activation_and_self_administration_block():
    ids = _setup()
    try:
        row = _draft(ids)
        # The subject cannot administer their own authority.
        with pytest.raises(aa.SelfAdministrationError):
            aa.activate(ids["reviewer"], row["id"], expected_status="draft")
        out = aa.activate(ids["admin"], row["id"], expected_status="draft")
        assert out["status"] == "active"
    finally:
        _teardown(ids)


def test_suspend_restore_revoke_require_reason_and_transitions():
    ids = _setup()
    try:
        row = _draft(ids)
        aa.activate(ids["admin"], row["id"], expected_status="draft")
        with pytest.raises(aa.AuthorityError):
            aa.suspend(ids["admin"], row["id"], reason="", expected_status="active")
        aa.suspend(ids["admin"], row["id"], reason="under review", expected_status="active")
        aa.restore(ids["admin"], row["id"], expected_status="suspended")
        aa.revoke(ids["admin"], row["id"], reason="no longer authorized", expected_status="active")
        got = aa.get_authority(row["id"])
        assert got["status"] == "revoked"
        assert got["revocation_reason"] == "no longer authorized"
        events = [e["event_type"] for e in got["events"]]
        assert events == ["created", "activate", "suspend", "restore", "revoke"]
    finally:
        _teardown(ids)


def test_invalid_transition_blocked():
    ids = _setup()
    try:
        row = _draft(ids)
        with pytest.raises(aa.InvalidTransitionError):
            aa.suspend(ids["admin"], row["id"], reason="x", expected_status="draft")  # draft can't suspend
    finally:
        _teardown(ids)


def test_stale_transition_rejected():
    ids = _setup()
    try:
        row = _draft(ids)
        with pytest.raises(aa.StaleAuthorityError):
            aa.activate(ids["admin"], row["id"], expected_status="active")  # wrong expected
    finally:
        _teardown(ids)


# --- conflict + supersede ----------------------------------------------------

def test_conflicting_active_scope_prevented():
    ids = _setup()
    try:
        a1 = _draft(ids, scope=(RULE,))
        aa.activate(ids["admin"], a1["id"], expected_status="draft")
        a2 = _draft(ids, scope=(RULE,))  # same principal + overlapping scope
        with pytest.raises(aa.ScopeConflictError):
            aa.activate(ids["admin"], a2["id"], expected_status="draft")
    finally:
        _teardown(ids)


def test_supersede_creates_new_version_and_marks_prior_superseded():
    ids = _setup()
    try:
        a1 = _draft(ids)
        aa.activate(ids["admin"], a1["id"], expected_status="draft")
        new = aa.supersede(ids["admin"], a1["id"], expected_status="active",
                           authority_scope=[RULE], reason="renewed license")
        assert new["status"] == "active"
        assert new["supersedes_authority_id"] == a1["id"]
        prior = aa.get_authority(a1["id"])
        assert prior["status"] == "superseded"
        assert prior["successor"] == new["id"]
        # No circular / double supersede.
        with pytest.raises(aa.InvalidTransitionError):
            aa.supersede(ids["admin"], a1["id"], expected_status="superseded")
    finally:
        _teardown(ids)


def test_authority_events_are_append_only():
    ids = _setup()
    try:
        row = _draft(ids)
        eid = aa.get_authority(row["id"])["events"][0]["id"]
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(reviewer_authority_events.update().where(
                    reviewer_authority_events.c.id == eid).values(reason="tamper"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(reviewer_authority_events).where(reviewer_authority_events.c.id == eid))
    finally:
        _teardown(ids)


# --- lookup / D.7 scope + date semantics -------------------------------------

def _active_authority(ids, *, scope=(RULE,), effective=TODAY - timedelta(days=1),
                      expiration=None):
    row = _draft(ids, scope=scope, effective=effective, expiration=expiration)
    aa.activate(ids["admin"], row["id"], expected_status="draft")
    return row


def test_lookup_matches_rule_and_gate_scope():
    ids = _setup()
    try:
        _active_authority(ids, scope=(RULE,))
        assert reviewer_authority(ids["reviewer"], rule_id=RULE, policy_gate="compliance_required", today=TODAY)
        # Out of scope.
        assert reviewer_authority(ids["reviewer"], rule_id="RULE-OTHER", policy_gate="license_required", today=TODAY) is None
    finally:
        _teardown(ids)

    ids = _setup()
    try:
        _active_authority(ids, scope=("COMPLIANCE_REQUIRED",))
        assert reviewer_authority(ids["reviewer"], rule_id="RULE-ANY", policy_gate="COMPLIANCE_REQUIRED", today=TODAY)
    finally:
        _teardown(ids)


def test_lookup_enforces_dates_and_status_and_active_user():
    ids = _setup()
    try:
        # Not yet effective.
        _active_authority(ids, effective=TODAY + timedelta(days=5))
        assert reviewer_authority(ids["reviewer"], rule_id=RULE, policy_gate="x", today=TODAY) is None
    finally:
        _teardown(ids)

    ids = _setup()
    try:
        _active_authority(ids, effective=TODAY - timedelta(days=10), expiration=TODAY - timedelta(days=1))
        assert reviewer_authority(ids["reviewer"], rule_id=RULE, policy_gate="x", today=TODAY) is None  # expired
    finally:
        _teardown(ids)

    ids = _setup()
    try:
        row = _active_authority(ids)
        aa.suspend(ids["admin"], row["id"], reason="x", expected_status="active")
        assert reviewer_authority(ids["reviewer"], rule_id=RULE, policy_gate="x", today=TODAY) is None  # suspended
    finally:
        _teardown(ids)

    ids = _setup()
    try:
        _active_authority(ids)
        with engine.begin() as c:  # make the principal inactive
            c.execute(users.update().where(users.c.id == ids["reviewer"]).values(status="inactive"))
        assert reviewer_authority(ids["reviewer"], rule_id=RULE, policy_gate="x", today=TODAY) is None
    finally:
        _teardown(ids)


def test_empty_scope_confers_nothing():
    ids = _setup()
    try:
        # An empty scope cannot be activated (evidence incomplete), and never matches.
        row = _draft(ids, scope=())
        with pytest.raises(aa.IncompleteEvidenceError):
            aa.activate(ids["admin"], row["id"], expected_status="draft")
    finally:
        _teardown(ids)


# --- list / search / filter / sort / pagination ------------------------------

def test_list_search_filter_sort_pagination():
    ids = _setup()
    try:
        _draft(ids)
        res = aa.list_authorities(status="draft")
        assert res["rows"] and all(r["status"] == "draft" for r in res["rows"])
        assert aa.list_authorities(search="chief_compliance")["total"] >= 1
        paged = aa.list_authorities(page=1, page_size=1)
        assert paged["page_size"] == 1
    finally:
        _teardown(ids)


# --- authorization + rendering -----------------------------------------------

def _req(path="/compliance/authorities"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def test_manage_is_distinct_from_read_and_decide():
    import inspect

    from app.routes import compliance as croute
    src = inspect.getsource(croute)
    assert 'require_capability("compliance.authority.read")' in src
    assert 'require_capability("compliance.authority.manage")' in src
    # audit.read / decide are not used to gate authority admin.
    assert 'require_capability("audit.read")' not in src


def test_list_and_detail_render_and_manage_forms_gated():
    from app.routes.compliance import authority_detail, authority_list
    ids = _setup()
    try:
        row = _draft(ids)
        reader = Principal(ids["admin"], "a@e.com", "R", frozenset({READ}))
        lbody = authority_list(_req(), principal=reader).body.decode()
        assert "Reviewer Authorities" in lbody
        assert "not regulatory certification" in lbody
        # No inline action forms / bulk / delete in the list (action URLs live on the detail page only).
        for control in ("/activate", "/suspend", "/revoke", "/supersede", "Administer", "Delete", "select-all"):
            assert control not in lbody
        dbody = authority_detail(_req(f"/compliance/authorities/{row['id']}"), row["id"], principal=reader).body.decode()
        assert "Authority history" in dbody
        assert "Administer" not in dbody  # manage forms hidden for read-only principal

        manager = Principal(ids["admin"], "a@e.com", "M", frozenset({READ, MANAGE}))
        mbody = authority_detail(_req(f"/compliance/authorities/{row['id']}"), row["id"], principal=manager).body.decode()
        assert "Administer" in mbody
        assert "Activate" in mbody
    finally:
        _teardown(ids)


# --- D.7 integration ---------------------------------------------------------

def _seed_review(ids):
    """Seed a governed recommendation, submit a review, assign the (authorized-or-not)
    reviewer, and return (review_id, reviewer_principal)."""
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {_sfx()}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(full_name=f"C {_sfx()}", active=True,
            primary_email=f"{_sfx()}@e.test", normalized_email=f"{_sfx()}@e.test",
            household_id=hh).returning(people.c.id)).scalar_one()
        c.execute(insert(accounts).values(person_id=pid, custodian="Schwab",
            account_number=f"IRA-{_sfx()}", account_name="IRA", status="open",
            registration_type="Traditional IRA", last_review_date=TODAY))
        c.execute(insert(record_assignments).values(user_id=ids["reviewer"], entity_type="person",
            entity_id=pid, assignment_type="owner", effective_date=TODAY))
    caps = frozenset({"client.read", "compliance.review.submit", "compliance.review.assign",
                      "compliance.review.decide", "record.read_all"})
    principal = Principal(ids["reviewer"], "r@e.com", "R", caps)
    rid = next(s.id for s in get_client_signals(principal, pid, now=NOW)
               if s.category == "recommendation"
               and s.recommendation.recommendation_type == "beneficiary_review")
    review = rsvc.submit_review(principal, person_id=pid, recommendation_id=rid, actor_user_id=ids["reviewer"])
    return principal, pid, hh, review


def _cleanup_review(pid, hh, reviewer_uid):
    with engine.begin() as c:
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == reviewer_uid))
        c.execute(delete(accounts).where(accounts.c.person_id == pid))
        # reviews with decisions are append-only leftovers; delete only decisionless ones
        from app.db import compliance_decisions, compliance_reviews
        for rvid in list(c.scalars(select(compliance_reviews.c.id).where(compliance_reviews.c.person_id == pid))):
            if not c.scalar(select(text("count(*)")).select_from(compliance_decisions).where(
                    compliance_decisions.c.compliance_review_id == rvid)):
                c.execute(delete(compliance_reviews).where(compliance_reviews.c.id == rvid))
        c.execute(delete(people).where(people.c.id == pid))
        c.execute(delete(households).where(households.c.id == hh))


def test_d7_approval_blocked_without_authority_then_allowed_with_matching_authority():
    ids = _setup()
    principal, pid, hh, review = _seed_review(ids)
    try:
        # Assign the reviewer BEFORE authority exists -> blocked.
        rsvc.assign_reviewer(principal, review["id"], expected_status="pending_assignment",
                             reviewer_principal_id=ids["reviewer"], reviewer_role="compliance_reviewer",
                             reviewer_name=None, actor_user_id=ids["reviewer"])
        with pytest.raises(rsvc.ApprovalBlockedError):
            rsvc.record_decision(principal, review["id"], decision="approved",
                                 expected_status="blocked_pending_authorized_reviewer",
                                 actor_user_id=ids["reviewer"])
        # Record + activate a matching authority (by a different admin) and RE-ASSIGN.
        _active_authority(ids, scope=(RULE,))
        rsvc.assign_reviewer(principal, review["id"], expected_status="blocked_pending_authorized_reviewer",
                             reviewer_principal_id=ids["reviewer"], reviewer_role="chief_compliance_officer",
                             reviewer_name="Recorded Reviewer", actor_user_id=ids["reviewer"])
        out = rsvc.record_decision(principal, review["id"], decision="approved",
                                   expected_status="pending_review", actor_user_id=ids["reviewer"],
                                   scope_reviewed="beneficiary")
        assert out["status"] == "approved"
    finally:
        _cleanup_review(pid, hh, ids["reviewer"])
        _teardown(ids)


def test_d7_approval_blocked_out_of_scope_and_after_suspension():
    ids = _setup()
    principal, pid, hh, review = _seed_review(ids)
    try:
        # Authority for a DIFFERENT rule -> out of scope -> assignment blocked.
        auth = _active_authority(ids, scope=("RULE-INSURANCE-REVIEW-CADENCE",))
        res = rsvc.assign_reviewer(principal, review["id"], expected_status="pending_assignment",
                                   reviewer_principal_id=ids["reviewer"], reviewer_role="chief_compliance_officer",
                                   reviewer_name="Recorded Reviewer", actor_user_id=ids["reviewer"])
        assert res["status"] == "blocked_pending_authorized_reviewer"
        # Grant in-scope authority, assign -> pending_review, then suspend -> approval blocked.
        inscope = _active_authority(ids, scope=(RULE,))
        rsvc.assign_reviewer(principal, review["id"], expected_status="blocked_pending_authorized_reviewer",
                             reviewer_principal_id=ids["reviewer"], reviewer_role="chief_compliance_officer",
                             reviewer_name="Recorded Reviewer", actor_user_id=ids["reviewer"])
        aa.suspend(ids["admin"], inscope["id"], reason="pending re-check", expected_status="active")
        with pytest.raises(rsvc.ApprovalBlockedError):
            rsvc.record_decision(principal, review["id"], decision="approved",
                                 expected_status="pending_review", actor_user_id=ids["reviewer"])
        _ = auth
    finally:
        _cleanup_review(pid, hh, ids["reviewer"])
        _teardown(ids)


# --- dependency direction + no seed ------------------------------------------

def test_advisor_intelligence_does_not_import_compliance():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app" / "services" / "advisor_intelligence.py").read_text()
    assert "services.compliance" not in src


def test_authority_catalog_not_seeded():
    from pathlib import Path

    # The D.8 migration seeds NO authority record (no reviewer is fabricated). Verified
    # statically (leftover test rows must not mask this) and by the absence of any
    # system-recorded (recorder-less) authority.
    mig = (Path(__file__).resolve().parent.parent / "migrations" / "versions"
           / "f8a9u1t2h3r4_reviewer_authority_admin.py").read_text().lower()
    assert "insert into reviewer_authorities" not in mig
    assert "reviewer_authorities.insert" not in mig
    with engine.connect() as c:
        seeded = c.scalar(select(text("count(*)")).select_from(reviewer_authorities).where(
            reviewer_authorities.c.recorded_by.is_(None)))
        assert seeded == 0
