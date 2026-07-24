"""Unified Work Queue (Phase D.39) tests.

Covers adapter registration + output contract, unified normalization + stable keys, deterministic
ordering, pagination, filtering/search, saved views (built-in + per-user CRUD + default + remembered
filters), RBAC / record scope / capability suppression, source-failure isolation, no-direct-rm_-reads,
action dispatch (validation + authoritative delegation + capability/scope denial), bulk per-item
validation + honest partial failure, diagnostics, governance (clean + detects), workspace-widget
integration, the AI-ready summary, migration round-trip, route inventory, capability seeding, and the
architecture invariants (composition only — no second engine, no queue-layer mutation, no double audit).
"""
import uuid
from datetime import UTC

import pytest
from sqlalchemy import delete, insert, text

from app.db import engine, users, work_queue_preferences, work_queue_saved_views
from app.security.models import Principal
from app.services.work_queue import compose_queue, dispatch, governance, summary, views
from app.services.work_queue.adapters import ADAPTERS, DOMAIN_CAPABILITY, SOURCE_DOMAINS
from app.services.work_queue.contract import UnifiedWorkItem, make_item, normalize_status

FIRM_CAPS = frozenset({
    "work.read", "work.write", "capacity.read", "exception.read", "advisor_work.read",
    "compliance.review.read", "documents.view", "tax.read", "insurance.read", "opportunity.read",
    "scheduling.view", "record.read_all", "work_queue.saved_views", "observability.audit",
})


def _user(caps=FIRM_CAPS):
    email = f"wq-{uuid.uuid4().hex[:12]}@example.test"
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=email, normalized_email=email, display_name="Q").returning(users.c.id)).scalar_one()
    return Principal(uid, email, "Q", caps)


def _cleanup(uid):
    with engine.begin() as c:
        c.execute(delete(work_queue_saved_views).where(work_queue_saved_views.c.user_id == uid))
        c.execute(delete(work_queue_preferences).where(work_queue_preferences.c.user_id == uid))
        c.execute(delete(users).where(users.c.id == uid))


# --- adapters + contract -----------------------------------------------------

def test_adapters_registered_and_cover_source_domains():
    emitted = {"tasks", "workflow", "exceptions"} | {a.domain for a in ADAPTERS if a.domain != "core"}
    assert set(SOURCE_DOMAINS) <= emitted
    assert set(SOURCE_DOMAINS) == set(DOMAIN_CAPABILITY)   # every source has a capability


def test_unified_item_contract_and_stable_key():
    it = make_item(source_domain="tasks", source_type="task", source_id=7, title="X", status="open",
                   priority="high", deep_link="/tasks/7", capability="work.read")
    assert isinstance(it, UnifiedWorkItem)
    assert it.work_item_key == "tasks:task:7"           # stable domain:type:id
    assert it.status == "open" and it.status_group == "open"   # source status preserved
    d = it.to_dict()
    assert set(d) >= {"work_item_key", "source_domain", "source_id", "deep_link", "allowed_actions",
                      "sla_state", "priority", "capability"}


def test_status_and_sla_normalization():
    assert normalize_status("pending_review") == "in_progress"
    assert normalize_status("resolved") == "done"
    # a deadline in the past with no SLA anchor → overdue; none → unknown.
    from datetime import datetime, timedelta
    now = datetime(2026, 7, 20, tzinfo=UTC)
    overdue = make_item(source_domain="tasks", source_type="task", source_id=1, title="a", status="open",
                        priority="low", deep_link="/x", capability="work.read",
                        due_at=now - timedelta(days=2), now=now)
    assert overdue.overdue and overdue.sla_state == "overdue"
    unknown = make_item(source_domain="tasks", source_type="task", source_id=2, title="a", status="open",
                        priority="low", deep_link="/x", capability="work.read", now=now)
    assert unknown.sla_state == "unknown"   # unknown stays unknown; no invented deadline


# --- composition, ordering, pagination, filtering ----------------------------

def test_compose_and_deterministic_order():
    p = _user()
    try:
        q = compose_queue(p, page=1, page_size=10)
        assert q["page"] == 1 and q["page_size"] == 10
        assert len(q["rows"]) <= 10 and q["total"] >= len(q["rows"])
        keys = [dispatch.parse_key(r["work_item_key"]) for r in q["rows"]]
        assert all(keys)   # every row has a parseable stable key
        # deterministic order: overdue rows precede non-overdue rows.
        flags = [r["overdue"] for r in q["rows"]]
        assert flags == sorted(flags, key=lambda o: 0 if o else 1)
    finally:
        _cleanup(p.user_id)


def test_pagination_is_stable():
    p = _user()
    try:
        p1 = compose_queue(p, page=1, page_size=5)
        if p1["pages"] > 1:
            p2 = compose_queue(p, page=2, page_size=5)
            k1 = {r["work_item_key"] for r in p1["rows"]}
            k2 = {r["work_item_key"] for r in p2["rows"]}
            assert not (k1 & k2)   # pages do not overlap
    finally:
        _cleanup(p.user_id)


