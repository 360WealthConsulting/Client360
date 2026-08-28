"""The client portal must advertise only what it will actually serve.

The nav, the dashboard affordances and the document controls were rendered unconditionally while
the middleware gated them, so a client whose firm had messaging or uploads switched off saw the
links and hit a 403 on click. Every link is now filtered through ``portal_gate.surface_available``
— the same evaluation the middleware enforces — so the UI cannot advertise a destination that the
identical evaluation would refuse.

These tests assert presentation only. The middleware remains authoritative and is covered by the
existing portal security/gate suites.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import engine
from app.portal import vault_documents
from app.routes.portal import (
    portal_documents_page,
    portal_page,
    portal_profile_page,
)
from app.services.features.portal_gate import surface_available
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user

_NAV_HREFS = ("/portal/", "/portal/action-needed", "/portal/documents", "/portal/upload",
              "/portal/messages", "/portal/requests", "/portal/tasks", "/portal/profile")


def _dashboard(principal):
    return render(portal_page("", fake_request("/portal/"), principal))


# --- navigation ------------------------------------------------------------------------------

def test_nav_hides_messages_and_upload_when_those_surfaces_are_off(portal_master_on):
    """Master gate on, child gates off — the portal works but those two surfaces do not."""
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = _dashboard(principal)
    assert 'href="/portal/messages"' not in html, "Messages advertised while messaging is off"
    assert 'href="/portal/upload"' not in html, "Upload advertised while uploads are off"
    # The surfaces that ARE available must still be there — this is not a blanket hide.
    assert 'href="/portal/documents"' in html
    assert 'href="/portal/profile"' in html


def test_nav_shows_messages_when_messaging_is_enabled(portal_messaging_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = _dashboard(principal)
    assert 'href="/portal/messages"' in html and ">Messages</a>" in html
    assert 'href="/portal/upload"' not in html, "only messaging was enabled"


def test_nav_shows_upload_when_uploads_are_enabled(portal_documents_upload_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = _dashboard(principal)
    assert 'href="/portal/upload"' in html and ">Upload</a>" in html
    assert 'href="/portal/messages"' not in html, "only uploads were enabled"


def test_nav_never_advertises_a_destination_that_would_403(portal_messaging_on):
    """The invariant: every rendered nav link must pass the SAME evaluation the middleware runs."""
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = _dashboard(principal)
    for href in _NAV_HREFS:
        if f'href="{href}"' in html:
            assert surface_available(principal, href), \
                f"{href} is advertised but the middleware would deny it"


def test_a_client_denied_messaging_by_entitlement_also_sees_no_link(portal_messaging_on):
    """Firm gate ON but the per-client grant withholds messages — still hidden."""
    _, principal, _, _ = seed_portal_account(seed_staff_user(),
                                             permissions={"documents": True, "messages": False})
    html = _dashboard(principal)
    if not surface_available(principal, "/portal/messages"):
        assert 'href="/portal/messages"' not in html


# --- dashboard affordances ---------------------------------------------------------------------

def test_dashboard_hides_messaging_and_upload_affordances_when_off(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = _dashboard(principal)
    assert "Message your advisor" not in html
    assert "Upload a document" not in html


def test_dashboard_shows_affordances_when_the_surfaces_are_on(
        portal_messaging_on, portal_documents_upload_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = _dashboard(principal)
    assert "Message your advisor" in html
    assert "Upload a document" in html


def test_the_upcoming_meetings_tile_is_gone(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    assert "Upcoming meetings" not in _dashboard(principal)


# --- documents ----------------------------------------------------------------------------------

def _doc_row(**over):
    row = {"id": 1, "display_name": "Statement", "category": "general", "document_type": None,
           "status": "approved", "pending_approval": False, "file_size": 10, "current_version": 1,
           "uploaded_by_portal_account_id": None, "created_at": None, "client_visible": True}
    row.update(over)
    return row


def test_downloadable_is_false_when_the_download_gate_is_off():
    view = vault_documents._client_view(_doc_row(), downloads_enabled=False)
    assert view["downloadable"] is False


def test_downloadable_is_true_when_the_gate_is_on_and_the_document_qualifies():
    view = vault_documents._client_view(_doc_row(), downloads_enabled=True)
    assert view["downloadable"] is True


def test_the_gate_cannot_make_an_unqualified_document_downloadable():
    view = vault_documents._client_view(_doc_row(status="uploaded"), downloads_enabled=True)
    assert view["downloadable"] is False, "the gate must not override document status"


def _seed_visible_document(person_id):
    """An approved, client-visible document — otherwise the page is empty and the assertion below
    would pass without ever exercising the download control."""
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        doc_id = c.execute(text(
            "INSERT INTO vault_documents (display_name, original_filename, category, storage_key,"
            " checksum_sha256, status, client_visible) VALUES"
            " (:n, :f, 'general', :k, :s, 'approved', true) RETURNING id"),
            {"n": f"Statement {tag}", "f": f"{tag}.pdf", "k": f"vault/{tag}", "s": tag * 8}).scalar_one()
        c.execute(text("INSERT INTO vault_document_links (document_id, person_id)"
                       " VALUES (:d, :p)"), {"d": doc_id, "p": person_id})
    return doc_id


def test_documents_page_renders_a_download_control_when_downloads_are_on(
        portal_documents_download_on):
    """Precondition for the test below: with the gate ON the control IS rendered."""
    _, principal, person_id, _ = seed_portal_account(seed_staff_user())
    _seed_visible_document(person_id)
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert "/download" in html and "Download" in html


def test_documents_page_renders_no_download_control_when_downloads_are_off(portal_master_on):
    _, principal, person_id, _ = seed_portal_account(seed_staff_user())
    _seed_visible_document(person_id)
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert "Statement" in html, "precondition: the document itself is listed"
    assert "/download" not in html, "a Download control would lead straight to a 403"


def test_documents_page_hides_upload_controls_when_uploads_are_off(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert 'href="/portal/upload"' not in html
    assert "Upload a document" not in html and "Upload your first document" not in html


def test_documents_page_shows_upload_controls_when_uploads_are_on(portal_documents_upload_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert 'href="/portal/upload"' in html


# --- copy ----------------------------------------------------------------------------------------

def test_settings_makes_no_multi_factor_claim(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_page("settings", fake_request("/portal/settings"), principal))
    lowered = html.lower()
    assert "multi-factor" not in lowered and "two-factor" not in lowered
    assert "one-time code" in lowered, "the real sign-in method should be described"


def test_settings_promises_no_unshipped_delivery_channel(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_page("settings", fake_request("/portal/settings"), principal)).lower()
    assert "will be available" not in html and "text message alerts" not in html


def test_profile_points_at_messaging_only_when_messaging_is_available(portal_messaging_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_profile_page(fake_request("/portal/profile"), principal))
    assert "please message your advisor" in html


def test_profile_gives_neutral_guidance_when_messaging_is_unavailable(portal_master_on):
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_profile_page(fake_request("/portal/profile"), principal))
    assert "please message your advisor" not in html
    assert "contact your advisory team" in html


# --- denial copy ----------------------------------------------------------------------------------

def test_denial_copy_is_accurate_for_both_kinds_of_unavailability_and_names_no_gate():
    from app.security import middleware
    source = open(middleware.__file__, encoding="utf-8").read()
    assert "not available on your account" not in source, \
        "firm-wide disablement is not an account-level fact"
    for leak in ("portal.messaging_enabled", "_disabled", "_denied"):
        assert f'"{leak}"' not in source.split("_detail =")[-1][:400], \
            "the denial must not reveal which gate or rule refused"
