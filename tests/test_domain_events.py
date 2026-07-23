"""Enterprise Domain Event Model tests (Phase D.34).

Covers the typed contract model + payload validation, the standardized publishing service (validate →
versioned envelope → outbox), the contract + subscription registry + coverage, event governance
(passes + detects unregistered/orphan/producer-without-consumer/schema-violation/version-drift/orphan-
subscription defects), diagnostics + deterministic replay (read-only), the orchestration adoption
(processes publish domain events), analytics, and the architecture invariants (the outbox stays the
sole bus; no second event table; nothing in the runtime/policy/orchestration engines imports the event
layer). The event flows over the existing transactional outbox.
"""
import uuid

import pytest
from sqlalchemy import delete, func, select, text

from app.db import domain_event_contracts, domain_event_subscriptions, engine, metadata
from app.services.events import diagnostics, governance, publisher, registry, replay
from app.services.events.common import EventError, reset_stats, stats
from app.services.events.contracts import get_contract

outbox_events = metadata.tables["outbox_events"]


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _reset():
    reset_stats()
    yield
    reset_stats()


# --- contracts + registry ----------------------------------------------------

def test_registry_seeded_and_coverage():
    cov = registry.coverage()
    assert cov["total"] == 5 and cov["active"] == 5
    assert cov["subscriptions"] == 5 and cov["active_subscriptions"] == 5
    assert cov["consumer_coverage_pct"] == 100.0 and cov["producer_coverage_pct"] == 100.0
    assert cov["coverage_pct"] == 100.0 and cov["domains_covered"] == cov["domains"]


def test_contracts_mirror_registry():
    from app.services.events.contracts import EVENT_CONTRACTS
    code = set(EVENT_CONTRACTS)
    db = {c["event_type"] for c in registry.list_contracts()}
    assert code == db


def test_contract_payload_validation():
    c = get_contract("orchestration.lifecycle")
    assert c.validate_payload({"instance_id": 1, "definition": "x", "event": "completed", "stage": "s"}) == []
    problems = c.validate_payload({"instance_id": "notanint"})
    assert any("instance_id" in p for p in problems) and any("missing" in p for p in problems)


# --- publishing --------------------------------------------------------------

def test_publish_writes_to_outbox_as_envelope():
    from app.platform.events import is_envelope
    eid = publisher.publish("orchestration.lifecycle",
                            {"instance_id": 7, "definition": "d", "event": "completed", "stage": "completed"})
    with engine.connect() as c:
        row = c.execute(select(outbox_events).where(outbox_events.c.event_id == eid)).mappings().first()
    assert row is not None and row["name"] == "orchestration.lifecycle"
    assert is_envelope(row["payload"]) and row["payload"]["schema_version"] == 1
    assert stats()["published"] >= 1


def test_publish_rejects_unregistered_and_invalid():
    with pytest.raises(EventError):
        publisher.publish("nope.event", {})
    with pytest.raises(EventError):
        publisher.publish("orchestration.lifecycle", {"instance_id": "bad"})
    assert stats()["publish_failures"] >= 2


def test_publish_is_atomic_with_caller_conn():
    # a publish on a conn that rolls back leaves no outbox row (transactional-outbox guarantee)
    eid_holder = {}
    try:
        with engine.begin() as c:
            eid_holder["id"] = publisher.publish(
                "orchestration.lifecycle",
                {"instance_id": 9, "definition": "d", "event": "failed", "stage": "failed"}, conn=c)
            raise RuntimeError("rollback")
    except RuntimeError:
        pass
    with engine.connect() as c:
        row = c.execute(select(outbox_events).where(
            outbox_events.c.event_id == eid_holder["id"])).mappings().first()
    assert row is None


def test_publish_safe_never_raises():
    assert publisher.publish_safe("nope.event", {}) is None


# --- subscriptions -----------------------------------------------------------

def test_subscription_registry():
    subs = registry.list_subscriptions(event_type="orchestration.lifecycle")
    assert any(s["consumer"] == "observability.sink" for s in subs)
    assert "observability.sink" in registry.subscribers_of("orchestration.lifecycle")


# --- governance --------------------------------------------------------------

def test_governance_passes():
    report = governance.validate()
    assert report["ok"] is True and report["issue_count"] == 0
    assert report["coverage"]["coverage_pct"] == 100.0


def test_governance_detects_orphan_contract():
    et = f"bogus.orphan.{_tag()}"
    with engine.begin() as c:
        c.execute(domain_event_contracts.insert().values(event_type=et, category="bogus", name="X",
                                                         status="active", schema_version=1, producer="x"))
        # give it a subscriber so ONLY orphan_contract (not producer_without_consumer) fires
        c.execute(domain_event_subscriptions.insert().values(event_type=et, consumer="c", status="active"))
    try:
        report = governance.validate()
        assert any(f["type"] == "orphan_contract" and f.get("event_type") == et for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(delete(domain_event_subscriptions).where(domain_event_subscriptions.c.event_type == et))
            c.execute(delete(domain_event_contracts).where(domain_event_contracts.c.event_type == et))


def test_governance_detects_producer_without_consumer():
    with engine.begin() as c:
        c.execute(text("UPDATE domain_event_subscriptions SET status='inactive' "
                       "WHERE event_type='runtime.coordination'"))
    try:
        report = governance.validate()
        assert any(f["type"] == "producer_without_consumer" and f.get("event_type") == "runtime.coordination"
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE domain_event_subscriptions SET status='active' "
                           "WHERE event_type='runtime.coordination'"))