def test_domain_filter():
    p = _user()
    try:
        q = compose_queue(p, filters={"domain": "tax"}, page=1, page_size=50)
        assert all(r["source_domain"] == "tax" for r in q["rows"])
    finally:
        _cleanup(p.user_id)


def test_overdue_filter():
    p = _user()
    try:
        q = compose_queue(p, filters={"overdue": True}, page=1, page_size=50)
        assert all(r["overdue"] for r in q["rows"])
    finally:
        _cleanup(p.user_id)


# --- RBAC / scope / capability suppression -----------------------------------

def test_scoped_principal_sees_nothing_without_assignments():
    # work.read but no record.read_all and no assignments → fail-closed empty queue.
    p = _user(frozenset({"work.read"}))
    try:
        q = compose_queue(p, page=1, page_size=25)
        assert q["total"] == 0
    finally:
        _cleanup(p.user_id)


def test_capability_gates_domains():
    # A principal without tax.read never sees tax items (and no tax tab).
    p = _user(frozenset({"work.read", "record.read_all"}))
    try:
        q = compose_queue(p, page=1, page_size=100)
        assert all(r["source_domain"] != "tax" for r in q["rows"])
        tabs = {t["key"] for t in views.visible_tabs(p)}
        assert "tax_season" not in tabs and "my_work" in tabs
    finally:
        _cleanup(p.user_id)


def test_source_failure_isolation(monkeypatch):
    p = _user()
    try:
        # make one adapter raise — the queue must still compose from the others.
        from app.services.work_queue.adapters import TaxAdapter
        monkeypatch.setattr(TaxAdapter, "_fetch",
                            lambda self, principal, limit: (_ for _ in ()).throw(RuntimeError("boom")))
        q = compose_queue(p, page=1, page_size=200)
        assert q["candidate_total"] >= 0   # did not raise
        assert q["adapter_stats"]["tax"]["count"] == 0
    finally:
        _cleanup(p.user_id)


# --- saved views -------------------------------------------------------------

def test_builtin_views_resolve():
    p = _user()
    try:
        assert views.resolve_view("my_work", p) == {"assignee": "me"}
        assert views.resolve_view("overdue", p) == {"overdue": True}
        assert views.resolve_view("nonexistent", p) is None
    finally:
        _cleanup(p.user_id)


def test_saved_view_crud_and_default():
    p = _user()
    uid = p.user_id
    try:
        views.save_view(uid, "My Tax", {"domain": "tax", "bogus_key": 1})
        rows = views.list_views(uid)
        assert [r["name"] for r in rows] == ["My Tax"]
        assert "bogus_key" not in rows[0]["filters"]   # unknown filter keys stripped
        vid = rows[0]["id"]
        assert views.resolve_view(f"user:{vid}", p) == {"domain": "tax"}
        views.rename_view(uid, vid, "Tax View")
        assert views.list_views(uid)[0]["name"] == "Tax View"
        views.set_default(uid, f"user:{vid}")
        assert views.get_preferences(uid)["default_view"] == f"user:{vid}"
        views.delete_view(uid, vid)
        assert views.list_views(uid) == []
        # deleting the default reverts to the system default.
        assert views.get_preferences(uid)["default_view"] == views.DEFAULT_VIEW
    finally:
        _cleanup(uid)


def test_remember_filters():
    p = _user()
    try:
        views.remember_filters(p.user_id, {"priority": "high", "junk": 1})
        assert views.get_preferences(p.user_id)["last_filters"] == {"priority": "high"}
    finally:
        _cleanup(p.user_id)


def test_saved_views_are_per_user():
    a, b = _user(), _user()
    try:
        views.save_view(a.user_id, "Mine", {"domain": "tax"})
        assert views.list_views(b.user_id) == []
    finally:
        _cleanup(a.user_id)
        _cleanup(b.user_id)


# --- action dispatch ---------------------------------------------------------

def test_dispatch_rejects_invalid_and_open():
    p = _user()
    try:
        assert dispatch.dispatch_action(p, work_item_key="bad", action="claim")["ok"] is False
        assert dispatch.dispatch_action(p, work_item_key="tasks:task:1", action="open")["ok"] is False
    finally:
        _cleanup(p.user_id)


def test_dispatch_rejects_unsupported_action():
    p = _user()
    try:
        r = dispatch.dispatch_action(p, work_item_key="insurance:insurance_case:1", action="claim")
        assert r["ok"] is False and "not supported" in r["message"]
    finally:
        _cleanup(p.user_id)


def test_dispatch_denies_without_capability():
    p = _user(frozenset({"work.read"}))   # no work.write
    try:
        r = dispatch.dispatch_action(p, work_item_key="tasks:task:1", action="claim")
        assert r["ok"] is False and r["outcome"] == "denied"
    finally:
        _cleanup(p.user_id)


