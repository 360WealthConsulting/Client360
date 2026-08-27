"""Inviting a client is client-centric: staff search a person, they never type internal ids.

The old form asked staff for ``person_id``, ``household_id`` and ``organization_id`` and offered a
raw ``self / joint / delegate`` dropdown. That exposed database keys as a normal workflow and made
the browser the authority on which record was granted external access.

Now staff search by name / email / phone through the existing principal-scoped ``universal_search``,
pick a person, and every internal id is derived and re-validated server-side by
``app.portal.invite_targets`` on each submission. Authorization semantics are unchanged: ``self``
still means "this person only" and ``joint`` still means "this household, expanded", exactly as
``app.portal.service._resolve_scope`` implements them.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import (
    engine,
    households,
    people,
    portal_access_grants,
    portal_accounts,
    users,
)
from app.portal import invite_targets
from app.routes.portal_admin import (
    portal_admin_client_search,
    portal_admin_invite_form,
)
from app.security.models import Principal

CANONICAL = "https://portal.example.com"


# --- seeding -----------------------------------------------------------------------

def _staff_user() -> int:
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"staff-{sfx}@example.com", normalized_email=f"staff-{sfx}@example.com",
            display_name="Invite Staff", auth_subject=f"staff-{sfx}", status="active")
            .returning(users.c.id)).scalar_one()


def _household(name=None) -> int:
    with engine.begin() as c:
        return c.execute(households.insert().values(
            name=name or f"HH {uuid.uuid4().hex[:8]}").returning(households.c.id)).scalar_one()


def _person(*, first, last, household_id=None, email=None, phone=None, active=True, city=None):
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last}",
            primary_email=email, normalized_email=(email or "").lower() or None,
            primary_phone=phone, city=city, active=active, household_id=household_id)
            .returning(people.c.id)).scalar_one()


def _principal(uid, caps=("client.read", "client.write", "record.read_all", "record.write_all")):
    """Search scopes on record.read_all; the invite write path scopes on record.write_all."""
    return Principal(uid, "staff@example.com", "Staff", frozenset(caps))


def _render_admin_home():
    """The real rendered staff page, so template wiring is proven rather than assumed."""
    from app.routes.portal_admin import portal_admin_home
    request = _req()
    request.url = SimpleNamespace(path="/admin/client-portal")
    return portal_admin_home(request, principal=_principal(_staff_user())).body.decode("utf-8")


def _req(session=None):
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}"),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        query_params={}, session={} if session is None else session,
        url_for=lambda name: "http://inbound-host.invalid/portal/login")


@pytest.fixture
def canonical_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", CANONICAL)
    yield CANONICAL


def _grant_for(person_id):
    with engine.connect() as c:
        account_id = c.scalar(select(portal_accounts.c.id)
                              .where(portal_accounts.c.person_id == person_id)
                              .order_by(portal_accounts.c.id.desc()))
        return account_id, c.execute(select(portal_access_grants).where(
            portal_access_grants.c.portal_account_id == account_id)).mappings().one()


# --- staff never type internal ids ---------------------------------------------------

def test_the_form_does_not_accept_a_person_id_or_household_id_as_typed_fields():
    """The handler's contract: a selected person and an email. No household, no organization."""
    import inspect

    params = inspect.signature(portal_admin_invite_form).parameters
    assert "household_id" not in params, "staff would still be typing a household id"
    assert "organization_id" not in params, "organization is not part of a normal client invitation"
    assert "display_name" not in params, "the display name comes from the client record"
    assert set(params) >= {"person_id", "email", "access_type"}


def test_the_page_offers_a_client_search_instead_of_id_fields():
    html = _render_admin_home()
    assert 'id="client-search"' in html
    assert "Person ID" not in html and "Household ID" not in html and "Organization ID" not in html
    # The person id survives only as a hidden selection, re-validated server-side.
    assert 'type="hidden" name="person_id"' in html
    # The search endpoint lives in the external script, not inline: the site CSP is
    # default-src 'self' with no 'unsafe-inline', so an inline <script> never executes.
    assert '/static/js/client_portal_admin.js' in html
    from pathlib import Path
    js = Path("app/static/js/client_portal_admin.js").read_text()
    assert "/admin/client-portal/client-search" in js


def test_the_page_tells_staff_what_to_do_when_the_client_does_not_exist():
    """No canonical create-person workflow exists, so the form points at the real entry point
    rather than inventing a second person-creation system inside portal admin."""
    html = _render_admin_home()
    assert "Add them to Client360 first" in html
    assert 'href="/search"' in html


