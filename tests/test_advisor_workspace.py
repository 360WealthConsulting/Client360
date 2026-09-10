"""Advisor Workspace Daily Dashboard tests (Phase D.1).

Covers authorization (capability + book-scope, not firm-wide), record-level scope
filtering (inaccessible clients excluded from EVERY panel), meetings-today date
filtering, empty states, population from existing services, deep links, and the
absence of any policy-gated Advisor Intelligence content.
"""
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, insert
from starlette.requests import Request

from app.db import (
    accounts,
    engine,
    exception_types,
    exceptions,
    people,
    record_assignments,
    tasks,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.advisor_workspace import FIRM_TZ, get_daily_dashboard

ADVISOR_CAPS = frozenset({"client.read", "work.read", "exception.read", "task.read"})


def _req(path="/workspace"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def _tax_type_id(conn):
    return conn.execute(exception_types.select().where(exception_types.c.domain == "tax")).mappings().first()["id"]


def _seed_client(conn, *, tag, now, assigned_to=None, meetings=("today",)):
    """Create a person and seed one row in every panel's source. Returns person id."""
    pid = conn.execute(people.insert().values(
        full_name=f"Client {tag}", primary_email=f"{tag}@example.test",
        normalized_email=f"{tag}@example.test", active=True).returning(people.c.id)).scalar_one()
    today = now.date()
    offsets = {"today": 0, "yesterday": -1, "tomorrow": 1}
    for label in meetings:
        when = datetime.combine(today + timedelta(days=offsets[label]), time(10, 0), tzinfo=FIRM_TZ)
        conn.execute(insert(timeline_events).values(
            person_id=pid, source="microsoft", event_type="calendar_event",
            title=f"Meeting {tag} {label}", event_time=when))
    # recent activity (a note event)
    conn.execute(insert(timeline_events).values(
        person_id=pid, source="staff", event_type="note_added",
        title=f"Note {tag}", event_time=now - timedelta(hours=1)))
    # account due for review (no last_review_date)
    conn.execute(insert(accounts).values(
        person_id=pid, custodian="Schwab", account_number=f"ACCT-{tag}",
        account_name=f"Acct {tag}", status="open", last_review_date=None))
    # open tax exception
    conn.execute(insert(exceptions).values(
        exception_type_id=_tax_type_id(conn), domain="tax", category="client",
        severity="high", status="open", title=f"Exc {tag}", source="system",
        opened_at=now, escalation_level=0, notification_count=0, person_id=pid))
    # open task
    conn.execute(insert(tasks).values(
        person_id=pid, title=f"Task {tag}", status="open", priority="normal"))
    if assigned_to is not None:
        conn.execute(insert(record_assignments).values(
            user_id=assigned_to, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=today))
    return pid


def _setup():
    """Advisor U with assigned person A and unassigned person B, both fully seeded."""
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(FIRM_TZ)
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"adv-{tag}@example.test", normalized_email=f"adv-{tag}@example.test",
            display_name=f"Advisor {tag}", status="active").returning(users.c.id)).scalar_one()
        a = _seed_client(conn, tag=f"A{tag}", now=now, assigned_to=uid,
                         meetings=("today", "yesterday", "tomorrow"))
        b = _seed_client(conn, tag=f"B{tag}", now=now, assigned_to=None, meetings=("today",))
    principal = Principal(uid, f"adv-{tag}@example.test", f"Advisor {tag}", ADVISOR_CAPS)
    return {"uid": uid, "a": a, "b": b, "now": now, "tag": tag, "principal": principal}


def _teardown(ids):
    with engine.begin() as conn:
        for pid in (ids["a"], ids["b"]):
            conn.execute(delete(exceptions).where(exceptions.c.person_id == pid))
            conn.execute(delete(tasks).where(tasks.c.person_id == pid))
            conn.execute(delete(timeline_events).where(timeline_events.c.person_id == pid))
            conn.execute(delete(accounts).where(accounts.c.person_id == pid))
        conn.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        conn.execute(delete(people).where(people.c.id.in_((ids["a"], ids["b"]))))
        # Intentionally NOT deleting the created user: the shared (un-isolated)
        # test DB relies on persistent users (e.g. hardcoded created_by_user_id=1),
        # so deleting a user cascades FK failures into unrelated tests.


# --- authorization -----------------------------------------------------------

def test_workspace_requires_client_read_and_is_not_firm_wide():
    from app.security.middleware import FIRM_WIDE_COLLECTION, RULES
    cap = next((code for pat, code in RULES if pat.search("/workspace")), None)
    assert cap == "client.read"
    # Book-scoped, NOT a firm-wide collection (advisors without record.read_all get it).
    assert not FIRM_WIDE_COLLECTION.match("/workspace")


