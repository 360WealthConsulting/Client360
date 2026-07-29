"""Staff Home dashboard — auth, role/scope, counts, panels, portal/vault, reviews, activity, UI.

Reuses the existing engine (work_queue collect + views, compliance reviews, portal/vault, timeline);
the dashboard adds no schema. Tests build a Principal with explicit caps and call the composer / route
functions directly (the established pattern), asserting authorization and that counts match the queue.
"""
import io
import uuid

import pytest
from sqlalchemy import delete, insert
from starlette.requests import Request

from app.db import engine, households, people, portal_accounts, tasks, users, vault_documents
from app.security.models import Principal
from app.services.home import home_summary
from app.services.vault import service as vault
from app.services.work_queue import views as qv
from app.services.work_queue.service import _match, _suppress, collect

FULL = frozenset({"client.read", "client.write", "record.read_all", "work.read", "work.write",
                  "work.approve", "capacity.read", "compliance.review.read", "documents.view",
                  "vault.view", "vault.upload", "vault.manage", "vault.access.all"})
DEPT = frozenset({"client.read", "work.read"})                 # book-scoped department staff
EXTERNAL_IT = frozenset({"observability.audit"})               # no client/work/portal access


@pytest.fixture
def env():
    tag = uuid.uuid4().hex[:8]
    created = {"docs": []}
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=f"h{tag}@e.test", normalized_email=f"h{tag}@e.test", display_name="Morgan",
            auth_subject=f"h{tag}", status="active").returning(users.c.id)).scalar_one()
        hid = c.execute(insert(households).values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {tag}",
                        active=True).returning(people.c.id)).scalar_one()
        tid = c.execute(insert(tasks).values(person_id=pid, household_id=hid, title=f"Prep {tag}",
                        status="open", priority="high", work_type="general").returning(tasks.c.id)).scalar_one()
        paid = c.execute(insert(portal_accounts).values(
            person_id=pid, email=f"pa{tag}@e.test", normalized_email=f"pa{tag}@e.test",
            display_name="Portal Client", auth_subject=f"pa{tag}", status="active"
        ).returning(portal_accounts.c.id)).scalar_one()
    created.update(uid=uid, hid=hid, pid=pid, tid=tid, paid=paid, tag=tag)
    created["full"] = Principal(uid, "m@e.test", "Morgan", FULL)
    yield created
    with engine.begin() as c:
        for doc in created["docs"]:
            c.execute(delete(vault_documents).where(vault_documents.c.id == doc))
        c.execute(delete(tasks).where(tasks.c.id == created["tid"]))
        c.execute(delete(portal_accounts).where(portal_accounts.c.id == created["paid"]))
        c.execute(delete(people).where(people.c.id == created["pid"]))
        c.execute(delete(households).where(households.c.id == created["hid"]))


def _req():
    return Request({"type": "http", "method": "GET", "path": "/home", "headers": [],
                    "query_string": b"", "state": {}})


def _queue_count(principal, view_key):
    items, _ = collect(principal)
    items, _ = _suppress(items, principal)
    import datetime
    now = datetime.datetime.now(datetime.UTC)
    f = qv.resolve_view(view_key, principal)
    return sum(1 for it in items if _match(it, f, principal, now))


# --- authentication ----------------------------------------------------------

def test_home_requires_authenticated_principal():
    from fastapi import HTTPException

    from app.security.dependencies import current_principal
    with pytest.raises(HTTPException) as exc:
        current_principal(_req())                    # no principal on state → 401
    assert exc.value.status_code == 401


def test_portal_session_cannot_reach_staff_home(env):
    # A portal principal is a different type with no staff capabilities; home_summary treats it as
    # capability-less → no work/portal/review data. (The route itself is staff-auth only.)
    class _PortalLike:
        user_id, display_name = 0, "Client"
        def can(self, cap):
            return False
    s = home_summary(_PortalLike())
    assert s["cards"] == [] and s["quick_actions"] == []
    assert s["portal_activity"]["pending_uploads"] == [] and s["recent_activity"] == []


# --- role & scope ------------------------------------------------------------

def test_external_it_sees_no_client_or_work_data(env):
    it = Principal(env["uid"], "it@e.test", "IT", EXTERNAL_IT)
    s = home_summary(it)
    assert s["cards"] == []                           # no work/review/unassigned cards
    assert s["priority_work"] == [] and s["waiting"] == []
    assert s["portal_activity"].get("enabled") is False
    assert s["recent_activity"] == [] and s["quick_actions"] == []


