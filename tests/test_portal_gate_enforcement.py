"""Firm-wide portal gate ENFORCEMENT (the six formerly inert governed flags).

The eight ``portal.*`` runtime flags were seeded as governed metadata by migration ``9483fa25e622``, but
six of them had no enforcement consumer: ``portal.appointments_enabled`` and
``portal.financial_summary_enabled`` were read by their service modules, and the other six gated nothing.
A firm-wide kill switch that switches nothing is worse than no switch, because it reads as protection.

Enforcement now lives in ONE central place for request surfaces — ``portal_gate.evaluate``, already called
by the auth middleware for every ``/portal``, ``/api/portal`` and ``/api/v1/portal`` request — plus
service-level checks on the document read/write chokepoints and the household scope resolver, so a
non-HTTP caller cannot bypass a kill switch.

Every test here drives the gates through fixtures. Production metadata is never written and every flag
stays disabled by default (proved by tests/test_portal_runtime_gates.py).
"""
from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import engine, people, portal_access_grants, vault_documents
from app.portal import vault_documents as pv
from app.portal.service import portal_base_scope
from app.services.features import portal_gate
from tests._portal_util import sample_upload, seed_portal_account, seed_staff_user

SIGNOFF = "portal.production_signed_off"
MASTER = {"portal.enabled", SIGNOFF}          # both halves of production_ready()

pytestmark = pytest.mark.usefixtures("production_identity_provider")

ALL_GATES = {
    "portal.enabled",
    SIGNOFF,
    "portal.household_enabled",
    "portal.documents.download_enabled",
    "portal.documents.upload_enabled",
    "portal.messaging_enabled",
    "portal.appointments_enabled",
    "portal.financial_summary_enabled",
    "portal.forms_enabled",
}

# (gate, path, method) — the surface each formerly-inert flag now governs.
SURFACES = [
    ("portal.documents.download_enabled", "/api/v1/portal/documents/7/download", "GET"),
    ("portal.documents.download_enabled", "/api/portal/documents/7/download", "GET"),
    ("portal.documents.upload_enabled", "/portal/upload", "POST"),
    ("portal.documents.upload_enabled", "/api/portal/documents", "POST"),
    ("portal.documents.upload_enabled", "/api/v1/portal/requests/3/upload", "POST"),
    ("portal.messaging_enabled", "/portal/messages", "GET"),
    ("portal.messaging_enabled", "/api/v1/portal/messages", "POST"),
    ("portal.messaging_enabled", "/api/portal/messages", "GET"),
    ("portal.forms_enabled", "/portal/tax-intake", "GET"),
    ("portal.forms_enabled", "/api/v1/portal/tax/intake/4/organizer", "PUT"),
    ("portal.forms_enabled", "/api/v1/portal/tax/returns/9/decision", "POST"),
]

EXEMPT = [("/portal/login", "GET"), ("/portal/logout", "POST"),
          ("/api/v1/portal/auth/invitations/accept", "POST"), ("/api/portal/login", "POST")]


@pytest.fixture
def client(staff_user_id=None):
    uid = seed_staff_user()
    account_id, principal, person_id, household_id = seed_portal_account(uid)
    return {"staff": uid, "account_id": account_id, "principal": principal,
            "person_id": person_id, "household_id": household_id}


# --- master gate: portal.enabled ---------------------------------------------

def test_master_gate_off_closes_every_non_exempt_surface(client, portal_gates):
    """portal.enabled=False blocks everything, regardless of the child flags."""
    portal_gates(ALL_GATES - {"portal.enabled"})          # every child + signoff ON, portal OFF
    for _gate, path, method in SURFACES + [(None, "/portal", "GET"), (None, "/portal/documents", "GET")]:
        allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is False, f"{method} {path} reachable with portal.enabled=False"
        assert reason == "portal_not_production_ready"


def test_master_gate_off_still_permits_auth_bootstrap_paths(client, portal_gates):
    """Login/logout/activation stay reachable so a user can be informed and sign out."""
    portal_gates(set())
    for path, method in EXEMPT:
        allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is True and reason == "exempt", f"{method} {path} was blocked"


def test_master_gate_fails_closed_when_runtime_unresolvable(client, monkeypatch):
    """gate() returns the production-safe default on any runtime failure -> portal closed."""
    monkeypatch.setattr(portal_gate, "runtime_gate", lambda name: False)
    allowed, reason, _f = portal_gate.evaluate(client["principal"], "/portal/documents", "GET")
    assert allowed is False and reason == "portal_not_production_ready"