def test_nav_shows_workspace_for_client_read_only_advisor():
    from app.templating import templates
    advisor = Principal(1, "a@e.com", "A", frozenset({"client.read"}))
    html = templates.env.get_template("base.html").render(request=_req(), principal=advisor)
    assert 'href="/workspace"' in html
    # A principal without client.read does not see it.
    nobody = Principal(2, "b@e.com", "B", frozenset({"work.read"}))
    html2 = templates.env.get_template("base.html").render(request=_req(), principal=nobody)
    assert 'href="/workspace"' not in html2


CATEGORIES = ("priorities", "widgets", "attention", "meetings", "reviews",
              "tasks", "exceptions", "intelligence", "activity")


def _render_workspace():
    """The rendered page, not the template source: an anchor that resolves in the file but not in
    the output is exactly the defect these assertions exist to catch.

    ``install_globals_on_all_templates`` is what ``app.main`` calls once every router is imported,
    and it is what registers ``datefmt`` on the instance this route owns. Calling it here makes
    these tests independent of whether some earlier test in the run happened to import app.main."""
    from app.routes.workspace import workspace_dashboard
    from app.templating import install_globals_on_all_templates
    install_globals_on_all_templates()
    advisor = Principal(1, "a@e.com", "A", ADVISOR_CAPS)
    return workspace_dashboard(_req(), principal=advisor).body.decode()


def test_every_category_link_has_a_target_that_exists():
    html = _render_workspace()
    assert 'aria-label="Workspace categories"' in html
    for category in CATEGORIES:
        assert f'href="#workspace-{category}"' in html, f"{category} has no navigator link"
        assert f'id="workspace-{category}"' in html, f"#workspace-{category} resolves to nothing"
    assert html.count("workspace-category workspace-anchor-section") == len(CATEGORIES)


def test_the_navigator_is_a_list_of_real_links_so_it_is_keyboard_reachable():
    """Anchors, not click handlers: Tab reaches them and Enter follows them with no JS at all.

    The bar is also the only always-visible index of the page once the sections collapse, so it
    must be a labelled landmark rather than a decorative strip."""
    html = _render_workspace()
    nav = html.split('aria-label="Workspace categories"', 1)[1].split("</nav>", 1)[0]
    assert nav.count("<a href=") == len(CATEGORIES)
    assert "onclick" not in nav and "javascript:" not in nav
    assert "<button" not in nav                       # a button would need script to navigate


def test_priorities_is_open_on_arrival_and_the_rest_collapse():
    """The compaction must not cost the advisor the one panel they came for.

    Everything else starts closed — that is the point of the redesign — but a collapsed
    Priorities means a page that answers "is anything on fire?" only after a click."""
    html = _render_workspace()
    priorities = html.split('id="workspace-priorities"', 1)[1].split("</summary>", 1)[0]
    assert priorities.lstrip().startswith('class="workspace-category workspace-anchor-section" open')
    for category in CATEGORIES:
        if category == "priorities":
            continue
        opening = html.split(f'id="workspace-{category}"', 1)[1].split(">", 1)[0]
        assert " open" not in opening, f"{category} is a secondary section and should collapse"


def test_collapsing_uses_native_details_rather_than_hidden_content():
    """<details>/<summary> keeps a collapsed section findable by in-page search and reachable by
    assistive technology. Content hidden with a class or `display:none` is neither."""
    html = _render_workspace()
    for category in CATEGORIES:
        before = html.split(f'id="workspace-{category}"', 1)[0]
        assert before.rstrip().endswith("<details"), f"{category} is not a native details element"
    assert "<summary>" in html


def test_the_long_lists_scroll_inside_their_own_panel():
    """Contained scrolling is what keeps the page compact when a category holds hundreds of rows.
    Priorities, Tasks, Exceptions and Activity are the four that grow without bound."""
    html = _render_workspace()
    for category in ("priorities", "tasks", "exceptions", "activity"):
        body = html.split(f'id="workspace-{category}"', 1)[1].split("</details>", 1)[0]
        assert "workspace-scroll-panel" in body, f"{category} can grow without bound"
    css = (Path(__file__).parents[1] / "app/static/css/workspace.css").read_text(encoding="utf-8")
    assert "max-height" in css and "overflow-y: auto" in css
    assert "overscroll-behavior: contain" in css      # scrolling a panel must not scroll the page


