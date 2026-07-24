"""Enterprise Compliance Intelligence & Supervisory Operations (Phase D.47) tests.

Covers the read-only supervisory composition over the authoritative compliance/review/exception/audit/
approval/licensing services WITHOUT a second compliance/approval/workflow/audit engine: the supervisory +
exception registries, supervisory composition, exception generation, the supervisor-vs-advisor authorization
boundary (advisors never see supervisory findings), advisor-visible compliance tasks (governed-only), Client
360 / Household 360 integration, Advisor Workspace integration, AI summarize-only grounding, runtime gates,
governance, diagnostics, analytics, and the architecture invariants (no second engine, no mutation, no
approval calls, supervisory info never leaks, every item deep-links + carries evidence). Deterministic —
seeds accounts that drive the authoritative cadence reads and composes over them.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import insert

from app.db import accounts, engine, household_relationships, households, people
from app.security.models import Principal
from app.services.compliance_intelligence import (
    advisor_compliance_tasks,
    client_compliance,
    compliance_summary,
    diagnostics,
    gate,
    governance,
    household_compliance,
    metrics,
    registry,
    stats,
    supervisory_dashboard,
)

_SUP_CAPS = frozenset({"compliance.supervise", "compliance.review.read", "record.read_all", "audit.read",
                       "observability.audit", "client.read"})
SUP = Principal(1, "s@e.com", "Supervisor", _SUP_CAPS)
ADV = Principal(2, "a@e.com", "Advisor", frozenset({"client.read", "record.read_all"}))  # NO supervise
SCOPED = Principal(3, "x@e.com", "Scoped", frozenset({"compliance.supervise"}))   # supervise but no scope


def _seed(label="CI"):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"{label} {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {suffix}",
                        active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(household_id=hid, person_id=pid,
                  relationship_type="head", is_primary=True, is_primary_household=True))
        c.execute(insert(accounts).values(person_id=pid, household_id=hid, custodian="Fidelity",
                  account_number=f"CI-{suffix}", account_name="Brokerage", total_value=500000,
                  status="open", last_review_date=date.today() - timedelta(days=900),
                  registration_type="IRA"))
    return hid, pid, suffix


# --- registries --------------------------------------------------------------

def test_supervisory_and_exception_registries_complete():
    assert len(registry.SUPERVISORY_REGISTRY) == 12
    assert len(registry.EXCEPTION_REGISTRY) == 10
    for t in registry.SUPERVISORY_REGISTRY:
        assert t.owner and t.governing_workflow and t.policy_owner and t.required_evidence
        assert t.approval_authority and t.escalation_path and t.retention_class and t.deep_link and t.runtime_gate
        assert t.lifecycle in registry.LIFECYCLES
    for t in registry.EXCEPTION_REGISTRY:
        assert t.owner and t.governing_policy and t.escalation and t.default_severity
    skeys = [t.key for t in registry.SUPERVISORY_REGISTRY]
    xkeys = [t.key for t in registry.EXCEPTION_REGISTRY]
    assert len(skeys) == len(set(skeys)) and len(xkeys) == len(set(xkeys))   # single ownership


def test_registry_declares_unpopulated_types():
    cov = registry.coverage()
    assert cov["review_types"] == 12 and cov["populated_review_types"] < 12   # some declared-but-unpopulated


# --- authorization: supervisor vs advisor ------------------------------------

def test_supervisor_sees_dashboard_advisor_does_not():
    _seed()
    d = supervisory_dashboard(SUP)
    assert d["enabled"] is True and "workload" in d
    # An advisor (no compliance.supervise) gets None — supervisory findings never reach them.
    assert supervisory_dashboard(ADV) is None


def test_advisor_client_and_household_views_are_none():
    hid, pid, _ = _seed()
    assert client_compliance(ADV, pid) is None
    assert household_compliance(ADV, hid) is None


def test_out_of_scope_supervisor_returns_none():
    hid, pid, _ = _seed()
    assert client_compliance(SCOPED, pid) is None   # has supervise but not record scope
    assert household_compliance(SCOPED, hid) is None


def test_summary_hides_supervisor_flag_from_advisor():
    hid, pid, _ = _seed()
    assert compliance_summary(ADV, person_id=pid)["supervisor"] is False
    assert compliance_summary(SUP, person_id=pid)["supervisor"] is True


# --- supervisory composition + exception generation --------------------------

def test_client_composition_generates_explainable_exceptions():
    hid, pid, _ = _seed()
    res = client_compliance(SUP, pid)
    assert res["enabled"] is True and res["counts"]["open_exceptions"] >= 1
    types = {e["exception_type"] for e in res["exceptions"]}
    assert {"overdue_review", "missing_beneficiary"} & types   # derived from portfolio cadence
    for e in res["exceptions"]:
        assert e["explanation"] and e["evidence"] and e["deep_link"]   # explainable + deep-links
        assert registry.exception_registered(e["exception_type"])


def test_every_review_item_registered_and_deep_links():
    _seed()
    d = supervisory_dashboard(SUP)
    for r in d["reviews"]:
        assert registry.review_registered(r["review_type"])
        assert r["explanation"] and r["evidence"] and r["deep_link"] and r["required_reviewer"]


def test_dashboard_has_counts_and_workload():
    _seed()
    d = supervisory_dashboard(SUP)
    assert {"open_reviews", "open_exceptions", "pending_approvals", "blocked"} <= set(d["counts"])
    assert set(d["workload"]) >= {"by_domain", "my_overdue", "sla_breaches"}


# --- advisor-visible compliance tasks (never supervisory) --------------------

def test_advisor_tasks_are_governed_only_never_supervisory():
    hid, pid, _ = _seed()
    at = advisor_compliance_tasks(ADV, person_id=pid)
    assert at["enabled"] is True
    for t in at["tasks"]:
        assert t.get("category") == "governed"   # only the D.46 governed advisor recommendations
        # never a supervisory item/exception shape.
        assert "review_type" not in t and "exception_type" not in t


# --- runtime + policy gates --------------------------------------------------

def test_master_gate_disables(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: False)
    d = supervisory_dashboard(SUP)
    assert d["enabled"] is False


def test_policy_deny_is_honored(monkeypatch):
    _seed()
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    d = supervisory_dashboard(SUP)
    assert d.get("denied") == "policy"


# --- Client 360 / Household 360 integration ----------------------------------

def test_client360_compliance_summary_section_supervisor_only():
    from app.services.client360 import get_workspace
    hid, pid, _ = _seed()
    ws_sup = get_workspace(SUP, person_id=pid)
    section = ws_sup["sections"]["compliance_summary"]
    assert section["source"] == "compliance_intelligence" and section["not_a_second_engine"] is True
    assert section["summary"]["supervisor"] is True
    # Advisor: the section is SUPPRESSED (never built) — separation at the section boundary.
    ws_adv = get_workspace(ADV, person_id=pid)
    assert "compliance_summary" not in ws_adv["sections"]
    assert "compliance_summary" in ws_adv["suppressed_sections"]


def test_household360_compliance_summary_section():
    from app.services.client360.household import get_household_workspace
    hid, pid, _ = _seed()
    hws = get_household_workspace(SUP, hid)
    assert hws["sections"]["compliance_summary"]["summary"]["supervisor"] is True


# --- Advisor Workspace integration -------------------------------------------

def test_advisor_workspace_has_advisor_compliance_tasks_not_supervisory():
    from app.services.workspace.service import get_workspace as ws_home
    home = ws_home(ADV)
    assert "compliance_tasks" in home and home["compliance_tasks"]["enabled"] is True
    # the advisor home never carries a supervisory dashboard key.
    assert "supervisory_dashboard" not in home


# --- AI grounding (summarize only, supervisor-gated) -------------------------

def test_ai_supervisory_facts_only_for_supervisor():
    from app.services.ai_assist.context import assemble
    hid, pid, _ = _seed()
    sup_facts = [f for f in assemble(SUP, "client_brief", person_id=pid).facts
                 if f.source_type == "compliance_intelligence"]
    assert sup_facts and all(isinstance(f.fact_value, int) for f in sup_facts)   # counts only
    # An advisor's brief carries NO supervisory facts.
    adv_facts = [f for f in assemble(ADV, "client_brief", person_id=pid).facts
                 if f.source_type == "compliance_intelligence"]
    assert adv_facts == []


# --- analytics + diagnostics + governance ------------------------------------

def test_low_cardinality_metrics_registered():
    from app.services.analytics.metrics import METRICS
    for k in ("supervisory_reviews_composed", "supervisory_exceptions_composed", "supervisory_dashboards",
              "supervisory_authorization_failures"):
        assert k in METRICS
    import json
    assert "@e.test" not in json.dumps(metrics.compliance_metrics(SUP))


def test_diagnostics_internal_shape():
    d = diagnostics.compliance_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "adapter_availability", "governance"} <= set(d)
    assert d["governance"]["ok"] is True and d["adapter_availability"]["reviews"] is True


def test_governance_clean():
    report = governance.validate_compliance_intelligence()
    assert report["ok"], report["findings"]


# --- architecture invariants -------------------------------------------------

def test_no_second_engine_no_mutation_no_approval_calls():
    import pathlib
    base = pathlib.Path("app/services/compliance_intelligence")
    for pyfile in base.rglob("*.py"):
        src = pyfile.read_text()
        if pyfile.name == "governance.py":
            continue  # holds detection literals
        for banned in ("Table(", "write_audit_event(", "submit_review(", "assign_reviewer(",
                       "record_decision(", "resolve_exception(", "publisher.publish", "publish_safe(",
                       ".insert(", ".update(", ".delete("):
            assert banned not in src, f"{banned} in {pyfile}"


def test_composes_authoritative_compliance_services():
    import pathlib
    rev = pathlib.Path("app/services/compliance_intelligence/adapters/reviews.py").read_text()
    exc = pathlib.Path("app/services/compliance_intelligence/adapters/exceptions.py").read_text()
    assert "compliance.reviews" in rev and "exception_engine" in exc


def test_stats_reset_and_note():
    stats.reset_stats()
    stats.note("reviews_composed", review_type="suitability_review")
    assert stats.compliance_stats()["reviews_composed"] == 1
