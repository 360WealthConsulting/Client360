"""Advisor Intelligence framework tests (Phase D.5A).

Covers the framework ONLY — signal/explainability/priority/policy-gate models, the
metadata registry, and the record-scoped composition layer. No rules are
registered to run, so every accessor returns an empty collection; these tests
prove the framework's shape and, crucially, that even empty responses respect
record scope (an inaccessible person/household never reaches a producer).
"""
import uuid

import pytest
from sqlalchemy import insert

from app.db import engine, households, people, record_assignments, users
from app.security.models import Principal
from app.services import advisor_intelligence as ai
from app.services.advisor_intelligence import (
    Explainability,
    PolicyGate,
    Priority,
    RegisteredSignal,
    Signal,
    SourceRecord,
    get_client_signals,
    get_dashboard_signals,
    get_household_signals,
    list_registered_signals,
    register_signal,
)

ADVISOR_CAPS = frozenset({"client.read", "client.write", "work.read", "task.read"})


@pytest.fixture(autouse=True)
def _clean_registry_and_producers():
    """Isolate the process-global registry + producer seam around every test."""
    saved_reg = dict(ai._REGISTRY)
    saved_prod = list(ai._PRODUCERS)
    ai.clear_registry()
    ai._PRODUCERS.clear()
    try:
        yield
    finally:
        ai._REGISTRY.clear()
        ai._REGISTRY.update(saved_reg)
        ai._PRODUCERS[:] = saved_prod


def _setup():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"ai-{tag}@e.test", normalized_email=f"ai-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = conn.execute(households.insert().values(
            name=f"HH {tag}").returning(households.c.id)).scalar_one()
        a = conn.execute(people.insert().values(full_name=f"A {tag}", primary_email=f"a{tag}@e.test",
            normalized_email=f"a{tag}@e.test", household_id=hh, active=True).returning(people.c.id)).scalar_one()
        b = conn.execute(people.insert().values(full_name=f"B {tag}", primary_email=f"b{tag}@e.test",
            normalized_email=f"b{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        other_hh = conn.execute(households.insert().values(
            name=f"HH2 {tag}").returning(households.c.id)).scalar_one()
        conn.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=a, assignment_type="owner",
            effective_date="2026-01-01"))
        conn.execute(insert(record_assignments).values(
            user_id=uid, entity_type="household", entity_id=hh, assignment_type="owner",
            effective_date="2026-01-01"))
    return {"uid": uid, "a": a, "b": b, "hh": hh, "other_hh": other_hh,
            "principal": Principal(uid, "a@e.com", "Adv", ADVISOR_CAPS)}


def _teardown(ids):
    with engine.begin() as conn:
        conn.execute(record_assignments.delete().where(record_assignments.c.user_id == ids["uid"]))
        conn.execute(people.delete().where(people.c.id.in_((ids["a"], ids["b"]))))
        conn.execute(households.delete().where(households.c.id.in_((ids["hh"], ids["other_hh"]))))


# --- registry ----------------------------------------------------------------

def test_empty_registry_lists_nothing():
    assert list_registered_signals() == ()


def test_signal_registration_and_ordering():
    register_signal("beneficiary_gap", category="compliance", source_service="portfolio",
                    default_priority=Priority.HIGH, policy_gate=PolicyGate.COMPLIANCE_REQUIRED,
                    description="Account without an active beneficiary.")
    register_signal("aging_contact", category="relationship", source_service="timeline",
                    default_priority=Priority.LOW)
    reg = list_registered_signals()
    assert [r.key for r in reg] == ["aging_contact", "beneficiary_gap"]  # sorted by key
    assert all(isinstance(r, RegisteredSignal) for r in reg)
    one = reg[1]
    assert one.category == "compliance"
    assert one.policy_gate is PolicyGate.COMPLIANCE_REQUIRED


def test_duplicate_registration_raises():
    register_signal("dup", category="x", source_service="s")
    with pytest.raises(ValueError):
        register_signal("dup", category="x", source_service="s")


def test_registration_does_not_execute_rules():
    # Registering never produces signals — the composition layer runs no rules.
    register_signal("would_fire", category="x", source_service="s")
    ids = _setup()
    try:
        assert get_dashboard_signals(ids["principal"]) == ()
        assert get_client_signals(ids["principal"], ids["a"]) == ()
    finally:
        _teardown(ids)


# --- models ------------------------------------------------------------------