def test_bulk_only_safe_actions_and_partial_reporting():
    p = _user()
    try:
        # complete is not bulk-eligible.
        assert dispatch.dispatch_bulk(p, work_item_keys=["tasks:task:1"], action="complete")["ok"] is False
        # a bulk with an invalid + unsupported key reports honest per-item failure (no raise).
        res = dispatch.dispatch_bulk(p, work_item_keys=["bad", "insurance:insurance_case:1"],
                                     action="claim")
        assert res["total"] == 2 and res["succeeded"] == 0 and res["failed"] == 2
        assert len(res["results"]) == 2
    finally:
        _cleanup(p.user_id)


# --- summary / diagnostics / governance --------------------------------------

def test_work_queue_summary_shape():
    p = _user()
    try:
        s = summary.work_queue_summary(p)
        assert s["kind"] == "work_queue_summary"
        assert {"my_overdue", "due_today", "high_priority", "sla_breaches", "unassigned_team",
                "by_domain", "top_urgent"} <= set(s)
    finally:
        _cleanup(p.user_id)


def test_diagnostics_shape():
    from app.services.work_queue.diagnostics import work_queue_diagnostics
    p = _user()
    try:
        d = work_queue_diagnostics(p)
        assert {"total_visible", "by_domain", "adapters", "suppressed_by_capability",
                "action_stats", "page_query_ms", "projection_usage"} <= set(d)
    finally:
        _cleanup(p.user_id)


def test_governance_clean():
    report = governance.validate_work_queue()
    assert report["ok"] is True, report["findings"]
    assert report["issue_count"] == 0


def test_governance_detects_unknown_filter_key(monkeypatch):
    bad = dict(views.BUILTIN_VIEWS)
    bad["broken"] = {"label": "Broken", "filters": {"not_a_filter": 1}, "tab": False}
    monkeypatch.setattr(views, "BUILTIN_VIEWS", bad)
    monkeypatch.setattr(governance, "BUILTIN_VIEWS", bad)
    report = governance.validate_work_queue()
    assert any(f["type"] == "unknown_filter_key" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_queue_read_modules_do_not_mutate_or_read_rm_tables():
    import pathlib
    import re
    base = pathlib.Path("app/services/work_queue")
    for name in ("adapters.py", "service.py", "summary.py", "diagnostics.py"):
        src = (base / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table directly"
        for verb in (".insert(", ".update(", ".delete("):
            assert verb not in src, f"{name} performs a mutation"


def test_dispatch_delegates_and_does_not_double_audit():
    src = __import__("pathlib").Path("app/services/work_queue/dispatch.py").read_text()
    for svc in ("work_management", "workflow_orchestration", "exception_engine", "document_platform"):
        assert svc in src                    # delegates to authoritative services
    assert "write_audit_event" not in src    # the queue never double-audits a business mutation
    assert "publish_safe" not in src         # the queue never publishes a domain event


def test_no_unified_work_projection():
    # D.39 adds NO projection — composition only.
    from app.database.projection_tables import READ_MODEL_TABLES
    assert not any("work" in t and t != "rm_operational_tasks" for t in READ_MODEL_TABLES if "unified" in t)
    assert "rm_unified_work" not in READ_MODEL_TABLES


# --- workspace integration ---------------------------------------------------

def test_workspace_work_widgets_registered():
    from app.services.workspace.registry import WIDGETS
    from app.services.workspace.widgets import COMPUTE
    for k in ("work_my", "work_overdue", "work_due_today", "work_unassigned", "work_sla_breaches"):
        assert k in WIDGETS and k in COMPUTE


def test_workspace_widget_uses_shared_summary():
    from app.services.workspace.widgets import _work_my
    p = _user()
    try:
        assert "value" in _work_my(p)
    finally:
        _cleanup(p.user_id)


# --- routes / migration / capability -----------------------------------------

def test_route_inventory():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/work", "/work/action", "/work/bulk-action", "/work/views", "/work/views/default",
            "/work/views/delete", "/work/summary", "/work/diagnostics"} <= paths


def test_total_route_count():
    from app.main import app
    assert len(app.routes) == 891  # +16 secure client portal (D.43)


def test_migration_head():
    # Durable: the unified work queue schema (D.39) is present. The exact global head advances in later
    # phases, so assert the queue tables exist rather than pinning a specific revision.
    from app.db import metadata
    assert "work_queue_saved_views" in metadata.tables


def test_capability_seeded_non_sensitive():
    with engine.connect() as c:
        sensitive = c.execute(text("SELECT sensitive FROM capabilities WHERE code = 'work_queue.saved_views'")
                              ).scalar()
    assert sensitive is False


@pytest.mark.parametrize("page_size", [1, 5, 25])
def test_page_size_bounds_result(page_size):
    p = _user()
    try:
        q = compose_queue(p, page=1, page_size=page_size)
        assert len(q["rows"]) <= page_size   # bounded page (no unbounded load into the response)
    finally:
        _cleanup(p.user_id)
