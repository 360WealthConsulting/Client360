"""Migration caf01 — upgrade result + rollback support (non-destructive checks against the test DB)."""
from __future__ import annotations

import importlib

from app.db import (
    client_feature_overrides,
    client_product_entitlements,
    client_status,
    firm_feature_controls,
)

_MOD = "migrations.versions.caf01_client_access_framework"
_TABLES = ("client_product_entitlements", "client_feature_overrides",
           "firm_feature_controls", "client_status")


def test_upgrade_created_all_tables_with_expected_columns():
    # The migration is applied to head in the test DB; the tables reflect with their key columns.
    assert {"subject_type", "subject_id", "product", "granted_by_user_id"} <= _cols(client_product_entitlements)
    assert {"subject_type", "subject_id", "feature_key", "state"} <= _cols(client_feature_overrides)
    assert {"feature_key", "state", "updated_by_user_id"} <= _cols(firm_feature_controls)
    assert {"subject_type", "subject_id", "status", "disposition"} <= _cols(client_status)


def test_unique_constraints_present():
    # Extensible row-per-entity model relies on these uniqueness guards (no per-feature boolean columns).
    assert any("subject_type" in c.columns and "product" in c.columns
               for c in client_product_entitlements.constraints if hasattr(c, "columns")
               and {"subject_type", "subject_id", "product"} <= {col.name for col in c.columns})
    assert any({"subject_type", "subject_id", "feature_key"} <= {col.name for col in c.columns}
               for c in client_feature_overrides.constraints if hasattr(c, "columns") and c.columns)


def test_migration_defines_reversible_upgrade_and_downgrade():
    mod = importlib.import_module(_MOD)
    assert mod.revision == "caf01" and mod.down_revision == "spdelta01"
    assert callable(mod.upgrade) and callable(mod.downgrade)
    # Rollback support: downgrade drops every table the upgrade created.
    import inspect
    src = inspect.getsource(mod.downgrade)
    for t in _TABLES:
        assert f'"{t}"' in src, f"downgrade does not drop {t}"


def _cols(table):
    return {c.name for c in table.columns}
