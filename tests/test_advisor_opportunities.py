"""Deterministic advisor-opportunity tests (Phase D.5C).

Exercises the three approved opportunity producers activated on the framework:
portfolio_review_opportunity (annual review approaching), insurance_review_opportunity
(servicing review due), and beneficiary_review_opportunity (IRA with no active
beneficiary). Every opportunity must be factual, evidence-backed, record-scoped,
propose-only, category "opportunity", PolicyGate.NONE — and an inaccessible record
must never reach producer logic. Also asserts the deferred types (tax, retirement)
are NOT registered.
"""
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, insert

from app.db import (
    account_beneficiaries,
    accounts,
    engine,
    households,
    insurance_policies,
    insurance_policy_reviews,
    insurance_product_families,
    insurance_product_versions,
    people,
    record_assignments,
    relationship_entities,
    users,
)
from app.security.models import Principal
from app.services.advisor_intelligence import (
    Priority,
    get_client_signals,
    get_dashboard_signals,
    get_household_signals,
    list_registered_signals,
)
from app.services.advisor_workspace import FIRM_TZ

CAPS = frozenset({"client.read", "work.read", "task.read", "exception.read", "insurance.read"})
READ_ALL = CAPS | {"record.read_all"}
NOW = datetime(2026, 7, 16, 9, 0, tzinfo=FIRM_TZ)
TODAY = NOW.date()


def _seed_client(conn, tag, *, assigned_to=None, household_id=None,
                 approaching=False, ira_missing_benef=False, insurance_due=False):
    pid = conn.execute(people.insert().values(
        full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
        normalized_email=f"{tag}@e.test", household_id=household_id, active=True,
    ).returning(people.c.id)).scalar_one()

    if approaching:
        # Reviewed ~350 days ago -> within the 30-day approaching window of the
        # 365-day cadence, NOT yet overdue.
        conn.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"REV-{tag}",
            account_name=f"Brokerage {tag}", status="open",
            last_review_date=TODAY - timedelta(days=350)))

    if ira_missing_benef:
        # IRA account with NO active beneficiary -> missing required designation.
        conn.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"IRA-{tag}",
            account_name=f"IRA {tag}", status="open", registration_type="Traditional IRA",
            last_review_date=TODAY))  # recent review so it is not also a review signal

    if insurance_due:
        carrier = conn.execute(relationship_entities.insert().values(
            entity_type="insurance_carrier", name=f"C {tag}", details={}, active=True
        ).returning(relationship_entities.c.id)).scalar_one()
        fam = conn.execute(insurance_product_families.insert().values(
            carrier_id=carrier, name=f"F {tag}", product_type="term_life", line="life"
        ).returning(insurance_product_families.c.id)).scalar_one()
        pv = conn.execute(insurance_product_versions.insert().values(
            family_id=fam, version_label="1").returning(insurance_product_versions.c.id)).scalar_one()
        policy = conn.execute(insurance_policies.insert().values(
            carrier_id=carrier, product_version_id=pv, person_id=pid, status="in_force"
        ).returning(insurance_policies.c.id)).scalar_one()
        conn.execute(insert(insurance_policy_reviews).values(
            policy_id=policy, review_type="annual", status="due", due_date=TODAY))

    if assigned_to is not None:
        conn.execute(insert(record_assignments).values(
            user_id=assigned_to, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=TODAY))
    return pid


def _setup(**a_flags):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"adv-{tag}@e.test", normalized_email=f"adv-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = conn.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        a = _seed_client(conn, f"A{tag}", assigned_to=uid, household_id=hh, **a_flags)
        b = _seed_client(conn, f"B{tag}", assigned_to=None, **a_flags)  # inaccessible
        conn.execute(insert(record_assignments).values(
            user_id=uid, entity_type="household", entity_id=hh,
            assignment_type="owner", effective_date=TODAY))
    return {"uid": uid, "a": a, "b": b, "hh": hh,
            "principal": Principal(uid, "a@e.com", "Adv", CAPS),
            "read_all": Principal(uid, "a@e.com", "Adv", READ_ALL)}


def _teardown(ids):
    with engine.begin() as conn:
        for pid in (ids["a"], ids["b"]):
            # Insurance review rows reference policies for this person; remove reviews
            # via their policies first, then policies, then accounts/beneficiaries.
            policy_ids = list(conn.scalars(
                insurance_policies.select().with_only_columns(insurance_policies.c.id)
                .where(insurance_policies.c.person_id == pid)))
            if policy_ids:
                conn.execute(delete(insurance_policy_reviews).where(
                    insurance_policy_reviews.c.policy_id.in_(policy_ids)))
                conn.execute(delete(insurance_policies).where(insurance_policies.c.person_id == pid))
            acct_ids = list(conn.scalars(
                accounts.select().with_only_columns(accounts.c.id).where(accounts.c.person_id == pid)))
            if acct_ids:
                conn.execute(delete(account_beneficiaries).where(
                    account_beneficiaries.c.account_id.in_(acct_ids)))
            conn.execute(delete(accounts).where(accounts.c.person_id == pid))
        conn.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        conn.execute(delete(people).where(people.c.id.in_((ids["a"], ids["b"]))))
        conn.execute(delete(households).where(households.c.id == ids["hh"]))
        # insurance_product_families/versions/carriers left as leftovers (shared DB).


