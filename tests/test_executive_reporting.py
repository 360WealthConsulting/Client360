"""Enterprise Reporting & Executive Intelligence (Phase D.48) tests.

Covers the read-only executive-dashboard composition over the authoritative operational services + the
SINGLE Analytics Registry WITHOUT a second analytics engine / data warehouse / BI platform / reporting
database / metrics system: the dashboard + widget registries, executive dashboard composition, the
operations/compliance/advisor dashboards, dashboard-level authorization (executive dashboards need
analytics.executive; a non-executive gets 404 + restricted widgets, never leaked values), runtime gates,
Client 360 / Household 360 executive sections, Advisor Workspace Executive Insights panel, AI summarize-only
grounding, analytics reuse (no second registry), governance, diagnostics, and the architecture invariants
(no second analytics platform / no reporting DB / no duplicated metrics / no mutation; every widget has an
authoritative owner; every dashboard deep-links). Deterministic — composes over the live services.
"""
from app.security.models import Principal
from app.services.executive_intelligence import (
    compose_dashboard,
    diagnostics,
    executive_summary,
    gate,
    get_widget,
    governance,
    list_dashboards,
    metrics,
    registry,
    stats,
)

_BASE = {"analytics.view", "record.read_all"}
EXEC = Principal(1, "e@e.com", "Exec", frozenset(_BASE | {"analytics.executive", "observability.audit"}))
ADV = Principal(2, "a@e.com", "Advisor", frozenset(_BASE))   # analytics.view but NOT analytics.executive
NONE = Principal(3, "n@e.com", "None", frozenset({"record.read_all"}))   # no analytics at all


# --- registries --------------------------------------------------------------

def test_dashboard_and_widget_registries_complete():
    assert len(registry.DASHBOARD_REGISTRY) == 9   # +practice_management (D.49, reuses existing widgets)
    assert len(registry.WIDGET_REGISTRY) == 14
    for d in registry.DASHBOARD_REGISTRY:
        assert d.owner and d.audience and d.runtime_gate and d.navigation and d.widgets
        assert d.required_capabilities and d.governing_services
        assert d.lifecycle in registry.LIFECYCLES
        for wkey in d.widgets:
            assert registry.widget_registered(wkey)   # every dashboard widget is registered
    for w in registry.WIDGET_REGISTRY:
        assert w.owner and w.source and w.deep_link and w.explainability and w.permission
        assert w.lifecycle in registry.LIFECYCLES
    dkeys = [d.key for d in registry.DASHBOARD_REGISTRY]
    wkeys = [w.key for w in registry.WIDGET_REGISTRY]
    assert len(dkeys) == len(set(dkeys)) and len(wkeys) == len(set(wkeys))   # single ownership


def test_every_widget_has_authoritative_owner_and_deep_link():
    for w in registry.WIDGET_REGISTRY:
        assert w.owner and w.deep_link.startswith("/")


# --- composition -------------------------------------------------------------

def test_executive_dashboard_composes_explainable_widgets():
    d = compose_dashboard(EXEC, "executive")
    assert d["enabled"] is True
    board = d["dashboard"]
    assert board["widget_count"] >= 1 and board["governing_services"] and board["generated_at"]
    for w in board["widgets"]:
        assert w["explanation"] and w["source"] and w["deep_link"]   # explainable + deep-links
        assert registry.widget_registered(w["key"])


def test_operations_and_advisor_dashboards_compose():
    for key in ("operations", "advisor", "compliance", "client_service", "workflow", "pipeline"):
        d = compose_dashboard(ADV, key)
        assert d["enabled"] is True and d["dashboard"]["key"] == key


def test_source_inventory_and_deep_links_present():
    board = compose_dashboard(EXEC, "executive")["dashboard"]
    assert board["source_inventory"] and board["deep_links"]


# --- authorization (executive vs non-executive) ------------------------------

def test_executive_dashboards_require_executive_capability():
    # executive + revenue dashboards require analytics.executive → ADV gets None (404).
    assert compose_dashboard(ADV, "executive") is None
    assert compose_dashboard(ADV, "revenue") is None
    # ADV can still see analytics.view dashboards.
    assert compose_dashboard(ADV, "operations")["enabled"] is True


def test_list_dashboards_filters_by_capability():
    exec_keys = {d["key"] for d in list_dashboards(EXEC)["dashboards"]}
    adv_keys = {d["key"] for d in list_dashboards(ADV)["dashboards"]}
    assert {"executive", "revenue"} <= exec_keys
    assert "executive" not in adv_keys and "revenue" not in adv_keys
    assert list_dashboards(NONE)["dashboards"] == []   # no analytics.* → nothing


def test_executive_widget_restricted_for_non_executive():
    # A non-executive requesting a firm-AUM widget gets a restricted result (value withheld), never leaked.
    w = get_widget(ADV, "firm_aum")
    assert w is not None and w["restricted"] is True and w["value"] is None


def test_executive_summary_non_leaking_for_non_executive():
    es = executive_summary(ADV)
    assert es.get("authorized") is False and es.get("kpis") == {}


def test_unknown_dashboard_and_widget_return_none():
    assert compose_dashboard(EXEC, "nope") is None
    assert get_widget(EXEC, "nope") is None


# --- runtime gates -----------------------------------------------------------

