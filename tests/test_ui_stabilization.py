"""Staff UI stabilization — the smallest complete workflow: Search → Client Workspace → Dashboard →
Documents → Open a document → Return.

Covers: search→workspace links, person + household routes render, Dashboard + Documents render, the
document-open flow returns a styled (never raw) error, back/breadcrumb navigation + search-on-every-page,
human-readable OCR/extraction states (no technical jargon), useful empty states, permission enforcement,
and no 404s across the primary workflow. Route functions are driven directly (same pattern as
test_client_workspace_tabs). Temp/test rows only.
"""
import uuid

import pytest
from starlette.requests import Request

from app.db import (
    document_ocr,
    documents,
    engine,
    household_relationships,
    households,
    people,
    users,
)
from app.routes.client360 import client_workspace, household_workspace
from app.routes.documents import download_document
from app.routes.search import search_page
from app.security.models import Principal
from app.services.knowledge_pipeline import run_knowledge_pipeline

_TAG = "UISTAB"
_CAPS = frozenset({"client.read", "documents.view", "record.read_all", "tax.read", "timeline.read"})
_STATE = {}


@pytest.fixture
def client():
    from sqlalchemy import delete
    with engine.begin() as c:
        tag = uuid.uuid4().hex[:6]
        uid = c.execute(users.insert().values(
            email=f"ui{tag}@e.test", normalized_email=f"ui{tag}@e.test",
            display_name="Sarah Advisor", status="active").returning(users.c.id)).scalar_one()
        hid = c.execute(households.insert().values(name=f"{_TAG} Whitfield {tag}").returning(
            households.c.id)).scalar_one()
        pids = []
        for first, primary in (("Marcus", True), ("Eleanor", False)):
            pid = c.execute(people.insert().values(
                first_name=first, last_name=f"{_TAG}{tag}", full_name=f"{first} {_TAG}{tag}",
                household_id=hid, active=True).returning(people.c.id)).scalar_one()
            c.execute(household_relationships.insert().values(
                household_id=hid, person_id=pid, relationship_type="member", is_primary=primary))
            pids.append(pid)
        did = c.execute(documents.insert().values(
            original_name=f"2023 Form 1040 {_TAG}.pdf", stored_name=f"ui-{tag}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/missing",
            size_bytes=20, sha256="a" * 64, person_id=pids[0], status="active", archived=False,
            tags={"source_system": "TaxDome Drive"}).returning(documents.c.id)).scalar_one()
        c.execute(document_ocr.insert().values(
            document_id=did, status="completed",
            text="Form 1040 U.S. Individual Income Tax Return 2023 EIN 12-3456789",
            char_count=60, engine="tesseract 5.4.0", attempts=1))
    run_knowledge_pipeline(document_ids=[did], mode="reprocess", actor_user_id=uid)
    _STATE.update(uid=uid, hid=hid, pids=pids, did=did, tag=tag)
    yield _STATE
    from app.db import document_classifications, document_facts, timeline_events
    with engine.begin() as c:
        c.execute(delete(document_facts).where(document_facts.c.document_id == did))
        c.execute(delete(document_classifications).where(document_classifications.c.document_id == did))
        c.execute(delete(document_ocr).where(document_ocr.c.document_id == did))
        c.execute(delete(documents).where(documents.c.id == did))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id.in_(pids)))
        c.execute(delete(household_relationships).where(household_relationships.c.household_id == hid))
        c.execute(delete(people).where(people.c.id.in_(pids)))
        c.execute(delete(households).where(households.c.id == hid))


def _principal(caps=_CAPS):
    return Principal(_STATE["uid"], "sarah@e.test", "Sarah Advisor", caps)


def _req(path, principal=None):
    r = Request({"type": "http", "method": "GET", "path": path, "headers": [],
                 "query_string": b"", "state": {}})
    r.state.principal = principal or _principal()
    r.state.request_id = f"ui-{uuid.uuid4()}"
    return r


def _html(resp):
    return resp.body.decode()


# --- search → workspace navigation -------------------------------------------

def test_search_links_to_workspace(client):
    resp = search_page(_req("/search"), q=f"{_TAG}{client['tag']}")
    html = _html(resp)
    assert "Universal search" in html
    assert f"/client/{client['pids'][0]}" in html          # result opens the Client Workspace


# --- person + household routes render ----------------------------------------

def test_person_workspace_renders_with_identity_and_backnav(client):
    html = _html(client_workspace(_req(f"/client/{client['pids'][0]}"), client["pids"][0],
                                  tab="dashboard", principal=_principal()))
    assert "Marcus" in html                                 # client identity in the header
    assert 'href="/search"' in html and "← Search" in html  # working back-to-search breadcrumb
    assert 'action="/search"' in html                       # search available on every page (header)


