"""Enterprise Security platform tests (Phase D.25).

Covers security-policy CRUD + approval, configuration baselines, identity/authentication/federation
provider CRUD (disabled-by-default), role-policy & capability-policy variants, password/MFA/session
policy variants, secret references (pointer + Fernet ciphertext, never plaintext-exposed) + rotation,
certificate references + renewal + expiry review, security incidents (lifecycle + client-anchored
timeline + record scope), security findings (reference governance findings), security exceptions,
the fail-closed crypto helper, Automation-dispatch/Analytics/Timeline integration, append-only audit
ledger, migration seeds, and architecture invariants. The authentication, session, Microsoft 365
OAuth, RBAC middleware, existing Fernet helpers, audit hash-chain, and D.5 golden are untouched.
"""
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.security import security_crypto

os.environ.setdefault("SECURITY_SECRET_KEY", security_crypto.generate_key())

from sqlalchemy import delete, select, text, update  # noqa: E402

from app.db import (  # noqa: E402
    engine,
    people,
    record_assignments,
    security_certificate_references,
    security_events,
    security_secret_references,
    timeline_events,
    users,
)
from app.security.models import Principal  # noqa: E402
from app.services.security import (  # noqa: E402
    common,
    incidents,
    policies,
    providers,
    scans,
    secrets,
)
from app.services.security import service as svc  # noqa: E402
from app.services.security.common import (  # noqa: E402
    SecurityError,
    SecurityNotFound,
    audit_history,
)

