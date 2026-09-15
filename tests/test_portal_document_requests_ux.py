"""The client-facing Document Requests page: what a client can see and act on.

These render the REAL page through ``portal_page("requests", ...)`` rather than grepping the
template source, because the things worth protecting are behavioural: an open request offers one
prominent upload action pointed at its own request id, an uploaded one does not offer that action at
all, and an account with no requests gets a useful empty state instead of a blank page.

The upload action is gated by ``portal_can(principal, '/portal/upload')``, which the firm-wide
upload surface gate closes by default (tests/conftest.py). Tests that care about the action request
``portal_documents_upload_on`` explicitly rather than through a module-level mark, because the gate
fixtures share one mutable set. Switching the surface on also makes the "no CTA once uploaded"
assertions meaningful: the action is absent because the request is done, not because the gate hid it.

Scope note: the page query in ``app/portal/service.py`` selects
``status IN ('open', 'uploaded')``, so those are the only two states this page can render. Nothing
here invents a new status, changes request ownership, or touches the upload workflow -- the
destination ``/portal/upload?request_id=<id>`` is asserted verbatim precisely so it cannot drift.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import update

from app.db import engine, portal_document_requests
from app.portal.service import create_document_request
from app.routes.portal import portal_page
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user


def _client():
    staff = seed_staff_user()
    _account_id, principal, person_id, household_id = seed_portal_account(staff)
    return staff, principal, person_id, household_id


def _request(staff, person_id, household_id, *, title=None, description=None, due_date=None):
    return create_document_request(
        person_id=person_id, household_id=household_id,
        title=title or f"2025 W-2 {uuid.uuid4().hex[:6]}",
        description=description, due_date=due_date, requested_by_user_id=staff)


def _mark_uploaded(request_id):
    """Put a request into the 'uploaded' state without driving a real file upload.

    confirm_request_upload also writes document_versions and a timeline event; this page cares only
    about the status it must render, so the narrower write keeps the test about the page.
    """
    with engine.begin() as c:
        c.execute(update(portal_document_requests)
                  .where(portal_document_requests.c.id == request_id)
                  .values(status="uploaded"))


def _page(principal) -> str:
    return render(portal_page("requests", fake_request("/portal/requests"), principal))


# --- page structure -------------------------------------------------------------------------------

def test_the_page_explains_itself_to_a_client(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    _request(staff, pid, hid, title="2025 W-2")

    html = _page(principal)

    assert "Document Requests" in html, "the page must name itself"
    assert "360 team" in html, "the client should be told who asked for these"
    assert "upload" in html.lower(), "the page must say the client can upload from here"


def test_a_request_shows_title_status_due_date_and_instructions(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    _request(staff, pid, hid, title="2025 Mortgage Interest Statement",
             description="Form 1098 from your lender, all pages.", due_date="2026-04-15")

    html = _page(principal)

    assert "2025 Mortgage Interest Statement" in html
    assert "Form 1098 from your lender, all pages." in html
    assert "2026-04-15" in html
    assert "Open" in html, "the backend status must be shown, not a reinvented label"


def test_the_title_is_the_dominant_element_of_a_request(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    _request(staff, pid, hid, title="2025 W-2")

    html = _page(principal)

    assert 'class="request-title"' in html
    # The title must precede the status row, so the card reads title-first.
    assert html.index("request-title") < html.index("request-facts")


def test_a_request_without_a_description_or_due_date_still_renders_cleanly(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    _request(staff, pid, hid, title="Bare Request")

    html = _page(principal)

    assert "Bare Request" in html
    assert "request-instructions" not in html, "an empty instructions block must not be emitted"
    assert "Due" not in html


# --- open requests: the action -----------------------------------------------------------------------

def test_an_open_request_offers_a_prominent_upload_action_at_its_own_request_id(
        portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    request_id = _request(staff, pid, hid, title="2025 W-2")

    html = _page(principal)

    assert f'href="/portal/upload?request_id={request_id}"' in html, \
        "the request-specific upload destination must be preserved exactly"
    assert "Upload document" in html
    # class="btn" is the primary button; "btn secondary" is the muted one.
    assert f'class="btn" href="/portal/upload?request_id={request_id}"' in html, \
        "an outstanding request must carry the PRIMARY action, not a muted secondary one"


def test_each_open_request_links_to_its_own_id_not_a_shared_one(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    first = _request(staff, pid, hid, title="First Request")
    second = _request(staff, pid, hid, title="Second Request")

    html = _page(principal)

    assert f"request_id={first}" in html
    assert f"request_id={second}" in html
    assert first != second


def test_the_upload_surface_gate_still_governs_the_action(portal_master_on):
    """Pre-existing behaviour, preserved: uploads off firm-wide means no advertised destination."""
    staff = seed_staff_user()
    _account_id, principal, pid, hid = seed_portal_account(staff)
    _request(staff, pid, hid, title="2025 W-2")

    html = _page(principal)

    assert "2025 W-2" in html, "the request is still listed"
    assert "/portal/upload" not in html, "the page must not advertise a destination that would 403"


# --- uploaded requests: no primary CTA -----------------------------------------------------------------

def test_an_uploaded_request_does_not_offer_the_outstanding_upload_action(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    request_id = _request(staff, pid, hid, title="2025 W-2")
    _mark_uploaded(request_id)

    html = _page(principal)

    assert f"/portal/upload?request_id={request_id}" not in html, \
        "a completed request must not present the same primary CTA as an outstanding one"
    assert "Upload document" not in html


def test_an_uploaded_request_is_visually_and_verbally_obvious(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    request_id = _request(staff, pid, hid, title="2025 W-2")
    _mark_uploaded(request_id)

    html = _page(principal)

    assert "2025 W-2" in html, "a completed request is still listed"
    assert "Uploaded" in html, "the backend status is shown"
    assert 'class="pill ok"' in html, "completed uses the positive pill, not the pending one"
    assert "request-card--done" in html, "the completed card is visually distinguished"
    assert "no further action needed" in html.lower()


def test_open_and_uploaded_requests_are_distinguishable_on_the_same_page(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    open_id = _request(staff, pid, hid, title="Still Needed")
    done_id = _request(staff, pid, hid, title="Already Sent")
    _mark_uploaded(done_id)

    html = _page(principal)

    assert "Still Needed" in html and "Already Sent" in html
    assert f"request_id={open_id}" in html, "the open request keeps its action"
    assert f"request_id={done_id}" not in html, "the completed one does not"
    assert 'class="pill pending"' in html and 'class="pill ok"' in html


# --- empty state -------------------------------------------------------------------------------------

def test_a_client_with_no_requests_gets_a_useful_empty_state(portal_documents_upload_on):
    _staff, principal, _pid, _hid = _client()

    html = _page(principal)

    assert "empty-state" in html
    assert "No document requests" in html
    assert "outstanding document requests" in html
    assert "request-card" not in html, "no request cards when there are no requests"
    assert "Upload document" not in html


# --- encoding ------------------------------------------------------------------------------------------

def test_the_separator_renders_as_a_real_middle_dot_not_mojibake(portal_documents_upload_on):
    staff, principal, pid, hid = _client()
    _request(staff, pid, hid, title="Dated Request", due_date="2026-04-15")

    html = _page(principal)

    assert chr(0x00B7) in html, "the separator must be a real U+00B7 middle dot"
    assert "Ã‚Â·" not in html, "mojibake must not reappear"
    assert "Â·" not in html, "UTF-8 read as latin-1 would show this"


@pytest.mark.parametrize("path", ["app/templates/portal/requests.html"])
def test_the_template_source_is_valid_utf8_with_no_mojibake(path):
    raw = open(path, "rb").read()
    text = raw.decode("utf-8")                      # raises if the file is not valid UTF-8
    assert b"\xc3\x82\xc2\xb7" not in raw, "the file must not contain a mojibake middle dot"
    assert chr(0x00B7) in text
