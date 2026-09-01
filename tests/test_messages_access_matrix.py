"""The intended Messages access matrix, pinned per ROLE against the seeded catalogue.

tests/test_messages_route_capability.py proves the middleware honours the capabilities. This proves
the right ROLES hold them - the half that silently drifts when someone edits role_library.py or adds
a grant in a later migration.

The matrix was set deliberately (msgcap01):

  view + reply   administrator, client_service, advisor, operations, senior_tax
  view only      tax_staff
  no access      accounting, payroll, compliance, reviewer, read_only,
                 benefits_*, insurance_*

Two properties matter and are asserted separately:

  * the roles that SHOULD have access do (a coordinator cannot do their job otherwise);
  * the roles that should NOT are absent - this is the whole reason the capability exists rather
    than reusing client.read, which eleven roles hold.

`tax_preparer` is a DEMO-ONLY role (app/demo/credentials.py) and is not in the production catalogue,
so it is checked there rather than here.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import capabilities, engine, role_capabilities, roles

READ = "communications.message.read"
WRITE = "communications.message.write"

VIEW_AND_REPLY = frozenset({"administrator", "client_service", "advisor", "operations", "senior_tax"})
VIEW_ONLY = frozenset({"tax_staff"})
NO_ACCESS = frozenset({
    "accounting", "payroll", "compliance", "reviewer", "read_only",
    "benefits_advisor", "benefits_operations", "benefits_compliance",
    "insurance_agent", "insurance_operations", "insurance_compliance",
})


def _holders(code):
    with engine.connect() as c:
        return set(c.scalars(
            select(roles.c.code)
            .select_from(roles.join(role_capabilities, role_capabilities.c.role_id == roles.c.id)
                              .join(capabilities, capabilities.c.id == role_capabilities.c.capability_id))
            .where(capabilities.c.code == code)))


def test_both_capabilities_exist_in_the_catalogue():
    with engine.connect() as c:
        known = set(c.scalars(select(capabilities.c.code).where(capabilities.c.code.in_([READ, WRITE]))))
    assert known == {READ, WRITE}, "msgcap01 has not been applied to this database"


@pytest.mark.parametrize("role", sorted(VIEW_AND_REPLY | VIEW_ONLY))
def test_intended_roles_can_view_messages(role):
    assert role in _holders(READ), role


@pytest.mark.parametrize("role", sorted(VIEW_AND_REPLY))
def test_intended_roles_can_reply(role):
    assert role in _holders(WRITE), role


@pytest.mark.parametrize("role", sorted(VIEW_ONLY))
def test_view_only_roles_cannot_reply(role):
    """Tax Staff reads the conversation for context; replying to the client is the coordinator's job."""
    assert role not in _holders(WRITE), role


@pytest.mark.parametrize("role", sorted(NO_ACCESS))
def test_excluded_roles_have_neither_capability(role):
    """The reason msgcap01 exists. Every one of these holds client.read (or, for the benefits and
    insurance profiles, no client access at all), and under the previous model the client.read
    holders among them could read client correspondence."""
    assert role not in _holders(READ), role
    assert role not in _holders(WRITE), role


def test_the_matrix_is_exhaustive_over_the_production_library():
    """A role added later must be classified here, not silently default to no coverage."""
    from app.security.role_library import ALL_PROFILE_CODES
    classified = VIEW_AND_REPLY | VIEW_ONLY | NO_ACCESS
    assert ALL_PROFILE_CODES <= classified, sorted(ALL_PROFILE_CODES - classified)


def test_write_is_never_granted_without_read():
    """A role that can reply but not view would be an incoherent grant, and the middleware's
    read-gate would refuse it anyway."""
    assert _holders(WRITE) <= _holders(READ)


def test_the_demo_tax_preparer_matches_the_tax_staff_profile():
    """The demo persona mirrors production tax_staff: view yes, reply no."""
    from app.demo.credentials import TAX_PREPARER_ROLE
    assert READ in TAX_PREPARER_ROLE["capabilities"]
    assert WRITE not in TAX_PREPARER_ROLE["capabilities"]
