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
from urllib.parse import quote

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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
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
    # The four human fields ARE the search now; there is no separate generic box.
    assert all(f'name="{n}"' in html for n in ("first_name", "last_name", "email", "phone"))
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

def _unique_phone_digits() -> str:
    """A 10-digit number unique to one test.

    Deliberately NOT a shared constant: the test database accumulates rows across runs, and a
    search for a phone every seeded person shares would match more people than the result limit
    returns — making the assertion depend on how many times the suite had been run."""
    return "540" + f"{uuid.uuid4().int % 10_000_000:07d}"


def _punctuate(digits: str, style: str) -> str:
    """The same 10-digit number as a person would actually type it, with or without a US
    country code. The country-code forms are query tolerance only — nothing is stored that way."""
    a, b, c = digits[:3], digits[3:6], digits[6:]
    return {
        "bare": digits, "dashes": f"{a}-{b}-{c}", "parens": f"({a}) {b}-{c}",
        "spaces": f"{a} {b} {c}", "dots": f"{a}.{b}.{c}",
        "cc_spaces": f"+1 {a} {b} {c}", "cc_parens": f"+1 ({a}) {b}-{c}",
        "cc_dashes": f"1-{a}-{b}-{c}", "cc_bare": f"1{digits}", "cc_plus_bare": f"+1{digits}",
    }[style]


def _searchable_person(sfx, *, first=None, phone=None, digits=None):
    """One person reachable by every supported field. ``normalized_phone`` is written exactly as
    app.services.people._normalize_phone writes it — digits only."""
    from app.services.people import _normalize_phone
    digits = digits or _unique_phone_digits()
    phone = phone if phone is not None else _punctuate(digits, "parens")
    # The names are suffixed for the same reason the phone is: a bare "Michael" would match every
    # person seeded by every earlier run, and the result limit would decide the assertion.
    first = first or f"Michael{sfx}"
    hid = _household(f"Shelton Household {sfx}")
    email = f"michael.shelton-{sfx}@example.com"
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=f"Shelton{sfx}", full_name=f"{first} Shelton{sfx}",
            primary_email=email, normalized_email=email.lower(),
            primary_phone=phone, normalized_phone=_normalize_phone(phone),
            active=True, household_id=hid).returning(people.c.id)).scalar_one()
    return pid, email


def _finds(principal, query, sfx):
    return [r for r in invite_targets.search_people(principal, query)
            if r["last_name"] == f"Shelton{sfx}"]


def test_existing_people_can_be_found_by_name_email_and_phone():
    sfx = uuid.uuid4().hex[:8]
    hid = _household(f"Nguyen Household {sfx}")
    _person(first="Thanh", last=f"Nguyen{sfx}", household_id=hid,
            email=f"thanh-{sfx}@example.com", phone=f"555{sfx[:7]}")
    principal = _principal(_staff_user())
    for query in (f"Nguyen{sfx}", f"thanh-{sfx}@example.com", f"555{sfx[:7]}"):
        found = invite_targets.search_people(principal, query)
        assert [r for r in found if r["last_name"] == f"Nguyen{sfx}"], f"not found by {query!r}"


# --- one box, every field ---------------------------------------------------------------

def test_a_person_is_found_by_first_name():
    """Matches people.first_name — not just the full_name column."""
    sfx = uuid.uuid4().hex[:8]
    _searchable_person(sfx)
    assert _finds(_principal(_staff_user()), f"Michael{sfx}", sfx)


def test_a_person_is_found_by_last_name():
    sfx = uuid.uuid4().hex[:8]
    _searchable_person(sfx)
    assert _finds(_principal(_staff_user()), f"Shelton{sfx}", sfx)


def test_a_person_is_found_by_combined_first_and_last_name():
    sfx = uuid.uuid4().hex[:8]
    _searchable_person(sfx)
    assert _finds(_principal(_staff_user()), f"Michael{sfx} Shelton{sfx}", sfx)


def test_a_person_is_found_by_exact_email():
    sfx = uuid.uuid4().hex[:8]
    _, email = _searchable_person(sfx)
    assert _finds(_principal(_staff_user()), email, sfx)
    assert _finds(_principal(_staff_user()), email.upper(), sfx), "email search must be case-insensitive"