CAPS = frozenset({"security.view", "security.manage", "security.execute", "security.audit",
                  "security.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"sec-{tag}@e.test", normalized_email=f"sec-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        stranger = c.execute(users.insert().values(
            email=f"str-{tag}@e.test", normalized_email=f"str-{tag}@e.test",
            display_name=f"S {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "stranger": stranger, "pid": pid, "tag": tag}


def _teardown(ids):
    uid = ids["uid"]
    with engine.begin() as c:
        c.execute(delete(timeline_events).where(timeline_events.c.source == "security",
                                                timeline_events.c.person_id == ids["pid"]))
        for t in ("security_findings", "security_exceptions", "security_incidents",
                  "security_identity_providers", "security_certificate_references",
                  "security_secret_references", "security_configurations", "security_policies"):
            c.execute(text(f"DELETE FROM {t} WHERE created_by_user_id = :u"), {"u": uid})
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        # security_events (append-only) and users that wrote to the audit hash-chain are left as
        # leftovers — deleting/updating an append-only row is trigger-blocked.


# --- crypto ------------------------------------------------------------------

def test_security_crypto_roundtrip_and_fail_closed():
    ct = security_crypto.encrypt("s3cr3t")
    assert ct != "s3cr3t" and security_crypto.decrypt(ct) == "s3cr3t"
    assert security_crypto.mask("abcd1234").endswith("1234")
    with pytest.raises(Exception):
        security_crypto.decrypt("not-valid-ciphertext")


# --- policies ----------------------------------------------------------------

def test_policy_crud_and_types_and_approval():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        # every policy variant the phase enumerates is a policy_type on the unified table
        for ptype in ("security", "session", "password", "mfa", "access", "capability", "role",
                      "api", "encryption", "key_rotation", "authentication", "federation"):
            pol = policies.create_policy(p, code=f"pol-{ptype}-{ids['tag']}", name=f"{ptype} policy",
                                         policy_type=ptype, actor_user_id=ids["uid"])
            assert pol["status"] == "draft" and pol["policy_type"] == ptype
        with pytest.raises(SecurityError):
            policies.create_policy(p, code=f"bad-{ids['tag']}", name="x", policy_type="nope",
                                   actor_user_id=ids["uid"])
        # secret-looking config is rejected
        with pytest.raises(SecurityError):
            policies.create_policy(p, code=f"leak-{ids['tag']}", name="x", config={"api_key": "z"},
                                   actor_user_id=ids["uid"])
        # approve
        first = policies.list_policies(policy_type="password")[0]
        approved = policies.set_policy_status(p, first["id"], "approved", actor_user_id=ids["uid"])
        assert approved["status"] == "approved" and approved["approved_by_user_id"] == ids["uid"]
        # an approved policy is immutable config
        with pytest.raises(SecurityError):
            policies.update_policy(p, first["id"], config={"len": 12}, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_configuration_baselines_seeded_and_upsert():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        seeded = {c["config_key"] for c in policies.list_configurations()}
        assert "mfa.required" in seeded and "audit.hash_chain_enabled" in seeded
        row = policies.upsert_configuration(p, config_key=f"custom.{ids['tag']}", name="Custom",
                                            category="hardening", applied=True, actor_user_id=ids["uid"])
        assert row["applied"] is True
        with pytest.raises(SecurityError):
            policies.upsert_configuration(p, config_key=f"c2.{ids['tag']}", name="x",
                                          category="not_a_category", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- providers ---------------------------------------------------------------

def test_provider_crud_disabled_by_default():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        prov = providers.create_provider(p, code=f"idp-{ids['tag']}", name="Entra",
                                         provider_kind="identity", protocol="oidc",
                                         actor_user_id=ids["uid"])
        assert prov["enabled"] is False and prov["status"] == "configured"
        enabled = providers.set_provider_status(p, prov["id"], "enabled", actor_user_id=ids["uid"])
        assert enabled["enabled"] is True
        with pytest.raises(SecurityError):
            providers.create_provider(p, code=f"b-{ids['tag']}", name="x", provider_kind="nope",
                                      actor_user_id=ids["uid"])
        with pytest.raises(SecurityError):
            providers.create_provider(p, code=f"s-{ids['tag']}", name="x", config={"secret": "z"},
                                      actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- secret references (never plaintext) -------------------------------------

def test_secret_reference_never_exposes_ciphertext_and_rotates():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = secrets.create_secret_reference(p, code=f"sec-{ids['tag']}", name="API secret",
                                            reference_kind="encrypted_secret", secret="top-secret",
                                            rotation_schedule="monthly", actor_user_id=ids["uid"])
        assert "secret_ciphertext" not in s and s["next_rotation_at"] is not None
        assert all("secret_ciphertext" not in r for r in secrets.list_secret_references())
        # ciphertext stored, not plaintext
        with engine.connect() as c:
            stored = c.execute(text("SELECT secret_ciphertext FROM security_secret_references WHERE id=:i"),
                               {"i": s["id"]}).scalar_one()
        assert stored is not None and stored != "top-secret"
        assert security_crypto.decrypt(stored) == "top-secret"
        # rotate
        rotated = secrets.rotate_secret_reference(p, s["id"], secret="new-secret",
                                                  actor_user_id=ids["uid"])
        assert "secret_ciphertext" not in rotated and rotated["last_rotated_at"] is not None
        with engine.connect() as c:
            stored2 = c.execute(text("SELECT secret_ciphertext FROM security_secret_references WHERE id=:i"),
                                {"i": s["id"]}).scalar_one()
        assert security_crypto.decrypt(stored2) == "new-secret"
    finally:
        _teardown(ids)


def test_secret_pointer_kind_stores_no_ciphertext():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = secrets.create_secret_reference(p, code=f"ptr-{ids['tag']}", name="M365 token",
                                            reference_kind="microsoft_account", reference_id=7,
                                            actor_user_id=ids["uid"])
        with engine.connect() as c:
            stored = c.execute(text("SELECT secret_ciphertext FROM security_secret_references WHERE id=:i"),
                               {"i": s["id"]}).scalar_one()
        assert stored is None
    finally:
        _teardown(ids)


# --- certificate references --------------------------------------------------

def test_certificate_reference_and_renew():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        cert = secrets.create_certificate_reference(p, code=f"crt-{ids['tag']}", name="TLS cert",
                                                    subject="cn=example", fingerprint="ab:cd",
                                                    actor_user_id=ids["uid"])
        assert cert["status"] == "valid"
        renewed = secrets.renew_certificate_reference(p, cert["id"],
                                                     not_after=datetime.now(UTC) + timedelta(days=365),
                                                     actor_user_id=ids["uid"])
        assert renewed["last_renewed_at"] is not None and renewed["status"] == "valid"
    finally:
        _teardown(ids)


# --- incidents (lifecycle + record scope + timeline) -------------------------

def test_incident_lifecycle_and_client_anchor_timeline():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        inc = incidents.open_incident(p, code=f"inc-{ids['tag']}", title="Suspicious login",
                                      severity="high", person_id=ids["pid"], actor_user_id=ids["uid"])
        assert inc["status"] == "open"
        incidents.set_incident_status(p, inc["id"], "investigating", actor_user_id=ids["uid"])
        resolved = incidents.set_incident_status(p, inc["id"], "resolved", actor_user_id=ids["uid"])
        assert resolved["status"] == "resolved" and resolved["resolved_at"] is not None
        # client-anchored → timeline events published
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "security", timeline_events.c.person_id == ids["pid"])))
        assert "security_incident_opened" in types and "security_incident_resolved" in types
    finally:
        _teardown(ids)


def test_firm_level_incident_skips_timeline():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        inc = incidents.open_incident(p, code=f"firm-{ids['tag']}", title="Firm incident",
                                      severity="low", actor_user_id=ids["uid"])
        # no client anchor → the guarded publish_timeline is a no-op (timeline requires person/hh)
        with engine.connect() as c:
            row = c.execute(select(timeline_events.c.id).where(
                timeline_events.c.external_id == f"security-incident_opened-{inc['id']}")).first()
        assert row is None
        # but the append-only ledger still recorded it firm-level
        assert any(e["event_type"] == "incident_opened"
                   for e in audit_history(p, entity_type="incident", entity_id=inc["id"]))
    finally:
        _teardown(ids)


def test_incident_scope_blocks_stranger():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        inc = incidents.open_incident(p, code=f"sc-{ids['tag']}", title="Scoped", severity="medium",
                                      person_id=ids["pid"], actor_user_id=ids["uid"])
        # stranger has no record.read_all and no assignment → cannot see the client-anchored incident
        stranger = _principal(ids["stranger"], caps={"security.view"})
        with pytest.raises(SecurityNotFound):
            incidents.get_incident(stranger, inc["id"])
        assert all(i["id"] != inc["id"] for i in incidents.list_incidents(stranger))
    finally:
        _teardown(ids)


# --- findings + exceptions ---------------------------------------------------

def test_finding_references_governance_and_status():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        f = incidents.create_finding(p, title="Weak policy", severity="medium", source="governance",
                                     governance_finding_id=999, actor_user_id=ids["uid"])
        assert f["governance_finding_id"] == 999 and f["status"] == "open"
        done = incidents.set_finding_status(p, f["id"], "remediated", actor_user_id=ids["uid"])
        assert done["status"] == "remediated" and done["resolved_at"] is not None
        with pytest.raises(SecurityError):
            incidents.create_finding(p, title="x", source="not_a_source", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_exception_request_and_decision():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        exc = incidents.request_exception(p, code=f"exc-{ids['tag']}", title="Temp waiver",
                                          justification="pilot", actor_user_id=ids["uid"])
        assert exc["status"] == "requested"
        approved = incidents.decide_exception(p, exc["id"], "approved", actor_user_id=ids["uid"])
        assert approved["status"] == "approved" and approved["approved_by_user_id"] == ids["uid"]
    finally:
        _teardown(ids)


# --- automation / analytics / reviews ----------------------------------------

def test_automation_dispatch_has_security_review():
    from app.services.automation import dispatch
    assert "security_review" in dispatch.DISPATCH_REGISTRY


def test_security_review_flags_overdue_and_expiring():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = secrets.create_secret_reference(p, code=f"due-{ids['tag']}", name="Due secret",
                                            reference_kind="encrypted_secret", secret="x",
                                            rotation_schedule="monthly", actor_user_id=ids["uid"])
        # force overdue
        with engine.begin() as c:
            c.execute(update(security_secret_references).where(security_secret_references.c.id == s["id"])
                      .values(next_rotation_at=datetime.now(UTC) - timedelta(days=1)))
        cert = secrets.create_certificate_reference(p, code=f"exp-{ids['tag']}", name="Expiring",
                                                    actor_user_id=ids["uid"])
        with engine.begin() as c:
            c.execute(update(security_certificate_references)
                      .where(security_certificate_references.c.id == cert["id"])
                      .values(not_after=datetime.now(UTC) - timedelta(days=1)))
        res = scans.run_due_reviews(p, actor_user_id=ids["uid"])
        assert res["secrets_flagged"] >= 1 and res["certificates_flagged"] >= 1
        # certificate deterministically marked expired
        assert any(c["status"] == "expired" for c in secrets.list_certificates())
    finally:
        _teardown(ids)


def test_analytics_consumes_security_metrics():
    ids = _setup()
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        p = _principal(ids["uid"])
        before = sources.security_open_finding_count(p)
        incidents.create_finding(p, title="A finding", actor_user_id=ids["uid"])
        assert sources.security_open_finding_count(p) == before + 1
        for key in ("security_open_findings", "security_open_incidents", "security_overdue_rotations",
                    "security_expired_certificates", "security_mfa_enabled_users"):
            assert key in METRICS
    finally:
        _teardown(ids)


# --- overview facade ---------------------------------------------------------

def test_overview_metrics_aggregates():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.overview_metrics(p)
        for k in ("active_policies", "enabled_providers", "overdue_secret_rotations",
                  "expired_certificates", "open_incidents", "open_findings", "pending_exceptions"):
            assert k in m
    finally:
        _teardown(ids)


# --- append-only audit + architecture invariants -----------------------------

def test_audit_ledger_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        inc = incidents.open_incident(p, code=f"au-{ids['tag']}", title="Audit", severity="low",
                                      actor_user_id=ids["uid"])
        assert any(e["event_type"] == "incident_opened"
                   for e in audit_history(p, entity_type="incident", entity_id=inc["id"]))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(security_events).where(security_events.c.entity_id == inc["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(security_events).where(security_events.c.entity_id == inc["id"]))
    finally:
        _teardown(ids)


def test_migration_seeds_capabilities():
    with engine.connect() as c:
        caps = set(c.scalars(text("SELECT code FROM capabilities WHERE code LIKE 'security.%'")))
    assert {"security.view", "security.manage", "security.execute", "security.audit",
            "security.admin"} <= caps


def test_security_does_not_import_composition_layers():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "policies.py", "providers.py", "secrets.py", "incidents.py",
                 "scans.py", "common.py"):
        src = (root / name).read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{name}:{layer}"


def test_security_reuses_shared_crypto_not_a_duplicate():
    # The domain must reference the shared, fail-closed helper — not embed its own Fernet logic.
    import pathlib
    src = (pathlib.Path(common.__file__)).read_text()
    assert "security_crypto" in src


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/security") for pattern, _cap in RULES)
    assert not any(pattern.search("/security/incidents/5") for pattern, _cap in RULES)