# --- search: scoped, human-readable, duplicate-safe ------------------------------------

def test_existing_people_can_be_found_by_name_email_and_phone():
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"Nguyen Household {sfx}")
    _person(first="Thanh", last=f"Nguyen{sfx}", household_id=hid,
            email=f"thanh-{sfx}@example.com", phone=f"555{sfx[:7]}")
    principal = _principal(_staff_user())
    for query in (f"Nguyen{sfx}", f"thanh-{sfx}@example.com", f"555{sfx[:7]}"):
        found = invite_targets.search_people(principal, query)
        assert [r for r in found if r["last_name"] == f"Nguyen{sfx}"], f"not found by {query!r}"


def test_search_results_distinguish_duplicate_names_without_showing_raw_ids():
    """Two people with the SAME name must be safely tellable apart."""
    sfx = uuid.uuid4().hex[:8]
    h1, h2 = _household(f"Alpha HH {sfx}"), _household(f"Beta HH {sfx}")
    _person(first="Chris", last=f"Dup{sfx}", household_id=h1,
            email=f"chris.a-{sfx}@example.com", phone="555-0001", city="Austin")
    _person(first="Chris", last=f"Dup{sfx}", household_id=h2,
            email=f"chris.b-{sfx}@example.com", phone="555-0002", city="Denver")
    rows = [r for r in invite_targets.search_people(_principal(_staff_user()), f"Dup{sfx}")]
    assert len(rows) == 2
    assert {r["email"] for r in rows} == {f"chris.a-{sfx}@example.com", f"chris.b-{sfx}@example.com"}
    assert {r["phone"] for r in rows} == {"555-0001", "555-0002"}
    assert {r["household_name"] for r in rows} == {f"Alpha HH {sfx}", f"Beta HH {sfx}"}
    assert {r["location"] for r in rows} == {"Austin", "Denver"}


def test_search_is_limited_to_people_the_principal_may_read():
    """A principal without read scope on anyone finds nobody — search cannot enumerate the firm."""
    sfx = uuid.uuid4().hex[:8]
    _person(first="Hidden", last=f"Scoped{sfx}", household_id=_household(),
            email=f"hidden-{sfx}@example.com")
    unscoped = _principal(_staff_user(), caps=("client.read", "client.write"))
    assert invite_targets.search_people(unscoped, f"Scoped{sfx}") == []
    assert invite_targets.search_people(_principal(_staff_user()), f"Scoped{sfx}")


def test_search_requires_two_characters_and_never_returns_everyone():
    assert invite_targets.search_people(_principal(_staff_user()), "") == []
    assert invite_targets.search_people(_principal(_staff_user()), "a") == []


def test_the_search_endpoint_is_capability_gated():
    import inspect
    src = inspect.getsource(portal_admin_client_search)
    assert 'require_capability("client.read")' in src


def test_inactive_people_are_not_offered_for_invitation():
    sfx = uuid.uuid4().hex[:8]
    _person(first="Gone", last=f"Inactive{sfx}", household_id=_household(), active=False)
    rows = invite_targets.search_people(_principal(_staff_user()), f"Inactive{sfx}")
    assert rows == []


# --- server-side resolution and tamper resistance ----------------------------------------

def test_the_selected_person_is_resolved_server_side():
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"Resolve HH {sfx}")
    pid = _person(first="Ada", last=f"Res{sfx}", household_id=hid,
                  email=f"ada-{sfx}@example.com", phone="555-9000")
    target = invite_targets.resolve_invite_target(_principal(_staff_user()), pid)
    assert target.person_id == pid
    assert target.household_id == hid, "the household was not derived from the record"
    assert target.first_name == "Ada" and target.email == f"ada-{sfx}@example.com"
    assert target.household_name == f"Resolve HH {sfx}"


def test_the_household_is_derived_and_never_submitted():
    """A caller cannot influence which household the grant anchors on — there is no parameter."""
    import inspect
    assert "household_id" not in inspect.signature(invite_targets.resolve_invite_target).parameters


@pytest.mark.parametrize("tampered", ["999999999", "-1", "0", "abc", "", None, "1 OR 1=1"])
def test_a_tampered_person_selection_fails_closed(tampered):
    with pytest.raises(invite_targets.InviteTargetError):
        invite_targets.resolve_invite_target(_principal(_staff_user()), tampered)