def test_a_person_is_found_by_partial_email_and_domain():
    """The existing architecture is substring (ILIKE %q%), so partial local part and domain work."""
    sfx = uuid.uuid4().hex[:8]
    _searchable_person(sfx)
    principal = _principal(_staff_user())
    assert _finds(principal, f"michael.shelton-{sfx}", sfx)      # local part only
    assert _finds(principal, f"shelton-{sfx}@example.com", sfx)  # tail of the address


@pytest.mark.parametrize("style", [
    "bare",          # 5405551212        (already worked)
    "dashes",        # 540-555-1212      (regression: previously NO match)
    "parens",        # (540) 555-1212
    "spaces",        # 540 555 1212      (regression: previously NO match)
    "dots",          # 540.555.1212
    "cc_spaces",     # +1 540 555 1212   (US country code — query tolerance)
    "cc_parens",     # +1 (540) 555-1212
    "cc_dashes",     # 1-540-555-1212
    "cc_bare",       # 15405551212
    "cc_plus_bare",  # +15405551212
])
def test_the_same_stored_phone_is_found_however_it_is_punctuated(style):
    """normalized_phone holds digits only, so the QUERY must be normalised the same way."""
    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()
    _searchable_person(sfx, digits=digits)              # stored as "(540) 555-1212" style
    typed = _punctuate(digits, style)
    assert _finds(_principal(_staff_user()), typed, sfx), f"{typed!r} did not match the stored phone"


@pytest.mark.parametrize("stored_style", ["parens", "dashes", "dots", "spaces", "bare"])
def test_a_phone_stored_with_punctuation_is_found_by_bare_digits(stored_style):
    """The reverse direction: whatever punctuation the RECORD carries, digits find it."""
    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()
    _searchable_person(sfx, digits=digits, phone=_punctuate(digits, stored_style))
    assert _finds(_principal(_staff_user()), digits, sfx), f"stored as {stored_style}"


def test_phone_normalisation_reuses_the_canonical_helper():
    """Not a parallel phone model: the same function that writes the column reads the query."""
    import inspect

    from app.services import universal_search as us
    assert "from app.services.people import _normalize_phone" in inspect.getsource(
        us._phone_query_digits)


@pytest.mark.parametrize("query, expected", [
    ("540-555-1212", "5405551212"),
    ("(540) 555-1212", "5405551212"),
    ("540 555 1212", "5405551212"),
    ("540.555.1212", "5405551212"),
    ("5405551212", "5405551212"),
    ("1234 Main St", None),        # an address is not a phone
    ("Michael", None),             # a name is not a phone
    ("Suite 12", None),
    ("12", None),                  # too few digits to be meaningful
    ("", None),
    (None, None),
])
def test_only_phone_shaped_queries_are_normalised(query, expected):
    """The guard that keeps name/email search free of new noise."""
    from app.services.universal_search import _phone_query_digits
    assert _phone_query_digits(query) == expected


def test_a_numeric_address_query_does_not_surface_unrelated_phone_numbers():
    """An address that happens to share digits with a phone must not drag that person in."""
    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()
    _searchable_person(sfx, digits=digits)
    assert _finds(_principal(_staff_user()), f"{digits[-4:]} Main Street", sfx) == []


# --- US country code: query tolerance only -----------------------------------------------

def test_a_country_code_query_finds_a_number_stored_without_one():
    """The everyday case: staff type the number the way the client wrote it, with a +1."""
    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()
    _searchable_person(sfx, digits=digits)                    # stored normalized as 10 digits
    principal = _principal(_staff_user())
    for typed in (f"+1 {digits[:3]} {digits[3:6]} {digits[6:]}",
                  f"+1 ({digits[:3]}) {digits[3:6]}-{digits[6:]}",
                  f"1-{digits[:3]}-{digits[3:6]}-{digits[6:]}"):
        assert _finds(principal, typed, sfx), f"{typed!r} did not match the stored 10-digit number"


def test_an_eleven_digit_number_stored_with_a_leading_one_is_still_found():
    """The original digits are searched too, so a genuinely 11-digit stored value is not excluded."""
    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()
    _searchable_person(sfx, digits="1" + digits, phone=f"+1 {digits}")
    principal = _principal(_staff_user())
    assert _finds(principal, "1" + digits, sfx), "the 11-digit stored value became unfindable"
    assert _finds(principal, f"+1 {digits}", sfx)