# --- child gates -------------------------------------------------------------

@pytest.mark.parametrize("gate_name,path,method", SURFACES)
def test_child_gate_off_blocks_its_surface_even_when_master_is_on(client, portal_gates,
                                                                  gate_name, path, method):
    portal_gates(ALL_GATES - {gate_name})                 # master + siblings ON, this one OFF
    allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
    assert allowed is False, f"{method} {path} reachable with {gate_name}=False"
    assert reason == f"{gate_name}_disabled"


@pytest.mark.parametrize("gate_name,path,method", SURFACES)
def test_surface_opens_when_its_own_gate_is_on(client, portal_gates, gate_name, path, method):
    portal_gates(ALL_GATES)
    allowed, _reason, _f = portal_gate.evaluate(client["principal"], path, method)
    assert allowed is True, f"{method} {path} blocked with every gate enabled"


def test_one_child_gate_does_not_open_another_surface(client, portal_gates):
    """Enabling messaging must not open documents, and vice versa."""
    portal_gates(MASTER | {"portal.messaging_enabled"})
    ok_msg, _r, _f = portal_gate.evaluate(client["principal"], "/portal/messages", "GET")
    assert ok_msg is True
    for path, method in [("/api/v1/portal/documents/7/download", "GET"), ("/portal/upload", "POST"),
                         ("/portal/tax-intake", "GET")]:
        allowed, _r, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is False, f"messaging gate leaked into {method} {path}"


def test_v0_and_v1_apis_are_gated_identically(client, portal_gates):
    """Step 11: /api/portal/* and /api/v1/portal/* must not diverge on the gate decision."""
    pairs = [("/api/portal/documents/7/download", "/api/v1/portal/documents/7/download", "GET"),
             ("/api/portal/messages", "/api/v1/portal/messages", "GET")]
    for v0, v1, method in pairs:
        assert portal_gate.runtime_gate_for_request(v0, method) == \
               portal_gate.runtime_gate_for_request(v1, method), f"{v0} vs {v1}"
    portal_gates(MASTER)
    for v0, v1, method in pairs:
        a0, _r0, _f0 = portal_gate.evaluate(client["principal"], v0, method)
        a1, _r1, _f1 = portal_gate.evaluate(client["principal"], v1, method)
        assert a0 == a1 is False, f"v0/v1 gate divergence on {method} {v0}"


# --- the two pre-existing gates still enforce (Step 8) -----------------------

def test_appointments_gate_still_enforces(client, portal_gates):
    from app.portal.appointments import request_appointment
    portal_gates(ALL_GATES - {"portal.appointments_enabled"})
    with pytest.raises(PermissionError):
        request_appointment(client["principal"], person_id=client["person_id"],
                            household_id=client["household_id"], preferred_window="any", reason="hi")


def test_financial_summary_gate_still_enforces(client, portal_gates):
    from app.portal.financial import financial_summary
    portal_gates(ALL_GATES - {"portal.financial_summary_enabled"})
    summary = financial_summary(client["principal"])
    assert summary == {"enabled": False, "accounts": [], "total_value": None}


# --- document download: service-level, no leak -------------------------------

def test_download_fails_closed_and_leaks_nothing_when_gate_off(client, portal_gates):
    portal_gates(ALL_GATES - {"portal.documents.download_enabled"})
    with pytest.raises(PermissionError) as exc:
        pv.download_document(client["principal"], 123456)
    message = str(exc.value)
    assert "not available" in message
    for leak in ("storage", "/srv", ".pdf", "sha256", "123456"):
        assert leak not in message, f"download denial leaked {leak!r}"


def test_download_gate_off_does_not_depend_on_document_existing(client, portal_gates):
    """The gate is checked BEFORE resolution, so existence is not disclosed either way."""
    portal_gates(ALL_GATES - {"portal.documents.download_enabled"})
    missing = str(pytest.raises(PermissionError,
                                pv.download_document, client["principal"], 999999).value)
    present = str(pytest.raises(PermissionError,
                                pv.download_document, client["principal"], 1).value)
    assert missing == present


# --- document upload: no write of any kind when gated off --------------------

