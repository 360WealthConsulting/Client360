"""Advisor Workspace home (Phase D.38) tests.

Covers the widget registry, the personalized ``get_workspace`` composition (greeting / today / priorities
/ widget grid), RBAC gating (a widget the principal cannot open is never assembled), personalization
(reorder / hide / show / pin / unpin / reset / presets — self-service, own-user only), the AI-ready
summary models (structure + record-scope enforcement), the routes (render, personalize gating, summary
JSON, out-of-scope 404), the route inventory + migration head, and the architecture invariants (view
state only — no business mutation, no bypass of RBAC, personalization gated by ``workspace.personalize``).
"""
import uuid

from sqlalchemy import delete, insert
from starlette.requests import Request

from app.db import engine, users, workspace_preferences, workspace_presets
from app.security.models import Principal
from app.services.workspace import preferences, service, summaries
from app.services.workspace.registry import WIDGETS, WidgetDef

FIRM_CAPS = frozenset({
    "client.read", "compliance.read", "tax.read", "insurance.read", "benefits.read",
    "exception.read", "task.read", "opportunity.read", "document.read", "capacity.read",
    "workspace.personalize", "record.read_all",
})


def _user(caps=FIRM_CAPS, *, display="Michael"):
    """Insert a throwaway user (preferences FK users.id) and return a Principal on it."""
    email = f"ws-{uuid.uuid4().hex[:12]}@example.test"
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=email, normalized_email=email, display_name=display).returning(users.c.id)).scalar_one()
    return Principal(uid, email, display, caps)


def _cleanup(uid):
    with engine.begin() as c:
        c.execute(delete(workspace_preferences).where(workspace_preferences.c.user_id == uid))
        c.execute(delete(workspace_presets).where(workspace_presets.c.user_id == uid))
        c.execute(delete(users).where(users.c.id == uid))


def _req(path="/workspace", qs=b""):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": qs})


# --- registry ----------------------------------------------------------------

def test_registry_widgets_are_valid():
    # The original 12 D.38 widgets, plus later phases may add more (e.g. D.39 work-queue widgets).
    assert len(WIDGETS) >= 12
    assert all(isinstance(w, WidgetDef) and w.capability and w.detail_href for w in WIDGETS.values())


def test_registry_widgets_have_distinct_compute_functions():
    from app.services.workspace.widgets import COMPUTE
    assert set(COMPUTE) == set(WIDGETS)               # a compute fn per widget
    assert len(set(COMPUTE.values())) == len(COMPUTE)  # no duplicate implementations


# --- composition + RBAC ------------------------------------------------------

def test_get_workspace_structure():
    p = _user()
    try:
        ws = service.get_workspace(p)
        assert ws["greeting"] in ("Good Morning", "Good Afternoon", "Good Evening")
        assert set(ws["today"]) == {"appointments", "compliance", "tax", "insurance", "benefits", "exceptions"}
        assert set(ws["priorities"]) >= {"high", "medium", "low", "total", "entries"}
        eligible = sum(1 for w in WIDGETS.values() if p.can(w.capability))
        assert len(ws["widgets"]) == eligible    # sees exactly the widgets it has capability for
        assert ws["can_personalize"] is True
    finally:
        _cleanup(p.user_id)


def test_widgets_are_capability_gated():
    # A principal with only client.read sees only the client.read widgets, never a 403-then-shown widget.
    p = _user(frozenset({"client.read"}))
    try:
        ws = service.get_workspace(p)
        keys = {w["key"] for w in ws["widgets"]}
        assert keys == {"calendar_today", "active_clients", "recent_activity"}
        assert ws["can_personalize"] is False
    finally:
        _cleanup(p.user_id)


def test_count_widgets_match_their_source():
    from app.services.analytics import sources
    p = _user()
    try:
        ws = service.get_workspace(p)
        by_key = {w["key"]: w for w in ws["widgets"]}
        assert by_key["active_clients"]["data"]["value"] == int(sources.client_count(p) or 0)
        assert by_key["compliance_queue"]["data"]["value"] == int(
            sources.projection_open_compliance_count(p) or 0)
    finally:
        _cleanup(p.user_id)


# --- personalization ---------------------------------------------------------

def test_hide_and_show_widget():
    p = _user()
    try:
        preferences.hide_widget(p.user_id, "benefits_pipeline")
        ws = service.get_workspace(p)
        assert "benefits_pipeline" not in {w["key"] for w in ws["widgets"]}
        assert "benefits_pipeline" in {h["key"] for h in ws["hidden_widgets"]}
        preferences.show_widget(p.user_id, "benefits_pipeline")
        assert "benefits_pipeline" in {w["key"] for w in service.get_workspace(p)["widgets"]}
    finally:
        _cleanup(p.user_id)


def test_pin_floats_to_top_and_overrides_hide():
    p = _user()
    try:
        preferences.pin_widget(p.user_id, "compliance_queue")
        assert service.get_workspace(p)["widgets"][0]["key"] == "compliance_queue"
        # pinning overrides hiding.
        preferences.hide_widget(p.user_id, "compliance_queue")
        keys = {w["key"] for w in service.get_workspace(p)["widgets"]}
        assert "compliance_queue" in keys
    finally:
        _cleanup(p.user_id)


def test_move_widget_reorders():
    p = _user()
    try:
        first = service.get_workspace(p)["widgets"][0]["key"]
        second = service.get_workspace(p)["widgets"][1]["key"]
        preferences.move_widget(p.user_id, second, "up")
        assert service.get_workspace(p)["widgets"][0]["key"] == second
        assert service.get_workspace(p)["widgets"][1]["key"] == first
    finally:
        _cleanup(p.user_id)


