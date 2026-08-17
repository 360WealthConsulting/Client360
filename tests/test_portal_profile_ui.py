"""Client profile UI — view/edit the fields a client is authorized to change, over the audited
profile service. Exercises the real /portal/profile routes directly with a live PortalPrincipal.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db import audit_events, engine, people
from app.portal import profile as portal_profile
from app.routes.portal import portal_profile_page, portal_profile_submit
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user


def _profile_audits(account_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == "portal.profile.updated")
            & (audit_events.c.entity_id == str(account_id))))


def test_profile_page_renders_current_details():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    portal_profile.update_profile(principal, {"email": "jane@example.com", "phone": "540-555-0100"})
    html = render(portal_profile_page(fake_request("/portal/profile"), principal))
    assert "jane@example.com" in html
    assert "540-555-0100" in html
    assert 'action="/portal/profile"' in html


def test_profile_submit_updates_editable_field_prg_and_audits():
    account_id, principal, _, _ = seed_portal_account(seed_staff_user())
    before = _profile_audits(account_id)
    resp = portal_profile_submit(
        request=fake_request("/portal/profile", "POST"), email=None, phone="703-555-0161",
        address=None, city="Reston", state="VA", postal_code=None,
        preferred_contact_method="email", principal=principal)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/portal/profile?notice=")

    saved = portal_profile.get_profile(principal)
    assert saved["primary_phone"] == "703-555-0161"
    assert saved["city"] == "Reston" and saved["state"] == "VA"
    assert saved["preferred_contact_method"] == "email"
    assert _profile_audits(account_id) == before + 1        # every change is audited


def test_profile_submit_blank_fields_make_no_change():
    account_id, principal, _, _ = seed_portal_account(seed_staff_user())
    before = _profile_audits(account_id)
    resp = portal_profile_submit(
        request=fake_request("/portal/profile", "POST"), email="   ", phone="", address=None,
        city=None, state=None, postal_code=None, preferred_contact_method=None, principal=principal)
    assert resp.status_code == 303
    assert "No%20changes" in resp.headers["location"]
    assert _profile_audits(account_id) == before            # nothing written


def test_protected_fields_are_not_writable_through_the_service():
    # The route only forwards allow-listed fields, and the service allowlist is the backstop:
    # a forged 'full_name' (or any non-editable field) is dropped even if it reaches update_profile.
    _, principal, pid, _ = seed_portal_account(seed_staff_user())
    with engine.connect() as c:
        original_name = c.scalar(select(people.c.full_name).where(people.c.id == pid))
    result = portal_profile.update_profile(
        principal, {"full_name": "Hacker McForge", "id": 999999, "phone": "202-555-0111"})
    assert "phone" not in result["changed"] or "primary_phone" in result["changed"]
    assert result["changed"] == ["primary_phone"]           # only the editable field changed
    with engine.connect() as c:
        assert c.scalar(select(people.c.full_name).where(people.c.id == pid)) == original_name