def test_upload_gate_off_creates_no_document_row(client, portal_gates):
    portal_gates(ALL_GATES - {"portal.documents.upload_enabled"})
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(vault_documents)).scalar_one()
    with pytest.raises(PermissionError):
        pv.upload_document(client["principal"], source=io.BytesIO(sample_upload()),
                           original_filename="x.pdf", display_name="x")
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(vault_documents)).scalar_one()
    assert after == before, "a gated-off upload created a vault document row"


def test_upload_works_and_still_enforces_ownership_when_gate_on(client, portal_gates):
    portal_gates(ALL_GATES)
    doc_id = pv.upload_document(client["principal"], source=io.BytesIO(sample_upload()),
                                original_filename="ok.pdf", display_name="ok")
    assert isinstance(doc_id, int)
    # ownership rules are unchanged: another client cannot download it
    other = seed_portal_account(seed_staff_user())[1]
    with pytest.raises(PermissionError):
        pv.download_document(other, doc_id)


# --- household expansion ------------------------------------------------------

def _joint_grant(account_id, household_id):
    with engine.begin() as c:
        c.execute(insert(portal_access_grants).values(
            portal_account_id=account_id, household_id=household_id, person_id=None,
            access_type="joint", permissions={"documents": True, "messages": True, "tasks": True}))


def test_household_gate_off_blocks_expansion_to_other_members(client, portal_gates):
    """A joint grant must not reach the OTHER household members while the gate is off."""
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        spouse = c.execute(insert(people).values(household_id=client["household_id"],
                                                 full_name=f"Spouse {suffix}", active=True)
                           .returning(people.c.id)).scalar_one()
    _joint_grant(client["account_id"], client["household_id"])

    portal_gates(ALL_GATES - {"portal.household_enabled"})
    scope_off = portal_base_scope(client["account_id"])
    assert scope_off["shared_household_ids"] == set()
    assert spouse not in scope_off["person_ids"], "household expansion leaked with the gate off"
    # self/person grants keep working
    assert client["person_id"] in scope_off["person_ids"]
    assert client["household_id"] in scope_off["household_ids"]

    portal_gates(ALL_GATES)
    scope_on = portal_base_scope(client["account_id"])
    assert spouse in scope_on["person_ids"], "household expansion did not work with the gate on"


def test_household_gate_never_crosses_into_another_household(client, portal_gates):
    """Cross-household isolation holds with the gate ON as well as off."""
    other = seed_portal_account(seed_staff_user())
    _joint_grant(client["account_id"], client["household_id"])
    portal_gates(ALL_GATES)
    scope = portal_base_scope(client["account_id"])
    assert other[3] not in scope["household_ids"]
    assert other[2] not in scope["person_ids"], "client A reached client B's person"


# --- isolation invariants that must survive the new gating -------------------

def test_staff_routes_are_untouched_by_portal_gates(client, portal_gates):
    """The gates govern the client fork only; no /admin or staff path is mapped."""
    portal_gates(set())
    for path in ("/admin/client-portal", "/admin/client-portal/accounts", "/tasks", "/api/v1/tax/intake"):
        assert portal_gate.runtime_gate_for_request(path, "GET") is None, f"{path} picked up a portal gate"


def test_gate_rules_never_map_a_staff_or_microsoft_surface():
    """No rule may capture staff, Microsoft mail, or canonical-document paths."""
    forbidden = ("/admin/", "/microsoft365", "/api/v1/documents", "/communications")
    for rx, _methods, _gate in portal_gate._RUNTIME_GATE_RULES:
        for path in forbidden:
            assert not rx.match(path), f"{rx.pattern} matches {path}"