def test_signal_serialization_is_json_safe():
    sig = Signal(
        id="sig-1", category="relationship", title="Aging contact",
        summary="No contact in 90 days.", source_service="timeline",
        source_record=SourceRecord("person", 42), severity="info",
        priority=Priority.MEDIUM, evidence=("last_contact=90d",),
        explainability=Explainability(why="deterministic rule", source_service="timeline",
                                      evidence=("last_contact=90d",), confidence=0.0,
                                      policy_gate=PolicyGate.NONE),
        policy_gate=PolicyGate.NONE, route="/people/42", status="open",
        created_at="2026-07-16T00:00:00Z")
    d = sig.to_dict()
    assert d["priority"] == "medium"
    assert d["policy_gate"] == "none"
    assert d["source_record"] == {"entity_type": "person", "entity_id": 42}
    assert d["explainability"]["confidence"] == 0.0
    assert d["explainability"]["policy_gate"] == "none"
    assert d["created_at"] == "2026-07-16T00:00:00Z"
    # Fully serializable (no enums/dataclasses leak through).
    import json
    json.loads(json.dumps(d))


def test_explainability_defaults_are_placeholders():
    ex = Explainability()
    assert ex.why == ""
    assert ex.evidence == ()
    assert ex.confidence == 0.0  # deterministic, never a probabilistic/AI score
    assert ex.policy_gate is PolicyGate.NONE


def test_priority_ordering_is_deterministic():
    shuffled = [Priority.LOW, Priority.CRITICAL, Priority.INFORMATIONAL,
                Priority.HIGH, Priority.MEDIUM]
    ordered = sorted(shuffled, key=lambda p: p.rank, reverse=True)
    assert ordered == [Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM,
                       Priority.LOW, Priority.INFORMATIONAL]
    assert Priority.CRITICAL.rank > Priority.INFORMATIONAL.rank


def test_policy_gate_model_has_expected_placeholders():
    assert {g.value for g in PolicyGate} == {
        "none", "compliance_required", "license_required", "suitability_required"}


# --- authorization -----------------------------------------------------------

def test_client_signals_respect_record_scope_before_any_producer():
    ids = _setup()
    seen: list = []
    # Probe the producer seam: a producer only runs AFTER scope is resolved.
    ai._PRODUCERS.append(lambda ctx: seen.append(ctx.person_id) or [])
    try:
        # Inaccessible person -> () and the producer is NEVER reached.
        assert get_client_signals(ids["principal"], ids["b"]) == ()
        assert seen == []
        # Accessible person -> still () (no real rules), producer sees only A.
        assert get_client_signals(ids["principal"], ids["a"]) == ()
        assert seen == [ids["a"]]
    finally:
        _teardown(ids)


def test_household_signals_respect_record_scope():
    ids = _setup()
    seen: list = []
    ai._PRODUCERS.append(lambda ctx: seen.append(ctx.household_id) or [])
    try:
        assert get_household_signals(ids["principal"], ids["other_hh"]) == ()
        assert seen == []
        assert get_household_signals(ids["principal"], ids["hh"]) == ()
        assert seen == [ids["hh"]]
    finally:
        _teardown(ids)


def test_dashboard_signals_are_book_scoped():
    ids = _setup()
    seen: list = []
    ai._PRODUCERS.append(lambda ctx: seen.append(ctx.person_ids) or [])
    try:
        assert get_dashboard_signals(ids["principal"]) == ()
        # The producer receives the advisor's accessible book (never firm-wide
        # for a scoped advisor); person A is in it, unassigned B is not.
        (scope,) = seen
        assert scope is not None
        assert ids["a"] in scope
        assert ids["b"] not in scope
    finally:
        _teardown(ids)


# --- content guarantees ------------------------------------------------------

def test_framework_emits_no_recommendation_or_ai_content():
    ids = _setup()
    try:
        for result in (get_dashboard_signals(ids["principal"]),
                       get_client_signals(ids["principal"], ids["a"]),
                       get_household_signals(ids["principal"], ids["hh"])):
            assert result == ()  # nothing generated — no recommendations, no advice
    finally:
        _teardown(ids)


def test_dashboard_panel_shows_empty_placeholder():
    from starlette.requests import Request

    from app.routes.workspace import workspace_dashboard
    ids = _setup()
    try:
        req = Request({"type": "http", "method": "GET", "path": "/workspace",
                       "headers": [], "query_string": b""})
        resp = workspace_dashboard(req, principal=ids["principal"])
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "Advisor Intelligence" in body
        assert "No advisor signals" in body
        assert "Phase D.5B" in body
    finally:
        _teardown(ids)