def test_household_workspace_renders_with_backnav(client):
    html = _html(household_workspace(_req(f"/client/household/{client['hid']}"), client["hid"],
                                     tab="documents", principal=_principal()))
    assert "← Search" in html and 'href="/search"' in html


# --- dashboard + documents render --------------------------------------------

def test_dashboard_renders(client):
    html = _html(client_workspace(_req(f"/client/{client['pids'][0]}"), client["pids"][0],
                                  tab="dashboard", principal=_principal()))
    assert "Newly classified documents" in html and "500" not in html[:50]


def test_documents_human_readable_states(client):
    """Plain-language text-capture, extraction and classification states — no technical jargon.

    They now live in the document's preview DRAWER rather than in dedicated table columns: the
    Documents screen grid is Document / Type / Year / Related To / Date / Source / Actions, and
    these states were relocated into the drawer's Details tab rather than dropped. The rule this
    test exists for is unchanged: whatever is shown must be readable by a person.
    """
    from app.routes.document_panel import person_document_panel
    pid, did = client["pids"][0], client["did"]

    html = _html(client_workspace(_req(f"/client/{pid}"), pid, tab="documents",
                                  principal=_principal()))
    assert "/ pending" not in html and "OCR / AI" not in html       # no technical jargon
    assert f"/documents/{did}/download" in html                     # the download route is reachable
    assert f"/client/{pid}/documents/{did}/panel" in html           # the row opens the drawer

    panel = _html(person_document_panel(
        _req(f"/client/{pid}/documents/{did}/panel"), pid, did, panel="details",
        principal=_principal()))
    assert "Text captured" in panel and "Searchable" in panel       # plain-language OCR state
    assert "details found" in panel                                 # plain-language extraction
    assert "%" in panel and "Confidence" in panel                   # plain-language classification
    assert "/ pending" not in panel and "OCR / AI" not in panel


# --- document open flow (styled errors, never raw) ---------------------------

def test_document_open_missing_file_returns_styled_error(client):
    # The seeded storage_uri points at a missing file → styled 404, not a raw <h1>.
    resp = download_document(client["did"], _req(f"/documents/{client['did']}/download"))
    body = resp.body.decode()
    assert resp.status_code == 404
    assert "<h1>Stored file is missing</h1>" not in body            # no raw internal error
    assert "could not be found" in body


def test_document_open_unknown_id_returns_styled_error(client):
    resp = download_document(99999999, _req("/documents/99999999/download"))
    body = resp.body.decode()
    assert resp.status_code == 404
    assert "<h1>Document not found</h1>" not in body                # no raw internal error
    assert "no longer available" in body


# --- empty states ------------------------------------------------------------

def test_documents_empty_state(client):
    # Eleanor (second member) owns no documents → friendly empty state, no error.
    html = _html(client_workspace(_req(f"/client/{client['pids'][1]}"), client["pids"][1],
                                  tab="documents", principal=_principal()))
    assert "No documents" in html


# --- permission enforcement --------------------------------------------------

def test_documents_tab_requires_capability(client):
    # A principal without documents.view must not see the Documents section content.
    caps = frozenset({"client.read", "record.read_all"})
    ws = client_workspace(_req(f"/client/{client['pids'][0]}", _principal(caps)),
                          client["pids"][0], tab="documents", principal=_principal(caps))
    html = _html(ws)
    assert "Text captured" not in html                             # gated content not rendered


def test_out_of_scope_person_is_not_found(client):
    from app.security.authorization import (
        accessible_person_ids,  # noqa: F401 (scope engine used by route)
    )
    scoped = Principal(_STATE["uid"], "s@e.test", "S", frozenset({"client.read", "documents.view"}))
    # No record assignment for this principal → the person is out of scope → 404 (not a 500/leak).
    resp = client_workspace(_req(f"/client/{client['pids'][0]}", scoped), client["pids"][0],
                            tab="dashboard", principal=scoped)
    assert resp.status_code == 404


# --- no 404s across the primary workflow -------------------------------------

def test_primary_workflow_has_no_dead_ends(client):
    pid, hid = client["pids"][0], client["hid"]
    assert search_page(_req("/search"), q=f"{_TAG}{client['tag']}").status_code == 200
    for tab in ("dashboard", "documents", "tax", "timeline"):
        r = client_workspace(_req(f"/client/{pid}"), pid, tab=tab, principal=_principal())
        assert r.status_code == 200
    assert household_workspace(_req(f"/client/household/{hid}"), hid, tab="documents",
                               principal=_principal()).status_code == 200
