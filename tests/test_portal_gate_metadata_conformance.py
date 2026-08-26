"""Every portal gate must be governable (migration ``b5d82e04c917``).

``app/portal/gate.py::gate()`` evaluates EVERY entry in ``GATES`` through
``consumption.feature_enabled``. Migration ``9483fa25e622`` seeded ``portal.production_signed_off`` and
``portal.mfa_required`` as configuration ITEMS, so ``feature_defined`` was False for both and
``feature_enabled`` silently returned the hard-coded default. Neither gate was actually governed: writing
the configuration item changed nothing, sign-off could never become True (so ``production_ready()`` could
never be True), and MFA reported True only because its default is True.

The metadata now conforms to the contract. These tests keep it that way — the conformance test below is
the one that would have caught the original defect — and prove both codes are genuinely governed end to
end through the supported service path.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import configuration_feature_flags, configuration_items, engine
from app.portal.gate import GATES, gate, gate_status, production_ready
from app.security.models import Principal
from app.services.configuration import features
from app.services.runtime import consumption
from app.services.runtime.cache import RUNTIME_CACHE
from tests._portal_util import seed_staff_user

SIGNOFF = "portal.production_signed_off"
MFA = "portal.mfa_required"


@pytest.fixture
def operator():
    return Principal(seed_staff_user(), "cfg@e.test", "Config Operator",
                     frozenset({"configuration.execute", "configuration.manage", "configuration.audit",
                                "runtime.execute"}))


@pytest.fixture
def restore_flags():
    """Snapshot the portal flag rows and restore them verbatim afterwards.

    Governance tests must not leak state into the rest of the suite — several other tests assert the
    exact production-safe gate values."""
    codes = tuple(GATES)
    with engine.connect() as c:
        saved = {r["code"]: dict(r) for r in c.execute(
            select(configuration_feature_flags).where(
                configuration_feature_flags.c.code.in_(codes))).mappings()}
    yield
    with engine.begin() as c:
        for code, row in saved.items():
            c.execute(configuration_feature_flags.update()
                      .where(configuration_feature_flags.c.code == code)
                      .values(status=row["status"], enabled=row["enabled"],
                              rollout_percentage=row["rollout_percentage"],
                              activation_starts_at=row["activation_starts_at"]))
    RUNTIME_CACHE.invalidate()


def _flag_id(code):
    with engine.connect() as c:
        return c.execute(select(configuration_feature_flags.c.id)
                         .where(configuration_feature_flags.c.code == code)).scalar_one()


def _set(operator, code, *, status, rollout):
    fid = _flag_id(code)
    features.set_flag_status(operator, fid, status, actor_user_id=operator.user_id)
    features.update_flag_rollout(operator, fid, rollout, actor_user_id=operator.user_id)
    RUNTIME_CACHE.invalidate()


# --- conformance: the guard that would have caught the original defect -------

def test_every_gates_code_is_feature_defined_in_a_resolved_runtime():
    """No GATES entry may exist only as a configuration item."""
    RUNTIME_CACHE.invalidate()
    ctx = consumption.runtime_context()
    assert ctx.resolved is True, "runtime did not resolve; the assertion below would be meaningless"
    undefined = [code for code in GATES if not ctx.feature_defined(code)]
    assert undefined == [], (
        f"portal gates not defined as runtime feature flags (gate() would silently return the "
        f"hard-coded default for these): {undefined}")


def test_signoff_and_mfa_are_specifically_feature_defined():
    RUNTIME_CACHE.invalidate()
    ctx = consumption.runtime_context()
    assert ctx.feature_defined(SIGNOFF) is True
    assert ctx.feature_defined(MFA) is True


def test_seeded_feature_rows_match_the_effective_values_they_replaced():
    with engine.connect() as c:
        rows = {r["code"]: r for r in c.execute(
            select(configuration_feature_flags.c.code, configuration_feature_flags.c.status,
                   configuration_feature_flags.c.enabled,
                   configuration_feature_flags.c.rollout_percentage)
            .where(configuration_feature_flags.c.code.in_([SIGNOFF, MFA]))).mappings()}
    assert (rows[SIGNOFF]["status"], rows[SIGNOFF]["enabled"], rows[SIGNOFF]["rollout_percentage"]) \
        == ("active", False, 0)
    assert (rows[MFA]["status"], rows[MFA]["enabled"], rows[MFA]["rollout_percentage"]) \
        == ("active", True, 100)


def test_historical_configuration_items_are_left_in_place():
    """The migration must not silently delete or migrate historical item data."""
    with engine.connect() as c:
        codes = set(c.scalars(select(configuration_items.c.code)
                              .where(configuration_items.c.code.in_([SIGNOFF, MFA]))))
    assert codes == {SIGNOFF, MFA}


def test_behaviour_is_preserved_and_portal_stays_closed():
    RUNTIME_CACHE.invalidate()
    status = gate_status()
    assert status[SIGNOFF] is False
    assert status[MFA] is True
    assert all(v is False for k, v in status.items() if k != MFA)
    assert production_ready() is False


# --- sign-off is now genuinely governed --------------------------------------

def test_production_signoff_is_governed_end_to_end(operator, restore_flags):
    """false/0 -> False ; active/100 -> True ; back to false/0 -> False."""
    RUNTIME_CACHE.invalidate()
    assert gate(SIGNOFF) is False, "must start blocked"

    _set(operator, SIGNOFF, status="active", rollout=100)
    assert gate(SIGNOFF) is True, "sign-off did not become effective through the governed path"

    _set(operator, SIGNOFF, status="draft", rollout=0)
    assert gate(SIGNOFF) is False, "sign-off did not revert"


def test_production_ready_follows_the_governed_signoff(operator, restore_flags, production_identity_provider):
    """The whole point: production_ready() can now actually be driven."""
    RUNTIME_CACHE.invalidate()
    assert production_ready() is False

    _set(operator, "portal.enabled", status="active", rollout=100)
    assert production_ready() is False, "portal.enabled alone must not be production-ready"

    _set(operator, SIGNOFF, status="active", rollout=100)
    assert production_ready() is True, "both master conditions met but production_ready() is False"

    _set(operator, SIGNOFF, status="draft", rollout=0)
    assert production_ready() is False


# --- MFA is no longer a hard-coded fallback ----------------------------------

def test_mfa_required_is_governed_not_hard_coded(operator, restore_flags):
    """If it were still falling back to the default, disabling it could not change the answer."""
    RUNTIME_CACHE.invalidate()
    assert gate(MFA) is True, "MFA must start required"

    _set(operator, MFA, status="draft", rollout=0)
    assert gate(MFA) is False, (
        "gate() still returns True with the flag disabled — it is reading the hard-coded default, "
        "so portal.mfa_required is not actually governed")

    _set(operator, MFA, status="active", rollout=100)
    assert gate(MFA) is True


def test_rollout_zero_alone_closes_a_gate(operator, restore_flags):
    """Rollout is part of the governed contract: active + enabled + rollout>=100."""
    RUNTIME_CACHE.invalidate()
    _set(operator, MFA, status="active", rollout=0)
    assert gate(MFA) is False


# --- the activation path itself ----------------------------------------------

def test_activation_path_needs_no_configuration_item_write(operator, restore_flags):
    """After this migration the governed path for BOTH codes is the feature-flag path alone."""
    with engine.connect() as c:
        before = {r["code"]: r["version"] for r in c.execute(
            select(configuration_items.c.code, configuration_items.c.version)
            .where(configuration_items.c.code.in_([SIGNOFF, MFA]))).mappings()}

    _set(operator, SIGNOFF, status="active", rollout=100)
    assert gate(SIGNOFF) is True

    with engine.connect() as c:
        after = {r["code"]: r["version"] for r in c.execute(
            select(configuration_items.c.code, configuration_items.c.version)
            .where(configuration_items.c.code.in_([SIGNOFF, MFA]))).mappings()}
    assert after == before, "the feature-flag path must not require a configuration-item write"
