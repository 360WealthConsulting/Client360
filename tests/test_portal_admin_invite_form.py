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
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"Invite {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
        hid = c.execute(households.insert().values(name=f"HH {sfx}").returning(households.c.id)).scalar_one()
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
    pid, hid = _person_household()
    raw_email = f"  jane-{uuid.uuid4().hex[:6]}@example.com "
    resp = portal_admin_invite_form(
        request=_req(), person_id=pid, household_id=hid, email=raw_email,
        display_name="  Jane Client  ", access_type="self", organization_id=None,
        principal=_principal(_staff_user(), {"client.write", "record.write_all"}))
    assert resp.status_code == 303                                   # Post/Redirect/Get
    assert "invited=Jane" in resp.headers["location"]                # flash carries the name
    with engine.connect() as c:
        row = c.execute(select(portal_accounts.c.status, portal_accounts.c.email)
                        .where(portal_accounts.c.person_id == pid)
                        .order_by(portal_accounts.c.id.desc())).mappings().first()
    assert row and row["status"] == "invited"
    assert row["email"] == raw_email.strip()                        # email trimmed before storage


def test_invite_form_out_of_scope_redirects_error_and_creates_nothing():
    pid, hid = _person_household()
    resp = portal_admin_invite_form(
        request=_req(), person_id=pid, household_id=hid, email="x@example.com",
        display_name="NoScope", access_type="self", organization_id=None,
        principal=_principal(_staff_user(), {"client.write"}))      # no record.write_all, no assignment
    assert resp.status_code == 303 and "error=" in resp.headers["location"]
    with engine.connect() as c:
        n = c.execute(select(portal_accounts.c.id).where(portal_accounts.c.person_id == pid)).fetchall()
    assert n == []                                                  # scope failure invited nobody


def test_invite_form_route_is_capability_gated_and_scope_checked():
    src = inspect.getsource(portal_admin_invite_form)
    assert 'require_capability("client.write")' in src              # same gate as the JSON invite
    assert "record_in_scope" in src                                 # record-level scope enforced


def test_client_portal_template_exposes_the_invite_form():
    html = open("app/templates/admin/client_portal.html", encoding="utf-8").read()
    assert 'action="/admin/client-portal/invite-form"' in html
    for field in ('name="display_name"', 'name="email"', 'name="person_id"', 'name="household_id"',
                  'name="access_type"'):
        assert field in html, field