@pytest.mark.parametrize("query, expected", [
    # 11 digits beginning with 1 → try the country-code-stripped form as well.
    ("+1 (540) 555-1212", ("15405551212", "5405551212")),
    ("1-540-555-1212", ("15405551212", "5405551212")),
    ("15405551212", ("15405551212", "5405551212")),
    # 11 digits NOT beginning with 1 → a real number; never truncated.
    ("25405551212", ("25405551212",)),
    ("98765432109", ("98765432109",)),
    # Other lengths are left exactly as-is.
    ("5405551212", ("5405551212",)),
    ("1234567890123", ("1234567890123",)),
    ("1540555121", ("1540555121",)),          # 10 digits starting with 1 — not a country code
    # Not phone-shaped at all.
    ("1234 Main St", ()),
    ("Michael", ()),
    ("", ()),
    (None, ()),
])
def test_only_a_leading_us_country_code_is_stripped(query, expected):
    from app.services.universal_search import _phone_query_variants
    assert _phone_query_variants(query) == expected


def test_an_eleven_digit_number_not_starting_with_one_is_not_truncated_in_search():
    """A real 11-digit number must not silently match a different 10-digit one."""
    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()                       # 10 digits, starts "540"
    _searchable_person(sfx, digits=digits)
    # "2" + the stored number is a DIFFERENT number; truncating it would wrongly match.
    assert _finds(_principal(_staff_user()), "2" + digits, sfx) == []


def test_country_code_tolerance_does_not_touch_stored_data():
    """Query compatibility only: no persistent second normalization scheme."""
    import inspect

    from app.services import universal_search as us
    source = inspect.getsource(us._phone_query_variants)
    for mutating in ("people.update(", "people.insert(", "normalized_phone=", "primary_phone="):
        assert mutating not in source, f"{mutating} in a query helper rewrites stored data"

    sfx = uuid.uuid4().hex[:8]
    digits = _unique_phone_digits()
    pid, _ = _searchable_person(sfx, digits=digits)
    _finds(_principal(_staff_user()), f"+1 {digits}", sfx)          # run a country-code search
    with engine.connect() as c:
        row = c.execute(select(people.c.primary_phone, people.c.normalized_phone)
                        .where(people.c.id == pid)).mappings().one()
    assert row["normalized_phone"] == digits, "the search rewrote normalized_phone"
    assert row["primary_phone"] == _punctuate(digits, "parens"), "the search rewrote primary_phone"


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


# --- the four human fields ARE the search --------------------------------------------------

def _family(sfx):
    """Three people who overlap on first name, last name and household — so a single field is
    ambiguous and combining fields is what narrows it.

    EVERY identifying value is suffixed or generated per test. The test database accumulates rows
    across runs, so a shared "Michael" or a shared 555 number would match every earlier run's
    people and the result limit, not the code, would decide the assertion."""
    hid = _household(f"Shelton HH {sfx}")
    made = {"household_id": hid, "first": f"Michael{sfx}", "other_first": f"Sarah{sfx}",
            "last": f"Shelton{sfx}", "other_last": f"Jones{sfx}"}
    for key, first, last in [("michael", made["first"], made["last"]),
                             ("mjones", made["first"], made["other_last"]),
                             ("sarah", made["other_first"], made["last"])]:
        digits = _unique_phone_digits()
        made[key + "_phone_digits"] = digits
        made[key + "_email"] = f"{key}-{sfx}@example.com"
        with engine.begin() as c:
            made[key] = c.execute(people.insert().values(
                first_name=first, last_name=last, full_name=f"{first} {last}",
                primary_email=made[key + "_email"], normalized_email=made[key + "_email"],
                primary_phone=_punctuate(digits, "parens"), normalized_phone=digits,
                active=True, household_id=hid).returning(people.c.id)).scalar_one()
    return made


def _names(principal, **kw):
    return sorted(r["full_name"] for r in invite_targets.search_people(principal, **kw))


def _names_ranked(principal, **kw):
    """Result order as returned — best match first."""
    return [r["full_name"] for r in invite_targets.search_people(principal, **kw)]