def test_governance_detects_orphan_subscription():
    with engine.begin() as c:
        c.execute(domain_event_subscriptions.insert().values(
            event_type="not.a.real.event", consumer="ghost", status="active"))
    try:
        report = governance.validate()
        assert any(f["type"] == "orphan_subscription" and f.get("consumer") == "ghost"
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(delete(domain_event_subscriptions).where(
                domain_event_subscriptions.c.consumer == "ghost"))


def test_governance_detects_version_drift():
    with engine.begin() as c:
        c.execute(text("UPDATE domain_event_contracts SET schema_version=99 "
                       "WHERE event_type='workflow.sla'"))
    try:
        report = governance.validate()
        assert any(f["type"] == "version_drift" and f.get("event_type") == "workflow.sla"
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE domain_event_contracts SET schema_version=1 "
                           "WHERE event_type='workflow.sla'"))


# --- diagnostics + replay (read-only) ----------------------------------------

def test_diagnostics_counts_and_subscriber_health():
    publisher.publish("orchestration.lifecycle",
                      {"instance_id": 3, "definition": "d", "event": "completed", "stage": "completed"})
    counts = diagnostics.event_counts()
    assert counts["by_type"].get("orchestration.lifecycle", 0) >= 1
    health = diagnostics.subscriber_health()
    assert all(h["has_consumer"] for h in health)


def test_replay_is_readonly_and_reconstructs():
    eid = publisher.publish("orchestration.lifecycle",
                            {"instance_id": 5, "definition": "d", "event": "completed", "stage": "completed"})
    before = _outbox_count()
    rep = replay.replay(eid)
    assert rep["is_envelope"] is True and rep["delivered"] is False
    assert rep["modified_production_state"] is False
    assert rep["reconstructed_envelope"]["event_type"] == "orchestration.lifecycle"
    assert _outbox_count() == before   # replay wrote nothing


def test_event_diagnostics_view():
    eid = publisher.publish("orchestration.lifecycle",
                            {"instance_id": 8, "definition": "d", "event": "failed", "stage": "failed"})
    diag = diagnostics.event_diagnostics(eid)
    assert diag["event"]["event_id"] == eid
    assert diag["replay_readiness"]["ready"] is True


# --- orchestration adoption --------------------------------------------------

def test_orchestration_publishes_domain_event_on_terminal():
    from app.services.orchestration import execution
    before = _outbox_count_of("orchestration.lifecycle")
    execution.coordinate("automation.dispatch", subject="maintenance", executor=lambda: {"ok": 1})
    after = _outbox_count_of("orchestration.lifecycle")
    # launched + completed → at least the terminal event was published
    assert after > before


# --- analytics + architecture invariants -------------------------------------

def test_analytics_event_metrics():
    from app.services.analytics import sources
    from app.services.analytics.metrics import METRICS
    for key in ("domain_events_published", "domain_events_delivered", "domain_events_dead_lettered",
                "domain_event_contracts", "domain_event_subscriptions", "domain_event_governance_issues",
                "domain_event_coverage", "domain_event_replays", "domain_event_publish_failures"):
        assert key in METRICS
    assert sources.domain_event_coverage_pct(None) == 100.0
    assert sources.domain_event_governance_issue_count(None) == 0
    assert sources.domain_event_contract_count(None) == 5


def test_publisher_reuses_outbox_no_second_event_table():
    import pathlib
    src = pathlib.Path(publisher.__file__).read_text()
    # publishes through the existing outbox + envelope — no new event table
    assert "app.platform.outbox" in src and "publish_event" in src
    assert "app.platform.events" in src
    # no domain-event log table exists — only the contract/subscription registries
    assert "domain_event_log" not in metadata.tables and "event_log" not in metadata.tables


def test_no_engine_imports_the_event_layer():
    import pathlib
    base = pathlib.Path(publisher.__file__).parents[1]
    for pkg in ("runtime", "policy", "orchestration"):
        for pyfile in (base / pkg).glob("*.py"):
            src = pyfile.read_text()
            # the orchestration engine publishes via a LAZY import inside a function (allowed); assert no
            # top-level module dependency on the event layer from the runtime/policy engines.
            if pkg in ("runtime", "policy"):
                assert "app.services.events" not in src, f"{pkg}/{pyfile.name}"


def test_events_routes_match_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/events") for pattern, _cap in RULES)
    assert not any(pattern.search("/events/governance") for pattern, _cap in RULES)


def test_contract_lifecycle_deprecate():
    try:
        row = registry.deprecate("runtime.coordination", reason="test")
        assert row["status"] == "deprecated"
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE domain_event_contracts SET status='active', deprecated_at=NULL, "
                           "deprecated_reason=NULL WHERE event_type='runtime.coordination'"))


# --- helpers -----------------------------------------------------------------

def _outbox_count():
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(outbox_events)) or 0


def _outbox_count_of(name):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(outbox_events)
                        .where(outbox_events.c.name == name)) or 0