def _opps(signals):
    return [s for s in signals if s.category == "opportunity"]


def _by_type(signals, t):
    return [s for s in signals if s.id.startswith(t + ":")]


# --- registry ----------------------------------------------------------------

def test_registered_opportunities_are_exactly_the_approved_three():
    keys = {r.key for r in list_registered_signals()}
    assert {"portfolio_review_opportunity", "insurance_review_opportunity",
            "beneficiary_review_opportunity"} <= keys
    # Deferred types are NOT registered.
    assert "tax_planning_opportunity" not in keys
    assert "retirement_review_opportunity" not in keys


# --- each opportunity type ---------------------------------------------------

def test_portfolio_review_opportunity_emits_for_approaching_review():
    ids = _setup(approaching=True)
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=NOW), "portfolio_review_opportunity")
        assert len(sig) == 1
        assert sig[0].category == "opportunity"
        assert sig[0].priority is Priority.MEDIUM
        assert sig[0].source_service == "portfolio"
        assert sig[0].policy_gate.value == "none"
    finally:
        _teardown(ids)


def test_portfolio_review_opportunity_absent_for_recent_review():
    ids = _setup(approaching=False, ira_missing_benef=False, insurance_due=False)
    try:
        # An account reviewed today is neither overdue nor approaching.
        with engine.begin() as conn:
            conn.execute(insert(accounts).values(
                person_id=ids["a"], custodian="Schwab", account_number="RECENT",
                account_name="Recent", status="open", last_review_date=TODAY))
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=NOW), "portfolio_review_opportunity") == []
    finally:
        _teardown(ids)


def test_insurance_review_opportunity_emits_for_due_review():
    ids = _setup(insurance_due=True)
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=NOW), "insurance_review_opportunity")
        assert len(sig) == 1
        assert sig[0].category == "opportunity"
        assert sig[0].source_service == "insurance"
        assert "review_type=annual" in sig[0].evidence
    finally:
        _teardown(ids)


def test_beneficiary_opportunity_emits_only_for_ira_without_active_beneficiary():
    ids = _setup(ira_missing_benef=True)
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=NOW), "beneficiary_review_opportunity")
        assert len(sig) == 1
        assert "registration_type=Traditional IRA" in sig[0].evidence
        assert sig[0].source_service == "portfolio"
    finally:
        _teardown(ids)


def test_beneficiary_opportunity_not_inferred_for_non_ira_or_when_beneficiary_present():
    # Non-IRA account -> no beneficiary opportunity (never inferred).
    ids = _setup()
    try:
        with engine.begin() as conn:
            conn.execute(insert(accounts).values(
                person_id=ids["a"], custodian="Schwab", account_number="BROK",
                account_name="Brokerage", status="open", registration_type="Individual",
                last_review_date=TODAY))
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=NOW), "beneficiary_review_opportunity") == []
    finally:
        _teardown(ids)

    # IRA WITH an active beneficiary -> no opportunity.
    ids = _setup()
    try:
        with engine.begin() as conn:
            acct = conn.execute(insert(accounts).values(
                person_id=ids["a"], custodian="Schwab", account_number="IRA2",
                account_name="IRA2", status="open", registration_type="Roth IRA",
                last_review_date=TODAY).returning(accounts.c.id)).scalar_one()
            conn.execute(insert(account_beneficiaries).values(
                account_id=acct, beneficiary_name="Spouse", beneficiary_type="primary",
                percentage=100, active=True))
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=NOW), "beneficiary_review_opportunity") == []
    finally:
        _teardown(ids)


# --- determinism -------------------------------------------------------------

def test_opportunities_are_deterministic_ids_order_and_no_dupes():
    ids = _setup(approaching=True, ira_missing_benef=True, insurance_due=True)
    try:
        first = get_client_signals(ids["principal"], ids["a"], now=NOW)
        second = get_client_signals(ids["principal"], ids["a"], now=NOW)
        opp_types = {s.id.split(":", 1)[0] for s in _opps(first)}
        assert opp_types == {"portfolio_review_opportunity", "insurance_review_opportunity",
                             "beneficiary_review_opportunity"}
        assert [s.id for s in first] == [s.id for s in second]  # deterministic
        assert len({s.id for s in first}) == len(first)  # no dupes
        ranks = [s.priority.rank for s in first]
        assert ranks == sorted(ranks, reverse=True)  # ordered
    finally:
        _teardown(ids)