def test_an_out_of_scope_person_is_refused_without_disclosing_that_they_exist():
    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Secret", last=f"Scope{sfx}", household_id=_household())
    unscoped = _principal(_staff_user(), caps=("client.read", "client.write"))   # no record.write_all
    with pytest.raises(invite_targets.InviteTargetError) as exc:
        invite_targets.resolve_invite_target(unscoped, pid)
    missing = _principal(_staff_user(), caps=("client.read", "client.write"))
    with pytest.raises(invite_targets.InviteTargetError) as exc2:
        invite_targets.resolve_invite_target(missing, 999999999)
    assert str(exc.value) == str(exc2.value), "the refusal disclosed that the record exists"


def test_a_person_with_no_household_is_refused_with_an_actionable_message():
    pid = _person(first="Lone", last=f"NoHH{uuid.uuid4().hex[:8]}", household_id=None)
    with pytest.raises(invite_targets.InviteTargetError, match="not in a household"):
        invite_targets.resolve_invite_target(_principal(_staff_user()), pid)


def test_a_tampered_person_id_creates_no_account(canonical_origin):
    resp = portal_admin_invite_form(
        request=_req(), person_id="999999999", email="x@example.com", access_type="self",
        principal=_principal(_staff_user()))
    assert resp.status_code == 303 and "error=" in resp.headers["location"]
    with engine.connect() as c:
        assert c.execute(select(portal_accounts.c.id).where(
            portal_accounts.c.email == "x@example.com")).fetchall() == []


# --- access semantics are UNCHANGED --------------------------------------------------------

def test_the_staff_labels_map_only_onto_existing_grant_types():
    assert invite_targets.PERMITTED_ACCESS_TYPES == {"self", "joint"}
    labels = {code: label for code, label, _ in invite_targets.ACCESS_CHOICES}
    assert labels["self"] == "Client access"
    assert labels["joint"] == "Household access"
    assert invite_targets.DEFAULT_ACCESS_TYPE == "self", "the safest option must be the default"
    assert invite_targets.ACCESS_CHOICES[0][0] == "self", "the default must be offered first"
    html = _render_admin_home()
    assert "Client access" in html and "Household access" in html
    assert 'value="self" checked' in html.replace('"self"', '"self"'), "self is not preselected"


def test_ordinary_client_invitation_creates_the_same_self_grant_as_before(canonical_origin):
    """Authorization equivalence: a self grant carries the person and does NOT expand the household."""
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"Self HH {sfx}")
    pid = _person(first="Sam", last=f"Self{sfx}", household_id=hid, email=f"sam-{sfx}@example.com")
    resp = portal_admin_invite_form(request=_req(), person_id=str(pid), email="",
                                    access_type="self", principal=_principal(_staff_user()))
    assert resp.status_code == 303 and "error=" not in resp.headers["location"]
    _account_id, grant = _grant_for(pid)
    assert grant["access_type"] == "self"
    assert grant["person_id"] == pid, "a self grant must carry the person"
    assert grant["household_id"] == hid
    assert grant["organization_id"] is None
    assert grant["permissions"] == {"messages": True, "documents": True, "tasks": True}


def test_household_invitation_creates_the_same_joint_grant_as_before(canonical_origin):
    """A joint grant carries NO person; _resolve_scope expands it to the household instead."""
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"Joint HH {sfx}")
    pid = _person(first="Pat", last=f"Joint{sfx}", household_id=hid, email=f"pat-{sfx}@example.com")
    _person(first="Robin", last=f"Joint{sfx}", household_id=hid)      # a second member
    resp = portal_admin_invite_form(request=_req(), person_id=str(pid), email="",
                                    access_type="joint", principal=_principal(_staff_user()))
    assert "error=" not in resp.headers["location"]
    _account_id, grant = _grant_for(pid)
    assert grant["access_type"] == "joint"
    assert grant["person_id"] is None, "a joint grant must not carry a person"
    assert grant["household_id"] == hid


def test_household_access_is_refused_when_it_would_reach_nobody_else(canonical_origin):
    """A one-person household under joint access would silently create an emptier scope."""
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"Solo HH {sfx}")
    pid = _person(first="Solo", last=f"Only{sfx}", household_id=hid, email=f"solo-{sfx}@e.test")
    resp = portal_admin_invite_form(request=_req(), person_id=str(pid), email="",
                                    access_type="joint", principal=_principal(_staff_user()))
    assert "error=" in resp.headers["location"]
    with engine.connect() as c:
        assert c.execute(select(portal_accounts.c.id).where(
            portal_accounts.c.person_id == pid)).fetchall() == []


