"""Enterprise Configuration platform tests (Phase D.27).

Covers configuration category/set/item CRUD + versioning + environment overrides, tenant/organization/
user preferences (+ record scope on org scope), feature groups/flags/rollouts (+ activation), edition
CRUD + edition capabilities (referencing RBAC capabilities) + license policies + edition assignments,
platform options, administrative policies, runtime-setting references, snapshots, configuration
changes (proposed→approved), the sensitive-value gate, Automation-dispatch/Analytics integration,
append-only audit ledger, migration seeds, and architecture invariants. The runtime config
(`app.config`), env loaders, RBAC, and D.5 golden are untouched.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, text, update

from app.db import (
    configuration_events,
    engine,
    organization_profiles,
    people,
    record_assignments,
    relationship_entities,
    users,
)
from app.security.models import Principal
from app.services.configuration import (
    catalog,
    editions,
    features,
    platform,
    preferences,
    scans,
)
from app.services.configuration import service as svc
from app.services.configuration.common import (
    ConfigurationError,
    audit_history,
)

CAPS = frozenset({"configuration.view", "configuration.manage", "configuration.execute",
                  "configuration.audit", "configuration.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup(with_org=False):
    tag = _sfx()
    org_id = None
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"cfg-{tag}@e.test", normalized_email=f"cfg-{tag}@e.test",
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
        if with_org:
            reid = c.execute(relationship_entities.insert().values(
                entity_type="organization", name=f"Org {tag}", details={}, active=True)
                .returning(relationship_entities.c.id)).scalar_one()
            org_id = c.execute(organization_profiles.insert().values(
                relationship_entity_id=reid, status="active", address_json={})
                .returning(organization_profiles.c.id)).scalar_one()
    return {"uid": uid, "stranger": stranger, "pid": pid, "tag": tag, "org_id": org_id}


def _set(ids):
    p = _principal(ids["uid"])
    return catalog.create_set(p, code=f"set-{ids['tag']}", name="Platform", actor_user_id=ids["uid"])


def _teardown(ids):
    uid = ids["uid"]
    with engine.begin() as c:
        for t in ("configuration_versions", "configuration_environment_overrides",
                  "configuration_snapshots"):
            c.execute(text(f"DELETE FROM {t}"))
        for t in ("configuration_changes", "configuration_edition_assignments",
                  "configuration_edition_capabilities", "configuration_license_policies",
                  "configuration_feature_rollouts", "configuration_feature_flags",
                  "configuration_feature_groups", "configuration_preferences",
                  "configuration_items", "configuration_sets", "configuration_editions",
                  "configuration_platform_options", "configuration_administrative_policies",
                  "configuration_runtime_setting_references"):
            c.execute(text(f"DELETE FROM {t} WHERE created_by_user_id = :u"), {"u": uid})
        # seeded categories are shared; delete only ones this user created
        c.execute(text("DELETE FROM configuration_categories WHERE created_by_user_id = :u"), {"u": uid})
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        if ids.get("org_id"):
            c.execute(delete(organization_profiles).where(organization_profiles.c.id == ids["org_id"]))
            c.execute(delete(relationship_entities).where(relationship_entities.c.name == f"Org {ids['tag']}"))
        # configuration_events (append-only) + audit-chain users are left as leftovers.


# --- categories / sets / items / versions / overrides ------------------------

def test_category_set_item_crud_and_versioning():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        cat = catalog.create_category(p, code=f"cat-{ids['tag']}", name="Cat", actor_user_id=ids["uid"])
        assert any(x["id"] == cat["id"] for x in catalog.list_categories())
        s = catalog.create_set(p, code=f"set-{ids['tag']}", name="Set", category_id=cat["id"],
                               actor_user_id=ids["uid"])
        assert s["status"] == "draft"
        item = catalog.create_item(p, set_id=s["id"], code=f"item-{ids['tag']}", name="Timeout",
                                   value_type="integer", value=30, default_value=30,
                                   actor_user_id=ids["uid"])
        assert item["version"] == 1
        updated = catalog.update_item_value(p, item["id"], 60, note="bump", actor_user_id=ids["uid"])
        assert updated["version"] == 2
        vers = catalog.list_versions(configuration_item_id=item["id"])
        assert len(vers) == 2 and vers[0]["version"] == 2
        with pytest.raises(ConfigurationError):
            catalog.create_item(p, set_id=s["id"], code=f"b-{ids['tag']}", name="x", value_type="nope",
                                actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_set_approval_and_item_status():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _set(ids)
        approved = catalog.set_set_status(p, s["id"], "approved", actor_user_id=ids["uid"])
        assert approved["status"] == "approved"
        assert any(e["event_type"] == "set_approved"
                   for e in audit_history(p, entity_type="set", entity_id=s["id"]))
    finally:
        _teardown(ids)


def test_sensitive_item_value_gated():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _set(ids)
        catalog.create_item(p, set_id=s["id"], code=f"sec-{ids['tag']}", name="Secret ref",
                            value_type="string", value="visible-secret", sensitive=True,
                            actor_user_id=ids["uid"])
        # non-audit principal cannot see the value
        viewer = _principal(ids["uid"], caps={"configuration.view"})
        items = catalog.list_items(viewer, set_id=s["id"])
        assert items[0]["sensitive"] is True and items[0]["value"] is None
        # audit principal can
        auditor = _principal(ids["uid"], caps={"configuration.view", "configuration.audit"})
        assert catalog.list_items(auditor, set_id=s["id"])[0]["value"] == "visible-secret"
    finally:
        _teardown(ids)


def test_environment_override():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _set(ids)
        item = catalog.create_item(p, set_id=s["id"], code=f"o-{ids['tag']}", name="Flag",
                                   value_type="boolean", actor_user_id=ids["uid"])
        ov = catalog.set_environment_override(p, item["id"], "staging", True, actor_user_id=ids["uid"])
        assert ov["environment"] == "staging" and ov["active"] is True
        # upsert same env
        ov2 = catalog.set_environment_override(p, item["id"], "staging", False, actor_user_id=ids["uid"])
        assert ov2["id"] == ov["id"]
        with pytest.raises(ConfigurationError):
            catalog.set_environment_override(p, item["id"], "nope", True, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- preferences (tenant / organization / user) + record scope ---------------

def test_tenant_and_user_preferences():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        t = preferences.set_preference(p, scope="tenant", preference_key="theme", value="dark",
                                       actor_user_id=ids["uid"])
        assert t["scope"] == "tenant"
        u = preferences.set_preference(p, scope="user", preference_key="digest", user_id=ids["uid"],
                                       reference="notification_preferences", actor_user_id=ids["uid"])
        assert u["scope"] == "user" and u["reference"] == "notification_preferences"
        with pytest.raises(ConfigurationError):
            preferences.set_preference(p, scope="organization", preference_key="x", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_organization_preference_record_scope():
    ids = _setup(with_org=True)
    try:
        p = _principal(ids["uid"])  # has record.write_all
        pref = preferences.set_preference(p, scope="organization", preference_key="branding",
                                          organization_id=ids["org_id"], value="logo",
                                          actor_user_id=ids["uid"])
        assert pref["organization_id"] == ids["org_id"]
        # a stranger without record.write_all and no org assignment cannot write the org preference
        stranger = _principal(ids["stranger"], caps={"configuration.manage"})
        with pytest.raises(ConfigurationError):
            preferences.set_preference(stranger, scope="organization", preference_key="branding2",
                                       organization_id=ids["org_id"], actor_user_id=ids["stranger"])
        # and cannot see it
        assert all(r["id"] != pref["id"] for r in preferences.list_preferences(stranger, scope="organization"))
    finally:
        _teardown(ids)


# --- feature management ------------------------------------------------------

def test_feature_group_flag_activation_and_rollout():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        g = features.create_group(p, code=f"fg-{ids['tag']}", name="Beta", actor_user_id=ids["uid"])
        flag = features.create_flag(p, code=f"ff-{ids['tag']}", name="New UI", feature_group_id=g["id"],
                                    rollout_percentage=10, runtime_setting_reference="app.config.automation_enabled",
                                    actor_user_id=ids["uid"])
        assert flag["enabled"] is False and flag["status"] == "draft"
        active = features.set_flag_status(p, flag["id"], "active", actor_user_id=ids["uid"])
        assert active["enabled"] is True and active["activation_starts_at"] is not None
        ro = features.create_rollout(p, flag["id"], stage="canary", percentage=25, actor_user_id=ids["uid"])
        assert ro["status"] == "planned"
        ro2 = features.set_rollout_status(p, ro["id"], "active", actor_user_id=ids["uid"])
        assert ro2["status"] == "active"
        with pytest.raises(ConfigurationError):
            features.create_flag(p, code=f"bad-{ids['tag']}", name="x", rollout_percentage=150,
                                 actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- editions / licensing ----------------------------------------------------

def test_edition_capabilities_and_license_and_assignment():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        ed = editions.create_edition(p, code=f"ed-{ids['tag']}", name="Enterprise", tier="enterprise",
                                     actor_user_id=ids["uid"])
        editions.set_edition_status(p, ed["id"], "active", actor_user_id=ids["uid"])
        # edition capability references an EXISTING RBAC capability
        cap = editions.add_edition_capability(p, ed["id"], "configuration.view", actor_user_id=ids["uid"])
        assert cap["capability_code"] == "configuration.view"
        with pytest.raises(ConfigurationError):
            editions.add_edition_capability(p, ed["id"], "not.a.real.capability", actor_user_id=ids["uid"])
        lic = editions.create_license_policy(p, code=f"lic-{ids['tag']}", name="Ent License",
                                             edition_id=ed["id"], max_users=50, actor_user_id=ids["uid"])
        asg = editions.assign_edition(p, edition_id=ed["id"], scope="tenant", license_policy_id=lic["id"],
                                      actor_user_id=ids["uid"])
        assert asg["status"] == "active"
        assert any(e["event_type"] == "edition_assigned"
                   for e in audit_history(p, entity_type="edition_assignment", entity_id=asg["id"]))
    finally:
        _teardown(ids)


# --- platform options / admin policies / runtime refs / snapshots / changes --

def test_platform_option_upsert_and_non_editable():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        opt = platform.upsert_option(p, code=f"opt-{ids['tag']}", name="Banner", option_type="string",
                                     value="hi", actor_user_id=ids["uid"])
        assert opt["code"].startswith("opt-")
        # make it non-editable then attempt update
        with engine.begin() as c:
            from app.db import configuration_platform_options as opts_t
            c.execute(update(opts_t).where(opts_t.c.id == opt["id"]).values(editable=False))
        with pytest.raises(ConfigurationError):
            platform.upsert_option(p, code=opt["code"], name="Banner2", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_administrative_policy_and_runtime_reference():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        pol = platform.create_admin_policy(p, code=f"ap-{ids['tag']}", name="Admin policy",
                                           actor_user_id=ids["uid"])
        approved = platform.set_admin_policy_status(p, pol["id"], "approved", actor_user_id=ids["uid"])
        assert approved["status"] == "approved" and approved["approved_by_user_id"] == ids["uid"]
        ref = platform.create_runtime_reference(p, code=f"rt-{ids['tag']}", name="Automation toggle",
                                                env_var="AUTOMATION_ENABLED",
                                                loader_reference="app.config.automation_enabled",
                                                value_type="boolean", actor_user_id=ids["uid"])
        assert ref["env_var"] == "AUTOMATION_ENABLED"
    finally:
        _teardown(ids)


def test_snapshot_and_change_workflow():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        snap = platform.capture_snapshot(p, actor_user_id=ids["uid"])
        assert snap["summary"] and platform.list_snapshots()
        ch = platform.propose_change(p, entity_type="item", entity_id=1, change_type="update",
                                     note="raise timeout", actor_user_id=ids["uid"])
        assert ch["status"] == "proposed"
        approved = platform.decide_change(p, ch["id"], "approved", actor_user_id=ids["uid"])
        assert approved["status"] == "approved" and approved["approved_by_user_id"] == ids["uid"]
    finally:
        _teardown(ids)


# --- automation / analytics --------------------------------------------------

def test_automation_dispatch_has_configuration_review():
    from app.services.automation import dispatch
    assert "configuration_review" in dispatch.DISPATCH_REGISTRY


def test_configuration_review_flags_invalid_items():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _set(ids)
        # active item referencing a runtime setting but with no value -> a validation finding
        item = catalog.create_item(p, set_id=s["id"], code=f"inv-{ids['tag']}", name="Invalid",
                                   value_type="string", runtime_setting_reference="app.config.automation_enabled",
                                   actor_user_id=ids["uid"])
        catalog.set_item_status(p, item["id"], "active", actor_user_id=ids["uid"])
        res = scans.run_due_reviews(p, actor_user_id=ids["uid"])
        assert res["validation_findings"] >= 1
        assert any(ch["entity_id"] == item["id"] for ch in platform.list_changes(status="proposed"))
    finally:
        _teardown(ids)


def test_analytics_consumes_configuration_metrics():
    ids = _setup()
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        p = _principal(ids["uid"])
        before = sources.configuration_enabled_feature_flag_count(p)
        flag = features.create_flag(p, code=f"an-{ids['tag']}", name="Flag", actor_user_id=ids["uid"])
        features.set_flag_status(p, flag["id"], "active", actor_user_id=ids["uid"])
        assert sources.configuration_enabled_feature_flag_count(p) == before + 1
        for key in ("configuration_enabled_feature_flags", "configuration_drift_overrides",
                    "configuration_active_editions", "configuration_pending_changes"):
            assert key in METRICS
    finally:
        _teardown(ids)


# --- overview facade ---------------------------------------------------------

def test_overview_metrics_aggregates():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.overview_metrics(p)
        for k in ("active_overrides", "enabled_feature_flags", "active_editions", "pending_changes",
                  "preferences", "platform_options"):
            assert k in m
    finally:
        _teardown(ids)


# --- append-only audit + architecture invariants -----------------------------

def test_audit_ledger_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        ed = editions.create_edition(p, code=f"au-{ids['tag']}", name="Ed", actor_user_id=ids["uid"])
        assert any(e["event_type"] == "edition_created"
                   for e in audit_history(p, entity_type="edition", entity_id=ed["id"]))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(configuration_events).where(configuration_events.c.entity_id == ed["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(configuration_events).where(configuration_events.c.entity_id == ed["id"]))
    finally:
        _teardown(ids)


def test_migration_seeds_categories_and_capabilities():
    with engine.connect() as c:
        cats = set(c.scalars(text("SELECT code FROM configuration_categories")))
        caps = set(c.scalars(text("SELECT code FROM capabilities WHERE code LIKE 'configuration.%'")))
    assert {"platform", "features", "licensing"} <= cats
    assert {"configuration.view", "configuration.manage", "configuration.execute",
            "configuration.audit", "configuration.admin"} <= caps


def test_configuration_does_not_import_composition_layers():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "catalog.py", "preferences.py", "features.py", "editions.py",
                 "platform.py", "scans.py", "common.py"):
        src = (root / name).read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{name}:{layer}"


def test_configuration_references_runtime_config_not_replaces_it():
    # Configuration must not re-read env or mutate app.config — it only references it as metadata.
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("catalog.py", "features.py", "platform.py", "editions.py", "preferences.py"):
        src = (root / name).read_text()
        assert "os.getenv" not in src and "os.environ" not in src, f"{name} must not read env"


def test_edition_capabilities_reference_rbac():
    import pathlib
    src = pathlib.Path(editions.__file__).read_text()
    assert "FROM capabilities WHERE code" in src  # validates against the authoritative RBAC catalog


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/configuration") for pattern, _cap in RULES)
    assert not any(pattern.search("/configuration/editions/5") for pattern, _cap in RULES)
