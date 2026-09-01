"""Production role library — verifies the seeded firm access profiles.

Confirms every profile is seeded with exactly its intended capabilities, that effective permissions
are the union of multiple assigned profiles (the RBAC engine is unchanged), that each profile is
least-privilege, and that no profile escalates toward administrator authority. The role/capability
model and resolver are exercised as-is; nothing here modifies the engine.
"""
import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import capabilities, engine, role_capabilities, roles, user_roles, users
from app.security.rbac import resolve_capabilities
from app.security.role_library import (
    ADMINISTRATOR_ONLY,
    ALL_PROFILE_CODES,
    EXISTING_PROFILES,
    FORBIDDEN_FOR_NEW_PROFILES,
    NEW_PROFILES,
    POST_SEED_GRANTS,
    effective_capabilities,
)


def _role_caps(code):
    with engine.connect() as c:
        rid = c.scalar(select(roles.c.id).where(roles.c.code == code))
        if rid is None:
            return None
        return set(c.scalars(
            select(capabilities.c.code).select_from(
                role_capabilities.join(capabilities, capabilities.c.id == role_capabilities.c.capability_id))
            .where(role_capabilities.c.role_id == rid)))


@pytest.fixture
def make_user():
    created = []

    def _make(profile_codes):
        tag = uuid.uuid4().hex[:8]
        with engine.begin() as c:
            uid = c.execute(insert(users).values(
                email=f"u{tag}@e.test", normalized_email=f"u{tag}@e.test",
                display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
            for code in profile_codes:
                rid = c.scalar(select(roles.c.id).where(roles.c.code == code))
                c.execute(insert(user_roles).values(user_id=uid, role_id=rid))
        created.append(uid)
        return uid

    yield _make
    with engine.begin() as c:
        for uid in created:
            c.execute(delete(user_roles).where(user_roles.c.user_id == uid))
            c.execute(delete(users).where(users.c.id == uid))


# --- every profile is seeded -------------------------------------------------

def test_all_fourteen_profiles_exist_and_active():
    with engine.connect() as c:
        active = set(c.scalars(select(roles.c.code).where(roles.c.active.is_(True))))
    assert ALL_PROFILE_CODES <= active
    assert len(ALL_PROFILE_CODES) == 14


@pytest.mark.parametrize("code", sorted(NEW_PROFILES))
def test_new_profile_has_exactly_its_intended_capabilities(code):
    # NEW_PROFILES is what prodrolelib01 seeds; POST_SEED_GRANTS is what later migrations add to a
    # library profile (a capability created after the library was seeded cannot be named in
    # NEW_PROFILES - prodrolelib01 hard-fails on any capability missing from the catalogue at its
    # point in history). The seeded role must equal exactly their union: still an exact-set
    # assertion, so an unrecorded grant anywhere still fails.
    expected = set(effective_capabilities(code))
    assert _role_caps(code) == expected, code
    # Every referenced capability is a real catalogue entry (no typos / invented caps).
    with engine.connect() as c:
        known = set(c.scalars(select(capabilities.c.code).where(capabilities.c.code.in_(list(expected)))))
    assert expected <= known


def test_post_seed_grants_are_declared_for_real_profiles_and_add_something_new():
    """Guards the escape hatch: it may only extend existing NEW_PROFILES entries, and may not
    restate a capability the profile is already seeded with (which would hide a drift)."""
    for code, granted in POST_SEED_GRANTS.items():
        assert code in NEW_PROFILES, code
        assert granted, code
        assert not (granted & set(NEW_PROFILES[code][2])), code


def test_post_seed_grants_hold_no_forbidden_capability():
    """The least-privilege ceiling applies to post-seed grants exactly as it does to NEW_PROFILES,
    so the escape hatch cannot be used to slip a profile past FORBIDDEN_FOR_NEW_PROFILES."""
    for code, granted in POST_SEED_GRANTS.items():
        assert not (granted & FORBIDDEN_FOR_NEW_PROFILES), code
        assert not (granted & ADMINISTRATOR_ONLY), code


@pytest.mark.parametrize("code", sorted(EXISTING_PROFILES))
def test_existing_profile_still_present_with_required_capabilities(code):
    caps = _role_caps(code)
    assert caps is not None, code
    assert EXISTING_PROFILES[code] <= caps, code


def test_advisor_has_firm_wide_read_but_not_firm_wide_write():
    # Office decision (advfw01): every advisor SEES the whole client book (record.read_all), so the
    # firm-wide collection screens (Search / People / Households / Home / Timeline) are reachable.
    # Firm-wide WRITE stays administrator-only — the advisor must NOT gain record.write_all.
    caps = _role_caps("advisor")
    assert "record.read_all" in caps
    assert "record.write_all" not in caps
    assert not (caps & ADMINISTRATOR_ONLY)          # still not administrator authority


# --- effective permissions are the union of assigned profiles -----------------

# `effective_capabilities` = what a profile holds once post-seed migrations have run, so these
# stay exact-set assertions of the real union rather than being loosened to a subset check.

def test_effective_permissions_are_union_jessica(make_user):
    # Jessica = Accounting + Payroll + Tax Staff
    uid = make_user(["accounting", "payroll", "tax_staff"])
    expected = (effective_capabilities("accounting") | effective_capabilities("payroll")
                | effective_capabilities("tax_staff"))
    assert resolve_capabilities(uid) == expected


def test_effective_permissions_are_union_lauren(make_user):
    # Lauren = Senior Tax + Client Service
    uid = make_user(["senior_tax", "client_service"])
    expected = effective_capabilities("senior_tax") | effective_capabilities("client_service")
    assert resolve_capabilities(uid) == expected


def test_single_profile_resolves_to_that_profile(make_user):
    # Sarah = Tax Staff (single profile still works — backward compatible)
    uid = make_user(["tax_staff"])
    assert resolve_capabilities(uid) == effective_capabilities("tax_staff")


def test_adding_a_profile_only_adds_capabilities(make_user):
    # Union is monotonic: a second profile can only widen, never remove, access.
    solo = make_user(["tax_staff"])
    combo = make_user(["tax_staff", "accounting"])
    assert resolve_capabilities(solo) <= resolve_capabilities(combo)


# --- least privilege ---------------------------------------------------------

def test_tax_staff_cannot_review_or_manage_deadlines():
    caps = _role_caps("tax_staff")
    for forbidden in ("tax.review", "tax.document.review", "tax.deadline.manage"):
        assert forbidden not in caps


def test_domain_profiles_only_carry_their_own_vault_category():
    vault_cats = {
        "senior_tax": "vault.category.tax", "tax_staff": "vault.category.tax",
        "accounting": "vault.category.accounting", "payroll": "vault.category.payroll",
    }
    all_cats = {"vault.category.tax", "vault.category.wealth", "vault.category.accounting",
                "vault.category.payroll", "vault.category.insurance", "vault.category.benefits",
                "vault.category.compliance"}
    for code, own in vault_cats.items():
        caps = _role_caps(code)
        assert own in caps, code
        assert not (caps & (all_cats - {own, })), f"{code} leaks foreign vault categories"


def test_client_service_has_no_tax_or_wealth_access():
    caps = _role_caps("client_service")
    for forbidden in ("tax.read", "tax.write", "vault.category.tax", "vault.category.wealth",
                      "record.read_all"):
        assert forbidden not in caps


def test_read_only_has_no_write_capability():
    caps = _role_caps("read_only")
    for cap in caps:
        assert cap.endswith((".read", ".view")) or cap == "workspace.personalize", cap


# --- no privilege escalation -------------------------------------------------

@pytest.mark.parametrize("code", sorted(NEW_PROFILES))
def test_new_profile_holds_no_forbidden_capability(code):
    assert not (_role_caps(code) & FORBIDDEN_FOR_NEW_PROFILES), code


def test_only_administrator_holds_control_plane_capabilities():
    for code in ALL_PROFILE_CODES:
        caps = _role_caps(code)
        overlap = caps & ADMINISTRATOR_ONLY
        if code == "administrator":
            assert ADMINISTRATOR_ONLY <= caps
        else:
            assert not overlap, f"{code} unexpectedly holds control-plane caps {overlap}"


def test_multi_profile_union_never_reaches_administrator(make_user):
    # No combination of firm profiles can add up to administrator authority.
    for combo in (["accounting", "payroll", "tax_staff"], ["senior_tax", "client_service"],
                  ["reviewer", "read_only", "client_service"]):
        uid = make_user(combo)
        caps = resolve_capabilities(uid)
        assert not (caps & ADMINISTRATOR_ONLY), combo
        assert "identity.manage" not in caps and "record.read_all" not in caps
