"""Enterprise Operational Intelligence & Explainable Recommendations (Phase D.46) tests.

Covers the composition layer that produces explainable advisor recommendations by composing the AUTHORITATIVE
recommendation sources (the advisor_intelligence Signal engine + the domain observation sets + the work
queue + the D.44 engagement summary) WITHOUT a second recommendation/workflow/opportunity engine and WITHOUT
ML: the recommendation registry, rule execution / recommendation generation, explanation completeness,
suppression, duplicate elimination, household aggregation, workspace panel, Client 360 / Household 360
integration, AI summarize-only grounding, runtime gates, governance, diagnostics, analytics, and the
architecture invariants (no ML dep, no mutation, no second engine, no policy/runtime bypass, every
recommendation deep-links + carries evidence). Deterministic — seeds facts that drive the authoritative
producers and composes over them.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import insert

from app.db import accounts, engine, household_relationships, households, people
from app.security.models import Principal
from app.services.recommendations import (
    client_recommendations,
    diagnostics,
    explain_recommendation,
    gate,
    governance,
    household_recommendations,
    metrics,
    registry,
    stats,
    workspace_recommendations,
)
from app.services.recommendations.adapters.signals import recommendation_from_signal

_CAPS = frozenset({"client.read", "record.read_all", "observability.audit"})
FIRM = Principal(1, "a@e.com", "Advisor", _CAPS)
SCOPED = Principal(2, "s@e.com", "Scoped", frozenset({"client.read"}))   # no read_all / assignment


def _seed(label="OI", *, overdue_review=True):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"{label} {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {suffix}",
                        active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(household_id=hid, person_id=pid,
                  relationship_type="head", is_primary=True, is_primary_household=True))
        if overdue_review:
            c.execute(insert(accounts).values(person_id=pid, household_id=hid, custodian="Fidelity",
                      account_number=f"OI-{suffix}", account_name="Brokerage", total_value=400000,
                      status="open", last_review_date=date.today() - timedelta(days=900)))
    return hid, pid, suffix


# --- registry ----------------------------------------------------------------

def test_registry_complete_and_single_ownership():
    assert len(registry.REGISTRY) == 10
    for t in registry.REGISTRY:
        assert t.owner_service and t.source_services and t.deep_link_target
        assert t.explanation_template and t.evidence_kind and t.workflow_owner
        assert t.lifecycle in registry.LIFECYCLES
    keys = [t.key for t in registry.REGISTRY]
    assert len(keys) == len(set(keys))   # no duplicate recommendation ownership


def test_signal_classification_deterministic():
    assert registry.classify_signal({"category": "recommendation"}) == "governed_recommendation"
    assert registry.classify_signal({"category": "opportunity"}) == "service_opportunity"
    assert registry.classify_signal({"category": "operational", "source_service": "tasks"}) == "task_workload"
    assert registry.classify_signal({"category": "operational", "title": "Meeting tomorrow"}) == "meeting_prep"


# --- rule execution + generation ---------------------------------------------

def test_client_recommendation_generated_from_authoritative_signal():
    hid, pid, _ = _seed()
    res = client_recommendations(FIRM, pid)
    assert res["enabled"] is True and res["total"] >= 1
    r = res["recommendations"][0]
    assert r["type"] in {t.key for t in registry.REGISTRY}
    assert r["authoritative_source"] and r["deep_link"] and r["evidence"]


def test_every_recommendation_is_explainable_and_deep_links():
    hid, pid, _ = _seed()
    res = client_recommendations(FIRM, pid)
    for r in res["recommendations"]:
        assert r["explanation"] and r["evidence"] and r["deep_link"]   # explainability + deep link
        assert r["recommended_next_action"] and r["workflow_owner"]


def test_confidence_is_deterministic_rule_based():
    hid, pid, _ = _seed()
    res = client_recommendations(FIRM, pid)
    for r in res["recommendations"]:
        assert isinstance(r["confidence"], (int, float)) and 0.0 <= r["confidence"] <= 1.0


# --- explanation completeness ------------------------------------------------

def test_non_explainable_recommendation_is_dropped():
    # A signal dict with no why / evidence / route → not explainable → normalizer returns None.
    bare = {"id": "x", "category": "operational", "title": "T", "summary": "",
            "explainability": {"why": "", "evidence": []}, "route": None, "source_record": None}
    assert recommendation_from_signal(bare) is None


def test_explain_endpoint_returns_full_explanation():
    hid, pid, _ = _seed()
    res = client_recommendations(FIRM, pid)
    rid = res["recommendations"][0]["recommendation_id"]
    ex = explain_recommendation(FIRM, rid, person_id=pid)["explanation"]
    assert ex["why"] and ex["governing_rule"] and ex["authoritative_source"] and ex["evidence"]
    assert ex["workflow_owner"] and ex["deep_link"]


# --- suppression + dedup -----------------------------------------------------

def test_suppression_when_no_signal():
    hid, pid, _ = _seed(overdue_review=False)
    res = client_recommendations(FIRM, pid)
    # No overdue review + no other facts → few/no recommendations, but always a valid envelope.
    assert res["enabled"] is True and isinstance(res["recommendations"], list)


def test_duplicate_ids_collapse():
    hid, pid, _ = _seed()
    res = client_recommendations(FIRM, pid)
    ids = [r["recommendation_id"] for r in res["recommendations"]]
    assert len(ids) == len(set(ids))


# --- household aggregation ---------------------------------------------------

def test_household_aggregates_and_dedups():
    hid, pid, _ = _seed()
    res = household_recommendations(FIRM, hid)
    assert res["enabled"] is True
    keys = [(r["type"], r["title"]) for r in res["recommendations"]]
    assert len(keys) == len(set(keys))   # no duplicate (type,title) across members


# --- scope enforcement -------------------------------------------------------

def test_out_of_scope_returns_none():
    hid, pid, _ = _seed()
    assert client_recommendations(SCOPED, pid) is None
    assert household_recommendations(SCOPED, hid) is None


# --- runtime + policy gates --------------------------------------------------

def test_master_gate_disables(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: False)
    res = client_recommendations(FIRM, 1)
    assert res["enabled"] is False and res["recommendations"] == []


def test_policy_deny_is_honored(monkeypatch):
    hid, pid, _ = _seed()
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    res = client_recommendations(FIRM, pid)
    assert res["recommendations"] == [] and res.get("denied") == "policy"


def test_workspace_gate(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: name != "recommendations.workspace.enabled")
    assert workspace_recommendations(FIRM)["enabled"] is False


# --- workspace panel ---------------------------------------------------------

def test_workspace_panel_has_recommendations_and_workload():
    hid, pid, _ = _seed()
    ws = workspace_recommendations(FIRM)
    assert ws["enabled"] is True
    assert "workload" in ws and set(ws["workload"]) >= {"by_domain", "my_overdue", "sla_breaches"}


# --- Client 360 / Household 360 integration ----------------------------------

def test_client360_recommendations_section():
    from app.services.client360 import get_workspace
    hid, pid, _ = _seed()
    ws = get_workspace(FIRM, person_id=pid)
    section = ws["sections"]["recommendations"]
    assert section["source"] == "recommendations.engine" and section["not_a_second_engine"] is True
    assert section["summary"]["total"] >= 1


def test_household360_recommendations_section():
    from app.services.client360.household import get_household_workspace
    hid, pid, _ = _seed()
    hws = get_household_workspace(FIRM, hid)
    assert hws["sections"]["recommendations"]["summary"]["total"] >= 1


# --- AI grounding (summarize only) -------------------------------------------

def test_ai_summarizes_recommendations_never_invents():
    from app.services.ai_assist.context import assemble
    hid, pid, _ = _seed()
    bundle = assemble(FIRM, "client_brief", person_id=pid)
    rf = [f for f in bundle.facts if f.source_type == "recommendations"]
    assert rf and any(f.fact_key == "recommendations.count" for f in rf)
    # counts / titles only — never fabricated advice.
    count_fact = next(f for f in rf if f.fact_key == "recommendations.count")
    assert isinstance(count_fact.fact_value, int)


# --- analytics + diagnostics + governance ------------------------------------

def test_low_cardinality_metrics_registered():
    from app.services.analytics.metrics import METRICS
    for k in ("recommendations_generated", "recommendations_suppressed", "recommendation_compositions",
              "recommendation_adapter_failures"):
        assert k in METRICS
    import json
    assert "@e.test" not in json.dumps(metrics.recommendation_metrics(FIRM))


def test_diagnostics_internal_shape():
    d = diagnostics.recommendation_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "adapter_availability", "governance"} <= set(d)
    assert d["governance"]["ok"] is True and d["adapter_availability"]["signals"] is True


def test_governance_clean():
    report = governance.validate_recommendations()
    assert report["ok"], report["findings"]


# --- architecture invariants -------------------------------------------------

def test_no_ml_no_writes_no_second_engine():
    import pathlib
    base = pathlib.Path("app/services/recommendations")
    for pyfile in base.rglob("*.py"):
        src = pyfile.read_text()
        if pyfile.name == "governance.py":
            continue  # holds detection literals
        for banned in ("import sklearn", "import torch", "import tensorflow", "predict_proba",
                       "Table(", "add_timeline_event(", "write_audit_event(", "publisher.publish",
                       ".insert(", ".update(", ".delete("):
            assert banned not in src, f"{banned} in {pyfile}"


def test_composes_advisor_intelligence_not_a_second_engine():
    import pathlib
    src = pathlib.Path("app/services/recommendations/adapters/signals.py").read_text()
    svc = pathlib.Path("app/services/recommendations/service.py").read_text()
    assert "advisor_intelligence" in svc and "recommendation_from_signal" in src


def test_stats_reset_and_note():
    stats.reset_stats()
    stats.note("generated", category="review", severity="high")
    assert stats.recommendation_stats()["generated"] == 1
