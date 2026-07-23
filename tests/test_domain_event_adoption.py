"""Enterprise Domain Event Producer Adoption tests (Phase D.35).

Covers each new contract, transactional + safe publishing, payload validation + sensitive-field
rejection, references-only schemas, an end-to-end real producer, governance detection (the new D.35
checks), producer-adoption diagnostics, analytics, the no-duplicate-event-table + no-direct-producer-
to-consumer-import + reuse-the-outbox invariants, and that nothing is delivered while the dispatcher is
disabled (behavior unchanged by default). The events flow over the existing transactional outbox.
"""
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.database.event_seed import ADOPTION_MODULES, D35_CONTRACTS_SEED
from app.db import domain_event_contracts, engine, metadata
from app.services.events import diagnostics, governance, publisher, registry
from app.services.events.common import EventError, reset_stats
from app.services.events.contracts import D35_EVENT_TYPES, EVENT_CONTRACTS
from app.services.events.payload_safety import sensitive_fields

outbox_events = metadata.tables["outbox_events"]


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _reset():
    reset_stats()
    yield
    reset_stats()


def _dummy_payload(schema: dict) -> dict:
    fill = {"int": 1, "str": "x", "float": 1.0, "bool": True, "list": [], "dict": {}}
    return {k: fill.get(v, "x") for k, v in (schema or {}).items()}


# --- contract catalog --------------------------------------------------------

def test_all_d35_contracts_registered():
    db = {c["event_type"] for c in registry.list_contracts()}
    for c in D35_CONTRACTS_SEED:
        assert c["event_type"] in db, c["event_type"]
    assert len(D35_EVENT_TYPES) == 31


def test_d35_contracts_are_references_only():
    # no registered D.35 contract may declare a prohibited sensitive field
    for et in D35_EVENT_TYPES:
        c = EVENT_CONTRACTS[et]
        assert sensitive_fields(c.payload_schema.keys()) == [], et


def test_every_d35_event_publishes_with_a_valid_payload():
    for et in sorted(D35_EVENT_TYPES):
        eid = publisher.publish(et, _dummy_payload(EVENT_CONTRACTS[et].payload_schema))
        with engine.connect() as c:
            row = c.execute(select(outbox_events).where(outbox_events.c.event_id == eid)).mappings().first()
        assert row is not None and row["name"] == et


# --- payload safety ----------------------------------------------------------

def test_sensitive_payload_rejected_for_business_event():
    with pytest.raises(EventError):
        publisher.publish("people.person_created", {"person_id": 1, "match_method": "m", "ssn": "x"})
    with pytest.raises(EventError):
        publisher.publish("insurance.policy_issued",
                          {"policy_id": 1, "status": "issued", "carrier_id": 1, "premium_amount": 100})


def test_publish_safe_never_raises_on_sensitive():
    assert publisher.publish_safe("people.person_created",
                                  {"person_id": 1, "match_method": "m", "email": "a@b.c"}) is None


# --- end-to-end real producer ------------------------------------------------

def test_real_producer_operations_task_created():
    from app.security.models import Principal
    from app.services.operations import tasks
    uid = _uid()
    p = Principal(uid, "a@e.test", "A", frozenset({"operations.manage", "record.read_all"}))
    before = _outbox_count_of("operations.task_created")
    t = tasks.create_task(p, title=f"T-{_tag()}", actor_user_id=uid)
    assert t["id"] and t["status"] == "planned"
    # the domain event landed in the outbox, references only
    with engine.connect() as c:
        row = c.execute(select(outbox_events).where(
            outbox_events.c.name == "operations.task_created").order_by(
                outbox_events.c.id.desc()).limit(1)).mappings().first()
    assert row is not None and row["payload"]["payload"]["task_id"] == t["id"]
    assert _outbox_count_of("operations.task_created") == before + 1
    assert "ssn" not in str(row["payload"])  # no sensitive data


def test_real_producer_transactional_rollback_leaves_no_event(monkeypatch):
    # if the business mutation raises after the publish, the event must roll back with it (same txn)
    from app.security.models import Principal
    from app.services.operations import tasks
    uid = _uid()
    p = Principal(uid, "a@e.test", "A", frozenset({"operations.manage", "record.read_all"}))
    before = _outbox_count_of("operations.task_created")
    orig = tasks.record_event

    def _boom(*a, **k):
        orig(*a, **k)
        raise RuntimeError("post-insert failure")
    monkeypatch.setattr(tasks, "record_event", _boom)
    with pytest.raises(RuntimeError):
        tasks.create_task(p, title=f"T-{_tag()}", actor_user_id=uid)
    monkeypatch.undo()
    assert _outbox_count_of("operations.task_created") == before   # rolled back with the mutation


# --- governance (D.35 detections) --------------------------------------------

def test_governance_passes():
    report = governance.validate()
    assert report["ok"] is True and report["issue_count"] == 0
    assert report["coverage"]["adopted_domains"] == 11


