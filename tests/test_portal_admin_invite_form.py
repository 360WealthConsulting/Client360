"""Staff HTML invite-client form over the existing scoped/audited invite service (onboarding entry point)."""
import inspect
import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.db import engine, households, people, portal_accounts, users
from app.routes.portal_admin import portal_admin_invite_form
from app.security.models import Principal


def _staff_user():
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"staff-{sfx}@example.com", normalized_email=f"staff-{sfx}@example.com",
            display_name="Invite Staff", auth_subject=f"staff-{sfx}", status="active")
            .returning(users.c.id)).scalar_one()


def _person_household():
    """A person already IN a household — the invite form derives the household itself now."""
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {sfx}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Invite {sfx}", first_name="Invite", last_name=sfx, active=True,
            household_id=hid, primary_email=f"person-{sfx}@example.com")
            .returning(people.c.id)).scalar_one()
    return pid, hid


def _req(session=None):
    """A staff request. ``session`` and ``url_for`` are what the one-time activation-link handoff
    needs (see tests/test_portal_invitation_delivery.py)."""
    return SimpleNamespace(state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}"),
                           client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
                           session={} if session is None else session,
                           url_for=lambda name: "http://testserver/portal/login")


def _principal(uid, caps):
    return Principal(uid, "staff@example.com", "Staff", frozenset(caps))


def test_invite_form_creates_account_and_redirects_prg():
    pid, _hid = _person_household()
    raw_email = f"  jane-{uuid.uuid4().hex[:6]}@example.com "
    resp = portal_admin_invite_form(
        request=_req(), person_id=str(pid), email=raw_email, access_type="self",
        principal=_principal(_staff_user(), {"client.write", "record.write_all"}))
    assert resp.status_code == 303                                   # Post/Redirect/Get
    assert "invited=Invite" in resp.headers["location"]              # flash carries the record's name
    with engine.connect() as c:
        row = c.execute(select(portal_accounts.c.status, portal_accounts.c.email)
                        .where(portal_accounts.c.person_id == pid)
                        .order_by(portal_accounts.c.id.desc())).mappings().first()
    assert row and row["status"] == "invited"
    assert row["email"] == raw_email.strip()                        # email trimmed before storage


def test_invite_form_out_of_scope_redirects_error_and_creates_nothing():
    pid, _hid = _person_household()
    resp = portal_admin_invite_form(
        request=_req(), person_id=str(pid), email="x@example.com", access_type="self",
        principal=_principal(_staff_user(), {"client.write"}))      # no record.write_all, no assignment
    assert resp.status_code == 303 and "error=" in resp.headers["location"]
    with engine.connect() as c:
        n = c.execute(select(portal_accounts.c.id).where(portal_accounts.c.person_id == pid)).fetchall()
    assert n == []                                                  # scope failure invited nobody


def test_invite_form_route_is_capability_gated_and_scope_checked():
    src = inspect.getsource(portal_admin_invite_form)
    assert 'require_capability("client.write")' in src              # same gate as the JSON invite
    # Record scope is now enforced inside resolve_invite_target(), which re-checks it server-side on
    # every submission rather than trusting the browser-supplied person id.
    assert "resolve_invite_target" in src


def test_client_portal_template_exposes_the_invite_form():
    """The form posts an EMAIL, an access level, and a hidden selected person — nothing else.
    Display name and household are derived server-side; they are no longer typed."""
    html = open("app/templates/admin/client_portal.html", encoding="utf-8").read()
    assert 'action="/admin/client-portal/invite-form"' in html
    for field in ('name="email"', 'name="access_type"', 'name="person_id"'):
        assert field in html, field
    for gone in ('name="display_name"', 'name="household_id"', 'name="organization_id"'):
        assert gone not in html, f"{gone} is an internal detail and must not be typed by staff"
