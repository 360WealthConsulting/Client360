"""Portal runtime-gate metadata tests (migration ``9483fa25e622``).

``app/portal/gate.py`` evaluates every portal gate through the runtime engine with a production-safe
``default=False``. Before this migration none of those gates existed as D.27 metadata, so a seeded value
and an unresolvable runtime were indistinguishable — both produced the same all-closed result. These
tests prove the metadata now EXISTS, that every seeded value equals the former hard-coded default (so the
portal is exactly as closed as it was), and that the migration is idempotent and cleanly reversible.

``upgrade``/``downgrade`` are exercised against the migration module itself inside a transaction that is
always rolled back, so the suite proves reversibility without mutating the test database.
"""
import importlib.util
from pathlib import Path

from sqlalchemy import func, select, text

from app.db import (
    configuration_feature_flags,
    configuration_items,
    configuration_sets,
    engine,
    people,
    portal_access_grants,
    portal_accounts,
    portal_messages,
)
from app.portal.gate import GATES, gate_status
from app.services.runtime import consumption
from app.services.runtime.cache import RUNTIME_CACHE

MIGRATION = Path("migrations/versions/9483fa25e622_seed_portal_runtime_gates.py")

# The eight portal feature definitions, and the two configuration items, seeded by the migration.
PORTAL_FLAGS = (
    "portal.enabled",
    "portal.household_enabled",
    "portal.documents.download_enabled",
    "portal.documents.upload_enabled",
    "portal.messaging_enabled",
    "portal.appointments_enabled",
    "portal.financial_summary_enabled",
    "portal.forms_enabled",
)
PORTAL_ITEMS = ("portal.mfa_required", "portal.production_signed_off")

# Seeded by z8a9b0c1d2e3_runtime_authority — must survive this migration's downgrade.
FOREIGN_ITEMS = ("benefits.new_hire_window_days", "microsoft365.sharepoint_site_ids")