@pytest.mark.parametrize("field", ["first_name", "last_name", "email", "phone"])
def test_each_human_field_is_a_typeable_input(field):
    """Nothing on the invite form is readonly: all four fields accept typing and drive search."""
    html = _render_admin_home()
    assert f'name="{field}"' in html, f"{field} is not a form field"
    marker = f'name="{field}"'
    tag = html[html.index(marker) - 120: html.index(marker) + 120]
    assert "readonly" not in tag, f"{field} is readonly and cannot be typed into"
    assert "disabled" not in tag


def test_the_form_no_longer_has_a_separate_generic_search_box():
    """One search system, not two: the four fields are the search."""
    html = _render_admin_home()
    assert 'id="client-search"' not in html and "Find the client" not in html


def test_first_name_only_search():
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    found = _names(_principal(_staff_user()), first_name=fam["first"])
    assert f'{fam["first"]} {fam["last"]}' in found
    assert f'{fam["first"]} {fam["other_last"]}' in found
    assert f'{fam["other_first"]} {fam["last"]}' not in found


def test_last_name_only_search():
    sfx = uuid.uuid4().hex[:8]
    _family(sfx)
    fam = _family(sfx)
    found = _names(_principal(_staff_user()), last_name=fam["last"])
    assert f'{fam["first"]} {fam["last"]}' in found
    assert f'{fam["other_first"]} {fam["last"]}' in found
    assert f'{fam["first"]} {fam["other_last"]}' not in found


def test_first_plus_last_name_ranks_the_exact_person_first():
    """Both names exact is the strongest name match, so that person leads — but the people who
    match only one of the two names remain visible as candidates."""
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    found = _names_ranked(_principal(_staff_user()),
                          first_name=fam["first"], last_name=fam["last"])
    assert found[0] == f'{fam["first"]} {fam["last"]}'
    assert f'{fam["other_first"]} {fam["last"]}' in found        # same last name
    assert f'{fam["first"]} {fam["other_last"]}' in found        # same first name


def test_email_search_and_partial_email_search():
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    principal = _principal(_staff_user())
    assert _names(principal, email=fam["michael_email"]) == [f'{fam["first"]} {fam["last"]}']
    assert _names(principal, email=f"sarah-{sfx}") == [f'{fam["other_first"]} {fam["last"]}']


@pytest.mark.parametrize("style", ["bare", "dashes", "parens", "spaces", "dots",
                                   "cc_spaces", "cc_dashes"])
def test_phone_search_in_every_supported_form(style):
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    typed = _punctuate(fam["michael_phone_digits"], style)
    assert _names(_principal(_staff_user()), phone=typed) == [f'{fam["first"]} {fam["last"]}']


def test_fields_combine_by_union_and_rank_the_strongest_match_first():
    """The four fields are alternate ways to FIND someone, not four mandatory filters. A field
    that matches nobody must not erase the candidates the other fields found."""
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    principal = _principal(_staff_user())
    # An exact phone outranks a shared first name.
    ranked = _names_ranked(principal, first_name=fam["first"],
                           phone=fam["mjones_phone_digits"])
    assert ranked[0] == f'{fam["first"]} {fam["other_last"]}'
    # A term matching nobody does not empty the result.
    still = _names_ranked(principal, first_name=fam["first"], last_name=fam["other_last"],
                          phone=fam["michael_phone_digits"])
    assert f'{fam["first"]} {fam["other_last"]}' in still
    assert f'{fam["first"]} {fam["last"]}' in still


def test_a_single_character_term_is_ignored_rather_than_matching_everyone():
    sfx = uuid.uuid4().hex[:8]
    _family(sfx)
    assert invite_targets.search_people(_principal(_staff_user()), first_name="M") == []


def test_multi_field_search_keeps_the_existing_authorization_scoping():
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    unscoped = _principal(_staff_user(), caps=("client.read", "client.write"))
    assert invite_targets.search_people(
        unscoped, first_name=fam["first"], last_name=fam["last"]) == []
    assert _names(_principal(_staff_user()), first_name=fam["first"], last_name=fam["last"])


def test_a_result_carries_everything_needed_to_select_and_populate_the_form():
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    row = invite_targets.search_people(
        _principal(_staff_user()), first_name=fam["first"], last_name=fam["last"])[0]
    assert row["person_id"] == fam["michael"]          # hidden state the JS stores
    assert row["first_name"] == fam["first"] and row["last_name"] == fam["last"]
    assert row["email"] == fam["michael_email"]
    assert row["phone"] == _punctuate(fam["michael_phone_digits"], "parens")
    assert row["household_name"] == f"Shelton HH {sfx}"


