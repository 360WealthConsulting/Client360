"""Demo Identity Review dashboard (/demo/identity) — coverage.

Proves the demo dashboard reuses the EXISTING matching intelligence (no new logic):
counts are database-backed, the "Run Identity Matching" button invokes
app.matching.promote.promote_unlinked, unresolved rows render from
list_ambiguous_unlinked, the page loads, and the "Identity Review" nav item is
demo-only (gated by request.state.demo_mode, absent in production).

The tests do NOT import app.demo.demo_app (that module mutates the shared app.main
app at import). They call the router's functions directly and read the rendered
TemplateResponse body (Starlette renders it at construction), so no HTTP client /
extra dependency is needed.
"""
import hashlib
import uuid

import pytest
from starlette.requests import Request

from app.db import engine, people, person_source_links, source_contacts
from app.demo import identity_review as ir
from app.security.models import Principal

MARKER = "demo.identity.test"
FAKE = Principal(4242, "reviewer@example.com", "Reviewer",
                 frozenset({"record.read_all", "client.read"}))


def _request(*, demo=True, path="/demo/identity", query=b""):
    scope = {"type": "http", "method": "GET", "path": path, "headers": [],
             "query_string": query, "state": {}}
    request = Request(scope)
    request.state.principal = FAKE
    if demo:
        request.state.demo_mode = True
    return request


def _render(*, demo=True, source="", query=b""):
    response = ir.identity_dashboard(_request(demo=demo, query=query), source=source, principal=FAKE)
    return response.status_code, response.body.decode("utf-8")


def _add_contact(conn, system, name, email, phone):
    h = hashlib.sha256(f"{MARKER}:{system}:{name}:{email}:{phone}".encode()).hexdigest()
    return conn.execute(
        source_contacts.insert().values(
            source_system=system, source_file=MARKER, source_hash=h, full_name=name,
            email=email, normalized_email=(email or "").lower() or None,
            phone=phone, normalized_phone=phone, raw_data={"Name": name, "Email": email},
        ).returning(source_contacts.c.id)
    ).scalar_one()


@pytest.fixture
def ambiguous_rows():
    """Two unlinked contacts sharing an email (ambiguous: shared_contact_info), committed so the
    dashboard's own DB connection sees them; cleaned up after."""
    n1 = f"Alpha {uuid.uuid4().hex[:6]}"
    n2 = f"Beta {uuid.uuid4().hex[:6]}"
    shared = f"{uuid.uuid4().hex[:8]}@shared.example"
    ids = []
    with engine.begin() as conn:
        ids.append(_add_contact(conn, "Wealthbox", n1, shared, "5550000001"))
        ids.append(_add_contact(conn, "Dave Ramsey", n2, shared, "5550000002"))
    try:
        yield {"names": (n1, n2), "ids": ids}
    finally:
        with engine.begin() as conn:
            conn.execute(person_source_links.delete().where(
                person_source_links.c.source_contact_id.in_(ids)))
            conn.execute(source_contacts.delete().where(source_contacts.c.source_file == MARKER))


# --- 1. page loads -----------------------------------------------------------

def test_page_loads(ambiguous_rows):
    status, html = _render()
    assert status == 200
    assert "Identity Review" in html
    assert "Run Identity Matching" in html
    assert "Canonical people" in html and "Ambiguous unresolved" in html


# --- 2. counts are database-backed -------------------------------------------

def test_counts_are_database_backed():
    conn = engine.connect()
    trans = conn.begin()
    try:
        p1 = conn.execute(people.insert().values(full_name="X One").returning(people.c.id)).scalar_one()
        wb = _add_contact(conn, "Wealthbox", "X One", "x1@e.example", "5551110001")
        am = _add_contact(conn, "AssetMark", "X One", "x1@e.example", "5551110001")
        _add_contact(conn, "Dave Ramsey", "Y Two", "y2@e.example", "5551110002")  # unlinked
        conn.execute(person_source_links.insert().values(
            person_id=p1, source_contact_id=wb, match_method="test", match_score=100))
        conn.execute(person_source_links.insert().values(
            person_id=p1, source_contact_id=am, match_method="test", match_score=100))

        counts = ir._counts(conn)

        assert counts["total_source_contacts"] >= 3
        assert counts["linked_source_contacts"] >= 2
        assert counts["by_system"].get("Wealthbox", 0) >= 1
        assert counts["by_system"].get("AssetMark", 0) >= 1
        assert counts["multi_source_people"] >= 1          # p1 is in Wealthbox + AssetMark
        assert counts["cross_source"]["Wealthbox + AssetMark"] >= 1
        assert counts["unlinked_source_contacts"] == (
            counts["total_source_contacts"] - counts["linked_source_contacts"])
    finally:
        trans.rollback()
        conn.close()


# --- 3. matching button invokes the existing promote_unlinked ----------------

def test_run_matching_invokes_promote_unlinked(monkeypatch):
    calls = {}

    class _Report:
        inspected, created, linked_existing, ambiguous = 7, 3, 2, 1

        def to_dict(self):
            return {"inspected": 7, "created": 3, "linked_existing": 2, "ambiguous": 1}

    def _spy(*, source_system=None, conn=None):
        calls["source_system"] = source_system
        return _Report()

    monkeypatch.setattr(ir, "promote_unlinked", _spy)
    monkeypatch.setattr(ir, "write_audit_event", lambda **kw: None)

    request = Request({"type": "http", "method": "POST", "path": "/demo/identity/run-matching",
                       "headers": [], "query_string": b"", "state": {}})
    resp = ir.run_matching(request, source="Wealthbox", principal=FAKE)

    assert calls == {"source_system": "Wealthbox"}          # existing logic invoked, not duplicated
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "inspected=7" in loc and "created=3" in loc
    assert "linked_existing=2" in loc and "ambiguous=1" in loc


# --- 4. unresolved rows render -----------------------------------------------

def test_unresolved_rows_render(ambiguous_rows):
    status, html = _render()
    assert status == 200
    n1, n2 = ambiguous_rows["names"]
    assert (n1 in html) or (n2 in html)                    # the shared-email pair is surfaced
    assert "/matches/unresolved/" in html                  # reuses the existing resolve endpoint
    # source filter narrows to one system and still renders
    fstatus, _ = _render(source="Wealthbox")
    assert fstatus == 200


# --- 5. navigation link is visible (demo only) -------------------------------

def test_nav_identity_review_visible_in_demo_only():
    # ◑ is the unique nav icon for the Identity Review item (nav-only marker).
    _, demo_html = _render(demo=True)
    assert "Identity Review" in demo_html and "◑" in demo_html      # nav item shown in demo

    _, prod_html = _render(demo=False)
    assert "◑" not in prod_html                                     # nav item hidden without demo_mode