def test_delegate_is_not_offered_and_is_refused_if_submitted(canonical_origin):
    """'delegate' is absent from _resolve_scope's expansion set {joint, trusted, delegated}, so such
    a grant sees nothing. It must not be offered, and must not be silently remapped to 'delegated' —
    that would widen the client's access to the whole household."""
    assert "delegate" not in invite_targets.PERMITTED_ACCESS_TYPES
    assert "delegated" not in invite_targets.PERMITTED_ACCESS_TYPES
    html = _render_admin_home()
    assert 'value="delegate"' not in html and 'value="delegated"' not in html
    assert invite_targets.AUTHORIZED_REPRESENTATIVE_NOTICE in html, (
        "staff are not told why authorized-representative access is unavailable")

    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Del", last=f"Egate{sfx}", household_id=_household(),
                  email=f"del-{sfx}@example.com")
    for submitted in ("delegate", "delegated", "trusted", "admin", "SELF", "self;joint"):
        resp = portal_admin_invite_form(request=_req(), person_id=str(pid), email="",
                                        access_type=submitted, principal=_principal(_staff_user()))
        assert "error=" in resp.headers["location"], f"{submitted!r} was accepted"
    with engine.connect() as c:
        assert c.execute(select(portal_accounts.c.id).where(
            portal_accounts.c.person_id == pid)).fetchall() == []


def test_surrounding_whitespace_on_a_valid_choice_is_normalised_not_rejected():
    """A form value of 'self ' is the permitted choice with stray whitespace, not a tamper attempt;
    it normalises through the same allow-list rather than bypassing it."""
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"WS HH {sfx}")
    pid = _person(first="Wsp", last=f"Ace{sfx}", household_id=hid, email=f"ws-{sfx}@example.com")
    target = invite_targets.resolve_invite_target(_principal(_staff_user()), pid)
    assert invite_targets.validate_access_type(" self ", target) == "self"
    assert invite_targets.validate_access_type(None, target) == "self"      # absent → safe default
    with pytest.raises(invite_targets.InviteTargetError):
        invite_targets.validate_access_type("SELF", target)                 # case is not guessed


def test_the_scope_resolver_still_treats_self_and_joint_exactly_as_before():
    """Read the semantics straight from the service this form depends on."""
    import inspect

    from app.portal import service
    src = inspect.getsource(service._resolve_scope)
    assert '{"joint", "trusted", "delegated"}' in src, "household expansion set changed"
    assert 'gate("portal.household_enabled")' in src, "the household gate was removed"
    invite_src = inspect.getsource(service.invite_portal_account)
    assert 'access_type == "self"' in invite_src, "self-grant person binding changed"


# --- organization is not part of a normal client invitation ---------------------------------

def test_a_client_invitation_never_sets_an_organization(canonical_origin):
    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Org", last=f"Free{sfx}", household_id=_household(),
                  email=f"org-{sfx}@example.com")
    portal_admin_invite_form(request=_req(), person_id=str(pid), email="", access_type="self",
                             principal=_principal(_staff_user()))
    _account_id, grant = _grant_for(pid)
    assert grant["organization_id"] is None


def test_the_employer_json_invite_still_supports_organization_scope():
    """Employer/organization invitations keep using the JSON API — unchanged by this UI change."""
    import inspect

    from app.routes import portal_admin
    assert "organization_id" in inspect.getsource(portal_admin.PortalInvite)
    assert "organization_id=payload.organization_id" in inspect.getsource(
        portal_admin.portal_admin_invite)


# --- email semantics -------------------------------------------------------------------------

def test_the_portal_email_defaults_to_the_client_record_but_may_differ(canonical_origin):
    """portal_accounts.email is a contact address, not an identity key — Microsoft sign-in binds the
    immutable subject — so staff may legitimately send the invitation somewhere else."""
    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Mail", last=f"Diff{sfx}", household_id=_household(),
                  email=f"record-{sfx}@example.com")
    portal_admin_invite_form(request=_req(), person_id=str(pid), email=f"other-{sfx}@example.com",
                             access_type="self", principal=_principal(_staff_user()))
    with engine.connect() as c:
        stored = c.scalar(select(portal_accounts.c.email)
                          .where(portal_accounts.c.person_id == pid)
                          .order_by(portal_accounts.c.id.desc()))
        person_email = c.scalar(select(people.c.primary_email).where(people.c.id == pid))
    assert stored == f"other-{sfx}@example.com"
    assert person_email == f"record-{sfx}@example.com", "the client record was modified"