# --- the form boundary can no longer produce raw 422 JSON ------------------------------------

def test_no_invite_form_field_is_required_at_the_fastapi_boundary():
    """The root cause: with Form(...) FastAPI validated the body BEFORE the handler ran, so a
    missing person_id became RequestValidationError → raw Pydantic JSON in the browser. Every
    field now has a default, so the handler always runs and missing input is a banner."""
    import inspect

    from fastapi import params
    for name, prm in inspect.signature(portal_admin_invite_form).parameters.items():
        if isinstance(prm.default, params.Form):
            assert not prm.default.is_required(), (
                f"{name} is a required Form field; a submission without it returns raw 422 JSON")


def test_a_submission_with_no_selected_client_returns_the_admin_page_with_a_message():
    """Server-side, not JavaScript: this is what protects a direct or scripted POST."""
    from app.routes.portal_admin import NO_SELECTION_ERROR
    resp = portal_admin_invite_form(request=_req(), person_id="", email="x@example.com",
                                    access_type="self", principal=_principal(_staff_user()))
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/admin/client-portal?error=")
    assert quote(NO_SELECTION_ERROR) in location
    for leak in ("detail", "loc", "pydantic", "missing", "body"):
        assert leak not in location.lower(), f"validation internals leaked: {leak}"


def test_the_client_side_guard_and_the_server_use_the_same_wording():
    from app.routes.portal_admin import NO_SELECTION_ERROR
    js = (REPO_ROOT / "app" / "static" / "js" / "client_portal_admin.js").read_text()
    assert NO_SELECTION_ERROR in js, "the two guards would tell staff different things"


def test_this_route_never_produces_a_validation_error_at_all():
    """The fix is scoped to this route's boundary, not to global FastAPI behaviour.

    Because no field is required, FastAPI cannot raise RequestValidationError here — the handler
    always runs and returns the ordinary admin page. Nothing app-wide is changed to achieve that,
    so every other route keeps FastAPI's default validation behaviour untouched."""
    import inspect

    from fastapi import params

    from app.routes import portal_admin
    form_params = [p for p in inspect.signature(portal_admin.portal_admin_invite_form)
                   .parameters.values() if isinstance(p.default, params.Form)]
    assert form_params, "the handler no longer takes form fields"
    assert all(not p.default.is_required() for p in form_params)


def test_no_app_wide_validation_handler_ships_with_this_change():
    """Scope guard: the portal fix must not alter validation behaviour for the whole application."""
    main = (REPO_ROOT / "app" / "main.py").read_text()
    assert "RequestValidationError" not in main, (
        "an app-wide validation handler crept back into the portal change")
    templating = (REPO_ROOT / "app" / "templating.py").read_text()
    assert "_ERROR_TEMPLATES = {403, 404, 500}" in templating, "the error-template set was widened"
    assert not (REPO_ROOT / "app" / "templates" / "errors" / "400.html").exists()


# --- selection, invalidation, and the invariants -----------------------------------------------

def test_editing_an_identifying_field_invalidates_a_stale_selection():
    """first/last/phone identify WHO; changing one must drop the selection. Email is the
    invitation address and is deliberately exempt once a client is chosen."""
    js = (REPO_ROOT / "app" / "static" / "js" / "client_portal_admin.js").read_text()
    assert 'var IDENTIFYING = ["first", "last", "phone"];' in js
    assert "clearSelection()" in js
    assert 'if (chosen && key === "email") { return; }' in js


def test_the_submit_guard_blocks_an_unselected_submission_client_side():
    js = (REPO_ROOT / "app" / "static" / "js" / "client_portal_admin.js").read_text()
    assert 'form.addEventListener("submit"' in js
    assert "event.preventDefault()" in js
    assert "f.personId.value" in js


def test_the_person_id_remains_hidden_implementation_state():
    html = _render_admin_home()
    assert 'type="hidden" name="person_id"' in html
    assert "Person ID" not in html and "Household ID" not in html
    assert "person_id" not in html.replace('type="hidden" name="person_id"', "")