def test_the_workspace_is_usable_on_a_narrow_screen():
    """The navigator scrolls sideways rather than wrapping into a wall of chips, and the layout
    has an explicit narrow-screen rule instead of relying on the desktop padding."""
    css = (Path(__file__).parents[1] / "app/static/css/workspace.css").read_text(encoding="utf-8")
    nav_rule = css.split(".workspace-category-nav {", 1)[1].split("}", 1)[0]
    assert "overflow-x: auto" in nav_rule
    assert "position: sticky" in nav_rule             # stays reachable while the page scrolls
    assert "@media (max-width: 700px)" in css


def test_no_priority_information_is_hidden_behind_a_click():
    """The counts on every summary mean a collapsed category still reports how much it holds, so
    nothing disappears silently when the page compacts."""
    html = _render_workspace()
    for category in ("priorities", "attention", "tasks", "exceptions"):
        summary = html.split(f'id="workspace-{category}"', 1)[1].split("</summary>", 1)[0]
        assert "workspace-category-count" in summary, f"{category} collapses without a count"


def test_route_renders_for_authorized_principal():
    from app.routes.workspace import workspace_dashboard
    advisor = Principal(1, "a@e.com", "A", ADVISOR_CAPS)
    resp = workspace_dashboard(_req(), principal=advisor)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Advisor Workspace" in resp.body.decode()


# --- population + record-level scope -----------------------------------------

def test_accessible_client_populates_panels_inaccessible_excluded():
    ids = _setup()
    a, b = ids["a"], ids["b"]
    try:
        d = get_daily_dashboard(ids["principal"], now=ids["now"])
        # Every panel includes the accessible client A and excludes B.
        assert any(r["person_id"] == a for r in d["attention"])
        assert any(m["person_id"] == a for m in d["meetings"])
        assert any(r["person_id"] == a for r in d["reviews"])
        assert any(t["person_id"] == a for t in d["tasks"])
        assert any(e["person_id"] == a for e in d["exceptions"])
        assert any(ev["person_id"] == a for ev in d["activity"])
        # B (unassigned) must not appear in ANY panel.
        for panel in ("attention", "meetings", "reviews", "tasks", "exceptions", "activity"):
            assert all(row.get("person_id") != b for row in d[panel]), f"{panel} leaked inaccessible client"
            assert all(f"/people/{b}" not in (row.get("link") or "") for row in d[panel]), f"{panel} leaked link"
    finally:
        _teardown(ids)


def test_meetings_today_date_filtering():
    ids = _setup()
    try:
        d = get_daily_dashboard(ids["principal"], now=ids["now"])
        titles = [m["title"] for m in d["meetings"]]
        # A has today/yesterday/tomorrow meetings; only today's is in the panel.
        assert any("today" in t for t in titles)
        assert all("yesterday" not in t and "tomorrow" not in t for t in titles)
    finally:
        _teardown(ids)


def test_empty_state_for_advisor_with_no_book():
    from app.routes.workspace import workspace_dashboard
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"empty-{tag}@example.test", normalized_email=f"empty-{tag}@example.test",
            display_name="Empty", status="active").returning(users.c.id)).scalar_one()
    try:
        principal = Principal(uid, "e@e.com", "Empty", ADVISOR_CAPS)  # no assignments
        d = get_daily_dashboard(principal)
        for panel in ("attention", "meetings", "reviews", "tasks", "exceptions", "activity"):
            assert d[panel] == [], f"{panel} should be empty for an advisor with no book"
        body = workspace_dashboard(_req(), principal=principal).body.decode()
        assert "Nothing needs attention" in body
        assert "No meetings today" in body
    finally:
        # User intentionally left in place (see _teardown note); it owns no other rows.
        pass


def test_no_advisor_intelligence_content_rendered():
    from app.routes.workspace import workspace_dashboard
    ids = _setup()
    try:
        body = workspace_dashboard(_req(), principal=ids["principal"]).body.decode().lower()
        for banned in ("recommend", "roth", "conversion", "opportunit", "cross-sell",
                       "coverage gap", "suitab", "retirement readiness", "estate planning"):
            assert banned not in body, f"policy-gated intelligence content leaked: {banned}"
    finally:
        _teardown(ids)


def test_deep_links_point_at_existing_authoritative_routes():
    ids = _setup()
    a = ids["a"]
    try:
        d = get_daily_dashboard(ids["principal"], now=ids["now"])
        assert any(r["link"] == f"/people/{a}" for r in d["attention"])
        assert any(r["link"] == f"/people/{a}" for r in d["reviews"])
        # links are person/household routes or the domain console
        for e in d["exceptions"]:
            assert e["link"].startswith("/people/") or e["link"] == "/exceptions"
    finally:
        _teardown(ids)