def test_governance_detects_sensitive_field_violation(monkeypatch):
    from app.services.events import contracts as contracts_mod
    bad = contracts_mod.EventContract("bad.sensitive", "people", "Bad", "people.service", 1,
                                      owner="people", payload_schema={"ssn": "str"})
    patched = dict(contracts_mod.EVENT_CONTRACTS)
    patched["bad.sensitive"] = bad
    monkeypatch.setattr(contracts_mod, "EVENT_CONTRACTS", patched)
    with engine.begin() as c:
        c.execute(domain_event_contracts.insert().values(event_type="bad.sensitive", category="people",
                  name="Bad", status="active", schema_version=1, producer="people.service",
                  payload_schema={"ssn": "str"}))
    try:
        report = governance.validate()
        assert any(f["type"] == "sensitive_field_violation" and f.get("field") == "ssn"
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(delete(domain_event_contracts).where(
                domain_event_contracts.c.event_type == "bad.sensitive"))


def test_governance_detects_producer_without_publishing_site():
    # register a D.35-style contract that no adoption module publishes → flagged
    et = "people.never_published"
    from app.services.events import contracts as contracts_mod
    contracts_mod.D35_EVENT_TYPES = frozenset(set(contracts_mod.D35_EVENT_TYPES) | {et})
    with engine.begin() as c:
        c.execute(domain_event_contracts.insert().values(event_type=et, category="people", name="NP",
                  status="active", schema_version=1, producer="people.service",
                  payload_schema={"person_id": "int"}))
    try:
        report = governance.validate()
        assert any(f["type"] == "producer_without_publishing_site" and f.get("event_type") == et
                   for f in report["findings"])
    finally:
        with engine.begin() as c:
            c.execute(delete(domain_event_contracts).where(domain_event_contracts.c.event_type == et))
        contracts_mod.D35_EVENT_TYPES = frozenset(set(contracts_mod.D35_EVENT_TYPES) - {et})


def test_governance_scan_finds_all_d35_sites():
    referenced, _literals = governance._scan_adoption()
    missing = set(D35_EVENT_TYPES) - referenced
    assert missing == set(), missing


# --- diagnostics + analytics -------------------------------------------------

def test_producer_adoption_full():
    pa = registry.producer_adoption()
    assert pa["adoption_pct"] == 100.0 and pa["stale_producers"] == 0
    assert pa["adopted_sites"] == pa["target_sites"] == 31
    assert pa["active_producers"] >= 11


def test_events_by_domain_breakdown():
    publisher.publish("operations.task_created",
                      {"task_id": 1, "project_id": 1, "status": "planned", "priority": "normal"})
    by = diagnostics.events_by_domain()
    assert by["published_by_domain"].get("operations", 0) >= 1


def test_analytics_producer_metrics():
    from app.services.analytics import sources
    from app.services.analytics.metrics import METRICS
    for key in ("domain_event_producer_adoption", "domain_event_active_producers",
                "domain_event_stale_producers", "domain_events_awaiting_delivery",
                "domain_event_adopted_domains"):
        assert key in METRICS
    assert sources.domain_event_producer_adoption_pct(None) == 100.0
    assert sources.domain_event_adopted_domain_count(None) == 11
    assert sources.domain_event_stale_producer_count(None) == 0


# --- architecture invariants -------------------------------------------------

def test_no_second_event_table():
    # domain events use the outbox log; only the contract/subscription registries are added
    assert "domain_event_log" not in metadata.tables and "event_log" not in metadata.tables
    assert {"domain_event_contracts", "domain_event_subscriptions"} <= set(metadata.tables)


def test_no_direct_producer_to_consumer_imports():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    for rel in ADOPTION_MODULES:
        src = (base / rel).read_text()
        # producers publish through the standardized publisher; they never import a consumer module
        assert "notification_intents" not in src or "publisher" in src
        assert "app.services.events.subscriptions" not in src


def test_producers_publish_only_through_the_standardized_publisher():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    for rel in ADOPTION_MODULES:
        src = (base / rel).read_text()
        if "publish_safe(" in src or "publisher.publish" in src:
            assert "from app.services.events import publisher" in src, rel


def test_no_delivery_while_dispatcher_disabled():
    from app.config import outbox_dispatcher_enabled
    assert outbox_dispatcher_enabled() is False        # default: dark-launched
    from app.services.events.subscriptions import delivered_count
    before = delivered_count()
    eid = publisher.publish("people.person_created", {"person_id": 1, "match_method": "m"})
    # the event is persisted pending, but no consumer runs (delivery count unchanged)
    with engine.connect() as c:
        row = c.execute(select(outbox_events).where(outbox_events.c.event_id == eid)).mappings().first()
    assert row["status"] == "pending"
    assert delivered_count() == before


def test_events_routes_include_producers():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/events/producers" in paths


# --- helpers -----------------------------------------------------------------

def _uid():
    from app.db import users
    with engine.begin() as c:
        t = _tag()
        return c.execute(users.insert().values(
            email=f"pa-{t}@e.test", normalized_email=f"pa-{t}@e.test", display_name="U",
            status="active").returning(users.c.id)).scalar_one()


def _outbox_count_of(name):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(outbox_events)
                        .where(outbox_events.c.name == name)) or 0