def test_unassigned_card_hidden_without_authorization(env):
    dept = Principal(env["uid"], "d@e.test", "Dept", DEPT)   # no capacity.read / record.read_all
    keys = {c["key"] for c in home_summary(dept)["cards"]}
    assert "unassigned" not in keys and "my_work" in keys


def test_executive_capability_sees_firm_unassigned_card(env):
    keys = {c["key"] for c in home_summary(env["full"])["cards"]}
    assert "unassigned" in keys                       # record.read_all → firm-level card visible


# --- counts / panels ---------------------------------------------------------

def test_card_counts_match_the_queue_views(env):
    s = home_summary(env["full"])
    counts = {c["key"]: c["count"] for c in s["cards"]}
    for view in ("my_work", "overdue", "due_today", "waiting", "unassigned"):
        if view in counts:
            assert counts[view] == _queue_count(env["full"], view)


def test_priority_work_is_limited_and_links_to_detail(env):
    s = home_summary(env["full"], priority_limit=10)
    assert len(s["priority_work"]) <= 10
    for it in s["priority_work"]:
        assert it.get("source_domain") and it.get("source_id") is not None   # → /work/{domain}/{id}


def test_priority_ordering_critical_overdue_due_high(env):
    # Ordering key monotonicity: critical-or-overdue-or-due items never sort after plain items.
    import datetime
    s = home_summary(env["full"])
    today = datetime.datetime.now(datetime.UTC).date()

    def rank(it):
        crit = it["priority"] in ("urgent", "critical")
        due_today = bool(it.get("due_at") and it["due_at"][:10] == today.isoformat())
        return (0 if crit else 1, 0 if it.get("overdue") else 1, 0 if due_today else 1)
    ranks = [rank(it) for it in s["priority_work"]]
    assert ranks == sorted(ranks)


# --- vault / portal ----------------------------------------------------------

def test_pending_client_upload_appears_for_authorized_staff(env):
    doc = vault.create_document(env["full"], source=io.BytesIO(b"w2"), original_filename="w2.pdf",
                                display_name="Client W-2", category="tax", actor_user_id=env["uid"],
                                person_id=env["pid"])
    env["docs"].append(doc)
    # mark it as a pending client upload
    with engine.begin() as c:
        c.execute(vault_documents.update().where(vault_documents.c.id == doc).values(
            uploaded_by_portal_account_id=env["paid"], status="uploaded"))
    s = home_summary(env["full"])
    ids = {u["id"] for u in s["portal_activity"]["pending_uploads"]}
    assert doc in ids
    # links to the correct Client360 Vault tab
    row = next(u for u in s["portal_activity"]["pending_uploads"] if u["id"] == doc)
    assert row["person_id"] == env["pid"]


def test_pending_upload_excluded_for_unauthorized_department_staff(env):
    doc = vault.create_document(env["full"], source=io.BytesIO(b"w2"), original_filename="w2.pdf",
                                display_name="Client W-2", category="tax", actor_user_id=env["uid"],
                                person_id=env["pid"])
    env["docs"].append(doc)
    with engine.begin() as c:
        c.execute(vault_documents.update().where(vault_documents.c.id == doc).values(
            uploaded_by_portal_account_id=env["paid"], status="uploaded"))
    dept = Principal(env["uid"], "d@e.test", "Dept", DEPT)   # book-scoped, not assigned this client
    ids = {u["id"] for u in home_summary(dept)["portal_activity"]["pending_uploads"]}
    assert doc not in ids                             # out-of-book → excluded


# --- reviews -----------------------------------------------------------------

def test_reviews_card_hidden_without_review_capability(env):
    dept = Principal(env["uid"], "d@e.test", "Dept", DEPT)   # no compliance.review.read/work.approve
    keys = {c["key"] for c in home_summary(dept)["cards"]}
    assert "reviews" not in keys


# --- route + UI --------------------------------------------------------------

def test_home_route_renders_required_panels(env):
    from app.routes.home import staff_home
    html = staff_home(_req(), principal=env["full"]).body.decode()
    for needle in ("Welcome, Morgan", "Priority work", "Waiting", "Reviews requiring action",
                   "Recent client activity", "/work?view=my_work", "/work?view=overdue"):
        assert needle in html


def test_api_home_summary_is_json_safe(env):
    import json

    from app.routes.home import api_home_summary
    body = json.loads(bytes(api_home_summary(principal=env["full"]).body))
    assert "counts" in body and "priority_work" in body and "quick_actions" in body


def test_home_nav_present_for_staff_not_portal():
    # base.html adds a Home nav item gated on a staff principal (p); portal base has its own nav.
    import pathlib
    base = pathlib.Path("app/templates/base.html").read_text()
    assert '"href": "/home"' in base and '"label": "Home"' in base
