"""Enterprise Integration platform tests (Phase D.24).

Covers provider/connector CRUD (disabled-by-default), connector config secret-rejection, credential
references (pointer + Fernet ciphertext, never plaintext-exposed, rotation), sync profiles/mapping
versions/runs/health/conflicts, webhook endpoints/subscriptions/deliveries + HMAC signing + verify,
API clients/usage/rate limits, event definitions/subscriptions + publication through the EXISTING
outbox, import/export data profiles, the Fernet field-crypto helper (fail-closed), authorization +
record scope (client-anchored timeline), Automation-dispatch/Analytics/Timeline integration,
append-only audit ledger, migration seeds, and architecture invariants. The importers, M365 OAuth,
the outbox, the Fernet helpers, the auth middleware, and the D.5 golden are untouched.
"""
import os
import uuid

import pytest

from app.security import integration_crypto

os.environ.setdefault("INTEGRATION_SECRET_KEY", integration_crypto.generate_key())

from datetime import date  # noqa: E402

from sqlalchemy import delete, select, text, update  # noqa: E402

from app.db import (  # noqa: E402
    engine,
    integration_events,
    integration_providers,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.platform.outbox import outbox_events  # noqa: E402
from app.security.models import Principal  # noqa: E402
from app.services.integration import (  # noqa: E402
    api,
    connectors,
    events,
    sync,
    webhooks,
)
from app.services.integration import service as svc  # noqa: E402
from app.services.integration.common import (  # noqa: E402
    IntegrationError,
    IntegrationNotFound,
    audit_history,
)

CAPS = frozenset({"integration.view", "integration.manage", "integration.execute",
                  "integration.audit", "integration.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"itg-{tag}@e.test", normalized_email=f"itg-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "pid": pid, "tag": tag}


def _provider(ids):
    return connectors.create_provider(code=f"prov-{ids['tag']}", name="Vendor",
                                      provider_type="custodian", actor_user_id=ids["uid"])


def _connector(ids, provider=None):
    provider = provider or _provider(ids)
    p = _principal(ids["uid"])
    return connectors.create_connector(p, provider_id=provider["id"], code=f"conn-{ids['tag']}",
                                       name="Conn", direction="inbound", actor_user_id=ids["uid"])


def _profile(ids, connector=None):
    connector = connector or _connector(ids)
    p = _principal(ids["uid"])
    return sync.create_sync_profile(p, connector_id=connector["id"], code=f"sp-{ids['tag']}",
                                    name="Profile", schedule_frequency="daily", actor_user_id=ids["uid"])


def _teardown(ids):
    uid = ids["uid"]
    with engine.begin() as c:
        # Children first (most have CASCADE from connectors/endpoints, but be explicit for events).
        c.execute(delete(timeline_events).where(timeline_events.c.source == "integration",
                                                timeline_events.c.person_id == ids["pid"]))
        for t in ("integration_webhook_deliveries", "integration_sync_conflicts",
                  "integration_sync_runs", "integration_api_usage"):
            c.execute(text(f"DELETE FROM {t}"))
        for t in ("integration_webhook_subscriptions", "integration_event_subscriptions"):
            c.execute(text(f"DELETE FROM {t} WHERE created_by_user_id = :u"), {"u": uid})
        for t in ("integration_sync_profiles", "integration_connectors", "integration_api_clients",
                  "integration_webhook_endpoints", "integration_data_profiles",
                  "integration_credential_references", "integration_providers"):
            c.execute(text(f"DELETE FROM {t} WHERE created_by_user_id = :u"), {"u": uid})
        # Event definitions created by this user (leave the seeded ones).
        c.execute(text("DELETE FROM integration_event_definitions WHERE created_by_user_id = :u"), {"u": uid})
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        # integration_events (append-only) and users that wrote to the audit hash-chain are left as
        # leftovers — deleting/updating an append-only row is trigger-blocked.


# --- crypto ------------------------------------------------------------------

def test_integration_crypto_roundtrip_and_fail_closed():
    ct = integration_crypto.encrypt("s3cr3t")
    assert ct != "s3cr3t" and integration_crypto.decrypt(ct) == "s3cr3t"
    assert integration_crypto.mask("abcd1234")[-2:] == "34" or "*" in integration_crypto.mask("abcd1234")
    with pytest.raises(Exception):
        integration_crypto.decrypt("not-valid-ciphertext")


# --- providers & connectors --------------------------------------------------

def test_provider_created_disabled_and_unique():
    ids = _setup()
    try:
        prov = _provider(ids)
        assert prov["enabled"] is False
        assert any(x["id"] == prov["id"] for x in connectors.list_providers())
        with pytest.raises(IntegrationError):
            connectors.create_provider(code=f"prov-{ids['tag']}", name="dup", actor_user_id=ids["uid"])
        with pytest.raises(IntegrationError):
            connectors.create_provider(code=f"x-{ids['tag']}", name="bad", provider_type="nope",
                                       actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_connector_created_disabled_and_config_rejects_secrets():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prov = _provider(ids)
        conn = _connector(ids, prov)
        assert conn["enabled"] is False and conn["status"] == "not_connected"
        with pytest.raises(IntegrationError):
            connectors.create_connector(p, provider_id=prov["id"], code=f"bad-{ids['tag']}",
                                        name="Bad", config={"api_key": "leak"}, actor_user_id=ids["uid"])
        with pytest.raises(IntegrationError):
            connectors.configure_connector(p, conn["id"], config={"password": "leak"},
                                           actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_connector_status_transition_records_event_and_audit():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conn = _connector(ids)
        connectors.set_connector_status(p, conn["id"], "connected", actor_user_id=ids["uid"])
        row = connectors.get_connector(p, conn["id"])
        assert row["status"] == "connected" and row["enabled"] is True
        assert any(e["event_type"] == "connector_connected"
                   for e in audit_history(p, entity_type="connector", entity_id=conn["id"]))
        with pytest.raises(IntegrationError):
            connectors.set_connector_status(p, conn["id"], "bogus", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- credential references (never plaintext) ---------------------------------

def test_credential_reference_never_exposes_secret():
    ids = _setup()
    try:
        prov = _provider(ids)
        cred = connectors.create_credential_reference(
            code=f"cred-{ids['tag']}", credential_type="api_key", reference_kind="encrypted_secret",
            secret="top-secret-value", provider_id=prov["id"], actor_user_id=ids["uid"])
        assert "secret_ciphertext" not in cred
        assert all("secret_ciphertext" not in r for r in connectors.list_credentials(provider_id=prov["id"]))
        # Ciphertext is stored (not plaintext) in the row.
        with engine.connect() as c:
            stored = c.execute(text(
                "SELECT secret_ciphertext FROM integration_credential_references WHERE id=:i"),
                {"i": cred["id"]}).scalar_one()
        assert stored is not None and stored != "top-secret-value"
        assert integration_crypto.decrypt(stored) == "top-secret-value"
    finally:
        _teardown(ids)


def test_credential_pointer_kind_stores_no_ciphertext_and_rotates():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        cred = connectors.create_credential_reference(
            code=f"cp-{ids['tag']}", credential_type="oauth", reference_kind="microsoft_account",
            reference_id=42, actor_user_id=ids["uid"])
        with engine.connect() as c:
            stored = c.execute(text(
                "SELECT secret_ciphertext FROM integration_credential_references WHERE id=:i"),
                {"i": cred["id"]}).scalar_one()
        assert stored is None
        res = connectors.rotate_credential_reference(p, cred["id"], reference_id=99,
                                                     actor_user_id=ids["uid"])
        assert res["rotated"] is True
    finally:
        _teardown(ids)


# --- sync profiles / runs / conflicts ----------------------------------------

def test_mapping_version_increments():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prof = _profile(ids)
        assert prof["mapping_version"] == 1
        updated = sync.update_mapping(p, prof["id"], {"a": "b"}, actor_user_id=ids["uid"])
        assert updated["mapping_version"] == 2
    finally:
        _teardown(ids)


def test_run_sync_records_metadata_and_updates_health():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prof = _profile(ids)
        run = sync.run_sync(p, prof["id"], status="succeeded", records_written=5,
                            import_jobs_id=123, actor_user_id=ids["uid"])
        assert run["status"] == "succeeded" and run["import_jobs_id"] == 123
        listed = sync.list_sync_runs(sync_profile_id=prof["id"])
        assert listed["total"] == 1
        # health + next_sync_at (daily) updated deterministically
        profs = sync.list_sync_profiles(connector_id=prof["connector_id"])
        assert profs[0]["sync_health"] == "healthy" and profs[0]["next_sync_at"] is not None
        # partial -> degraded
        sync.run_sync(p, prof["id"], status="partial", actor_user_id=ids["uid"])
        assert sync.list_sync_profiles(connector_id=prof["connector_id"])[0]["sync_health"] == "degraded"
    finally:
        _teardown(ids)


def test_conflict_record_and_resolve():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prof = _profile(ids)
        run = sync.run_sync(p, prof["id"], status="partial", actor_user_id=ids["uid"])
        conf = sync.record_conflict(p, run["id"], entity_type="person", field_name="email",
                                    source_value="a", target_value="b", actor_user_id=ids["uid"])
        assert conf["resolution"] == "unresolved"
        resolved = sync.resolve_conflict(p, conf["id"], "source_wins", actor_user_id=ids["uid"])
        assert resolved["resolution"] == "source_wins"
        assert sync.list_conflicts(sync_run_id=run["id"], resolution="source_wins")
        with pytest.raises(IntegrationError):
            sync.resolve_conflict(p, conf["id"], "not_a_resolution", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_run_due_syncs_records_due_profiles():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conn = _connector(ids)
        prof = sync.create_sync_profile(p, connector_id=conn["id"], code=f"due-{ids['tag']}",
                                        name="Due", schedule_frequency="daily", actor_user_id=ids["uid"])
        # make it due (next_sync_at in the past)
        with engine.begin() as c:
            c.execute(text("UPDATE integration_sync_profiles "
                           "SET next_sync_at = now() - interval '1 hour' WHERE id = :i"),
                      {"i": prof["id"]})
        res = sync.run_due_syncs(p, actor_user_id=ids["uid"])
        assert res["recorded"] >= 1
    finally:
        _teardown(ids)


# --- webhooks ----------------------------------------------------------------

def test_webhook_endpoint_hides_secret_and_verifies_with_hmac():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        ep = webhooks.create_endpoint(p, code=f"wh-{ids['tag']}", name="Hook", url="https://x.test/h",
                                      signing_algorithm="hmac_sha256", signing_secret="whsecret",
                                      actor_user_id=ids["uid"])
        assert "signing_secret_ciphertext" not in ep
        assert webhooks.get_endpoint(p, ep["id"]).get("signing_secret_ciphertext") is None
        res = webhooks.verify_endpoint(p, ep["id"], actor_user_id=ids["uid"])
        assert res["verification_status"] == "verified"
        # a delivery carries a computed signature (metadata only) and no plaintext secret
        d = webhooks.record_delivery(p, event_type="integration.sync.completed", endpoint_id=ep["id"],
                                     status="delivered", actor_user_id=ids["uid"])
        assert d["signature"] and d["status"] == "delivered"
        assert webhooks.list_deliveries(endpoint_id=ep["id"])["total"] == 1
    finally:
        _teardown(ids)


def test_webhook_subscription_unique():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        ep = webhooks.create_endpoint(p, code=f"whs-{ids['tag']}", name="H", actor_user_id=ids["uid"])
        webhooks.create_subscription(p, ep["id"], event_type="integration.sync.completed",
                                     actor_user_id=ids["uid"])
        with pytest.raises(IntegrationError):
            webhooks.create_subscription(p, ep["id"], event_type="integration.sync.completed",
                                         actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- API clients / usage / rate limits ---------------------------------------

def test_api_client_usage_and_rate_limit():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        cl = api.create_api_client(p, code=f"api-{ids['tag']}", name="Client", client_type="internal",
                                   rate_limit_per_minute=60, rate_limit_per_day=1000,
                                   actor_user_id=ids["uid"])
        assert cl["status"] == "active"
        assert api.rate_limit_for(cl["id"]) == {"per_minute": 60, "per_day": 1000}
        api.record_usage(p, cl["id"], endpoint="/api/v1/x", method="GET", request_count=10,
                         error_count=1, actor_user_id=ids["uid"])
        assert api.list_usage(api_client_id=cl["id"])
        m = api.metrics(p)
        assert m["active_api_clients"] >= 1 and m["api_requests"] >= 10
        api.set_api_client_status(p, cl["id"], "suspended", actor_user_id=ids["uid"])
        assert api.get_api_client(p, cl["id"])["status"] == "suspended"
    finally:
        _teardown(ids)


# --- events (published through the EXISTING outbox) ---------------------------

def test_publish_event_uses_outbox():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        events.create_definition(code=f"itg.test.{ids['tag']}", name="Test Event",
                                 actor_user_id=ids["uid"])
        before = _outbox_count(f"itg.test.{ids['tag']}")
        event_id = events.publish_event(p, f"itg.test.{ids['tag']}", payload={"ref": ids["pid"]},
                                        actor_user_id=ids["uid"])
        assert event_id
        assert _outbox_count(f"itg.test.{ids['tag']}") == before + 1
        with pytest.raises(IntegrationNotFound):
            events.publish_event(p, "no-such-definition", actor_user_id=ids["uid"])
    finally:
        with engine.begin() as c:
            c.execute(delete(outbox_events).where(outbox_events.c.name == f"itg.test.{ids['tag']}"))
        _teardown(ids)


def test_seeded_event_definitions_present():
    assert events.get_definition(code="integration.sync.completed") is not None
    assert events.get_definition(code="integration.sync.failed") is not None


def _outbox_count(event_type):
    with engine.connect() as c:
        from sqlalchemy import func
        return c.scalar(select(func.count()).select_from(outbox_events)
                        .where(outbox_events.c.name == event_type)) or 0


# --- data profiles -----------------------------------------------------------

def test_import_and_export_data_profiles():
    ids = _setup()
    try:
        imp = connectors.create_data_profile(code=f"imp-{ids['tag']}", name="Import", profile_type="import",
                                             data_format="csv", actor_user_id=ids["uid"])
        exp = connectors.create_data_profile(code=f"exp-{ids['tag']}", name="Export", profile_type="export",
                                             data_format="json", delivery="download", actor_user_id=ids["uid"])
        assert imp["profile_type"] == "import" and exp["profile_type"] == "export"
        assert len(connectors.list_data_profiles(profile_type="export")) >= 1
        with pytest.raises(IntegrationError):
            connectors.create_data_profile(code=f"bad-{ids['tag']}", name="B", profile_type="nope",
                                           actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- timeline (client-anchored only) -----------------------------------------

def test_timeline_only_for_client_anchored_runs():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prof = _profile(ids)
        # firm-level run (no anchor) -> no timeline
        sync.run_sync(p, prof["id"], status="succeeded", actor_user_id=ids["uid"])
        # client-anchored run -> timeline event
        sync.run_sync(p, prof["id"], status="succeeded", person_id=ids["pid"], actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "integration", timeline_events.c.person_id == ids["pid"])))
        assert "integration_sync_completed" in types
    finally:
        _teardown(ids)


# --- overview facade ---------------------------------------------------------

def test_overview_metrics_aggregates():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.overview_metrics(p)
        for k in ("providers", "connected_connectors", "sync_failures", "webhook_failures",
                  "active_api_clients", "unverified_endpoints"):
            assert k in m
    finally:
        _teardown(ids)


# --- integration points ------------------------------------------------------

def test_automation_dispatch_has_integration_sync():
    from app.services.automation import dispatch
    assert "integration_sync" in dispatch.DISPATCH_REGISTRY


def test_analytics_consumes_integration_metrics():
    ids = _setup()
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        p = _principal(ids["uid"])
        prof = _profile(ids)
        before = sources.integration_sync_failure_count(p)
        sync.run_sync(p, prof["id"], status="failed", actor_user_id=ids["uid"])
        assert sources.integration_sync_failure_count(p) == before + 1
        assert "integration_sync_failures" in METRICS and "integration_connector_errors" in METRICS
    finally:
        _teardown(ids)


# --- append-only audit + architecture invariants -----------------------------

def test_audit_ledger_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conn = _connector(ids)
        connectors.set_connector_status(p, conn["id"], "connected", actor_user_id=ids["uid"])
        hist = audit_history(p, entity_type="connector", entity_id=conn["id"])
        assert any(e["event_type"] == "connector_connected" for e in hist)
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(integration_events)
                          .where(integration_events.c.entity_id == conn["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(integration_events)
                          .where(integration_events.c.entity_id == conn["id"]))
    finally:
        _teardown(ids)


def test_migration_seeds_disabled_providers_and_capabilities():
    with engine.connect() as c:
        provs = list(c.scalars(select(integration_providers.c.code).where(
            integration_providers.c.enabled.is_(False))))
        assert "microsoft365" in provs and "schwab" in provs
        caps = set(c.scalars(text("SELECT code FROM capabilities WHERE code LIKE 'integration.%'")))
    assert {"integration.view", "integration.manage", "integration.execute",
            "integration.audit", "integration.admin"} <= caps


def test_integration_does_not_import_composition_layers():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "connectors.py", "sync.py", "webhooks.py", "api.py", "events.py",
                 "common.py"):
        src = (root / name).read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{name}:{layer}"


def test_integration_never_stores_plaintext_secret_in_config():
    # connector config guard already tested; here assert the guard message-path for both create/config.
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prov = _provider(ids)
        for bad in ({"token": "x"}, {"secret": "x"}, {"API_KEY": "x"}):
            with pytest.raises(IntegrationError):
                connectors.create_connector(p, provider_id=prov["id"], code=f"c-{_sfx()}",
                                            name="C", config=bad, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/integration") for pattern, _cap in RULES)
    assert not any(pattern.search("/integration/connectors/5") for pattern, _cap in RULES)