def test_selecting_a_client_still_produces_a_working_invitation(canonical_origin):
    """End to end after the redesign: resolve → grant → one-time handoff, all unchanged."""
    from app.routes.portal_admin import HANDOFF_SESSION_KEY
    from app.portal import invitation_handoff

    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    session: dict = {}
    resp = portal_admin_invite_form(
        request=_req(session), person_id=str(fam["michael"]),
        email=f"new-address-{sfx}@example.com", access_type="self",
        principal=_principal(_staff_user()))
    assert "error=" not in resp.headers["location"]
    _account_id, grant = _grant_for(fam["michael"])
    assert grant["access_type"] == "self" and grant["person_id"] == fam["michael"]
    payload = invitation_handoff.take(session[HANDOFF_SESSION_KEY])
    assert payload and payload["url"].startswith(f"{CANONICAL}/portal/login?invitation=")


def test_the_redesign_did_not_touch_the_security_invariants():
    import inspect

    from app.routes import portal_admin
    handler = inspect.getsource(portal_admin.portal_admin_invite_form)
    assert "resolve_invite_target" in handler and "validate_access_type" in handler
    assert "_remember_activation_url" in handler
    assert "record_in_scope" in inspect.getsource(invite_targets.resolve_invite_target)
    assert invite_targets.PERMITTED_ACCESS_TYPES == {"self", "joint"}


# --- ANY entered identifier is enough to find a client -----------------------------------------
#
# The production defect: staff typed a correct name and phone but a NEW email, and the page said
# "No clients found." The four fields were intersected, so one stale stored value hid the person
# entirely. They are alternate ways to find someone; the sets are now unioned and ranked.

def _stale_email_person(sfx):
    """A canonical record where 3 of 4 entered values will match and the email differs."""
    hid = _household(f"Shelton Household {sfx}")
    digits = _unique_phone_digits()
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=f"Michael{sfx}", last_name=f"Shelton{sfx}",
            full_name=f"Michael{sfx} Shelton{sfx}",
            primary_email=f"oldemail-{sfx}@example.com",
            normalized_email=f"oldemail-{sfx}@example.com",
            primary_phone=_punctuate(digits, "parens"), normalized_phone=digits,
            active=True, household_id=hid).returning(people.c.id)).scalar_one()
    return {"id": pid, "first": f"Michael{sfx}", "last": f"Shelton{sfx}",
            "email": f"oldemail-{sfx}@example.com", "digits": digits,
            "phone": _punctuate(digits, "parens"), "household": f"Shelton Household {sfx}"}


def test_the_production_case_three_of_four_match_and_the_email_differs():
    """The exact reported scenario: name and phone right, email new. Must be found, and first."""
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    ranked = _names_ranked(_principal(_staff_user()),
                           first_name=who["first"], last_name=who["last"],
                           email="michael@360wealthconsulting.com", phone=who["digits"])
    assert ranked, "no clients found — the non-matching email erased the other three matches"
    assert ranked[0] == f'{who["first"]} {who["last"]}'


@pytest.mark.parametrize("field", ["first", "last", "email", "phone"])
def test_any_single_matching_identifier_surfaces_the_client(field):
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    kw = {"first_name": {"first_name": who["first"]}, "last": {"last_name": who["last"]},
          "email": {"email": who["email"]}, "phone": {"phone": who["digits"]}}[
        {"first": "first_name", "last": "last", "email": "email", "phone": "phone"}[field]]
    assert f'{who["first"]} {who["last"]}' in _names_ranked(_principal(_staff_user()), **kw)


def test_a_wrong_email_alongside_a_right_phone_still_finds_the_client():
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    ranked = _names_ranked(_principal(_staff_user()),
                           email="definitely-not-theirs@example.com", phone=who["digits"])
    assert ranked[0] == f'{who["first"]} {who["last"]}'


def test_a_wrong_phone_alongside_a_right_email_still_finds_the_client():
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    ranked = _names_ranked(_principal(_staff_user()), email=who["email"], phone="9995551234")
    assert ranked[0] == f'{who["first"]} {who["last"]}'


@pytest.mark.parametrize("style", ["bare", "dashes", "parens", "spaces", "dots",
                                   "cc_spaces", "cc_dashes"])