def test_an_invitation_with_no_email_anywhere_is_refused(canonical_origin):
    pid = _person(first="No", last=f"Mail{uuid.uuid4().hex[:8]}", household_id=_household(),
                  email=None)
    resp = portal_admin_invite_form(request=_req(), person_id=str(pid), email="",
                                    access_type="self", principal=_principal(_staff_user()))
    assert "error=" in resp.headers["location"]
    with engine.connect() as c:
        assert c.execute(select(portal_accounts.c.id).where(
            portal_accounts.c.person_id == pid)).fetchall() == []


# --- the one-time handoff and token handling still hold ----------------------------------------

def test_the_invitation_still_produces_the_one_time_activation_url(canonical_origin):
    from app.routes.portal_admin import HANDOFF_SESSION_KEY
    from app.portal import invitation_handoff

    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Hand", last=f"Off{sfx}", household_id=_household(),
                  email=f"hand-{sfx}@example.com")
    session: dict = {}
    resp = portal_admin_invite_form(request=_req(session), person_id=str(pid), email="",
                                    access_type="self", principal=_principal(_staff_user()))
    assert "error=" not in resp.headers["location"]
    payload = invitation_handoff.take(session[HANDOFF_SESSION_KEY])
    assert payload and payload["url"].startswith(f"{CANONICAL}/portal/login?invitation=")
    assert invitation_handoff.take(session.get(HANDOFF_SESSION_KEY)) is None   # one-time


def test_the_raw_token_still_never_leaks_through_the_new_form(canonical_origin):
    from unittest.mock import patch
    from urllib.parse import parse_qs, urlsplit

    from app.routes.portal_admin import HANDOFF_SESSION_KEY
    from app.portal import invitation_handoff
    from app.db import portal_invitations
    from app.portal.service import _hash

    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Leak", last=f"Check{sfx}", household_id=_household(),
                  email=f"leak-{sfx}@example.com")
    session: dict = {}
    captured = []
    with patch("app.routes.portal_admin.write_audit_event",
               side_effect=lambda **kw: captured.append(kw)):
        resp = portal_admin_invite_form(request=_req(session), person_id=str(pid), email="",
                                        access_type="self", principal=_principal(_staff_user()))
    url = invitation_handoff.take(session[HANDOFF_SESSION_KEY])["url"]
    token = parse_qs(urlsplit(url).query)["invitation"][0]

    assert token not in resp.headers["location"], "the token reached the redirect URL"
    assert "invitation" not in resp.headers["location"]
    assert captured and all(token not in repr(e) for e in captured), "the token reached the audit"
    with engine.connect() as c:
        row = c.execute(select(portal_invitations)
                        .order_by(portal_invitations.c.id.desc())).mappings().first()
    assert token not in {str(v) for v in row.values()}, "the token was persisted in plaintext"
    assert row["token_hash"] == _hash(token)


def test_the_invitation_still_activates_through_the_microsoft_callback_path(canonical_origin):
    """End to end: the new form still produces a credential accept_invitation consumes, and the
    account binds the Microsoft subject with MFA — unchanged."""
    from urllib.parse import parse_qs, urlsplit

    from app.portal.service import accept_invitation, sign_in_with_subject
    from app.routes.portal_admin import HANDOFF_SESSION_KEY
    from app.portal import invitation_handoff

    sfx = uuid.uuid4().hex[:8]
    pid = _person(first="Flow", last=f"End{sfx}", household_id=_household(),
                  email=f"flow-{sfx}@example.com")
    session: dict = {}
    portal_admin_invite_form(request=_req(session), person_id=str(pid), email="",
                             access_type="self", principal=_principal(_staff_user()))
    url = invitation_handoff.take(session[HANDOFF_SESSION_KEY])["url"]
    token = parse_qs(urlsplit(url).query)["invitation"][0]
    subject = f"microsoft:OID-{uuid.uuid4().hex[:10]}"
    account_id = accept_invitation(token, subject, True)
    assert sign_in_with_subject(subject, True) == account_id
    with pytest.raises(ValueError, match="MFA"):
        sign_in_with_subject(subject, False)          # MFA enforcement untouched


def test_portal_gates_and_identity_binding_are_untouched():
    import inspect

    from app.portal import gate, service
    assert "portal.production_signed_off" in inspect.getsource(gate)
    src = inspect.getsource(service.accept_invitation)
    assert "auth_subject=auth_subject" in src and "mfa_verified" in src
