"""Runtime Default Activation, Legacy Retirement, and Configuration Governance (Phase D.31).

Makes the D.28 runtime engine the AUTHORITATIVE source for the behaviors migrated in D.30. It seeds
D.27 runtime metadata (feature definitions + configuration items) so the engine drives each fixed
migrated behavior — behavior-preserving (the seeded values equal the legacy defaults) — retires the
legacy fallback for the fixed behaviors (keeping a documented compatibility shim), and migrates the
last legacy candidate (advisor-workspace section gating). Adds ``authoritative``/``compatibility_shim``
/``runtime_default`` columns to ``runtime_behaviors``.

The runtime engine remains the sole evaluator; D.29 coordination remains the sole synchronization
mechanism; D.27 remains the sole metadata owner (this migration seeds D.27 metadata rows). Reuses the
D.28 ``runtime.*`` capabilities (no new capabilities). Additive and reversible. Single Alembic head
(down ``z4e5f6a7b8c9``).
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "z8a9b0c1d2e3"
down_revision = "z4e5f6a7b8c9"
branch_labels = None
depends_on = None

# Feature definitions to seed ON (status active + enabled + rollout 100 → evaluates enabled=True).
_FLAGS = (
    "analytics.executive_metrics",
    "microsoft365.sync",
    "advisor_workspace.section.work",
    "advisor_workspace.section.tasks",
    "advisor_workspace.section.exceptions",
)

# Configuration items to seed (value equals the current app.config legacy default → behavior unchanged).
# (code, value_type, json_value)
_ITEMS = (
    ("benefits.new_hire_window_days", "integer", "30"),
    ("benefits.open_enrollment_warning_days", "integer", "7"),
    ("benefits.census_grace_days", "integer", "0"),
    ("benefits.document_grace_days", "integer", "0"),
    ("benefits.renewal_warning_days", "integer", "60"),
    ("microsoft365.sharepoint_site_ids", "string", json.dumps("")),
)

# Behaviors made runtime-authoritative and retired (fixed/enumerable definition sets seeded above).
_RETIRE = ("analytics.executive_metrics", "microsoft365.sync", "benefits.detector_windows",
           "microsoft365.sharepoint_scope")
# Per-instance behaviors that stay migrated compatibility-shims (unbounded key space; keep default).
_SHIMS = ("automation.job_dispatch", "reporting.optional_modules")


def upgrade():
    bind = op.get_bind()

    # --- registry columns ---------------------------------------------------
    op.add_column("runtime_behaviors",
                  sa.Column("authoritative", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("runtime_behaviors",
                  sa.Column("compatibility_shim", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("runtime_behaviors", sa.Column("runtime_default", sa.JSON))

    # --- seed D.27 feature definitions (runtime default activation) ---------
    for code in _FLAGS:
        if bind.execute(sa.text("SELECT id FROM configuration_feature_flags WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO configuration_feature_flags (code, name, status, enabled, rollout_percentage) "
                "VALUES (:c, :c, 'active', true, 100)"), {"c": code})

    # --- seed a configuration set + items (runtime default activation) ------
    set_id = bind.execute(sa.text("SELECT id FROM configuration_sets WHERE code='runtime-defaults'")).scalar()
    if set_id is None:
        set_id = bind.execute(sa.text(
            "INSERT INTO configuration_sets (code, name, status) "
            "VALUES ('runtime-defaults', 'Runtime Defaults', 'active') RETURNING id")).scalar()
    for code, vtype, jval in _ITEMS:
        if bind.execute(sa.text("SELECT id FROM configuration_items WHERE code=:c"), {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO configuration_items (set_id, code, name, value_type, value, status, version) "
                "VALUES (:s, :c, :c, :vt, CAST(:v AS json), 'active', 1)"),
                {"s": set_id, "c": code, "vt": vtype, "v": jval})

    # --- legacy retirement: mark the fixed behaviors authoritative + retired -
    for code in _RETIRE:
        bind.execute(sa.text(
            "UPDATE runtime_behaviors SET status='retired', authoritative=true, "
            "retired_at=now(), updated_at=now(), runtime_default=CAST(:d AS json) WHERE code=:c"),
            {"c": code, "d": json.dumps({"activated": True})})

    # --- advisor-workspace section gating: migrate + authoritative (fixed) ---
    bind.execute(sa.text(
        "UPDATE runtime_behaviors SET status='migrated', authoritative=true, "
        "migrated_at=now(), updated_at=now() WHERE code='advisor_workspace.sections'"))

    # --- per-instance behaviors: documented compatibility shims -------------
    for code in _SHIMS:
        bind.execute(sa.text(
            "UPDATE runtime_behaviors SET compatibility_shim=true, updated_at=now() WHERE code=:c"),
            {"c": code})


def downgrade():
    bind = op.get_bind()
    # revert registry statuses (best-effort; the D.30 seed statuses)
    for code in _RETIRE:
        bind.execute(sa.text("UPDATE runtime_behaviors SET status='migrated', authoritative=false, "
                             "retired_at=NULL, runtime_default=NULL WHERE code=:c"), {"c": code})
    bind.execute(sa.text("UPDATE runtime_behaviors SET status='legacy', authoritative=false, "
                         "migrated_at=NULL WHERE code='advisor_workspace.sections'"))
    for code in _SHIMS:
        bind.execute(sa.text("UPDATE runtime_behaviors SET compatibility_shim=false WHERE code=:c"), {"c": code})

    # remove seeded D.27 metadata
    for code in _FLAGS:
        bind.execute(sa.text("DELETE FROM configuration_feature_flags WHERE code=:c"), {"c": code})
    for code, _vt, _v in _ITEMS:
        bind.execute(sa.text("DELETE FROM configuration_items WHERE code=:c"), {"c": code})
    bind.execute(sa.text("DELETE FROM configuration_sets WHERE code='runtime-defaults'"))

    op.drop_column("runtime_behaviors", "runtime_default")
    op.drop_column("runtime_behaviors", "compatibility_shim")
    op.drop_column("runtime_behaviors", "authoritative")