def test_explainability_and_evidence_populated_and_serializable():
    ids = _setup(approaching=True, ira_missing_benef=True, insurance_due=True)
    try:
        for s in _opps(get_client_signals(ids["principal"], ids["a"], now=NOW)):
            d = s.to_dict()
            json.loads(json.dumps(d))
            assert d["policy_gate"] == "none"
            assert d["source_record"] is not None
            assert d["evidence"]
            assert d["explainability"]["why"]
            assert d["explainability"]["source_service"]
            assert d["explainability"]["confidence"] == 1.0
            assert d["route"] and d["route"].startswith("/people/")
    finally:
        _teardown(ids)


# --- authorization -----------------------------------------------------------

def test_inaccessible_client_opportunities_never_reach_producer_logic():
    ids = _setup(approaching=True, ira_missing_benef=True, insurance_due=True)
    try:
        # Scoped advisor cannot see B -> () (gate before any producer).
        assert get_client_signals(ids["principal"], ids["b"], now=NOW) == ()
        # Same B records DO produce for a read_all principal -> proves the gate.
        assert _opps(get_client_signals(ids["read_all"], ids["b"], now=NOW))
    finally:
        _teardown(ids)


def test_dashboard_book_scope_excludes_unassigned_client_opportunities():
    ids = _setup(approaching=True, ira_missing_benef=True, insurance_due=True)
    try:
        sigs = get_dashboard_signals(ids["principal"], now=NOW)
        assert _opps(sigs)
        for s in sigs:
            assert f"person_id={ids['b']}" not in s.evidence
            assert (s.route or "") != f"/people/{ids['b']}"
    finally:
        _teardown(ids)


def test_household_scope_covers_members_only():
    ids = _setup(approaching=True)
    try:
        sigs = get_household_signals(ids["principal"], ids["hh"], now=NOW)
        # A is a household member with an approaching review; B is not in the household.
        assert any(s.id.startswith("portfolio_review_opportunity:") for s in sigs)
        for s in sigs:
            assert f"person_id={ids['b']}" not in s.evidence
    finally:
        _teardown(ids)


# --- content safety ----------------------------------------------------------

def test_no_recommendation_ai_or_regulated_language():
    ids = _setup(approaching=True, ira_missing_benef=True, insurance_due=True)
    banned = ("recommend", "should ", "advise", "advice", "suitable", "suitability",
              "roth conversion", "harvest", "rollover", "1035", "replace",
              "allocation", "fiduciary", "risk score", "probability", "ai-generated",
              "coverage gap")
    try:
        for s in _opps(get_client_signals(ids["principal"], ids["a"], now=NOW)):
            blob = " ".join((s.title, s.summary, s.explainability.why, *s.evidence)).lower()
            for term in banned:
                assert term not in blob, f"banned term {term!r} in {s.id}"
            assert s.policy_gate.value == "none"
    finally:
        _teardown(ids)


# --- UI rendering ------------------------------------------------------------

def test_dashboard_renders_opportunities_grouped():
    from starlette.requests import Request

    from app.routes.workspace import workspace_dashboard
    ids = _setup(approaching=True, ira_missing_benef=True, insurance_due=True)
    try:
        req = Request({"type": "http", "method": "GET", "path": "/workspace",
                       "headers": [], "query_string": b""})
        body = workspace_dashboard(req, principal=ids["principal"]).body.decode()
        assert "Advisor Intelligence" in body
        assert "Advisor Opportunities" in body  # bucket group heading
        assert f"/people/{ids['a']}" in body
        for control in ("Dismiss", "Snooze", "Execute", "Create task"):
            assert control not in body
    finally:
        _teardown(ids)


def test_meeting_brief_renders_opportunities_section():
    import asyncio

    from starlette.requests import Request

    from app.routes.workspace import meeting_brief
    ids = _setup(approaching=True)
    try:
        req = Request({"type": "http", "method": "GET", "path": f"/workspace/meetings/{ids['a']}",
                       "headers": [], "query_string": b""})
        resp = meeting_brief(req, ids["a"], None, principal=ids["principal"])
        body = resp.body.decode()
        assert "Advisor Intelligence" in body
        assert "Annual portfolio review is due soon" in body
    finally:
        _teardown(ids)
    _ = asyncio  # meeting_brief is sync; import kept for parity/no-op


def test_client_workspace_renders_opportunities_section():
    from starlette.requests import Request

    from app.routes.people import person_profile
    ids = _setup(approaching=True)
    try:
        scope = {"type": "http", "method": "GET", "path": f"/people/{ids['a']}",
                 "headers": [], "query_string": b""}
        req = Request(scope)
        req.state.principal = ids["principal"]
        resp = person_profile(req, ids["a"])
        body = resp.body.decode()
        assert "Advisor Intelligence" in body
        assert "Annual portfolio review is due soon" in body
    finally:
        _teardown(ids)
