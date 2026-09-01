"""The intended Messages access matrix, pinned per ROLE against the seeded capability catalogue.

``test_messages_route_capability`` proves the middleware and the route dependencies agree on WHICH
capability gates Messages. This file proves the other half: which production ROLES actually hold that
capability once msgcap01 has run.

    VIEW + REPLY   administrator, advisor, client_service, operations, senior_tax
    VIEW ONLY      tax_staff
    NO ACCESS      every other production role

`tax_preparer` is deliberately absent: it is a DEMO persona seeded by the demo seeder, not a
production role (``select count(*) from roles where code='tax_preparer'`` is 0 on a production
catalogue). Its demo grant is pinned separately below, in the demo credentials module where it lives.

The excluded roles are asserted EXPLICITLY rather than by "everything not in the allow-list", and a
final test fails if a role is ever added to the catalogue without being classified here — so a new
role cannot silently inherit or silently miss Messages access.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import engine

READ_CAP = "communications.message.read"
WRITE_CAP = "communications.message.write"

VIEW_AND_REPLY = ("administrator", "advisor", "client_service", "operations", "senior_tax")
VIEW_ONLY = ("tax_staff",)
NO_ACCESS = (
    "accounting",
    "payroll",
    "compliance",
    "reviewer",
    "read_only",
    "benefits_advisor",
    "benefits_compliance",
    "benefits_operations",
    "insurance_agent",
    "insurance_compliance",
    "insurance_operations",
)

CLASSIFIED = frozenset(VIEW_AND_REPLY) | frozenset(VIEW_ONLY) | frozenset(NO_ACCESS)


def _role_caps(role_code):
    with engine.connect() as c:
        return {r[0] for r in c.execute(text(
            "SELECT c.code FROM roles r "
            "JOIN role_capabilities rc ON rc.role_id = r.id "
            "JOIN capabilities c ON c.id = rc.capability_id "
            "WHERE r.code = :role"), {"role": role_code})}


# tests/test_e2_2_authorization.py seeds transient authorization fixtures named "e2_2-role-<suffix>".
# They are not production roles and must not be classified here - the same exclusion the architecture
# manifest applies to their "e2_2.cap.*" capabilities.
_FIXTURE_ROLE_PREFIX = "e2_2-role-"


def _catalogue_roles():
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("SELECT code FROM roles"))
                if not r[0].startswith(_FIXTURE_ROLE_PREFIX)}


# --- the capabilities exist at all -------------------------------------------

@pytest.mark.parametrize("cap", [READ_CAP, WRITE_CAP])
def test_the_messages_capabilities_are_seeded(cap):
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM capabilities WHERE code = :c"),
                         {"c": cap}).scalar() == 1, f"{cap} missing — has msgcap01 run?"


# --- VIEW + REPLY ------------------------------------------------------------

@pytest.mark.parametrize("role", VIEW_AND_REPLY)
def test_role_can_view_and_reply(role):
    caps = _role_caps(role)
    assert READ_CAP in caps, f"{role} must be able to VIEW Messages"
    assert WRITE_CAP in caps, f"{role} must be able to REPLY to Messages"


# --- VIEW ONLY ---------------------------------------------------------------

@pytest.mark.parametrize("role", VIEW_ONLY)
def test_role_can_view_but_cannot_reply(role):
    caps = _role_caps(role)
    assert READ_CAP in caps, f"{role} must be able to VIEW Messages"
    assert WRITE_CAP not in caps, (
        f"{role} must NOT be able to reply — a preparer reads for context; answering the client is "
        "the coordinator's job")


# --- NO ACCESS ---------------------------------------------------------------

@pytest.mark.parametrize("role", NO_ACCESS)
def test_role_has_no_messages_access(role):
    caps = _role_caps(role)
    assert READ_CAP not in caps, f"{role} must NOT be able to view client correspondence"
    assert WRITE_CAP not in caps, f"{role} must NOT be able to reply to clients"


def test_roles_holding_client_read_do_not_thereby_hold_messages():
    """The precise over-broad model this repair replaces. Several NO_ACCESS roles hold client.read;
    none of them may reach Messages through it."""
    for role in NO_ACCESS:
        caps = _role_caps(role)
        if "client.read" in caps:
            assert READ_CAP not in caps, (
                f"{role} holds client.read — that must not carry Messages access with it")


# --- structural invariants ---------------------------------------------------

def test_write_is_never_granted_without_read():
    """Effective write can never exist without read: the GET gate is the read capability, so a
    write-without-read grant would be a role that can post but never open the surface."""
    with engine.connect() as c:
        offenders = [r[0] for r in c.execute(text(
            "SELECT r.code FROM roles r WHERE EXISTS ("
            "  SELECT 1 FROM role_capabilities rc JOIN capabilities c ON c.id = rc.capability_id"
            "  WHERE rc.role_id = r.id AND c.code = :w) AND NOT EXISTS ("
            "  SELECT 1 FROM role_capabilities rc JOIN capabilities c ON c.id = rc.capability_id"
            "  WHERE rc.role_id = r.id AND c.code = :r)"), {"w": WRITE_CAP, "r": READ_CAP})]
    assert offenders == [], f"roles hold write without read: {offenders}"


def test_the_exact_set_of_roles_holding_each_capability():
    """Exact-set, not subset: an unintended grant anywhere fails this."""
    with engine.connect() as c:
        def holders(cap):
            return {r[0] for r in c.execute(text(
                "SELECT r.code FROM roles r "
                "JOIN role_capabilities rc ON rc.role_id = r.id "
                "JOIN capabilities c ON c.id = rc.capability_id "
                "WHERE c.code = :c"), {"c": cap})}
        assert holders(READ_CAP) == set(VIEW_AND_REPLY) | set(VIEW_ONLY)
        assert holders(WRITE_CAP) == set(VIEW_AND_REPLY)


def test_every_production_role_is_classified():
    """If a role is added to the catalogue later, it must be classified here deliberately — it may
    not silently inherit Messages access, nor silently miss an intended grant."""
    unclassified = _catalogue_roles() - CLASSIFIED
    assert unclassified == set(), (
        f"unclassified production roles: {sorted(unclassified)} — add each to VIEW_AND_REPLY, "
        "VIEW_ONLY or NO_ACCESS")


def test_tax_preparer_is_not_a_production_role():
    """Pins the architecture decision: tax_preparer stays a demo persona. If it ever appears in the
    production catalogue, the matrix above must be revisited rather than silently extended."""
    assert "tax_preparer" not in _catalogue_roles()


# --- the demo persona --------------------------------------------------------

def test_demo_tax_preparer_can_view_but_not_reply():
    """Demo-only, mirroring production tax_staff: read yes, write no."""
    from app.demo.credentials import TAX_PREPARER_ROLE
    caps = set(TAX_PREPARER_ROLE["capabilities"])
    assert READ_CAP in caps
    assert WRITE_CAP not in caps


def test_messages_capability_does_not_carry_broader_client_access():
    """A Messages grant authorizes the Messages FEATURE and nothing else. tax_staff is the clean
    case: it gains read here and gains no additional client-data capability from doing so."""
    from app.security.role_library import POST_SEED_GRANTS
    for profile, granted in POST_SEED_GRANTS.items():
        assert granted <= {READ_CAP, WRITE_CAP}, (
            f"{profile} post-seed grant reaches beyond Messages: {sorted(granted)}")


def test_existing_communications_capabilities_are_unchanged():
    """Additive only. The pre-existing communications family must hold exactly the roles it held
    before msgcap01 — this repair may not quietly re-point them at the Messages matrix."""
    assert _holders("communication.read") == {
        "administrator", "advisor", "client_service", "compliance", "operations", "read_only",
        "senior_tax"}
    assert _holders("communication.write") == {
        "administrator", "advisor", "client_service", "operations"}
    assert _holders("communications.view") == {
        "administrator", "advisor", "client_service", "compliance", "operations"}
    assert _holders("communications.send") == {
        "administrator", "advisor", "client_service", "operations"}


def _holders(cap):
    with engine.connect() as c:
        return {r[0] for r in c.execute(text(
            "SELECT r.code FROM roles r "
            "JOIN role_capabilities rc ON rc.role_id = r.id "
            "JOIN capabilities c ON c.id = rc.capability_id "
            "WHERE c.code = :c"), {"c": cap})}


def test_the_declared_production_library_is_fully_classified():
    """Belt and braces: the classification must cover the library's own declared profiles, not just
    the rows present in whichever database the suite ran against."""
    from app.security.role_library import ALL_PROFILE_CODES
    unclassified = set(ALL_PROFILE_CODES) - CLASSIFIED
    assert unclassified == set(), sorted(unclassified)