def test_every_governed_gate_has_an_enforcement_consumer():
    """The regression this whole change exists to prevent: no governed flag may be inert again."""
    from pathlib import Path

    from app.portal.gate import GATES

    sources = "\n".join(p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py"))
    mapped = {g for _rx, _m, g in portal_gate._RUNTIME_GATE_RULES}
    inert = []
    for name in GATES:
        if name in {"portal.mfa_required", "portal.production_signed_off"}:
            continue                                  # policy/config items, not surface gates
        if name in mapped:
            continue
        if f'gate("{name}")' in sources or f"gate('{name}')" in sources:
            continue
        inert.append(name)
    assert inert == [], f"governed portal flags with no enforcement consumer: {inert}"


# --- NEGATIVE PROOF: the narrow test fixtures do not leak into other surfaces ---
# These guard the test harness itself. A fixture that quietly enabled extra gates would make every other
# portal test a weaker assertion than it appears, and would hide exactly the class of defect this whole
# change exists to prevent.

CHILD_GATES = ALL_GATES - MASTER


def test_portal_master_on_enables_no_child_feature(portal_master_on):
    assert portal_master_on == MASTER          # both halves of production_ready(), no child feature
    from app.services.features import portal_gate as pg
    assert pg.runtime_gate("portal.enabled") is True
    for child in CHILD_GATES:
        assert pg.runtime_gate(child) is False, f"portal_master_on leaked {child}"


@pytest.mark.parametrize("fixture_name,expected_child", [
    ("portal_household_on", "portal.household_enabled"),
    ("portal_documents_download_on", "portal.documents.download_enabled"),
    ("portal_documents_upload_on", "portal.documents.upload_enabled"),
    ("portal_messaging_on", "portal.messaging_enabled"),
    ("portal_appointments_on", "portal.appointments_enabled"),
    ("portal_financial_on", "portal.financial_summary_enabled"),
    ("portal_forms_on", "portal.forms_enabled"),
])
def test_each_child_fixture_enables_only_its_own_gate(request, fixture_name, expected_child):
    state = request.getfixturevalue(fixture_name)
    assert state == MASTER | {expected_child}, f"{fixture_name} enabled {state}"
    from app.services.features import portal_gate as pg
    for other in CHILD_GATES - {expected_child}:
        assert pg.runtime_gate(other) is False, f"{fixture_name} leaked {other}"


def test_messaging_fixture_leaves_documents_forms_and_money_closed(portal_messaging_on):
    """The explicit case called out in review: messaging must not open anything else."""
    from app.services.features import portal_gate as pg
    for closed in ("portal.documents.download_enabled", "portal.documents.upload_enabled",
                   "portal.forms_enabled", "portal.appointments_enabled",
                   "portal.financial_summary_enabled", "portal.household_enabled"):
        assert pg.runtime_gate(closed) is False, f"messaging fixture leaked {closed}"


def test_download_fixture_does_not_enable_messaging_or_upload(portal_documents_download_on):
    from app.services.features import portal_gate as pg
    assert pg.runtime_gate("portal.documents.download_enabled") is True
    assert pg.runtime_gate("portal.messaging_enabled") is False
    assert pg.runtime_gate("portal.documents.upload_enabled") is False


def test_fixtures_compose_without_widening(portal_messaging_on, portal_documents_upload_on):
    """Two fixtures together enable exactly those two child gates — not a third."""
    from app.services.features import portal_gate as pg
    assert portal_documents_upload_on == MASTER | {"portal.messaging_enabled",
                                                   "portal.documents.upload_enabled"}
    for closed in ("portal.documents.download_enabled", "portal.forms_enabled",
                   "portal.household_enabled", "portal.appointments_enabled",
                   "portal.financial_summary_enabled"):
        assert pg.runtime_gate(closed) is False, f"composition leaked {closed}"


def test_fixtures_simulate_signoff_without_touching_real_metadata(portal_forms_on):
    """Fixtures simulate sign-off in-process only; the governed runtime value must stay False."""
    from app.services.runtime import consumption
    assert SIGNOFF in portal_forms_on                       # simulated for this test
    assert consumption.config_value(SIGNOFF, default=None) is False   # real metadata untouched


def test_requesting_no_fixture_leaves_every_gate_at_its_real_value():
    """A test that asks for nothing gets the real, production-safe values."""
    from app.portal.gate import gate_status
    assert all(v is False for k, v in gate_status().items() if k != "portal.mfa_required")


# --- production sign-off: the four-state master matrix -----------------------
# External client access requires BOTH portal.enabled and portal.production_signed_off. Three of the four
# states must serve nothing; only the fourth hands control to the child gates.

MASTER_STATES = [
    (False, False, False),
    (False, True, False),
    (True, False, False),
    (True, True, True),
]

PROTECTED = [("/portal", "GET"), ("/portal/documents", "GET"), ("/api/v1/portal/dashboard", "GET"),
             ("/api/portal/dashboard", "GET"), ("/api/v1/portal/messages", "GET")]


@pytest.mark.parametrize("enabled,signed_off,expect_open", MASTER_STATES)
def test_master_matrix_requires_both_enabled_and_signoff(client, portal_gates,
                                                         enabled, signed_off, expect_open):
    state = set()
    if enabled:
        state.add("portal.enabled")
    if signed_off:
        state.add(SIGNOFF)
    state |= (ALL_GATES - MASTER)                 # every child ON, so only the master pair decides
    portal_gates(state)
    for path, method in PROTECTED:
        allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is expect_open, (
            f"enabled={enabled} signed_off={signed_off}: {method} {path} allowed={allowed}")
        if not expect_open:
            assert reason == "portal_not_production_ready"


@pytest.mark.parametrize("enabled,signed_off,_expect", MASTER_STATES)
def test_exempt_paths_reachable_in_every_master_state(client, portal_gates,
                                                      enabled, signed_off, _expect):
    state = set()
    if enabled:
        state.add("portal.enabled")
    if signed_off:
        state.add(SIGNOFF)
    portal_gates(state)
    for path, method in EXEMPT:
        allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is True and reason == "exempt", f"{method} {path} blocked"


def test_no_child_gate_can_bypass_missing_signoff(client, portal_gates):
    """Every child gate on, sign-off missing -> still nothing is served."""
    portal_gates(ALL_GATES - {SIGNOFF})
    for _g, path, method in SURFACES:
        allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is False and reason == "portal_not_production_ready", f"{method} {path}"


def test_signoff_alone_does_not_open_the_portal(client, portal_gates):
    portal_gates({SIGNOFF} | (ALL_GATES - MASTER))
    allowed, reason, _f = portal_gate.evaluate(client["principal"], "/portal/documents", "GET")
    assert allowed is False and reason == "portal_not_production_ready"


def test_child_gates_control_their_surfaces_once_both_master_halves_pass(client, portal_gates):
    """State D: master open, then each child gate decides its own surface, exactly as before."""
    portal_gates(MASTER | {"portal.messaging_enabled"})
    ok, _r, _f = portal_gate.evaluate(client["principal"], "/portal/messages", "GET")
    assert ok is True
    for path, method in [("/api/v1/portal/documents/7/download", "GET"), ("/portal/upload", "POST"),
                         ("/portal/tax-intake", "GET")]:
        allowed, reason, _f = portal_gate.evaluate(client["principal"], path, method)
        assert allowed is False and reason.endswith("_disabled"), f"{method} {path} -> {reason}"


def test_v0_and_v1_agree_in_every_master_state(client, portal_gates):
    pairs = [("/api/portal/dashboard", "/api/v1/portal/dashboard", "GET"),
             ("/api/portal/messages", "/api/v1/portal/messages", "GET")]
    for enabled, signed_off, _expect in MASTER_STATES:
        state = set()
        if enabled:
            state.add("portal.enabled")
        if signed_off:
            state.add(SIGNOFF)
        state |= (ALL_GATES - MASTER)
        portal_gates(state)
        for v0, v1, method in pairs:
            a0 = portal_gate.evaluate(client["principal"], v0, method)[0]
            a1 = portal_gate.evaluate(client["principal"], v1, method)[0]
            assert a0 == a1, f"v0/v1 divergence on {method} {v0} at enabled={enabled}/{signed_off}"


def test_production_ready_is_an_enforcement_dependency():
    """The finding this change closes: evaluate() must consult production_ready(), not re-spell it."""
    import inspect

    source = inspect.getsource(portal_gate.evaluate)
    assert "production_ready()" in source
    assert 'runtime_gate("portal.enabled")' not in source, "master condition was duplicated"


def test_master_gate_denial_returns_no_client_data(client, portal_gates):
    """A blocked request yields only (False, reason, 'portal_access') — never a payload."""
    portal_gates(ALL_GATES - {SIGNOFF})
    allowed, reason, feature = portal_gate.evaluate(client["principal"], "/api/v1/portal/documents", "GET")
    assert (allowed, feature) == (False, "portal_access")
    assert reason == "portal_not_production_ready"


def test_staff_admin_portal_routes_are_unaffected_by_signoff(client, portal_gates):
    portal_gates(set())
    for path in ("/admin/client-portal", "/admin/client-portal/accounts",
                 "/admin/client-portal/diagnostics", "/microsoft365/mail", "/tasks"):
        assert portal_gate.runtime_gate_for_request(path, "GET") is None
        assert not any(rx.match(path) for rx, _m, _f in portal_gate._RULES), path