def test_reset_restores_defaults():
    p = _user()
    try:
        preferences.hide_widget(p.user_id, "tax_pipeline")
        preferences.pin_widget(p.user_id, "compliance_queue")
        preferences.reset(p.user_id)
        ws = service.get_workspace(p)
        eligible = sum(1 for w in WIDGETS.values() if p.can(w.capability))
        assert len(ws["widgets"]) == eligible and ws["widgets"][0]["key"] == next(iter(WIDGETS))
    finally:
        _cleanup(p.user_id)


def test_presets_save_apply_delete():
    p = _user()
    try:
        preferences.pin_widget(p.user_id, "compliance_queue")
        preferences.save_preset(p.user_id, "Compliance Day")
        presets = preferences.list_presets(p.user_id)
        assert [pr["name"] for pr in presets] == ["Compliance Day"]
        pid = presets[0]["id"]
        preferences.reset(p.user_id)
        assert service.get_workspace(p)["widgets"][0]["key"] != "compliance_queue"
        preferences.apply_preset(p.user_id, pid)
        assert service.get_workspace(p)["widgets"][0]["key"] == "compliance_queue"
        preferences.delete_preset(p.user_id, pid)
        assert preferences.list_presets(p.user_id) == []
    finally:
        _cleanup(p.user_id)


def test_presets_are_per_user():
    a, b = _user(), _user()
    try:
        preferences.save_preset(a.user_id, "Mine")
        assert preferences.list_presets(b.user_id) == []   # b cannot see a's preset
    finally:
        _cleanup(a.user_id)
        _cleanup(b.user_id)


# --- summaries ---------------------------------------------------------------

def test_daily_brief_shape():
    p = _user()
    try:
        brief = summaries.daily_brief(p)
        assert brief["kind"] == "daily_brief"
        assert set(brief["priorities"]) == {"high", "medium", "low", "total", "items"}
        assert "today" in brief and "attention" in brief
    finally:
        _cleanup(p.user_id)


def test_opportunity_and_compliance_summaries():
    p = _user()
    try:
        assert summaries.opportunity_summary(p)["kind"] == "opportunity_summary"
        cs = summaries.compliance_summary(p)
        assert cs["kind"] == "compliance_summary" and "open_total" in cs
    finally:
        _cleanup(p.user_id)


def test_client_summary_enforces_record_scope():
    # A scoped principal (no record.read_all, no assignments) cannot snapshot an arbitrary client.
    p = _user(frozenset({"client.read"}))
    try:
        assert summaries.client_snapshot(p, 999999999) is None
        assert summaries.meeting_prep(p, 999999999) is None
    finally:
        _cleanup(p.user_id)


# --- routes ------------------------------------------------------------------

def test_workspace_page_renders():
    from app.routes.workspace import workspace_dashboard
    p = _user()
    try:
        body = workspace_dashboard(_req(), principal=p).body.decode()
        assert "Good " in body and "widget-grid" in body and "Priorities" in body
        assert "Customize" in body   # personalize control shown for a personalizer
    finally:
        _cleanup(p.user_id)


def test_customize_control_hidden_without_capability():
    from app.routes.workspace import workspace_dashboard
    p = _user(frozenset({"client.read"}))
    try:
        body = workspace_dashboard(_req(), principal=p).body.decode()
        assert "/workspace?customize=1" not in body   # no personalize capability → no customize entry
    finally:
        _cleanup(p.user_id)


def test_summary_routes_return_json():
    import json

    from app.routes.workspace import summary_compliance, summary_daily
    p = _user()
    try:
        assert json.loads(bytes(summary_daily(principal=p).body))["kind"] == "daily_brief"
        assert json.loads(bytes(summary_compliance(principal=p).body))["kind"] == "compliance_summary"
    finally:
        _cleanup(p.user_id)


def test_client_summary_route_404_out_of_scope():
    from fastapi import HTTPException

    from app.routes.workspace import summary_client
    p = _user(frozenset({"client.read"}))
    try:
        raised = False
        try:
            summary_client(999999999, principal=p)
        except HTTPException as exc:
            raised = exc.status_code == 404
        assert raised
    finally:
        _cleanup(p.user_id)


# --- inventory + invariants --------------------------------------------------

def test_route_inventory():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/workspace", "/workspace/customize", "/workspace/presets", "/workspace/reset",
            "/workspace/summaries/daily", "/workspace/summaries/opportunities",
            "/workspace/summaries/compliance", "/workspace/summaries/client/{person_id}",
            "/workspace/summaries/meeting/{person_id}"} <= paths


def test_total_route_count():
    from app.main import app
    assert len(app.routes) == 906


def test_workspace_personalization_tables_exist():
    # D.38 added the workspace personalization tables — a durable invariant (the head moves each phase).
    from app.db import metadata
    assert "workspace_preferences" in metadata.tables
    assert "workspace_presets" in metadata.tables


def test_personalization_touches_only_workspace_tables():
    # The personalization store must only write its own view-state tables — never a business table.
    import inspect

    from app.services.workspace import preferences as prefs_mod
    src = inspect.getsource(prefs_mod)
    assert "workspace_preferences" in src and "workspace_presets" in src
    # no import of authoritative domain tables into the personalization store.
    for banned in ("import people", "import opportunities", "import compliance_reviews", "import tasks"):
        assert banned not in src


def test_capability_seeded():
    from sqlalchemy import text
    with engine.connect() as c:
        row = c.execute(text("SELECT sensitive FROM capabilities WHERE code = 'workspace.personalize'")
                        ).scalar()
    assert row is False   # personalization is a non-sensitive, self-service capability