def test_master_gate_disables(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: False)
    assert compose_dashboard(EXEC, "executive")["enabled"] is False


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    d = compose_dashboard(EXEC, "executive")
    assert d.get("denied") == "policy"


def test_widgets_gate_yields_empty_widget_list(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: name != "executive_widgets.enabled")
    board = compose_dashboard(EXEC, "executive")["dashboard"]
    assert board["widgets"] == []


# --- Client 360 / Household 360 integration ----------------------------------

def test_client360_executive_section_executive_only():
    import uuid

    from sqlalchemy import insert

    from app.db import engine, household_relationships, households, people
    from app.services.client360 import get_workspace
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"EX {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"C {suffix}",
                        active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(household_id=hid, person_id=pid,
                  relationship_type="head", is_primary=True, is_primary_household=True))
    caps = {"client.read", "record.read_all", "analytics.view", "analytics.executive", "tax.read",
            "insurance.read", "benefits.read", "opportunity.view", "documents.view", "compliance.review.read",
            "compliance.supervise", "communications.view", "timeline.read", "advisor_work.read", "work.read"}
    exec_p = Principal(1, "e@e.com", "E", frozenset(caps))
    adv_p = Principal(2, "a@e.com", "A", frozenset(caps - {"analytics.executive"}))
    ws = get_workspace(exec_p, person_id=pid)
    assert ws["sections"]["executive"]["source"] == "executive_intelligence"
    assert ws["sections"]["executive"]["not_a_second_analytics_engine"] is True
    ws_adv = get_workspace(adv_p, person_id=pid)
    assert "executive" not in ws_adv["sections"] and "executive" in ws_adv["suppressed_sections"]


# --- Advisor Workspace integration -------------------------------------------

def test_advisor_workspace_executive_insights_panel():
    from app.services.workspace.service import get_workspace as ws_home
    home = ws_home(EXEC)
    assert "executive_insights" in home and home["executive_insights"]["authorized"] is True
    home_adv = ws_home(ADV)
    assert home_adv["executive_insights"]["authorized"] is False   # no leak to non-executive


# --- AI grounding (summarize only) -------------------------------------------

def test_ai_executive_facts_only_for_executive():
    import uuid

    from sqlalchemy import insert

    from app.db import engine, household_relationships, households, people
    from app.services.ai_assist.context import assemble
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"AI {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"AI {suffix}",
                        active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(household_id=hid, person_id=pid,
                  relationship_type="head", is_primary=True, is_primary_household=True))
    caps = {"client.read", "record.read_all", "analytics.view", "tax.read", "insurance.read",
            "benefits.read", "opportunity.view", "documents.view", "compliance.review.read",
            "communications.view", "timeline.read", "advisor_work.read", "work.read"}
    exec_p = Principal(1, "e@e.com", "E", frozenset(caps | {"analytics.executive"}))
    adv_p = Principal(2, "a@e.com", "A", frozenset(caps))
    ef = [f for f in assemble(exec_p, "client_brief", person_id=pid).facts
          if f.source_type == "executive_intelligence"]
    assert all(isinstance(f.fact_value, (int, float, str)) for f in ef)   # summarized values only
    adv_ef = [f for f in assemble(adv_p, "client_brief", person_id=pid).facts
              if f.source_type == "executive_intelligence"]
    assert adv_ef == []   # no executive facts for a non-executive


# --- analytics reuse + diagnostics + governance ------------------------------

def test_reuses_single_analytics_registry_no_second_metrics():
    # The layer registers only operational COUNTERS into the existing Analytics Registry (no business
    # metrics, no second registry).
    from app.services.analytics.metrics import METRICS
    for k in ("executive_dashboards_composed", "executive_widgets_composed", "executive_widget_failures",
              "executive_authorization_failures"):
        assert k in METRICS
    import json
    assert "@e.com" not in json.dumps(metrics.reporting_metrics(EXEC))


def test_diagnostics_internal_shape():
    d = diagnostics.reporting_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "widget_compute_coverage", "governance"} <= set(d)
    assert d["governance"]["ok"] is True
    assert d["widget_compute_coverage"]["with_compute"] == d["widget_compute_coverage"]["total"]


def test_governance_clean():
    report = governance.validate_executive_reporting()
    assert report["ok"], report["findings"]


# --- architecture invariants -------------------------------------------------

def test_no_second_analytics_no_warehouse_no_mutation():
    import pathlib
    base = pathlib.Path("app/services/executive_intelligence")
    for pyfile in base.rglob("*.py"):
        src = pyfile.read_text()
        if pyfile.name == "governance.py":
            continue  # holds detection literals
        for banned in ("Table(", "define_", "write_audit_event(", "publisher.publish", "publish_safe(",
                       ".insert(", ".update(", ".delete(", "_DEFS ="):
            assert banned not in src, f"{banned} in {pyfile}"


def test_widget_values_flow_through_analytics_compute_metric():
    import pathlib
    src = pathlib.Path("app/services/executive_intelligence/widgets.py").read_text()
    assert "compute_metric" in src   # KPI values come from the single Analytics Registry


def test_stats_reset_and_note():
    stats.reset_stats()
    stats.note("dashboards_composed", dashboard="executive")
    assert stats.reporting_stats()["dashboards_composed"] == 1