def test_phone_formats_still_work_alongside_a_non_matching_email(style):
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    ranked = _names_ranked(_principal(_staff_user()), email="wrong@example.com",
                           phone=_punctuate(who["digits"], style))
    assert ranked[0] == f'{who["first"]} {who["last"]}'


def test_an_exact_email_outranks_every_other_kind_of_match():
    """Ranking tier 1: the strongest identifier wins even against several weaker ones."""
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    ranked = _names_ranked(_principal(_staff_user()),
                           first_name=fam["first"],           # matches two people
                           email=fam["sarah_email"])          # exact, matches one
    assert ranked[0] == f'{fam["other_first"]} {fam["last"]}'


def test_a_person_matching_more_fields_ranks_above_one_matching_fewer():
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    ranked = _names_ranked(_principal(_staff_user()),
                           first_name=fam["first"], last_name=fam["last"])
    assert ranked[0] == f'{fam["first"]} {fam["last"]}'        # both names
    assert ranked.index(f'{fam["first"]} {fam["last"]}') < ranked.index(
        f'{fam["first"]} {fam["other_last"]}')                # only the first name


def test_the_documented_ranking_tiers_are_implemented_in_order():
    from app.portal.invite_targets import _tier
    exact = lambda **kw: {**{f: None for f in ("first_name", "last_name", "email", "phone")}, **kw}
    assert _tier(exact(email="exact")) == 1
    assert _tier(exact(phone="exact")) == 2
    assert _tier(exact(first_name="exact", last_name="exact")) == 3
    assert _tier(exact(last_name="exact", first_name="partial")) == 4
    assert _tier(exact(first_name="exact", phone="partial")) == 5
    assert _tier(exact(email="partial")) == 6
    assert _tier(exact(phone="partial")) == 7
    assert _tier(exact(last_name="partial")) == 8
    assert _tier(exact(first_name="partial")) == 8


def test_candidates_show_canonical_stored_values_not_what_staff_typed():
    """Staff must be able to SEE that the stored email differs from the one they entered."""
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    row = invite_targets.search_people(
        _principal(_staff_user()), first_name=who["first"], last_name=who["last"],
        email="michael@360wealthconsulting.com", phone=who["digits"])[0]
    assert row["email"] == who["email"], "the typed email was echoed instead of the stored one"
    assert row["phone"] == who["phone"] and row["household_name"] == who["household"]
    assert row["full_name"] == f'{who["first"]} {who["last"]}'


def test_a_union_result_never_leaves_scope():
    """A union of principal-scoped sets is still principal-scoped."""
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    unscoped = _principal(_staff_user(), caps=("client.read", "client.write"))
    assert invite_targets.search_people(
        unscoped, first_name=who["first"], last_name=who["last"],
        email=who["email"], phone=who["digits"]) == []


def test_no_result_row_carries_a_household_id():
    sfx = uuid.uuid4().hex[:8]
    who = _stale_email_person(sfx)
    row = invite_targets.search_people(_principal(_staff_user()), phone=who["digits"])[0]
    assert "household_id" not in row, "an internal id reached the browser"
    assert set(row) == {"person_id", "first_name", "last_name", "full_name", "email", "phone",
                        "location", "household_name", "has_household"}


def test_returning_a_candidate_is_not_selecting_one():
    """A fuzzy/partial match must never auto-select: only an explicit click sets person_id."""
    js = (REPO_ROOT / "app" / "static" / "js" / "client_portal_admin.js").read_text()
    assert 'button.addEventListener("click", function () { select(person); });' in js
    # select() is CALLED from that click handler and nowhere else — not from render() or search().
    assert js.count("select(person);") == 1, "select() is invoked outside the click handler"
    assert js.count("function select(person)") == 1
    assert "results.length === 1" not in js and "rows.length === 1" not in js, (
        "a single candidate is being auto-selected")


def test_common_first_names_produce_candidates_without_selecting_any():
    """Several people share a first name: all are offered, none is chosen for the staff member."""
    sfx = uuid.uuid4().hex[:8]
    fam = _family(sfx)
    ranked = _names_ranked(_principal(_staff_user()), first_name=fam["first"])
    assert len(ranked) >= 2, "the ambiguous case should offer every candidate"
    assert f'{fam["first"]} {fam["last"]}' in ranked
    assert f'{fam["first"]} {fam["other_last"]}' in ranked
