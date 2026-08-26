"""Portal engagement must enforce a PER-CLIENT feature and project its rows (criterion #3, final task).

``portal_engagement`` was gated only by the firm-wide ``portal.timeline.enabled`` runtime flag plus
relationship scope — no per-client decision at all, and no ``portal_gate`` rule mapped its paths, so not
even middleware ``client_can`` covered it. Its rows were ``Interaction.to_dict()``, the INTERNAL model
serialization shared with staff surfaces, carrying ``source_system``, the internal ``visibility`` flag,
``related_person_id``/``related_household_id``/``related_business_id``, ``participants``, ``lifecycle``,
``retention_class`` and the raw ``interaction_id``.

The new ``client_timeline`` Core feature (firm-enabled, so existing clients keep the surface) supplies
the missing layer. The runtime gate remains a separate kill switch.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.db import client_feature_overrides, engine, firm_feature_controls
from app.portal import gate as portal_gate_module
from app.services.communications.engagement.service import portal_engagement
from app.services.features import catalog, portal_gate
from app.services.features import service as feat
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_master_on")

ROW_KEYS = {"interaction_type", "timestamp", "subject", "preview", "direction", "unread",
            "action_required", "deep_link"}

FORBIDDEN_FIELDS = {
    "interaction_id", "source_system", "visibility", "related_person_id", "related_household_id",
    "related_business_id", "participants", "lifecycle", "retention_class", "source_freshness",
    "attachments_available", "event_metadata", "external_id", "source", "organization_id",
    "person_id", "household_id", "audit_metadata", "raw_payload",
}


@pytest.fixture(autouse=True)
def _isolate_features():
    for t in (firm_feature_controls, client_feature_overrides):
        with engine.begin() as c:
            c.execute(delete(t))
    yield


@pytest.fixture
def timeline_on(monkeypatch):
    """The firm-wide runtime kill switch open, so the per-client decision is what's under test."""
    monkeypatch.setattr(portal_gate_module, "gate", lambda name: True)
    monkeypatch.setattr("app.services.communications.engagement.service.gate.enabled", lambda: True)
    monkeypatch.setattr("app.services.communications.engagement.service.gate.gate", lambda name: True)


def walk(value, path="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")


def assert_clean(payload, label):
    hits = [(p, k) for p, k, _v in walk(payload) if k in FORBIDDEN_FIELDS]
    assert hits == [], f"{label} disclosed internal field(s): {hits}"


def _client():
    return seed_portal_account(seed_staff_user(),
                               permissions={"messages": True, "documents": True, "tasks": True})


# --- the Core feature -----------------------------------------------------------

def test_client_timeline_is_a_core_feature_defaulting_to_firm_enabled():
    f = catalog.FEATURES.get("client_timeline")
    assert f is not None, "client_timeline is missing from the Core catalog"
    assert f.product == "core"
    assert f.default_firm_state == catalog.FIRM_ENABLED, \
        "default must be enabled so existing portal clients keep the surface"


def test_client_timeline_is_enabled_by_default_with_no_overrides():
    assert feat.firm_state("client_timeline") == "enabled"


# --- portal gate mapping --------------------------------------------------------

@pytest.mark.parametrize("path", ["/portal/engagement", "/api/v1/portal/engagement",
                                  "/api/portal/engagement"])
def test_engagement_paths_map_to_client_timeline(path):
    assert portal_gate.feature_for_request(path, "GET") == "client_timeline"


@pytest.mark.parametrize("path", ["/portal/documents", "/portal/billing", "/portal/messages",
                                  "/api/v1/portal/tasks"])
def test_unrelated_paths_do_not_map_to_client_timeline(path):
    assert portal_gate.feature_for_request(path, "GET") != "client_timeline"


# --- service-boundary authorization ---------------------------------------------

def test_runtime_gate_off_still_closes_the_surface():
    _acct, principal, _pid, _hid = _client()
    out = portal_engagement(principal)
    assert out["enabled"] is False and out["rows"] == []


def test_feature_off_closes_the_surface_on_a_direct_service_call(timeline_on):
    """Middleware is bypassed by calling the service directly; the feature must still be enforced."""
    _acct, principal, _pid, hid = _client()
    assert portal_engagement(principal)["enabled"] is True

    feat.set_override("household", hid, "client_timeline", "disable",
                      actor_user_id=seed_staff_user())
    out = portal_engagement(principal)
    assert out["rows"] == [], "engagement data served despite client_timeline disabled"
    assert out["unread"] == 0 and out["action_required"] == 0

    feat.set_override("household", hid, "client_timeline", "inherit",
                      actor_user_id=seed_staff_user())
    assert portal_engagement(principal)["enabled"] is True


def test_engagement_is_scoped_per_client(timeline_on):
    """A's engagement must never contain B's interactions."""
    from app.portal.service import create_thread
    _a, principal_a, pid_a, hid_a = _client()
    _b, principal_b, pid_b, hid_b = _client()
    create_thread(principal_a, household_id=hid_a, person_id=pid_a, subject="A only", body="a")
    create_thread(principal_b, household_id=hid_b, person_id=pid_b, subject="B only", body="b")

    a_subjects = {r["subject"] for r in portal_engagement(principal_a)["rows"]}
    b_subjects = {r["subject"] for r in portal_engagement(principal_b)["rows"]}
    assert "B only" not in a_subjects, "client A saw client B's interaction"
    assert "A only" not in b_subjects, "client B saw client A's interaction"


# --- projection and disclosure ---------------------------------------------------

def test_engagement_rows_have_an_exact_projected_key_set(timeline_on):
    from app.portal.service import create_thread
    _acct, principal, pid, hid = _client()
    create_thread(principal, household_id=hid, person_id=pid, subject="Question", body="hello")
    rows = portal_engagement(principal)["rows"]
    assert rows, "fixture produced no interaction"
    for r in rows:
        assert set(r) == ROW_KEYS, f"engagement row drifted: {set(r) ^ ROW_KEYS}"


def test_engagement_response_discloses_nothing_internal(timeline_on):
    from app.portal.service import create_thread
    _acct, principal, pid, hid = _client()
    create_thread(principal, household_id=hid, person_id=pid, subject="Question", body="hello")
    assert_clean(portal_engagement(principal), "portal_engagement")


def test_internal_model_serialization_is_unchanged_for_staff():
    """to_dict() is shared with staff surfaces and must keep its internal fields."""
    import inspect

    from app.services.communications.engagement import model
    src = inspect.getsource(model.Interaction.to_dict)
    for internal in ("source_system", "visibility", "related_person_id", "retention_class"):
        assert f'"{internal}"' in src, f"staff to_dict lost {internal}"
