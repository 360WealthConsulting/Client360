"""Compliance-governed advisor-recommendation tests (Phase D.5D).

Exercises the three approved recommendation producers activated on the framework:
annual_portfolio_review_recommendation, insurance_review_recommendation, and
beneficiary_review_recommendation. Every recommendation must be advisor-facing,
deterministic, evidence-backed, policy-gated, and carry immutable governance
metadata (governing rule, version, compliance owner, approval status) — display
only, no enforcement. Inaccessible records must never reach producer logic.
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
    PolicyGate,
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

REC_KEYS = {"annual_portfolio_review_recommendation", "insurance_review_recommendation",
            "beneficiary_review_recommendation"}


def _seed_client(conn, tag, *, assigned_to=None, household_id=None,
                 approaching=False, ira_missing_benef=False, insurance_due=False):
    pid = conn.execute(people.insert().values(
        full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
        normalized_email=f"{tag}@e.test", household_id=household_id, active=True,
    ).returning(people.c.id)).scalar_one()
    if approaching:
        conn.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"REV-{tag}",
            account_name=f"Brokerage {tag}", status="open",
            last_review_date=TODAY - timedelta(days=350)))
    if ira_missing_benef:
        conn.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"IRA-{tag}",
            account_name=f"IRA {tag}", status="open", registration_type="Traditional IRA",
            last_review_date=TODAY))
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


def _setup(**flags):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"adv-{tag}@e.test", normalized_email=f"adv-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = conn.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        a = _seed_client(conn, f"A{tag}", assigned_to=uid, household_id=hh, **flags)
        b = _seed_client(conn, f"B{tag}", assigned_to=None, **flags)
        conn.execute(insert(record_assignments).values(
            user_id=uid, entity_type="household", entity_id=hh,
            assignment_type="owner", effective_date=TODAY))
    return {"uid": uid, "a": a, "b": b, "hh": hh,
            "principal": Principal(uid, "a@e.com", "Adv", CAPS),
            "read_all": Principal(uid, "a@e.com", "Adv", READ_ALL)}


def _teardown(ids):
    with engine.begin() as conn:
        for pid in (ids["a"], ids["b"]):
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


def _recs(signals):
    return [s for s in signals if s.category == "recommendation"]


def _all_flags():
    return dict(approaching=True, ira_missing_benef=True, insurance_due=True)


# --- registry ----------------------------------------------------------------

def test_registry_records_recommendation_governance_metadata():
    reg = {r.key: r for r in list_registered_signals()}
    assert REC_KEYS <= set(reg)
    for key in REC_KEYS:
        r = reg[key]
        assert r.category == "recommendation"
        assert r.governing_rule and r.governing_rule.startswith("RULE-")
        assert r.rule_version == "1.0.0"
        assert r.compliance_owner
        assert r.approval_status in ("approved", "pending_compliance_review")
    # Deferred recommendation types are NOT registered.
    assert "tax_planning_recommendation" not in reg
    assert "retirement_review_recommendation" not in reg


def test_policy_gates_are_declared_per_recommendation():
    reg = {r.key: r for r in list_registered_signals()}
    assert reg["annual_portfolio_review_recommendation"].policy_gate is PolicyGate.NONE
    assert reg["insurance_review_recommendation"].policy_gate is PolicyGate.LICENSE_REQUIRED
    assert reg["beneficiary_review_recommendation"].policy_gate is PolicyGate.COMPLIANCE_REQUIRED


# --- emission + metadata -----------------------------------------------------

def test_each_recommendation_type_emits_with_full_metadata():
    ids = _setup(**_all_flags())
    try:
        recs = _recs(get_client_signals(ids["principal"], ids["a"], now=NOW))
        types = {s.recommendation.recommendation_type for s in recs}
        assert types == {"annual_portfolio_review", "insurance_review", "beneficiary_review"}
        for s in recs:
            m = s.recommendation
            assert s.group == "Advisor Recommendations"
            assert m.governing_rule.startswith("RULE-")
            assert m.rule_version == "1.0.0"
            assert m.compliance_owner
            assert m.approval_status in ("approved", "pending_compliance_review")
            assert m.created_from_rule == s.id.split(":", 1)[0]  # producer key
            assert s.explainability.why and s.evidence
            assert s.route and s.route.startswith("/people/")
    finally:
        _teardown(ids)


def test_gated_recommendations_are_pending_and_none_gate_is_approved():
    ids = _setup(**_all_flags())
    try:
        by_type = {s.recommendation.recommendation_type: s.recommendation
                   for s in _recs(get_client_signals(ids["principal"], ids["a"], now=NOW))}
        assert by_type["annual_portfolio_review"].approval_status == "approved"
        assert by_type["insurance_review"].approval_status == "pending_compliance_review"
        assert by_type["beneficiary_review"].approval_status == "pending_compliance_review"
    finally:
        _teardown(ids)


def test_serialization_includes_recommendation_metadata():
    ids = _setup(approaching=True)
    try:
        s = _recs(get_client_signals(ids["principal"], ids["a"], now=NOW))[0]
        d = s.to_dict()
        json.loads(json.dumps(d))
        assert d["group"] == "Advisor Recommendations"
        assert d["recommendation"]["governing_rule"] == "RULE-PORTFOLIO-REVIEW-CADENCE"
        assert d["recommendation"]["rule_version"] == "1.0.0"
        assert d["recommendation"]["compliance_owner"]
        assert d["recommendation"]["approval_status"] == "approved"
    finally:
        _teardown(ids)


# --- determinism -------------------------------------------------------------

def test_recommendations_deterministic_ids_order_and_no_dupes():
    ids = _setup(**_all_flags())
    try:
        first = get_client_signals(ids["principal"], ids["a"], now=NOW)
        second = get_client_signals(ids["principal"], ids["a"], now=NOW)
        assert [s.id for s in first] == [s.id for s in second]
        assert len({s.id for s in first}) == len(first)
        rec_ids = {s.id for s in _recs(first)}
        # Distinct from opportunity ids for the same records (different prefixes).
        assert not any(i.startswith("portfolio_review_opportunity:") for i in rec_ids)
        ranks = [s.priority.rank for s in first]
        assert ranks == sorted(ranks, reverse=True)
    finally:
        _teardown(ids)


# --- authorization -----------------------------------------------------------

def test_inaccessible_client_recommendations_never_reach_producer():
    ids = _setup(**_all_flags())
    try:
        assert get_client_signals(ids["principal"], ids["b"], now=NOW) == ()
        assert _recs(get_client_signals(ids["read_all"], ids["b"], now=NOW))  # same records DO produce
    finally:
        _teardown(ids)


def test_dashboard_scope_excludes_unassigned_client_recommendations():
    ids = _setup(**_all_flags())
    try:
        sigs = get_dashboard_signals(ids["principal"], now=NOW)
        assert _recs(sigs)
        for s in _recs(sigs):
            assert f"person_id={ids['b']}" not in s.evidence
            assert (s.route or "") != f"/people/{ids['b']}"
    finally:
        _teardown(ids)


def test_household_scope_covers_members_only():
    ids = _setup(approaching=True)
    try:
        recs = _recs(get_household_signals(ids["principal"], ids["hh"], now=NOW))
        assert any(s.recommendation.recommendation_type == "annual_portfolio_review" for s in recs)
        for s in recs:
            assert f"person_id={ids['b']}" not in s.evidence
    finally:
        _teardown(ids)


# --- content safety ----------------------------------------------------------

def test_no_prohibited_recommendation_language():
    ids = _setup(**_all_flags())
    banned = ("roth", "harvest", "asset allocation", "should buy", "should roll",
              "rollover", "1035", "replace", "suitable", "suitability", "fiduciary",
              "social security", "estate plan", "risk score", "advisor should",
              "recommend roth", "client should", "ai-generated", "probability")
    try:
        for s in _recs(get_client_signals(ids["principal"], ids["a"], now=NOW)):
            blob = " ".join((s.title, s.summary, s.explainability.why, *s.evidence)).lower()
            for term in banned:
                assert term not in blob, f"banned term {term!r} in {s.id}"
            # Advisor-facing, non-instructing wording.
            assert "may be appropriate" in s.summary.lower()
    finally:
        _teardown(ids)


# --- UI rendering ------------------------------------------------------------

def test_dashboard_renders_recommendations_group_with_governance():
    from starlette.requests import Request

    from app.routes.workspace import workspace_dashboard
    ids = _setup(**_all_flags())
    try:
        req = Request({"type": "http", "method": "GET", "path": "/workspace",
                       "headers": [], "query_string": b""})
        body = workspace_dashboard(req, principal=ids["principal"]).body.decode()
        assert "Advisor Recommendations" in body  # group heading
        assert "RULE-PORTFOLIO-REVIEW-CADENCE" in body  # governing rule shown
        assert "may be appropriate" in body
        # Governance surfaced; no action controls.
        for control in ("Approve<", "Reject", "Execute", "Run workflow", "Create task"):
            assert control not in body
    finally:
        _teardown(ids)


def test_meeting_brief_renders_recommendations():
    from starlette.requests import Request

    from app.routes.workspace import meeting_brief
    ids = _setup(approaching=True)
    try:
        req = Request({"type": "http", "method": "GET", "path": f"/workspace/meetings/{ids['a']}",
                       "headers": [], "query_string": b""})
        body = meeting_brief(req, ids["a"], None, principal=ids["principal"]).body.decode()
        assert "Advisor Recommendations" in body
        assert "Annual portfolio review" in body
    finally:
        _teardown(ids)


def test_client_workspace_renders_recommendations():
    from starlette.requests import Request

    from app.routes.people import person_profile
    ids = _setup(approaching=True)
    try:
        req = Request({"type": "http", "method": "GET", "path": f"/people/{ids['a']}",
                       "headers": [], "query_string": b""})
        req.state.principal = ids["principal"]
        body = person_profile(req, ids["a"]).body.decode()
        assert "Advisor Recommendations" in body
        assert "Annual portfolio review" in body
    finally:
        _teardown(ids)