def _load_migration(conn):
    """Load the migration module with ``op`` stubbed to a caller-supplied connection."""
    spec = importlib.util.spec_from_file_location("mig_9483fa25e622", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _Op:
        def get_bind(self):
            return conn

    mod.op = _Op()
    return mod


def _counts(conn):
    return {
        "flags": conn.execute(select(func.count()).select_from(configuration_feature_flags)).scalar_one(),
        "items": conn.execute(select(func.count()).select_from(configuration_items)).scalar_one(),
        "accounts": conn.execute(select(func.count()).select_from(portal_accounts)).scalar_one(),
        "grants": conn.execute(select(func.count()).select_from(portal_access_grants)).scalar_one(),
        "people": conn.execute(select(func.count()).select_from(people)).scalar_one(),
        "messages": conn.execute(select(func.count()).select_from(portal_messages)).scalar_one(),
    }


# --- A/B: the eight feature definitions are seeded, and every one is closed ---

def test_all_eight_portal_features_are_seeded():
    with engine.connect() as c:
        found = set(c.scalars(select(configuration_feature_flags.c.code)
                              .where(configuration_feature_flags.c.code.in_(PORTAL_FLAGS))))
    assert found == set(PORTAL_FLAGS), f"missing portal feature definitions: {set(PORTAL_FLAGS) - found}"


def test_every_portal_feature_is_disabled_at_rollout_zero():
    with engine.connect() as c:
        rows = c.execute(
            select(configuration_feature_flags.c.code, configuration_feature_flags.c.status,
                   configuration_feature_flags.c.enabled, configuration_feature_flags.c.rollout_percentage)
            .where(configuration_feature_flags.c.code.in_(PORTAL_FLAGS))).all()
    assert len(rows) == 8
    for code, status, enabled, rollout in rows:
        assert status == "active", f"{code} status={status}"
        assert enabled is False, f"{code} is ENABLED — the portal must stay closed"
        assert rollout == 0, f"{code} rollout={rollout}"


# --- C/D: the two configuration items carry the production-safe values -------

def test_mfa_required_is_true_and_signed_off_is_false():
    assert consumption.config_value("portal.mfa_required", default=None) is True
    assert consumption.config_value("portal.production_signed_off", default=None) is False


def test_portal_items_are_active_version_one_booleans():
    with engine.connect() as c:
        rows = c.execute(
            select(configuration_items.c.code, configuration_items.c.value_type,
                   configuration_items.c.status, configuration_items.c.version)
            .where(configuration_items.c.code.in_(PORTAL_ITEMS))).all()
    assert len(rows) == 2
    for _code, value_type, status, version in rows:
        assert (value_type, status, version) == ("boolean", "active", 1)


def test_portal_items_live_in_the_shared_runtime_defaults_set():
    with engine.connect() as c:
        set_id = c.execute(select(configuration_sets.c.id)
                           .where(configuration_sets.c.code == "runtime-defaults")).scalar_one()
        owners = set(c.scalars(select(configuration_items.c.set_id)
                               .where(configuration_items.c.code.in_(PORTAL_ITEMS))))
    assert owners == {set_id}


# --- E: idempotent against pre-existing rows ---------------------------------

def test_upgrade_is_idempotent_and_creates_no_duplicates():
    with engine.connect() as c:
        trans = c.begin()
        try:
            before = _counts(c)
            _load_migration(c).upgrade()          # the database is ALREADY at this revision
            after = _counts(c)
            assert after["flags"] == before["flags"], "re-running upgrade duplicated feature flags"
            assert after["items"] == before["items"], "re-running upgrade duplicated configuration items"
            for code in PORTAL_FLAGS:
                n = c.execute(select(func.count()).select_from(configuration_feature_flags)
                              .where(configuration_feature_flags.c.code == code)).scalar_one()
                assert n == 1, f"{code} present {n} times"
        finally:
            trans.rollback()


# --- F/G/H: downgrade removes the portal rows and nothing else ---------------

def test_downgrade_removes_only_portal_metadata():
    with engine.connect() as c:
        trans = c.begin()
        try:
            before = _counts(c)
            _load_migration(c).downgrade()

            remaining_flags = set(c.scalars(select(configuration_feature_flags.c.code)
                                            .where(configuration_feature_flags.c.code.in_(PORTAL_FLAGS))))
            remaining_items = set(c.scalars(select(configuration_items.c.code)
                                            .where(configuration_items.c.code.in_(PORTAL_ITEMS))))
            assert remaining_flags == set(), f"downgrade left portal flags: {remaining_flags}"
            assert remaining_items == set(), f"downgrade left portal items: {remaining_items}"

            # G — the SHARED configuration set and its other owners survive.
            assert c.execute(select(func.count()).select_from(configuration_sets)
                             .where(configuration_sets.c.code == "runtime-defaults")).scalar_one() == 1
            survivors = set(c.scalars(select(configuration_items.c.code)
                                      .where(configuration_items.c.code.in_(FOREIGN_ITEMS))))
            assert survivors == set(FOREIGN_ITEMS), "downgrade removed another migration's items"

            after = _counts(c)
            assert before["flags"] - after["flags"] == 8
            assert before["items"] - after["items"] == 2

            # H — no portal account/grant/person/message row is touched.
            for domain in ("accounts", "grants", "people", "messages"):
                assert after[domain] == before[domain], f"downgrade modified {domain}"
        finally:
            trans.rollback()


def test_downgrade_leaves_no_orphaned_portal_metadata_behind():
    """Only the codes this migration introduced are deleted — no prefix over-match."""
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("INSERT INTO configuration_feature_flags (code, name, status, enabled, "
                           "rollout_percentage) VALUES ('portal.timeline.enabled', "
                           "'portal.timeline.enabled', 'active', false, 0)"))
            _load_migration(c).downgrade()
            still = c.execute(select(func.count()).select_from(configuration_feature_flags)
                              .where(configuration_feature_flags.c.code == "portal.timeline.enabled")).scalar_one()
            assert still == 1, "downgrade deleted a portal flag it did not create"
        finally:
            trans.rollback()


# --- runtime: the hydrated snapshot keeps every gate closed ------------------

def test_runtime_gate_status_matches_the_production_safe_defaults():
    RUNTIME_CACHE.invalidate()
    status = gate_status()
    assert status == {
        "portal.enabled": False,
        "portal.household_enabled": False,
        "portal.documents.download_enabled": False,
        "portal.documents.upload_enabled": False,
        "portal.messaging_enabled": False,
        "portal.appointments_enabled": False,
        "portal.financial_summary_enabled": False,
        "portal.forms_enabled": False,
        "portal.mfa_required": True,
        "portal.production_signed_off": False,
    }
    assert status == GATES, "seeded runtime values must equal the hard-coded production-safe defaults"


def test_no_portal_feature_evaluates_enabled():
    RUNTIME_CACHE.invalidate()
    ctx = consumption.runtime_context()
    for code in PORTAL_FLAGS:
        assert ctx.feature_enabled(code, False) is False, f"{code} evaluated ENABLED"


def test_portal_gates_are_runtime_defined_not_merely_defaulting():
    """The point of the migration: the values come from the snapshot, not the ``default=`` fallback."""
    RUNTIME_CACHE.invalidate()
    ctx = consumption.runtime_context()
    assert ctx.resolved is True, "runtime context did not resolve — gate values would be fallbacks"
    undefined = [c for c in PORTAL_FLAGS if not ctx.feature_defined(c)]
    assert undefined == [], f"portal features absent from the runtime snapshot: {undefined}"
