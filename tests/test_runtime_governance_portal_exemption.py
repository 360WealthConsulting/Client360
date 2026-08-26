"""The runtime-governance exemption for Client360 portal gates.

Portal gates are governed outside ``runtime_behaviors`` — enforced directly by ``app/portal/gate.py`` and
``app/services/features/portal_gate.py``, several are deliberately kill switches rather than behaviors,
and they have their own validator (``app/portal/governance.py::validate_portal``). Without an exemption
the runtime orphan rule reports every ENABLED portal gate as an ``unused_definition``: seeding
``portal.mfa_required`` as a real feature flag (migration ``b5d82e04c917``) made that finding appear, and
enabling ``portal.enabled`` for a controlled test would have added two more.

The exemption is an EXPLICIT allow-list, never a ``portal.`` prefix. These tests exist to keep it that
way: an unlisted portal code is still an orphan, non-portal orphans are still reported, and the list must
stay identical to ``app.portal.gate.GATES`` so adding a gate forces a deliberate governance decision.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import configuration_feature_flags, engine
from app.portal.gate import GATES
from app.security.models import Principal
from app.services.configuration import features
from app.services.runtime import governance
from app.services.runtime.cache import RUNTIME_CACHE
from tests._portal_util import seed_staff_user


@pytest.fixture
def operator():
    return Principal(seed_staff_user(), "gov@e.test", "Governance Operator",
                     frozenset({"configuration.execute", "configuration.manage", "runtime.execute"}))


@pytest.fixture
def restore_flags():
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
                              rollout_percentage=row["rollout_percentage"]))
    RUNTIME_CACHE.invalidate()


@pytest.fixture
def temp_flag():
    """Create throwaway ACTIVE+ENABLED flags and always remove them."""
    created = []

    def _make(code):
        with engine.begin() as c:
            c.execute(insert(configuration_feature_flags).values(
                code=code, name=code, status="active", enabled=True, rollout_percentage=100))
        created.append(code)
        RUNTIME_CACHE.invalidate()
        return code

    yield _make
    with engine.begin() as c:
        for code in created:
            c.execute(delete(configuration_feature_flags)
                      .where(configuration_feature_flags.c.code == code))
    RUNTIME_CACHE.invalidate()


def _orphans(report):
    return {f["definition"] for f in report["findings"] if f["type"] == "unused_definition"}


def _enable(operator, code):
    with engine.connect() as c:
        fid = c.execute(select(configuration_feature_flags.c.id)
                        .where(configuration_feature_flags.c.code == code)).scalar_one()
    features.set_flag_status(operator, fid, "active", actor_user_id=operator.user_id)
    features.update_flag_rollout(operator, fid, 100, actor_user_id=operator.user_id)
    RUNTIME_CACHE.invalidate()


# --- the exemption works for the real gates ----------------------------------

def test_governance_is_clean_at_rest():
    RUNTIME_CACHE.invalidate()
    report = governance.validate()
    assert report["ok"] is True, report["findings"]
    assert report["issue_count"] == 0


def test_enabled_mfa_required_is_not_an_orphan():
    """The finding that appeared when portal.mfa_required became a real flag."""
    RUNTIME_CACHE.invalidate()
    assert "portal.mfa_required" not in _orphans(governance.validate())


def test_enabling_portal_enabled_creates_no_orphan(operator, restore_flags):
    _enable(operator, "portal.enabled")
    report = governance.validate()
    assert "portal.enabled" not in _orphans(report)
    assert report["ok"] is True, report["findings"]


def test_enabling_documents_download_creates_no_orphan(operator, restore_flags):
    _enable(operator, "portal.documents.download_enabled")
    report = governance.validate()
    assert "portal.documents.download_enabled" not in _orphans(report)
    assert report["ok"] is True, report["findings"]


def test_every_governed_portal_gate_can_be_enabled_without_a_finding(operator, restore_flags):
    """The controlled synthetic test enables several at once; none may dirty the report."""
    for code in ("portal.enabled", "portal.documents.download_enabled",
                 "portal.local_identity_provider_enabled", "portal.production_signed_off"):
        _enable(operator, code)
    report = governance.validate()
    assert report["ok"] is True, report["findings"]


# --- and is NOT a portal namespace escape hatch ------------------------------

def test_an_unlisted_portal_definition_is_still_an_orphan(temp_flag):
    """A future portal gate that nobody added to the exemption must still be reported."""
    code = temp_flag(f"portal.some_future_gate_{uuid.uuid4().hex[:6]}")
    report = governance.validate()
    assert code in _orphans(report), "unlisted portal code was silently exempted"
    assert report["ok"] is False


def test_a_non_portal_unused_feature_is_still_an_orphan(temp_flag):
    code = temp_flag(f"analytics.some_unused_thing_{uuid.uuid4().hex[:6]}")
    report = governance.validate()
    assert code in _orphans(report), "the orphan rule stopped working for non-portal codes"


def test_exemption_is_an_explicit_set_not_a_prefix():
    assert isinstance(governance._PORTAL_GATE_DEFINITIONS, frozenset)
    for entry in governance._PORTAL_GATE_DEFINITIONS:
        assert not entry.endswith("."), f"{entry!r} looks like a prefix, not an exact code"
    assert "portal." not in governance._INSTANCE_PREFIXES


# --- synchronization guard ----------------------------------------------------

def test_exemption_set_equals_portal_gates_exactly():
    """Adding a portal gate must force a deliberate governance decision here.

    If this fails, add the new code to ``_PORTAL_GATE_DEFINITIONS`` only after deciding the gate really is
    governed by the portal validator rather than by a runtime behavior."""
    exempt = set(governance._PORTAL_GATE_DEFINITIONS)
    gates = set(GATES)
    assert exempt == gates, (
        f"runtime-governance portal exemption is out of sync with app.portal.gate.GATES — "
        f"missing from exemption: {sorted(gates - exempt)}; "
        f"exempted but not a gate: {sorted(exempt - gates)}")
